#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------------------
# Logs (persistent, easy to read)
# -------------------------------------------------------------------
LOG="/home/pi/_RunScanner/voice/llm_browser_start.log"
WID_FILE="/home/pi/_RunScanner/voice/llm_browser_wid.txt"
mkdir -p "$(dirname "$LOG")"
: >"$LOG"

log(){ echo "[$(date +'%F %T')] $*" | tee -a "$LOG" >/dev/null; }

# -------------------------------------------------------------------
# Hard deadline: voice_service waits 20s; we must finish (success/fail)
# before that so we don't get killed mid-flight.
# -------------------------------------------------------------------
MAX_TOTAL_SEC=18
T0="$(date +%s)"
deadline_ok() {
  local now
  now="$(date +%s)"
  (( now - T0 < MAX_TOTAL_SEC ))
}

# -------------------------------------------------------------------
# GUI env (systemd often lacks these)
# - Avoid hardcoding /run/user/1000: derive from uid
# -------------------------------------------------------------------
UID_NUM="$(id -u)"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"

# Pick current mutter Xwayland auth file each boot
pick_xauth() {
  local xa=""
  xa="$(ls -t "${XDG_RUNTIME_DIR}"/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1 || true)"
  if [[ -n "$xa" ]]; then
    echo "$xa"
  else
    echo "$HOME/.Xauthority"
  fi
}

export XAUTHORITY="${XAUTHORITY:-$(pick_xauth)}"

log "BEGIN uid=${UID_NUM} user=$(id -un) PWD=$PWD"
log "ENV DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR XAUTHORITY=$XAUTHORITY"
log "ENV DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS-}"

# -------------------------------------------------------------------
# Read JSON config (same behavior as your script)
# -------------------------------------------------------------------
CHROME_PROFILE="$(python3 - <<'PY'
import json
p="/home/pi/_RunScanner/voice/voice_config.json"
try:
    j=json.load(open(p,"r",encoding="utf-8"))
    print((j.get("llm_browser") or {}).get("profile_dir") or "/tmp/voice_llm_browser_profile")
except Exception:
    print("/tmp/voice_llm_browser_profile")
PY
)"

URL="$(python3 - <<'PY'
import json
p="/home/pi/_RunScanner/voice/voice_config.json"
try:
    j=json.load(open(p,"r",encoding="utf-8"))
    print((j.get("llm_browser") or {}).get("url") or "https://chatgpt.com/")
except Exception:
    print("https://chatgpt.com/")
PY
)"

log "CFG profile=$CHROME_PROFILE url=$URL"

# -------------------------------------------------------------------
# Wait for Xwayland/X11 to be ready and authorized (bounded by deadline)
# -------------------------------------------------------------------
attempt=0
while true; do
  attempt=$((attempt+1))
  export XAUTHORITY="$(pick_xauth)"

  # Basic conditions: socket + readable xauth + xdotool can query geometry
  if [[ -S /tmp/.X11-unix/X0 && -r "$XAUTHORITY" ]]; then
    if xdotool getdisplaygeometry >/dev/null 2>&1; then
      log "X11 ready (attempt=$attempt) XAUTHORITY=$XAUTHORITY"
      break
    fi
  fi

  if ! deadline_ok; then
    log "ERROR: X11 not ready/authorized before deadline (${MAX_TOTAL_SEC}s). DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"
    exit 11
  fi

  # keep log noise low: print every ~8 attempts
  if (( attempt % 8 == 0 )); then
    log "WAIT: X11 not ready yet (attempt=$attempt) sock=$([[ -S /tmp/.X11-unix/X0 ]] && echo ok || echo no) xauth=$([[ -r "$XAUTHORITY" ]] && echo ok || echo no)"
  fi

  sleep 0.25
done

# -------------------------------------------------------------------
# Start clean for this dedicated profile only (as you do)
# -------------------------------------------------------------------
pkill -f "chromium.*--user-data-dir=${CHROME_PROFILE}" >/dev/null 2>&1 || true
rm -f "$WID_FILE"

# -------------------------------------------------------------------
# Launch Chromium on Xwayland (X11)
# IMPORTANT: Do not block; we must return quickly.
# -------------------------------------------------------------------
log "Launching Chromium..."
nohup chromium-browser \
  --ozone-platform=x11 \
  --user-data-dir="$CHROME_PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=TranslateUI \
  --disable-gpu \
  --disable-software-rasterizer=false \
  --new-window \
  "$URL" >>"$LOG" 2>&1 &

CHROME_PID=$!
log "Chromium PID=$CHROME_PID"

# -------------------------------------------------------------------
# Find window by PID (bounded by deadline)
# -------------------------------------------------------------------
WID=""
probe=0
while [[ -z "$WID" ]]; do
  probe=$((probe+1))

  if ! kill -0 "$CHROME_PID" >/dev/null 2>&1; then
    log "ERROR: Chromium exited before window appeared."
    exit 12
  fi

  WID="$(xdotool search --onlyvisible --pid "$CHROME_PID" 2>/dev/null | tail -n 1 || true)"
  [[ -n "$WID" ]] && break

  if ! deadline_ok; then
    log "ERROR: Timed out waiting for Chromium window (pid=$CHROME_PID)."
    exit 13
  fi

  if (( probe % 8 == 0 )); then
    log "WAIT: still looking for window (probe=$probe pid=$CHROME_PID)"
  fi

  sleep 0.25
done

echo "$WID" > "$WID_FILE"
sync || true
log "WROTE WID_FILE=$WID_FILE value=$WID"
ls -l "$WID_FILE" >>"$LOG" 2>&1 || true
head -c 80 "$WID_FILE" >>"$LOG" 2>&1 || true
echo >>"$LOG"
log "Found window WID=$WID"

# -------------------------------------------------------------------
# Position/size (best-effort; do not fail script if these fail)
# Keep your original geometry logic
# -------------------------------------------------------------------
LEFT_MARGIN=90
TOP_MARGIN=0
WIN_W=$((800 - LEFT_MARGIN))
WIN_H=460

xdotool windowactivate --sync "$WID" || true
sleep 0.2
xdotool windowmove "$WID" "$LEFT_MARGIN" "$TOP_MARGIN" || true
sleep 0.1
xdotool windowsize "$WID" "$WIN_W" "$WIN_H" || true

log "LLM browser started OK. WID=$WID"
echo "LLM browser started. WID=$WID"
exit 0
