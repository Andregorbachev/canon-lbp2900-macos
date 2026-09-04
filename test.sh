#!/bin/bash
#
# test.sh - end-to-end test of the built filter WITHOUT a printer.
#
#   ./test.sh
#
# 1. macOS's own filter chain turns a text page into CUPS raster using our PPD
#    (cupsfilter -> cgpdftoraster), exactly as cupsd would.
# 2. tools/capt-fake-printer.py runs build/rastertocapt with the three channels a
#    CUPS backend provides (stdout, back channel fd 3, side channel fd 4) and plays
#    the printer side of the CAPT handshake.
# 3. build/tools/captdefilter decodes the captured Hi-SCoA stream back to a PBM,
#    tools/compare-raster.py checks it is pixel-identical to the input raster,
#    tools/pbm-to-png.py renders a PNG you can look at.
# Everything lands in build/test/.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$DIR/build/test"
PPD="$DIR/ppd/Canon-LBP2900-captdriver.ppd"
FILTER="$DIR/build/rastertocapt"

[ -x "$FILTER" ] || { echo "!! $FILTER missing: run ./build.sh first" >&2; exit 1; }
mkdir -p "$OUT"

echo "==> 1/4  Hi-SCoA round trip (compress + decompress a real page)"
printf 'Canon LBP2900 test page\ncaptdriver on macOS\n' > "$OUT/test-page.txt"
cupsfilter -p "$PPD" -m application/vnd.cups-raster "$OUT/test-page.txt" \
    > "$OUT/test-page.ras" 2> "$OUT/cupsfilter.log"
python3 - "$OUT/test-page.ras" "$OUT/test-page.pbm" <<'PY'
import struct, sys
f = open(sys.argv[1], 'rb').read()
w, h, bpl = struct.unpack_from('<I', f, 4 + 256 + 20 + 8 + 16 + 12 + 8 + 32 + 8 + 12)[0], \
            struct.unpack_from('<I', f, 4 + 256 + 20 + 8 + 16 + 12 + 8 + 32 + 8 + 16)[0], \
            struct.unpack_from('<I', f, 4 + 256 + 20 + 8 + 16 + 12 + 8 + 32 + 8 + 32)[0]
open(sys.argv[2], 'wb').write(b'P4\n#\n%d %d\n' % (bpl * 8, h) + f[1800:1800 + bpl * h])
PY
"$DIR/build/tools/test-hiscoa" "$OUT/test-page.pbm" 2>&1 | grep FINISHED

echo "==> 2/4  Run rastertocapt against the emulated printer"
python3 "$DIR/tools/capt-fake-printer.py" --filter "$FILTER" --raster "$OUT/test-page.ras" \
    --capture "$OUT/test-page.capt" --log "$OUT/rastertocapt.log" --timeout 240 \
    > "$OUT/fake-printer.log"
grep -E 'RESULT|milestones|^.{11}page ' "$OUT/fake-printer.log"
grep -q 'RESULT: PASS' "$OUT/fake-printer.log"

echo "==> 2b   Paper-out on page 2 of 3: the emulated tray runs empty, the user loads paper and presses the button"
printf 'page one\f\npage two\f\npage three\n' > "$OUT/three-pages.txt"
cupsfilter -p "$PPD" -m application/vnd.cups-raster "$OUT/three-pages.txt" \
    > "$OUT/three-pages.ras" 2> "$OUT/cupsfilter-3p.log"
python3 "$DIR/tools/capt-fake-printer.py" --filter "$FILTER" --raster "$OUT/three-pages.ras" \
    --paper-out-page 2 --paper-out-polls 8 \
    --log "$OUT/rastertocapt-paper-out.log" --timeout 240 \
    > "$OUT/fake-printer-paper-out.log"
grep -E 'RESULT|milestones' "$OUT/fake-printer-paper-out.log" | cut -c1-600
grep -q 'RESULT: PASS' "$OUT/fake-printer-paper-out.log"

echo "==> 2c   Same, printer variants seen on the real unit: silent drop (no NOPAPER flags), unit never released, both"
for variant in "silent:--paper-out-silent" "never-release:--release-after-polls=-1" "silent-never:--paper-out-silent --release-after-polls=-1"; do
    name=${variant%%:*}; opts=${variant#*:}
    # shellcheck disable=SC2086
    python3 "$DIR/tools/capt-fake-printer.py" --filter "$FILTER" --raster "$OUT/three-pages.ras" \
        --paper-out-page 2 --paper-out-polls 8 $opts \
        --log "$OUT/rastertocapt-paper-out-$name.log" --timeout 240 \
        > "$OUT/fake-printer-paper-out-$name.log"
    printf '    %-14s %s\n' "$name" "$(grep -E 'RESULT' "$OUT/fake-printer-paper-out-$name.log")"
    grep -q 'RESULT: PASS' "$OUT/fake-printer-paper-out-$name.log"
done

echo "==> 2d   Tray stays empty through the automatic retry: LED must come on and the page wait for the button"
python3 "$DIR/tools/capt-fake-printer.py" --filter "$FILTER" --raster "$OUT/three-pages.ras" \
    --paper-out-page 2 --paper-out-polls 40 \
    --log "$OUT/rastertocapt-paper-out-button.log" --timeout 240 \
    > "$OUT/fake-printer-paper-out-button.log"
grep -E 'RESULT' "$OUT/fake-printer-paper-out-button.log"
grep -q 'RESULT: PASS' "$OUT/fake-printer-paper-out-button.log"
grep -q 'user pressed the blinking button' "$OUT/fake-printer-paper-out-button.log" || { echo "!! the button path was not exercised"; exit 1; }
drops=$(grep milestones "$OUT/fake-printer-paper-out-button.log" | grep -o 'page dropped' | wc -l | tr -d ' ')
[ "$drops" -eq 2 ] || { echo "!! expected exactly 2 drops (one automatic retry), got $drops"; exit 1; }

echo "==> 2e   Photo page (~4 MB of CAPT data): the filter must pace by BUFFERFULL, the emulated buffer is 1 MB"
python3 "$DIR/tools/make-photo-page.py" "$OUT/photo.png" > /dev/null
cupsfilter -p "$PPD" -m application/vnd.cups-raster "$OUT/photo.png" > "$OUT/photo.ras" 2> "$OUT/cupsfilter-photo.log"
python3 "$DIR/tools/capt-fake-printer.py" --filter "$FILTER" --raster "$OUT/photo.ras" \
    --log "$OUT/rastertocapt-photo.log" --timeout 240 > "$OUT/fake-printer-photo.log"
grep -E 'RESULT|ERROR' "$OUT/fake-printer-photo.log"
grep -q 'RESULT: PASS' "$OUT/fake-printer-photo.log"

echo "==> 3/4  Decode the CAPT stream and compare with the input raster"
"$DIR/build/tools/captdefilter" "$OUT/test-page.capt" > "$OUT/test-page-decoded.pbm" 2> "$OUT/captdefilter.log"
python3 "$DIR/tools/compare-raster.py" "$OUT/test-page.ras" "$OUT/test-page-decoded.pbm"

echo "==> 4/4  Render PNG"
python3 "$DIR/tools/pbm-to-png.py" "$OUT/test-page-decoded.pbm" "$OUT/test-page-decoded.png" --scale 4

echo
echo "PASS. Logs and images: $OUT"
