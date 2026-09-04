#!/bin/bash
# uninstall.sh - remove the queue, filter and PPD installed by install.sh.
#   sudo ./uninstall.sh
set -uo pipefail
QUEUE="Canon_LBP2900"
FILTER_DIR="/Library/Printers/captdriver"
PPD_DST="/Library/Printers/PPDs/Contents/Resources/Canon-LBP2900-captdriver.ppd"
if [ "$(id -u)" -ne 0 ]; then echo "!! Run as administrator:  sudo ./uninstall.sh" >&2; exit 1; fi
/usr/sbin/lpadmin -x "$QUEUE" 2>/dev/null && echo "removed queue $QUEUE" || echo "(no queue $QUEUE)"
rm -rf "$FILTER_DIR" && echo "removed $FILTER_DIR"
rm -f "$PPD_DST" && echo "removed $PPD_DST"
