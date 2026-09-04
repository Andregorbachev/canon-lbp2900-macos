#!/usr/bin/env python3
"""
capt-probe.py - talk to a Canon LBP2900 directly over USB (libusb) to study the
paper-out state machine step by step, without CUPS in the way.

Needs the project venv with pyusb and Homebrew libusb:
    python3 -m venv .venv && .venv/bin/pip install pyusb && brew install libusb
macOS holds the printer interface with a kernel driver, so run it as root:
    sudo .venv/bin/python tools/capt-probe.py status
    sudo .venv/bin/python tools/capt-probe.py usb-reset
    sudo .venv/bin/python tools/capt-probe.py paper-out --capture build/test/test-page.capt --log build/probe/run1.log

Modes
  status     read and decode the extended status record (0xA0A8) once
  usb-reset  class SOFT_RESET, then a USB port reset, status after each; tells
             whether a USB-level reset clears the printer's error state
  paper-out  guided experiment: print one page from a CAPT capture with the
             tray EMPTY, watch the printer while paper is loaded and the button
             pressed, map which commands it accepts in the error state, try to
             re-send the page, and fall back to USB resets. Everything is logged.
  --fake     run against the in-process emulator from capt-fake-printer.py
             (no printer needed; the "operator" is simulated)

The page data comes from a CAPT stream captured by test.sh (build/test/*.capt):
the first SET_PARMS / PRINT_DATA / PRINT_DATA_END group is replayed as the page.
"""
import argparse
import importlib.util
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

NAMES = {
    0xA0A1: 'CHKJOBSTAT', 0xA0A8: 'CHKXSTATUS', 0xA1A0: 'DEVICE_ID', 0xA1A1: 'IDENT',
    0xA2A0: 'JOB_BEGIN/ReserveUnit', 0xA3A2: 'START_0',
    0xC0A0: 'PRINT_DATA', 0xC0A4: 'PRINT_DATA_END', 0xD0A9: 'SET_PARMS',
    0xE0A0: 'CHKSTATUS', 0xE0A2: 'START_2/ClearError', 0xE0A3: 'START_1/ClearMisPrint',
    0xE0A4: 'START_3/DiscardData', 0xE0A5: 'UPLOAD_2/GoOnline', 0xE0A6: 'GoOffline',
    0xE0A7: 'FIRE', 0xE0A9: 'JOB_END/ReleaseUnit', 0xE1A1: 'JOB_SETUP', 0xE1A2: 'GPIO',
}
NO_REPLY = {0xC0A0, 0xC0A4, 0xD0A9}
STATUS_CMDS = {0xE0A0, 0xA0A8, 0xA0A1}

MAGIC_JOB_BEGIN = bytes([0x00, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00])
MAGIC_JOB_BEGIN_RECOVER = bytes([0x02, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00])
MAGIC_UPLOAD = bytes([0xEE, 0xDB, 0xEA, 0xAD] + [0] * 12)
GPIO_INIT = bytes(12)
GPIO_BLINK = bytes([0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00])
GPIO_JOB_INIT = bytes([0x13] + [0] * 15)     # what lbp2900_job_prologue sends (lbp3010_gpio_init)
GO_OFFLINE = bytes([0x00, 0x00])


def job_setup(fg, page, job):
    t = time.localtime()
    head = bytes([
        0, 0, 0, 0, page & 0xFF, page >> 8, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        fg, 0x01, job & 0xFF, job >> 8,
        0xC4, 0xFF, 0x88, 0xFF,
        (t.tm_year - 1900) & 0xFF, (t.tm_year - 1900) >> 8, t.tm_mon - 1, t.tm_mday,
        t.tm_hour, t.tm_min, t.tm_sec, 0x01,
    ])
    return head + bytes(40)


# ----------------------------------------------------------------------------- status decoding
STATUS0_BITS = {0: 'bit0(PROCESSING1/unit-free?)', 1: 'NOPAPER1', 2: 'BUFFERFULL', 4: 'UNINIT2', 5: 'UNINIT1',
                7: 'BUSY', 8: 'XSTATUS_CHANGED'}
STATUS1_BITS = {2: 'PRINTING', 5: 'BUTTON', 14: 'NOPAPER2'}
STATUS2_BITS = {7: 'nERROR(normal=1)', 8: 'bit8?'}


def decode_status(rec):
    """rec = reply payload (after the 4-byte header)."""
    if len(rec) < 40:
        return f'short status record ({len(rec)} bytes): {rec.hex(" ")}'
    w = lambda o: rec[o] | (rec[o + 1] << 8)
    s0, s1, s2, s3, s4 = w(0), w(8), w(10), w(12), w(24)
    names = [n for b, n in STATUS0_BITS.items() if s0 >> b & 1]
    names += [n for b, n in STATUS1_BITS.items() if s1 >> b & 1]
    names += [n for b, n in STATUS2_BITS.items() if s2 >> b & 1]
    return (f'S0={s0:04X} S1={s1:04X} S2={s2:04X} S3={s3:04X} S4={s4:04X} '
            f'pages dec/prn/out/done={w(14)}/{w(16)}/{w(18)}/{w(20)} recv={w(34)} '
            f'[{" ".join(names)}]')


def status_words(rec):
    w = lambda o: rec[o] | (rec[o + 1] << 8)
    return dict(s0=w(0), s1=w(8), s2=w(10), decoding=w(14), printing=w(16), out=w(18), completed=w(20))


# ----------------------------------------------------------------------------- transports
class UsbTransport:
    VID, PID = 0x04A9, 0x2676

    def __init__(self, log):
        import usb.core, usb.util, usb.backend.libusb1
        self.usb, self.util = usb, usb.util
        lib = next((p for p in ('/opt/homebrew/lib/libusb-1.0.dylib', '/usr/local/lib/libusb-1.0.dylib')
                    if os.path.exists(p)), None)
        be = usb.backend.libusb1.get_backend(find_library=(lambda x: lib) if lib else None)
        self.dev = usb.core.find(idVendor=self.VID, idProduct=self.PID, backend=be)
        if self.dev is None:
            sys.exit('!! LBP2900 (04a9:2676) not found on USB. Is it on and plugged in?')
        self.log = log
        self.intf = 0
        self._claim()

    def _claim(self):
        try:
            if self.dev.is_kernel_driver_active(self.intf):
                self.log('usb: kernel driver active, detaching (needs root)')
                self.dev.detach_kernel_driver(self.intf)
        except NotImplementedError:
            pass
        self.util.claim_interface(self.dev, self.intf)
        cfg = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]
        d = self.util.endpoint_direction
        self.ep_out = self.util.find_descriptor(intf, custom_match=lambda e: d(e.bEndpointAddress) == self.util.ENDPOINT_OUT).bEndpointAddress
        self.ep_in = self.util.find_descriptor(intf, custom_match=lambda e: d(e.bEndpointAddress) == self.util.ENDPOINT_IN).bEndpointAddress
        self.log(f'usb: claimed interface {self.intf}, out {self.ep_out:#04x} in {self.ep_in:#04x}')
        self.clear_halt()

    def clear_halt(self):
        for ep in (self.ep_out, self.ep_in):
            try:
                self.dev.clear_halt(ep)
            except Exception as e:
                self.log(f'usb: clear_halt {ep:#04x}: {e}')

    def write(self, data, timeout_ms=10000):
        try:
            return self.dev.write(self.ep_out, data, timeout=timeout_ms)
        except self.usb.core.USBTimeoutError:
            self.log('usb: bulk write timed out; clearing endpoint halts and retrying once')
            self.clear_halt()
            return self.dev.write(self.ep_out, data, timeout=timeout_ms)

    def read(self, n=512, timeout_ms=5000):
        return bytes(self.dev.read(self.ep_in, n, timeout=timeout_ms))

    def soft_reset(self):
        # USB printer class request SOFT_RESET (bRequest 2): flush buffers, reset bulk pipes
        self.dev.ctrl_transfer(0x21, 2, 0, self.intf, None, timeout=5000)

    def port_reset(self):
        self.dev.reset()
        time.sleep(1)
        self._claim()

    def close(self):
        try:
            self.util.release_interface(self.dev, self.intf)
            self.dev.attach_kernel_driver(self.intf)
        except Exception:
            pass
        self.util.dispose_resources(self.dev)


class FakeTransport:
    """In-process emulator from capt-fake-printer.py; the operator is simulated."""

    def __init__(self, log):
        spec = importlib.util.spec_from_file_location('fake', os.path.join(HERE, 'capt-fake-printer.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.printer = mod.Printer(lambda m: None)
        self.printer.paper_out_page = 1
        self.printer.paper_out_polls = 10 ** 9      # the operator is driven by the script, not by poll count
        self.log = log
        self.pending = None
        self.hung_writes = 0

    def write(self, data, timeout_ms=10000):
        cmd, size = struct.unpack_from('<HH', data, 0)
        reply = self.printer.handle(cmd, data[4:size])
        if self.printer.hung:
            self.hung_writes += 1
            if self.hung_writes > 3:
                raise TimeoutError('fake: printer hung, bulk write timed out')
        self.pending = None if reply is None else struct.pack('<HH', cmd, 4 + len(reply)) + reply
        return len(data)

    def read(self, n=512, timeout_ms=5000):
        if self.pending is None:
            raise TimeoutError('fake: no reply (printer hung)')
        r, self.pending = self.pending, None
        return r

    def soft_reset(self):
        pass

    def port_reset(self):
        p = self.printer
        p.hung = p.rejecting = p.reserved = False      # a USB reset clears the session in the emulator
        p.uninit = True

    def close(self):
        pass

    # operator simulation
    def operator(self, what):
        p = self.printer
        if what == 'load paper':
            p.tray_empty = False
            if getattr(self, 'reject_on_paper_out', False):
                p.rejecting = True
        elif what == 'press button':
            p.button_pressed = True


# ----------------------------------------------------------------------------- CAPT layer
class Capt:
    def __init__(self, tr, log):
        self.tr, self.log = tr, log
        self.job = 0

    def send(self, cmd, payload=b'', quiet=False):
        pkt = struct.pack('<HH', cmd, 4 + len(payload)) + payload
        if not quiet:
            self.log(f'  send {NAMES.get(cmd, "%04X" % cmd):22s} {pkt[:24].hex(" ")}{" ..." if len(pkt) > 24 else ""}')
        self.tr.write(pkt)

    def recv(self, cmd, timeout_ms=5000):
        r = self.tr.read(512, timeout_ms)
        if len(r) < 4:
            raise IOError(f'short reply {r.hex(" ")}')
        rcmd, size = struct.unpack_from('<HH', r, 0)
        while len(r) < size:
            r += self.tr.read(512, timeout_ms)
        if rcmd != cmd:
            self.log(f'  !! reply is for {rcmd:04X}, expected {cmd:04X}: {r.hex(" ")}')
        return r[4:size]

    def cmd(self, cmd, payload=b'', timeout_ms=5000):
        """Send + receive. Returns the reply payload; logs the result code."""
        self.send(cmd, payload, quiet=cmd in STATUS_CMDS)
        if cmd in NO_REPLY:
            return None
        try:
            rep = self.recv(cmd, timeout_ms)
        except Exception as e:
            self.log(f'  !! no reply to {NAMES.get(cmd, "%04X" % cmd)}: {e}')
            raise
        if cmd in STATUS_CMDS:
            return rep
        code = rep[0] if rep else -1
        verdict = 'OK' if code == 0 else f'REJECTED code 0x{code:02X}'
        self.log(f'  recv {NAMES.get(cmd, "%04X" % cmd):22s} {rep[:8].hex(" ")}  -> {verdict}')
        return rep

    def xstatus(self, note=''):
        rep = self.cmd(0xA0A8)
        self.log(f'  status{(" " + note) if note else ""}: {decode_status(rep)}')
        return status_words(rep)

    def jobstat(self):
        rep = self.cmd(0xA0A1)
        self.log(f'  jobstat: {rep.hex(" ")}')
        return rep

    def chkstatus(self):
        rep = self.cmd(0xE0A0)
        self.log(f'  chkstatus: {rep.hex(" ")}')
        return rep

    def wait_not_busy(self, max_s=20):
        t0 = time.time()
        while time.time() - t0 < max_s:
            s = self.xstatus()
            if not (s['s0'] >> 7 & 1):
                return s
            time.sleep(0.1)
        self.log('  !! still BUSY after %ds' % max_s)
        return s

    def reset_engine(self):
        self.cmd(0xE0A3); self.cmd(0xE0A2); self.cmd(0xE0A4)
        self.wait_not_busy()
        self.cmd(0xE0A5, MAGIC_UPLOAD)
        self.wait_not_busy()

    def job_begin(self, magic=MAGIC_JOB_BEGIN):
        rep = self.cmd(0xA2A0, magic)
        if rep and rep[0] == 0 and len(rep) >= 4:
            self.job = rep[2] | (rep[3] << 8)
            self.log(f'  job number {self.job}')
        return rep

    def job_prologue(self):
        self.cmd(0xA1A1)
        time.sleep(1)
        self.xstatus('before START_0')
        self.cmd(0xA3A2)
        self.job_begin()
        self.cmd(0xE1A2, GPIO_JOB_INIT)
        self.wait_not_busy()
        self.cmd(0xE1A1, job_setup(1, 0, self.job))
        self.wait_not_busy()

    def page(self, page_cmds, n, timeout_ms=15000):
        """page_cmds: list of (cmd, payload) = SET_PARMS, PRINT_DATA*, PRINT_DATA_END from the capture."""
        s = self.xstatus('before page')
        if s['s0'] & 0x30:
            self.log('  UNINIT set -> START_1/2/3 + UPLOAD_2')
            self.reset_engine()
        for _ in range(200):
            if not (s['s0'] >> 2 & 1):
                break
            time.sleep(0.1); s = self.xstatus()
        nbytes = 0
        for i, (c, p) in enumerate(page_cmds):
            self.send(c, p, quiet=(c == 0xC0A0 and 0 < i < len(page_cmds) - 2))
            nbytes += len(p)
        self.log(f'  page {n}: {len(page_cmds)} packets, {nbytes} bytes of payload sent')
        s = self.xstatus('after data')
        self.cmd(0xE1A1, job_setup(2, n, self.job))
        self.cmd(0xE0A7, struct.pack('<H', n))
        self.cmd(0xE1A1, job_setup(6, n, self.job))

    def wait_page_out(self, n, max_s=40):
        """Poll until the page is out or the printer reports no paper. Returns 'out' | 'nopaper' | 'timeout'."""
        t0 = time.time()
        last = None
        while time.time() - t0 < max_s:
            s = self._quiet_status()
            key = (s['s0'], s['s1'], s['s2'], s['out'], s['decoding'])
            if key != last:
                self.log(f'  status: {self._last_decoded}')
                last = key
            if s['out'] >= s['decoding'] and s['decoding'] > 0:
                return 'out'
            nopaper = (s['s0'] >> 1 & 1) or (s['s1'] >> 14 & 1)
            printing = s['s1'] >> 2 & 1
            if nopaper and not printing:
                return 'nopaper'
            time.sleep(0.3)
        return 'timeout'

    def _quiet_status(self):
        rep = self.cmd(0xA0A8)
        self._last_decoded = decode_status(rep)
        return status_words(rep)

    def observe(self, seconds, every=0.5):
        """Log status only when it changes."""
        t0 = time.time(); last = None
        while time.time() - t0 < seconds:
            s = self._quiet_status()
            key = (s['s0'], s['s1'], s['s2'], s['out'], s['decoding'])
            if key != last:
                self.log(f'  [{time.time() - t0:5.1f}s] {self._last_decoded}')
                last = key
            time.sleep(every)


def load_page_from_capture(path):
    data = open(path, 'rb').read()
    off = 0; cmds = []
    while off + 4 <= len(data):
        cmd, size = struct.unpack_from('<HH', data, off)
        cmds.append((cmd, data[off + 4:off + size]))
        off += size
    try:
        i = next(i for i, (c, _) in enumerate(cmds) if c == 0xD0A9)
        j = next(j for j, (c, _) in enumerate(cmds) if j > i and c == 0xC0A4)
    except StopIteration:
        sys.exit(f'!! {path}: no SET_PARMS ... PRINT_DATA_END group found')
    page = [(c, p) for c, p in cmds[i:j + 1] if c in (0xD0A9, 0xC0A0, 0xC0A4)]
    return page


# ----------------------------------------------------------------------------- experiment
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=['status', 'usb-reset', 'paper-out'])
    ap.add_argument('--capture', default=os.path.join(HERE, '..', 'build', 'test', 'test-page.capt'),
                    help='CAPT stream from test.sh to take the page from')
    ap.add_argument('--log', help='write the full log here (also printed)')
    ap.add_argument('--fake', action='store_true', help='use the in-process emulator instead of USB')
    ap.add_argument('--no-resend', action='store_true', help='paper-out: skip the page re-send attempt')
    ap.add_argument('--fake-reject', action='store_true', help='with --fake: the emulated printer rejects every command after the paper-out, like the real one did')
    args = ap.parse_args()

    logf = None
    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        logf = open(args.log, 'a')

    def log(msg):
        line = f'[{time.strftime("%H:%M:%S")}] {msg}'
        print(line, flush=True)
        if logf:
            logf.write(line + '\n'); logf.flush()

    fake = args.fake

    def ask(prompt, action=None):
        log(f'>>> OPERATOR: {prompt}')
        if fake:
            if action:
                tr.operator(action)
            return
        try:
            input('    ... press Enter when done: ')
        except EOFError:
            pass

    tr = FakeTransport(log) if fake else UsbTransport(log)
    if fake and args.fake_reject:
        tr.reject_on_paper_out = True
    capt = Capt(tr, log)
    log(f'capt-probe {args.mode} {"(fake printer)" if fake else "(USB 04a9:2676)"}')

    try:
      try:
        if args.mode == 'status':
            capt.xstatus()
            capt.chkstatus()
            capt.jobstat()
            return

        if args.mode == 'usb-reset':
            try:
                capt.xstatus('before')
            except Exception as e:
                log(f'  status failed: {e}')
            log('== class SOFT_RESET (USB printer-class request)')
            try:
                tr.soft_reset(); time.sleep(1)
                capt.xstatus('after soft reset')
            except Exception as e:
                log(f'  soft reset path failed: {e}')
            log('== USB port reset (re-enumeration, like re-plugging the cable)')
            try:
                tr.port_reset(); time.sleep(1)
                capt.xstatus('after port reset')
            except Exception as e:
                log(f'  port reset path failed: {e}')
                log('END: the printer does not answer even after a USB reset; power-cycle it')
                return
            log('== can we reserve the unit now?')
            capt.cmd(0xA1A1); capt.cmd(0xA3A2)
            rep = capt.job_begin()
            if rep and rep[0] == 0:
                capt.cmd(0xE1A1, job_setup(4, 0, capt.job))
                capt.cmd(0xE0A9, struct.pack('<H', capt.job))
            capt.xstatus('end')
            return

        # ---------------- paper-out experiment
        page = load_page_from_capture(args.capture)
        log(f'page from {args.capture}: {len(page)} packets')
        ask('Make sure the printer is ON, idle, and the paper tray is EMPTY.')
        s = capt.xstatus('idle')

        log('== 1. print one page into the empty tray')
        capt.job_prologue()
        capt.page(page, 1)
        res = capt.wait_page_out(1)
        log(f'  result: {res}')
        if res == 'out':
            log('!! the page came out: the tray was not empty. Nothing to study; ending the job.')
            capt.cmd(0xE1A1, job_setup(4, 1, capt.job)); capt.cmd(0xE0A9, struct.pack('<H', capt.job))
            return

        log('== 2. watch the printer on its own (15 s, tray still empty)')
        capt.observe(15)
        ask('Load paper into the tray. Do NOT press the button.', 'load paper')
        capt.observe(10)
        ask('Now press the printer button once.', 'press button')
        capt.observe(10)

        def ready_now(note):
            s = capt.xstatus(note)
            ok = not (s['s0'] & 0x32) and not (s['s1'] >> 14 & 1)
            log(f'  printer looks {"READY" if ok else "NOT ready"} (UNINIT/NOPAPER bits {"clear" if ok else "set"})')
            return ok

        def try_resend(where):
            if args.no_resend:
                return False
            log(f'== 4. re-send the page ({where})')
            try:
                capt.page(page, 1)
                res = capt.wait_page_out(1)
                log(f'  result: {res}')
                capt.cmd(0xE1A1, job_setup(4, 1, capt.job))
                capt.cmd(0xE0A9, struct.pack('<H', capt.job))
                if res == 'out':
                    log(f'SUCCESS: page printed after paper-out recovery ({where})')
                    return True
            except Exception as e:
                log(f'  !! re-send failed: {e}')
            return False

        def step(name, fn):
            log(f'-- {name}')
            try:
                rep = fn()
                capt.xstatus()
                return rep
            except Exception as e:
                log(f'  !! {name} failed: {e}')
                raise

        log('== 3a. reset inside the current job (upstream captdriver way): does it clear NOPAPER/UNINIT?')
        step('CHKSTATUS', capt.chkstatus)
        step('CHKJOBSTAT', capt.jobstat)
        step('GPIO init', lambda: capt.cmd(0xE1A2, GPIO_INIT))
        step('JOB_SETUP fg=6', lambda: capt.cmd(0xE1A1, job_setup(6, 1, capt.job)))
        step('START_1 ClearMisPrint', lambda: capt.cmd(0xE0A3))
        step('START_2 ClearError', lambda: capt.cmd(0xE0A2))
        step('START_3 DiscardData', lambda: capt.cmd(0xE0A4))
        step('UPLOAD_2 GoOnline', lambda: capt.cmd(0xE0A5, MAGIC_UPLOAD))
        time.sleep(1)
        if ready_now('after in-job reset') and try_resend('same job, after reset'):
            return

        log('== 3b. release and re-acquire the unit (Canon Windows driver way)')
        step('JOB_END current job', lambda: capt.cmd(0xE0A9, struct.pack('<H', capt.job)))
        log('  polling CHKXSTATUS + CHKJOBSTAT for 3 s, as Windows does')
        for _ in range(6):
            capt.jobstat(); capt.xstatus(); time.sleep(0.5)
        rep = step('JOB_BEGIN byte0=02', lambda: capt.job_begin(MAGIC_JOB_BEGIN_RECOVER))
        if not rep or rep[0] != 0:
            rep = step('JOB_BEGIN byte0=00', lambda: capt.job_begin(MAGIC_JOB_BEGIN))
        if not rep or rep[0] != 0:
            log('  START_0 then JOB_BEGIN again')
            step('START_0', lambda: capt.cmd(0xA3A2))
            rep = step('JOB_BEGIN byte0=00', lambda: capt.job_begin(MAGIC_JOB_BEGIN))
        if rep and rep[0] == 0:
            step('JOB_SETUP fg=2', lambda: capt.cmd(0xE1A1, job_setup(2, 0, capt.job)))
            step('START_1 ClearMisPrint', lambda: capt.cmd(0xE0A3))
            step('START_2 ClearError', lambda: capt.cmd(0xE0A2))
            step('START_3 DiscardData', lambda: capt.cmd(0xE0A4))
            step('UPLOAD_2 GoOnline', lambda: capt.cmd(0xE0A5, MAGIC_UPLOAD))
            time.sleep(1)
            if ready_now('after re-acquire + reset') and try_resend('new job, after reset'):
                return
            log('== 3c. GoOffline, button, reset again (rest of the Canon sequence)')
            step('GPIO blink', lambda: capt.cmd(0xE1A2, GPIO_BLINK))
            step('GoOffline', lambda: capt.cmd(0xE0A6, GO_OFFLINE))
            ask('Press the printer button once more.', 'press button')
            capt.observe(5)
            step('GPIO init', lambda: capt.cmd(0xE1A2, GPIO_INIT))
            step('JOB_SETUP fg=2', lambda: capt.cmd(0xE1A1, job_setup(2, 0, capt.job)))
            step('START_1 ClearMisPrint', lambda: capt.cmd(0xE0A3))
            step('START_2 ClearError', lambda: capt.cmd(0xE0A2))
            step('START_3 DiscardData', lambda: capt.cmd(0xE0A4))
            step('UPLOAD_2 GoOnline', lambda: capt.cmd(0xE0A5, MAGIC_UPLOAD))
            time.sleep(1)
            if ready_now('after GoOffline/button/reset') and try_resend('new job, after button'):
                return
        else:
            log('  the printer refuses to be reserved while the error is latched')

        log('== 5. USB-level recovery')
        for step, fn in (('class SOFT_RESET', tr.soft_reset), ('USB port reset', tr.port_reset)):
            log(f'-- {step}')
            try:
                fn(); time.sleep(1)
                s = capt.xstatus(f'after {step}')
                log('-- fresh job after reset')
                capt.job_prologue()
                if not args.no_resend:
                    capt.page(page, 1)
                    res = capt.wait_page_out(1)
                    log(f'  result: {res}')
                    if res == 'out':
                        capt.cmd(0xE1A1, job_setup(4, 1, capt.job))
                        capt.cmd(0xE0A9, struct.pack('<H', capt.job))
                        log(f'SUCCESS: page printed after {step}')
                        return
                capt.cmd(0xE0A9, struct.pack('<H', capt.job))
            except Exception as e:
                log(f'  !! {step} path failed: {e}')
        log('END: no recovery path worked; power-cycle the printer')
      except Exception:
        import traceback
        for line in traceback.format_exc().splitlines():
            log('!! ' + line)
    finally:
        try:
            tr.close()
        except Exception:
            pass
        if logf:
            logf.close()


if __name__ == '__main__':
    main()
