#!/usr/bin/env python3
"""pbm-to-png.py IN.pbm OUT.png [--scale N] - convert a binary PBM (P4) to an 8-bit
grayscale PNG, optionally box-downscaled by N, using only the standard library."""
import argparse, struct, sys, zlib

def read_pbm(path):
    data = open(path, 'rb').read()
    tokens, pos = [], 0
    while len(tokens) < 3:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b'#':
            while data[pos:pos + 1] not in (b'\n', b''):
                pos += 1
            continue
        start = pos
        while not data[pos:pos + 1].isspace():
            pos += 1
        tokens.append(data[start:pos])
    pos += 1
    if tokens[0] != b'P4':
        sys.exit('not a P4 pbm')
    w, h = int(tokens[1]), int(tokens[2])
    return w, h, data[pos:pos + ((w + 7) // 8) * h]

def png_gray(path, w, h, rows):
    raw = b''.join(b'\x00' + r for r in rows)
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--scale', type=int, default=1)
    a = ap.parse_args()
    w, h, bits = read_pbm(a.src)
    bpl = (w + 7) // 8
    n = a.scale
    ow, oh = w // n, h // n
    rows = []
    for oy in range(oh):
        acc = [0] * ow
        for dy in range(n):
            line = bits[(oy * n + dy) * bpl:(oy * n + dy + 1) * bpl]
            lb = int.from_bytes(line, 'big')
            for ox in range(ow):
                x0 = ox * n
                acc[ox] += bin((lb >> (w - x0 - n)) & ((1 << n) - 1)).count('1')
        rows.append(bytes(255 - (255 * v) // (n * n) for v in acc))
    png_gray(a.dst, ow, oh, rows)
    print(f'{a.src}: {w}x{h} -> {a.dst}: {ow}x{oh}')

if __name__ == '__main__':
    main()
