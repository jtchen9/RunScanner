#!/usr/bin/env bash
set -euo pipefail

# Keep coherent with llm_browser_start.sh (same WID file location)
WID_FILE="/opt/_RunScanner/voice/llm_browser_wid.txt"

# GUI env (systemd often lacks these) - keep consistent and avoid hardcoding 1000
UID_NUM="$(id -u)"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"
pick_xauth() {
  local xa=""
  xa="$(ls -t "${XDG_RUNTIME_DIR}"/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1 || true)"
  if [[ -n "$xa" ]]; then echo "$xa"; else echo "$HOME/.Xauthority"; fi
}
export XAUTHORITY="${XAUTHORITY:-$(pick_xauth)}"

if [[ ! -f "$WID_FILE" ]]; then
  echo "LLM browser: no WID file; nothing to stop."
  exit 0
fi

WID="$(cat "$WID_FILE" | tr -d '[:space:]' || true)"
rm -f "$WID_FILE"

if [[ -z "$WID" ]]; then
  echo "LLM browser: empty WID; nothing to stop."
  exit 0
fi

# Try graceful close of just this window
if xdotool getwindowname "$WID" >/dev/null 2>&1; then
  xdotool windowactivate --sync "$WID" || true
  # Politely ask Chromium to close that window
  xdotool key --window "$WID" ctrl+w || true
  sleep 0.3
fi

# If still exists, force-close the window (still only that window)
if xdotool getwindowname "$WID" >/dev/null 2>&1; then
  xdotool windowkill "$WID" || true
fi

echo "LLM browser stopped."
