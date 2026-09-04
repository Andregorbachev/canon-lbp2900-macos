/*
 * halftone-size.c - which ordered-dither matrix compresses best with Hi-SCoA.
 *
 *   build/tools/halftone-size image.pgm MATRIX [out.pbm]
 *
 * Reads an 8-bit P5 PGM (0 = black), halftones it with the named threshold
 * matrix (1 = black in the output), compresses it band by band as rastertocapt
 * does and prints the total Hi-SCoA size. The LBP2900 holds 2 MB, so a page
 * must stay well below that. Matrices: bayer2 bayer4 bayer8 bayer16 clus4
 * clus8 bayer8x4 line4 threshold.
 */
#include "../src/std.h"
#include "../src/hiscoa-compress.h"
#include "../src/hiscoa-common.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned bayer(unsigned n, unsigned x, unsigned y)   /* n = 2,4,8,16: value 0..n*n-1 */
{
	unsigned v = 0, bit;
	for (bit = 1; bit < n; bit <<= 1) {
		unsigned xb = (x / bit) & 1, yb = (y / bit) & 1;
		v = (v << 2) | ((xb ^ yb) << 1) | yb;
	}
	return v;
}

/* 4x4 clustered dot (spiral) */
static const unsigned clus4[4][4] = { { 12, 5, 6, 13 }, { 4, 0, 1, 7 }, { 11, 3, 2, 8 }, { 15, 10, 9, 14 } };
/* 8x8 clustered dot, two dots per cell (classic 45-degree screen) */
static const unsigned clus8[8][8] = {
	{ 24, 10, 12, 26, 35, 47, 49, 37 }, { 8, 0, 2, 14, 45, 59, 61, 51 }, { 22, 6, 4, 16, 43, 57, 63, 53 },
	{ 30, 20, 18, 28, 33, 41, 55, 39 }, { 34, 46, 48, 36, 25, 11, 13, 27 }, { 44, 58, 60, 50, 9, 1, 3, 15 },
	{ 42, 56, 62, 52, 23, 7, 5, 17 }, { 32, 40, 54, 38, 31, 21, 19, 29 } };

/* threshold in 0..255 for pixel (x,y) */
static unsigned thr(const char *m, unsigned x, unsigned y)
{
	if (! strcmp(m, "bayer2")) return (bayer(2, x, y) * 256 + 128) / 4;
	if (! strcmp(m, "bayer4")) return (bayer(4, x, y) * 256 + 128) / 16;
	if (! strcmp(m, "bayer8")) return 254 - bayer(8, x, y) * 4;   /* same as rastertocapt: black if blackness > bayer*4+1 */
	if (! strcmp(m, "bayer16")) return (bayer(16, x, y) * 256 + 128) / 256;
	if (! strcmp(m, "bayer8x4")) return (bayer(8, x, y % 4) * 256 + 128) / 64;
	if (! strcmp(m, "clus4")) return (clus4[y % 4][x % 4] * 256 + 128) / 16;
	if (! strcmp(m, "clus8")) return (clus8[y % 8][x % 8] * 256 + 128) / 64;
	if (! strcmp(m, "line4")) return ((y % 4) * 256 + 128) / 4;
	return 128; /* threshold */
}

int main(int argc, char **argv)
{
	if (argc < 3) { fprintf(stderr, "usage: halftone-size image.pgm MATRIX [out.pbm]\n"); return 2; }
	FILE *f = fopen(argv[1], "rb");
	if (! f) { perror(argv[1]); return 1; }
	char magic[3] = { 0 }; unsigned w, h, maxv; int c;
	if (fscanf(f, "%2s", magic) != 1 || strcmp(magic, "P5")) { fprintf(stderr, "not a P5 PGM\n"); return 1; }
	while ((c = fgetc(f)) != EOF) {
		if (c == '#') { while ((c = fgetc(f)) != EOF && c != '\n') { } continue; }
		if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
		ungetc(c, f); break;
	}
	if (fscanf(f, "%u %u %u", &w, &h, &maxv) != 3) { fprintf(stderr, "bad header\n"); return 1; }
	fgetc(f);
	const char *m = argv[2];
	FILE *out = argc > 3 ? fopen(argv[3], "wb") : NULL;
	unsigned line = (w + 7) / 8, band_lines = 70;
	uint8_t *gray = malloc(w), *band = calloc(line, band_lines), *comp = calloc(2, line * band_lines);
	size_t total = 0, largest = 0; unsigned bands = 0, over = 0;
	if (out) fprintf(out, "P4\n%u %u\n", line * 8, h);
	for (unsigned y0 = 0; y0 < h; y0 += band_lines) {
		unsigned n = band_lines; if (y0 + n > h) n = h - y0;
		memset(band, 0, line * band_lines);
		for (unsigned i = 0; i < n; ++i) {
			if (fread(gray, 1, w, f) != w) { fprintf(stderr, "short read\n"); return 1; }
			uint8_t *row = band + i * line;
			for (unsigned x = 0; x < w; ++x)
				if (gray[x] < thr(m, x, y0 + i)) row[x >> 3] |= 0x80 >> (x & 7);
		}
		if (out) fwrite(band, line, n, out);
		size_t s = hiscoa_compress_band(comp, 2 * line * band_lines, band, line, n, 0, &hiscoa_default_params);
		total += s; ++bands; if (s > largest) largest = s; if (s > (size_t) line * n) ++over;
	}
	if (out) fclose(out);
	printf("%-10s %s: total %7zu B (%.2f MB), largest band %5zu B, %u/%u bands larger than raw\n",
			m, argv[1], total, total / 1048576.0, largest, over, bands);
	return 0;
}
