#!/usr/bin/env bash
set -euo pipefail

# --- single-instance guard ---
mkdir -p /opt/_RunScanner/.cache
exec 9>"/opt/_RunScanner/.cache/scanner-poller.lock" || exit 1
flock -n 9 || { echo "Another scanner-poller instance is running; exiting."; exit 0; }
# -----------------------------

get_ax210_iface() {
  for iface in /sys/class/net/*; do
    iface="$(basename "$iface")"
    [[ "$iface" == "lo" || "$iface" == "wlan0" ]] && continue

    driver_link="/sys/class/net/$iface/device/driver"
    if [[ -L "$driver_link" ]]; then
      target="$(readlink -f "$driver_link" || true)"
      if [[ "$target" == *"/iwlwifi" ]]; then
        echo "$iface"
        return 0
      fi
    fi
  done
  return 1
}

IFACE="${IFACE:-$(get_ax210_iface || true)}"
if [[ -z "${IFACE:-}" ]]; then
  echo "AX210 interface not found"
  exit 1
fi

OUT_CSV="/tmp/latest_scan.csv"
OUT_JSON="/tmp/latest_scan.json"
MODE="${1:-once}"

PARSER="/opt/_RunScanner/parse_iw.py"
IW_BIN="$(command -v iw)"

# Bound the entire pipeline, including a parser blocked waiting for EOF.
SCAN_TIMEOUT_SEC="${SCAN_TIMEOUT_SEC:-30}"
if ! [[ "$SCAN_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]]; then
  echo "SCAN_TIMEOUT_SEC must be a positive integer" >&2
  exit 2
fi
TIMEOUT_BIN="$(command -v timeout)" || { echo "GNU timeout is required" >&2; exit 1; }

scan_once() (
  local work rc
  # Keep the original not-ready signal for the uploader. Never expose a
  # partially written JSON or let an older report masquerade as this scan.
  : > "$OUT_JSON" || return 1
  : > "$OUT_CSV" || return 1
  work="$(mktemp -d /tmp/scanner-poller.XXXXXXXX)" || return 1
  trap 'rm -rf -- "$work"' EXIT

  # timeout controls the worker process group. Keep the worker shell around
  # through TERM if a pipeline member needs the subsequent KILL.
  if "$TIMEOUT_BIN" --kill-after=3s "${SCAN_TIMEOUT_SEC}s" \
      /bin/bash -o pipefail -c '
        trap ":" TERM
        "$1" dev "$2" scan 2>/tmp/iw_err.txt | /usr/bin/python3 "$3" "$4" "$5"
      ' scan-worker "$IW_BIN" "$IFACE" "$PARSER" "$work/scan.csv" "$work/scan.json" 9>&-; then
    if [[ ! -s "$work/scan.json" || ! -f "$work/scan.csv" ]]; then
      echo "SCAN_INCOMPLETE iface=$IFACE: parser did not produce scan outputs" >&2
      return 1
    fi
    # Same filesystem: publish ready JSON last, in one rename.
    mv -f -- "$work/scan.csv" "$OUT_CSV" || return 1
    mv -f -- "$work/scan.json" "$OUT_JSON" || return 1
  else
    rc=$?
    case "$rc" in
      124) echo "SCAN_TIMEOUT iface=$IFACE limit=${SCAN_TIMEOUT_SEC}s; result discarded" >&2 ;;
      137) echo "SCAN_KILLED iface=$IFACE rc=137 (forced kill); result discarded" >&2 ;;
      *)   echo "SCAN_FAILED iface=$IFACE rc=$rc; see /tmp/iw_err.txt" >&2 ;;
    esac
    return "$rc"
  fi
)

if [[ "$MODE" == "loop" ]]; then
  while true; do
    echo "---- $(date '+%F %T') ----"
    scan_once || true
    sleep 60
  done
else
  scan_once
fi
