#!/usr/bin/env python3
"""
make-photo-page.py OUT.png [WIDTH HEIGHT]

Writes an 8-bit grey PNG that behaves like a photograph once cgpdftoraster
halftones it: smooth gradients plus noise, so the Hi-SCoA compressor cannot
shrink it (a band comes out slightly larger than raw). Used by test.sh to check
that the filter paces page data by the printer's BUFFERFULL flag: a text page
is ~25 KB of CAPT data, this page is ~4 MB, like a real photo. Stdlib only.
"""
import math, random, struct, sys, zlib

out = sys.argv[1]
W = int(sys.argv[2]) if len(sys.argv) > 2 else 1240   # A4 at 150 dpi
H = int(sys.argv[3]) if len(sys.argv) > 3 else 1754
random.seed(1)
rows = []
for y in range(H):
    row = bytearray()
    for x in range(W):
        v = int(127 + 120 * math.sin(x / 37.0) * math.cos(y / 53.0) + random.randint(-40, 40))
        row.append(max(0, min(255, v)))
    rows.append(bytes(row))

def chunk(t, d):
    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)

raw = b''.join(b'\x00' + r for r in rows)
png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 0, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
open(out, 'wb').write(png)
print(f'{out}: {W}x{H} grey PNG, {len(png)} bytes')
