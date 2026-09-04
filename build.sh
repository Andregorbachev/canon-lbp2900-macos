#!/bin/bash
#
# build.sh - build the CAPT filter (captdriver + macOS patch) and its PPD.
#
#   ./build.sh
#
# Inputs : vendor/captdriver (pinned upstream), patches/*.patch (applied in name order)
# Outputs: build/rastertocapt              CUPS filter, arm64/x86_64 (whatever this Mac is)
#          build/tools/captdefilter        decodes a CAPT stream back to a PBM image (from upstream tests/)
#          build/tools/test-hiscoa         Hi-SCoA compress/decompress round-trip test
#          ppd/Canon-LBP2900-captdriver.ppd  PPD compiled from vendor/captdriver/src/canon-lbp.drv with ppdc,
#                                          cupsFilter pointed at the absolute install path
# Needs Xcode Command Line Tools (cc, cups-config, ppdc all ship with macOS). No autotools.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$DIR/vendor/captdriver"
PATCHES="$DIR/patches"/*.patch          # applied in name order
BUILD="$DIR/build"
PPD_OUT="$DIR/ppd/Canon-LBP2900-captdriver.ppd"
FILTER_INSTALL_PATH="/Library/Printers/captdriver/rastertocapt"   # keep in sync with install.sh

command -v cc >/dev/null || { echo "!! cc not found: xcode-select --install" >&2; exit 1; }
command -v cups-config >/dev/null || { echo "!! cups-config not found: xcode-select --install" >&2; exit 1; }
command -v ppdc >/dev/null || { echo "!! ppdc not found (it ships with macOS in /usr/bin)" >&2; exit 1; }

echo "==> 1/4  Copy upstream source and apply patch"
rm -rf "$BUILD/captdriver" "$BUILD/ppd"
mkdir -p "$BUILD/captdriver" "$BUILD/tools" "$BUILD/ppd" "$DIR/ppd"
cp -R "$SRC/src" "$SRC/tests" "$BUILD/captdriver/"
for p in $PATCHES; do echo "    patch: $(basename "$p")"; ( cd "$BUILD/captdriver" && patch -p1 --silent < "$p" ); done

echo "==> 2/4  Compile rastertocapt"
# Universal binary (Apple Silicon + Intel), runs on macOS 10.13 and newer; libcups is in every macOS.
CFLAGS="-std=c99 -Wall -Wextra -pedantic -O2 -arch arm64 -arch x86_64 -mmacosx-version-min=10.13 $(cups-config --cflags)"
LIBS="$(cups-config --image --libs)"
cc $CFLAGS -o "$BUILD/rastertocapt" "$BUILD"/captdriver/src/*.c $LIBS
file "$BUILD/rastertocapt" | sed 's#^.*rastertocapt: ##'
lipo -info "$BUILD/rastertocapt"

echo "==> 3/4  Compile test tools"
cc -std=c99 -O2 -I"$BUILD/captdriver/src" -o "$BUILD/tools/captdefilter" \
   "$BUILD/captdriver/tests/captdefilter.c" "$BUILD/captdriver/tests/hiscoa-decompress.c"
cc -std=c99 -O2 -I"$BUILD/captdriver/src" -o "$BUILD/tools/test-hiscoa" \
   "$BUILD/captdriver/tests/test-hiscoa.c" "$BUILD/captdriver/src/hiscoa-compress.c" \
   "$BUILD/captdriver/tests/hiscoa-decompress.c" 2>&1 | grep -v 'set but not used' || true

echo "==> 4/4  Compile PPD from canon-lbp.drv"
ppdc -d "$BUILD/ppd" "$SRC/src/canon-lbp.drv" >/dev/null
sed -e "s#^\*cupsFilter: \"application/vnd.cups-raster 1 rastertocapt\"#*cupsFilter: \"application/vnd.cups-raster 1 $FILTER_INSTALL_PATH\"#" \
    -e 's#^\*NickName: .*#*NickName: "Canon LBP2900 (captdriver 0.1.4, macOS)"#' \
    -e 's#^\*ShortNickName: .*#*ShortNickName: "Canon LBP2900 captdriver"#' \
    "$BUILD/ppd/CanonLBP-2900-3000.ppd" > "$PPD_OUT"
grep -q "$FILTER_INSTALL_PATH" "$PPD_OUT" || { echo "!! cupsFilter line not rewritten" >&2; exit 1; }
# -I filters: the filter is not installed yet, so its absence is not an error here.
cupstestppd -I filters -q "$PPD_OUT" && echo "    cupstestppd: PASS"

echo
echo "Built: $BUILD/rastertocapt and $PPD_OUT"
echo "Next:  ./test.sh (no printer needed), then sudo ./install.sh"
