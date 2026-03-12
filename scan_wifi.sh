#!/usr/bin/env bash
set -euo pipefail

# --- single-instance guard ---
mkdir -p /opt/_RunScanner/.cache
exec 9>"/opt/_RunScanner/.cache/scanner-poller.lock" || exit 1
flock -n 9 || { echo "Another scanner-poller instance is running; exiting."; exit 0; }
# -----------------------------

IFACE="${IFACE:-wlan0}"
OUT_CSV="/tmp/latest_scan.csv"
OUT_JSON="/tmp/latest_scan.json"
MODE="${1:-once}"

PARSER="/opt/_RunScanner/parse_iw.py"
IW_BIN="$(command -v iw)"

scan_once() {
  : > "$OUT_CSV"
  : > "$OUT_JSON"
  # run iw (stderr -> debug file), pipe stdout to parser
  if ! "$IW_BIN" dev "$IFACE" scan 2>/tmp/iw_err.txt | "$PARSER" "$OUT_CSV" "$OUT_JSON"; then
    echo "Parser failed; see /tmp/iw_err.txt"
    return 1
  fi
}

if [[ "$MODE" == "loop" ]]; then
  while true; do
    echo "---- $(date '+%F %T') ----"
    scan_once || true
    sleep 60
  done
else
  scan_once
fi
