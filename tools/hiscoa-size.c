/*
 * hiscoa-size.c - how much Hi-SCoA data a 1-bit page turns into.
 *
 *   build/tools/hiscoa-size page.pbm [band_lines]
 *
 * Reads a P4 PBM, compresses it band by band exactly as rastertocapt does
 * (hiscoa_default_params, 70-line bands by default) and prints the total,
 * the largest band, and how many bands came out larger than their raw size.
 * Used to compare halftoning methods: the LBP2900 has little memory and a
 * page that does not compress prints as garbage.
 */
#include "../src/std.h"
#include "../src/hiscoa-compress.h"
#include "../src/hiscoa-common.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
	if (argc < 2) { fprintf(stderr, "usage: hiscoa-size page.pbm [band_lines]\n"); return 2; }
	unsigned band_lines = argc > 2 ? (unsigned) atoi(argv[2]) : 70;
	FILE *f = fopen(argv[1], "rb");
	if (! f) { perror(argv[1]); return 1; }
	char magic[3] = { 0 };
	unsigned width, height;
	if (fscanf(f, "%2s", magic) != 1 || strcmp(magic, "P4")) { fprintf(stderr, "not a P4 PBM\n"); return 1; }
	int c;
	/* skip comments/whitespace, read dims */
	while ((c = fgetc(f)) != EOF) {
		if (c == '#') { while ((c = fgetc(f)) != EOF && c != '\n') { } continue; }
		if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
		ungetc(c, f); break;
	}
	if (fscanf(f, "%u %u", &width, &height) != 2) { fprintf(stderr, "bad header\n"); return 1; }
	fgetc(f); /* single whitespace after height */
	unsigned line = (width + 7) / 8;
	uint8_t *band = calloc(line, band_lines);
	uint8_t *out = calloc(2, line * band_lines);
	size_t total = 0, largest = 0, raw = (size_t) line * band_lines;
	unsigned bands = 0, over = 0;
	for (unsigned y = 0; y < height; y += band_lines) {
		unsigned n = band_lines;
		if (y + n > height) n = height - y;
		memset(band, 0, line * band_lines);
		if (fread(band, line, n, f) != n) { fprintf(stderr, "short read at line %u\n", y); return 1; }
		size_t s = hiscoa_compress_band(out, 2 * line * band_lines, band, line, n, 0, &hiscoa_default_params);
		total += s; ++bands;
		if (s > largest) largest = s;
		if (s > (size_t) line * n) ++over;
	}
	printf("%s: %ux%u, %u bands of %u lines, raw band %zu B: total %zu B (%.2f MB), largest band %zu B, %u bands larger than raw, ratio %.2f\n",
			argv[1], width, height, bands, band_lines, raw, total, total / 1048576.0, largest, over,
			(double) ((size_t) line * height) / (double) (total ? total : 1));
	return 0;
}
