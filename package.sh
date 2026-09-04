#!/bin/bash
#
# package.sh - make a self-contained archive for another Mac (no Xcode needed there).
#
#   ./build.sh && ./test.sh && ./package.sh
#   -> dist/canon-lbp2900-macos-<version>-universal.tar.gz
#
# The archive keeps the project layout install.sh expects: build/rastertocapt, ppd/, install.sh.
# The filter inside is a universal binary (arm64 + x86_64), so the same archive serves
# Apple Silicon and Intel Macs running macOS 10.13 or newer.
#
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="$(cat "$DIR/VERSION")"
NAME="canon-lbp2900-macos-$VER-universal"
OUT="$DIR/dist"
[ -x "$DIR/build/rastertocapt" ] || { echo "!! run ./build.sh first" >&2; exit 1; }
rm -rf "$OUT/$NAME"; mkdir -p "$OUT/$NAME/build" "$OUT/$NAME/ppd"
cp "$DIR/build/rastertocapt" "$OUT/$NAME/build/"
cp "$DIR/ppd/Canon-LBP2900-captdriver.ppd" "$OUT/$NAME/ppd/"
cp "$DIR/install.sh" "$DIR/uninstall.sh" "$DIR/README.md" "$DIR/LICENSE" "$DIR/VERSION" "$OUT/$NAME/"
cp -R "$DIR/docs" "$OUT/$NAME/"
( cd "$OUT" && tar -czf "$NAME.tar.gz" "$NAME" && rm -rf "$NAME" )
shasum -a 256 "$OUT/$NAME.tar.gz" | tee "$OUT/$NAME.tar.gz.sha256"
echo "On the other Mac:  tar -xzf $NAME.tar.gz && cd $NAME && sudo ./install.sh"
