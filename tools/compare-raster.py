#!/usr/bin/env python3
"""compare-raster.py PAGE.ras DECODED.pbm - check that the page decoded from the CAPT
stream is pixel-identical to the CUPS raster the filter was given.

rastertocapt uses PageSize[0] (points) as the line size in bytes and centres the
wider CUPS raster line inside it (see src/rastertocapt.c, center_pixels), so the
comparison crops the raster line the same way. Exit 0 on a perfect match."""
import struct, sys

def raster_pages(path):
    f = open(path, 'rb').read()
    assert f[:4] in (b'RaS2', b'RaS3', b'2SaR', b'3SaR'), 'not a CUPS raster'
    off = 4
    while off + 1796 <= len(f):
        hdr = f[off:off + 1796]
        page_w = struct.unpack_from('<I', hdr, 256 + 20 + 8 + 16 + 12 + 8 + 32)[0]
        cups_w, cups_h, _mt, _bpc, bpp, bpl = struct.unpack_from('<6I', hdr, 256 + 20 + 8 + 16 + 12 + 8 + 32 + 8 + 12)
        off += 1796
        data = f[off:off + bpl * cups_h]
        off += bpl * cups_h
        if bpp == 8:
            # rastertocapt halftones 8-bit grey itself (8x8 Bayer); reproduce that with the same C code
            import os, subprocess, tempfile
            tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'build', 'tools', 'halftone-size')
            with tempfile.TemporaryDirectory() as td:
                pgm = os.path.join(td, 'page.pgm'); out = os.path.join(td, 'page.pbm')
                # CUPS_CSPACE_K: 255 = black; halftone-size expects PGM 0 = black
                inv = bytes(255 - b for b in range(256))
                open(pgm, 'wb').write(b'P5\n%d %d\n255\n' % (cups_w, cups_h) + data.translate(inv))
                subprocess.run([tool, pgm, 'bayer8', out], check=True, stdout=subprocess.DEVNULL)
                pw, ph, data = read_pbm(out)
            bpl = (cups_w + 7) // 8
        yield page_w, cups_w, cups_h, bpl, data

def read_pbm(path):
    """P4 header: magic, optional comment lines, width height, one whitespace, then bits."""
    data = open(path, 'rb').read()
    pos = 2; tokens = []
    while len(tokens) < 2:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b'#':
            pos = data.index(b'\n', pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        tokens.append(int(data[pos:end])); pos = end
    return tokens[0], tokens[1], data[pos + 1:]

ras, pbm = sys.argv[1], sys.argv[2]
pw, ph, pixels = read_pbm(pbm)
page = next(raster_pages(ras))
page_w, cups_w, cups_h, bpl, rdata = page
line_size = page_w
# Mirror rastertocapt.c: the narrower of (raster line, printer line) is centred in the wider one.
shift_src = (bpl - line_size) // 2 if bpl > line_size else 0        # bytes skipped in the raster line
shift_dst = (line_size - bpl) // 2 if line_size > bpl else 0        # bytes of padding in the printer line
copy = min(bpl, line_size)
if pw != line_size * 8:
    sys.exit(f'FAIL: decoded width {pw} px, expected {line_size * 8}')
lines = min(ph, cups_h)
bad = 0
for y in range(lines):
    src = rdata[y * bpl + shift_src: y * bpl + shift_src + copy]
    row = pixels[y * line_size:(y + 1) * line_size]
    dst = row[shift_dst:shift_dst + copy]
    pad = row[:shift_dst] + row[shift_dst + copy:]
    if src != dst or pad.strip(b'\x00'):
        bad += 1
print(f'raster {cups_w}x{cups_h} ({bpl} B/line) vs decoded {pw}x{ph}: {lines} lines compared, {bad} differ')
sys.exit(0 if bad == 0 and ph >= cups_h else 1)
