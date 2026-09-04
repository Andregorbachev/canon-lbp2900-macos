#!/usr/bin/env python3
"""
raster-info.py: print the page header of a CUPS raster stream (stdin or file).

    cupsfilter -p PPD -m application/vnd.cups-raster photo.png | python3 tools/raster-info.py

Shows width, height, bits per colour/pixel, bytes per line and colour space for
every page. rastertocapt expects 1 bit per pixel, colour space 3 (black), and
for A4 with the shipped PPD 4722 x 6780 px at 591 bytes per line.
"""
import struct
import sys

# the sync word is written as a native 32-bit int: 'RaSt'/'RaS2'/'RaS3' in the file means big-endian,
# the reversed spelling little-endian (cups/raster.h)
SYNC = {b'RaSt': '>', b'tSaR': '<', b'RaS2': '>', b'2SaR': '<', b'RaS3': '>', b'3SaR': '<'}


def main():
    f = open(sys.argv[1], 'rb') if len(sys.argv) > 1 else sys.stdin.buffer
    sync = f.read(4)
    if sync not in SYNC:
        sys.exit(f'not a CUPS raster stream (sync {sync!r}, {len(sync)} bytes read)')
    end = SYNC[sync]
    page = 0
    while True:
        hdr = f.read(1796 - 4) if page == 0 else f.read(1796)
        if len(hdr) < 1792:
            break
        if page > 0:
            hdr = hdr[4:]
        o = 256 + 20 + 8 + 16 + 12 + 8 + 32 + 8
        g = lambda k: struct.unpack_from(end + 'I', hdr, o + k)[0]
        res = struct.unpack_from(end + 'II', hdr, 256 + 20)
        size = struct.unpack_from(end + 'II', hdr, o - 12)
        page += 1
        w, h, bpc, bpp, bpl, cs = g(12), g(16), g(24), g(28), g(32), g(40)
        print(f'page {page}: {w} x {h} px, {res[0]}x{res[1]} dpi, PageSize {size[0]}x{size[1]} pt, '
              f'bitsPerColor {bpc}, bitsPerPixel {bpp}, bytesPerLine {bpl}, colorSpace {cs}'
              + ('' if bpp == 1 and cs == 3 else '   <-- rastertocapt expects 1 bpp, colorSpace 3'))
        # skip pixel data
        remaining = bpl * h
        while remaining > 0:
            chunk = f.read(min(remaining, 1 << 20))
            if not chunk:
                break
            remaining -= len(chunk)
    if page == 0:
        print('no pages in the stream')


if __name__ == '__main__':
    main()
