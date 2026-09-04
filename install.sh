#!/bin/bash
#
# install.sh - install the filter + PPD and create the print queue.
#
#   sudo ./install.sh              # finds the LBP2900 on USB by itself
#   sudo ./install.sh usb://...    # or pass the device URI from `lpinfo -v`
#
# Puts the filter in /Library/Printers/captdriver/ (Apple's place for third-party
# print drivers; it survives macOS updates, unlike /usr/libexec/cups/filter/) and
# the PPD in /Library/Printers/PPDs/Contents/Resources/. The PPD references the
# filter by absolute path, the same way the Brother and Canon drivers on this Mac do.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER_SRC="$DIR/build/rastertocapt"
PPD_SRC="$DIR/ppd/Canon-LBP2900-captdriver.ppd"
FILTER_DIR="/Library/Printers/captdriver"
FILTER_DST="$FILTER_DIR/rastertocapt"          # keep in sync with build.sh
PPD_DST="/Library/Printers/PPDs/Contents/Resources/Canon-LBP2900-captdriver.ppd"
QUEUE="Canon_LBP2900"

if [ "$(id -u)" -ne 0 ]; then
  echo "!! Run as administrator:  sudo ./install.sh" >&2
  exit 1
fi
[ -x "$FILTER_SRC" ] || { echo "!! $FILTER_SRC missing: run ./build.sh first (without sudo)" >&2; exit 1; }
[ -f "$PPD_SRC" ] || { echo "!! $PPD_SRC missing: run ./build.sh first" >&2; exit 1; }

echo "==> 1/3  Filter -> $FILTER_DST"
install -d -o root -g wheel -m 0755 "$FILTER_DIR"
install -o root -g wheel -m 0755 "$FILTER_SRC" "$FILTER_DST"

echo "==> 2/3  PPD    -> $PPD_DST"
install -o root -g wheel -m 0644 "$PPD_SRC" "$PPD_DST"
cupstestppd -q "$PPD_DST" || { echo "!! PPD failed cupstestppd" >&2; exit 1; }

echo "==> 3/3  Print queue '$QUEUE'"
URI="${1:-}"
if [ -z "$URI" ]; then
  URI="$(/usr/sbin/lpinfo -v 2>/dev/null | awk '$2 ~ /^usb:/ && tolower($2) ~ /lbp(%20| )?2900/ {print $2; exit}')"
fi
if [ -z "$URI" ]; then
  echo "    LBP2900 not found on USB (lpinfo -v shows no usb://...LBP2900 device)."
  echo "    Filter and PPD are installed. Plug in and switch on the printer, then run again:"
  echo "        sudo ./install.sh"
  exit 0
fi
echo "    device: $URI"
/usr/sbin/lpadmin -p "$QUEUE" -E -v "$URI" -P "$PPD_DST" -D "Canon LBP2900" -L "USB" \
    -o printer-is-shared=false
/usr/sbin/cupsenable "$QUEUE" 2>/dev/null || true
/usr/sbin/cupsaccept "$QUEUE" 2>/dev/null || true
/usr/bin/lpstat -p "$QUEUE"
echo
echo "Done. Test print:   lpr -P $QUEUE build/test/test-page.txt"
echo "Driver log:         sudo cupsctl LogLevel=debug; tail -f /var/log/cups/error_log | grep CAPT"
