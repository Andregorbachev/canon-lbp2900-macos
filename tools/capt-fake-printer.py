#!/usr/bin/env python3
"""
capt-fake-printer.py - run captdriver's `rastertocapt` filter against an emulated
Canon LBP2900, without a printer attached.

A CUPS filter talks to the printer through three channels that the CUPS backend
normally provides.  This script provides them itself:

  filter stdout  -> printer : CAPT packets (parsed and answered here)
  fd 3 (pipe)    <- printer : back channel, replies to CAPT commands
  fd 4 (socket)  <->        : CUPS side channel (DRAIN_OUTPUT, GET_DEVICE_ID, GET_BIDI)

The emulated printer follows the state machine reverse-engineered in captdriver's
SPECS file and src/prn_lbp2900.c.  It is only a plumbing test: it proves that the
macOS build of the filter drives all three channels correctly and produces a
decodable Hi-SCoA page stream.  It says nothing about the real printer's firmware.

Usage:
  capt-fake-printer.py --filter ./rastertocapt --raster page.ras \
      [--capture stream.capt] [--log filter.log] [--timeout 180]
Exit status 0 = filter exited 0 and the emulated printer saw a complete job.
"""
import argparse
import os
import socket
import struct
import subprocess
import sys
import threading
import time

SC_NAMES = {1: 'SOFT_RESET', 2: 'DRAIN_OUTPUT', 3: 'GET_BIDI', 4: 'GET_DEVICE_ID',
            5: 'GET_STATE', 6: 'SNMP_GET', 7: 'SNMP_GET_NEXT', 8: 'GET_CONNECTED'}
SC_STATUS_OK = 1
SC_STATUS_NOT_IMPLEMENTED = 7

NAMES = {
    0xA0A0: 'NOP', 0xA0A1: 'CHKJOBSTAT', 0xA0A8: 'CHKXSTATUS', 0xA1A0: 'IEEE_IDENT',
    0xA1A1: 'IDENT', 0xA2A0: 'JOB_BEGIN', 0xA3A2: 'START_0',
    0xC0A0: 'PRINT_DATA', 0xC0A4: 'PRINT_DATA_END',
    0xD0A0: 'SET_PARM_PAGE', 0xD0A1: 'SET_PARM_1', 0xD0A2: 'SET_PARM_2',
    0xD0A4: 'SET_PARM_HISCOA', 0xD0A9: 'SET_PARMS',
    0xE0A0: 'CHKSTATUS', 0xE0A2: 'START_2', 0xE0A3: 'START_1', 0xE0A4: 'START_3',
    0xE0A5: 'UPLOAD_2', 0xE0A6: 'LBP3000_SETUP_0', 0xE0A7: 'FIRE', 0xE0A9: 'JOB_END',
    0xE0BA: 'LBP6000_SETUP_0', 0xE1A1: 'JOB_SETUP', 0xE1A2: 'GPIO',
}
# 0xC0xx and 0xD0xx commands are one-way (SPECS 1.4); everything else gets a reply.
NO_REPLY = {0xC0A0, 0xC0A4, 0xD0A0, 0xD0A1, 0xD0A2, 0xD0A4, 0xD0A9}


def now():
    return time.strftime('%H:%M:%S')


class Printer:
    """Emulated LBP2900 state (see SPECS section 1.3 for the status record)."""

    def __init__(self, log):
        self.log = log
        self.uninit = True          # STATUS0 bits 5-4 until UPLOAD_2
        self.busy_polls = 0         # how many status polls still report BUSY
        self.poll = 0
        self.job = 0x0042
        self.page_decoding = 0
        self.page_printing = 0
        self.page_out = 0
        self.page_completed = 0
        self.page_received = 0
        self.upload_count = 0
        self.pages_in_job = 0
        self.pages = []             # list of dicts: params + band payloads
        self.milestones = []
        self.errors = []
        self.paper_out_page = 0     # emulate an empty tray when this page is fired (0 = never)
        self.paper_out_polls = 0    # ...for this many status polls, then "paper loaded"
        self.no_paper = False
        self.no_paper_polls = 0
        self.no_paper_pending_page = 0

    def mark(self, m):
        self.milestones.append(m)
        self.log(f'  >> {m}')

    def status0(self):
        v = 0
        if self.uninit:
            v |= (1 << 5) | (1 << 4)
        if self.busy_polls > 0:
            v |= 1 << 7
            self.busy_polls -= 1
        self.poll += 1
        if self.poll % 2 == 0:
            v |= 1 << 8          # XSTATUS changed: makes the driver fetch 0xA0A8 too
        if self.page_decoding and self.page_out < self.page_decoding:
            v |= 1 << 0          # processing job
        if self.no_paper:
            v |= 1 << 1                # NOPAPER1
            self.uninit = True         # UNINIT2 is set while out of paper and stays until UPLOAD_2
        return v

    def status_record(self):
        b = bytearray(84)
        struct.pack_into('<H', b, 0, self.status0())
        b[2:8] = bytes([0x00, 0x00, 0x0F, 0x00, 0x00, 0x00])
        status1 = (1 << 2) if self.page_printing > self.page_out else 0
        status2 = 0
        if self.no_paper:
            self.no_paper_polls += 1
            status1 = (1 << 14) | ((1 << 2) if self.no_paper_polls <= 2 else 0)
            # a real LBP2900 does not resume by itself; after K polls the user presses the button
            if self.paper_out_polls < self.no_paper_polls <= self.paper_out_polls + 2:
                status1 |= 1 << 5            # STATUS1 bit 5: button pressed
                status2 = 1 << 7             # STATUS2 bit 7: "problem" bit, seen together with it
                if self.no_paper_polls == self.paper_out_polls + 1:
                    self.mark('user loaded paper and pressed the button')
        struct.pack_into('<H', b, 8, status1)
        struct.pack_into('<H', b, 10, status2)
        struct.pack_into('<H', b, 12, 0)             # STATUS3
        struct.pack_into('<H', b, 14, self.page_decoding)
        struct.pack_into('<H', b, 16, self.page_printing)
        struct.pack_into('<H', b, 18, self.page_out)
        struct.pack_into('<H', b, 20, self.page_completed)
        struct.pack_into('<H', b, 24, (1 << 6) | (1 << 4))   # STATUS4: ready
        struct.pack_into('<H', b, 28, self.job)
        b[32] = 0x55
        b[33] = self.upload_count & 0xFF
        struct.pack_into('<H', b, 34, self.page_received)
        struct.pack_into('<H', b, 40, self.pages_in_job)
        struct.pack_into('<H', b, 44, self.page_completed)
        b[54] = 0x01
        struct.pack_into('<H', b, 64, self.poll)
        return bytes(b)

    def handle(self, cmd, payload):
        """Return the reply payload (bytes) or None for one-way commands."""
        name = NAMES.get(cmd, f'0x{cmd:04X}')
        if cmd == 0xC0A0:
            if not self.pages:
                self.errors.append('PRINT_DATA before SET_PARMS')
                return None
            self.pages[-1]['bands'].append(payload)
            n = len(self.pages[-1]['bands'])
            if n == 1 or n % 25 == 0:
                self.log(f'  {name}: chunk {n}, {len(payload)} bytes')
            return None
        self.log(f'  {name} ({len(payload)} bytes payload)' +
                 (' ' + payload[:24].hex(' ') if payload and cmd != 0xD0A9 else ''))
        if cmd == 0xA1A1:
            self.mark('IDENT')
            return b'\x00\x00'
        if cmd == 0xA1A0:
            return b'MFG:Canon;MDL:LBP2900;'
        if cmd == 0xA3A2:
            return b'\x00\x00'
        if cmd == 0xA2A0:
            self.mark('JOB_BEGIN')
            return struct.pack('<HHI', 0, self.job, 0)     # job number at bytes 2-3
        if cmd == 0xE1A2:
            return b'\x00\x00'
        if cmd == 0xE1A1:
            fg = payload[16] if len(payload) > 16 else -1
            page = struct.unpack_from('<H', payload, 4)[0] if len(payload) > 5 else -1
            self.mark(f'JOB_SETUP fg={fg} page={page}')
            if fg == 1:
                self.pages_in_job = 0
            self.busy_polls = 1
            return b'\x00\x00'
        if cmd in (0xE0A0, 0xA0A8, 0xA0A1):
            return self.status_record()
        if cmd in (0xE0A3, 0xE0A2, 0xE0A4):
            self.mark(name)
            return b'\x00\x00'
        if cmd == 0xE0A5:
            self.uninit = False
            self.upload_count += 1
            # observed on a real LBP2900: START/UPLOAD resets the page counters and drops a held page
            self.page_decoding = self.page_printing = self.page_out = self.page_completed = self.page_received = 0
            if self.no_paper:
                self.no_paper = False
                self.mark('UPLOAD_2: printer re-initialised, held page dropped, counters reset')
            else:
                self.mark('UPLOAD_2 (printer initialised)')
            self.busy_polls = 2
            return b'\x00\x00'
        if cmd == 0xD0A9:
            self.page_decoding += 1
            self.pages_in_job += 1
            page = {'n': self.page_decoding, 'bands': [], 'parms': {}}
            self.pages.append(page)
            off = 0
            while off + 4 <= len(payload):
                sc, ss = struct.unpack_from('<HH', payload, off)
                sp = payload[off + 4:off + ss]
                page['parms'][sc] = sp
                self.log(f'    sub {NAMES.get(sc, hex(sc))}: {sp.hex(" ")}')
                off += ss
            pp = page['parms'].get(0xD0A0)
            if pp and len(pp) >= 34:
                line_size, num_lines, pw, ph = struct.unpack_from('<HHHH', pp, 26)
                page['line_size'] = line_size
                page['num_lines'] = num_lines
                self.log(f'    page {page["n"]}: line_size={line_size} bytes, '
                         f'num_lines={num_lines}, paper={pw}x{ph} px, media_size_code=0x{pp[4]:02X}, '
                         f'toner_save={pp[19]}, fuser=0x{pp[36]:02X}' if len(pp) > 36 else '')
            self.mark(f'SET_PARMS page {page["n"]}')
            return None
        if cmd == 0xC0A4:
            self.page_received = self.page_decoding
            self.page_printing = self.page_decoding
            self.mark(f'PRINT_DATA_END page {self.page_decoding} '
                      f'({sum(len(b) for b in self.pages[-1]["bands"])} bytes in '
                      f'{len(self.pages[-1]["bands"])} chunks)')
            return None
        if cmd == 0xE0A7:
            page = struct.unpack_from('<H', payload, 0)[0] if len(payload) >= 2 else -1
            if self.paper_out_page and self.pages and self.pages[-1]['n'] == self.paper_out_page and not self.no_paper and self.page_out < page:
                self.no_paper = True
                self.no_paper_polls = 0
                self.no_paper_pending_page = page
                self.mark(f'FIRE page {page} -> tray empty, page held in printer buffer')
            else:
                self.page_out = self.page_decoding
                self.page_completed = self.page_decoding
                self.mark(f'FIRE page {page}')
            self.busy_polls = 1
            return b'\x00\x00'
        if cmd == 0xE0A9:
            job = struct.unpack_from('<H', payload, 0)[0] if len(payload) >= 2 else -1
            self.mark(f'JOB_END job=0x{job:04X}')
            return b'\x00\x00'
        if cmd in (0xE0A6, 0xE0BA, 0xA0A0):
            return b'\x00\x00'
        self.errors.append(f'unexpected command 0x{cmd:04X}')
        return b'\x00\x00'


def side_channel_thread(sock, device_id, log, stop):
    """Answer CUPS side-channel requests: [cmd][status][len hi][len lo][data] (big-endian length)."""
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            hdr = sock.recv(4)
        except socket.timeout:
            continue
        except OSError:
            break
        if not hdr:
            break
        cmd, status, dlen = hdr[0], hdr[1], (hdr[2] << 8) | hdr[3]   # length is big-endian (cups/sidechannel.c)
        data = b''
        while len(data) < dlen:
            chunk = sock.recv(dlen - len(data))
            if not chunk:
                break
            data += chunk
        name = SC_NAMES.get(cmd, str(cmd))
        if cmd == 2:                      # DRAIN_OUTPUT
            reply = (SC_STATUS_OK, b'')
        elif cmd == 3:                    # GET_BIDI
            reply = (SC_STATUS_OK, b'\x01')
        elif cmd == 4:                    # GET_DEVICE_ID
            reply = (SC_STATUS_OK, device_id.encode())
            log(f'  side channel: {name} -> "{device_id}"')
        elif cmd == 8:                    # GET_CONNECTED
            reply = (SC_STATUS_OK, b'\x01')
        else:
            reply = (SC_STATUS_NOT_IMPLEMENTED, b'')
            log(f'  side channel: {name} -> NOT_IMPLEMENTED')
        st, rd = reply
        sock.sendall(bytes([cmd, st, len(rd) >> 8, len(rd) & 0xFF]) + rd)


def read_exact(f, n):
    buf = b''
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--filter', required=True, help='path to the rastertocapt binary')
    ap.add_argument('--raster', required=True, help='CUPS raster file (application/vnd.cups-raster)')
    ap.add_argument('--capture', help='write the raw CAPT stream the printer received here')
    ap.add_argument('--log', help='write the filter stderr (its DEBUG log) here')
    ap.add_argument('--device-id', default='MFG:Canon;CMD:CAPT;MDL:LBP2900;CLS:PRINTER;DES:Canon LBP2900;')
    ap.add_argument('--timeout', type=float, default=180.0)
    ap.add_argument('--paper-out-page', type=int, default=0, help='emulate an empty tray when this page (1-based, per job) is fired')
    ap.add_argument('--paper-out-polls', type=int, default=8, help='status polls to stay out of paper before "paper loaded"')
    args = ap.parse_args()

    def log(msg):
        print(f'[{now()}] {msg}', flush=True)

    printer = Printer(log)
    printer.paper_out_page = args.paper_out_page
    printer.paper_out_polls = args.paper_out_polls

    back_r, back_w = os.pipe()                 # printer -> filter (fd 3)
    side_filter, side_printer = socket.socketpair()   # fd 4
    out_r, out_w = os.pipe()                   # filter stdout -> printer
    log_fd = os.open(args.log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644) if args.log else os.open(os.devnull, os.O_WRONLY)
    null_in = os.open(os.devnull, os.O_RDONLY)
    capture = open(args.capture, 'wb') if args.capture else None

    argv = [args.filter, '1', 'tester', 'fake-printer-run', '1', '', args.raster]
    log(f'starting filter: {" ".join(argv)}')
    # posix_spawn file actions give the child exactly the fd layout cupsd gives a filter:
    # 0 stdin, 1 stdout -> backend, 2 stderr -> log, 3 back channel, 4 side channel.
    actions = [
        (os.POSIX_SPAWN_DUP2, null_in, 0),
        (os.POSIX_SPAWN_DUP2, out_w, 1),
        (os.POSIX_SPAWN_DUP2, log_fd, 2),
        (os.POSIX_SPAWN_DUP2, back_r, 3),
        (os.POSIX_SPAWN_DUP2, side_filter.fileno(), 4),
    ]
    # dup2(fd, fd) keeps the close-on-exec flag, so make every source fd inheritable first.
    for fd in (null_in, out_w, log_fd, back_r, side_filter.fileno()):
        os.set_inheritable(fd, True)
    pid = os.posix_spawn(args.filter, argv, os.environ, file_actions=actions)
    for fd in (null_in, out_w, log_fd, back_r):
        os.close(fd)
    side_filter.close()
    out = os.fdopen(out_r, 'rb', buffering=0)

    class Proc:
        def kill(self):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass

        def wait(self):
            _, st = os.waitpid(pid, 0)
            return os.waitstatus_to_exitcode(st)
    proc = Proc()

    stop = threading.Event()
    sc = threading.Thread(target=side_channel_thread, args=(side_printer, args.device_id, log, stop), daemon=True)
    sc.start()

    def watchdog():
        if not stop.wait(args.timeout):
            printer.errors.append(f'timeout after {args.timeout}s')
            log('TIMEOUT: killing filter')
            proc.kill()
    threading.Thread(target=watchdog, daemon=True).start()

    total = 0
    while True:
        hdr = read_exact(out, 4)
        if len(hdr) < 4:
            if hdr:
                printer.errors.append(f'trailing {len(hdr)} bytes')
            break
        cmd, size = struct.unpack('<HH', hdr)
        if size < 4:
            printer.errors.append(f'bad packet size {size} for 0x{cmd:04X}')
            break
        payload = read_exact(out, size - 4)
        if capture:
            capture.write(hdr + payload)
        total += size
        if len(payload) != size - 4:
            printer.errors.append(f'short payload for 0x{cmd:04X}: {len(payload)} of {size - 4}')
            break
        reply = printer.handle(cmd, payload)
        if reply is not None:
            if cmd in NO_REPLY:
                printer.errors.append(f'reply generated for one-way command 0x{cmd:04X}')
            pkt = struct.pack('<HH', cmd, 4 + len(reply)) + reply
            try:
                os.write(back_w, pkt)
            except OSError as e:
                printer.errors.append(f'back channel write failed: {e}')
                break
        elif cmd not in NO_REPLY:
            printer.errors.append(f'no reply for command 0x{cmd:04X} that expects one')

    rc = proc.wait()
    stop.set()
    os.close(back_w)
    side_printer.close()
    if capture:
        capture.close()

    log(f'filter exited with {rc}; {total} bytes received from filter')
    log('milestones: ' + ' | '.join(printer.milestones))
    for p in printer.pages:
        nbytes = sum(len(b) for b in p['bands'])
        log(f'page {p["n"]}: {len(p["bands"])} data chunks, {nbytes} compressed bytes, '
            f'line_size={p.get("line_size")} num_lines={p.get("num_lines")}')

    ok = rc == 0 and not printer.errors and printer.pages and \
        any(m.startswith('JOB_END') for m in printer.milestones) and \
        all(any(m == f'PRINT_DATA_END page {p["n"]}' or m.startswith(f'PRINT_DATA_END page {p["n"]} ') for m in printer.milestones) for p in printer.pages)
    if printer.errors:
        for e in printer.errors:
            log(f'ERROR: {e}')
    log('RESULT: ' + ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
