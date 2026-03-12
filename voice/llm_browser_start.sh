#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------------------
# Logs (persistent, easy to read)
# -------------------------------------------------------------------
LOG="/opt/_RunScanner/voice/llm_browser_start.log"
WID_FILE="/opt/_RunScanner/voice/llm_browser_wid.txt"
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
p="/opt/_RunScanner/voice/voice_config.json"
try:
    j=json.load(open(p,"r",encoding="utf-8"))
    print((j.get("llm_browser") or {}).get("profile_dir") or "/tmp/voice_llm_browser_profile")
except Exception:
    print("/tmp/voice_llm_browser_profile")
PY
)"

URL="$(python3 - <<'PY'
import json
p="/opt/_RunScanner/voice/voice_config.json"
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

# --- Ensure DBus session bus env exists BEFORE launching Chromium ---
if [[ -z "${DBUS_SESSION_BUS_ADDRESS-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi
log "ENV DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS-}"


# -------------------------------------------------------------------
# Launch Chromium on Xwayland (X11)
# IMPORTANT: Do not block; we must return quickly.
# -------------------------------------------------------------------
log "Launching Chromium..."
nohup chromium-browser \
  --ozone-platform=x11 \
  --force-device-scale-factor=1 \
  --high-dpi-support=1 \
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

# -------------------------------------------------------------------
# From here on: choose mic coords (dual vs single monitor), wait until
# ChatGPT UI is likely ready, click mic, then minimize.
# -------------------------------------------------------------------

# --- Detect monitor count (works in dev + production) ---
MON_COUNT="$(xrandr --listmonitors 2>/dev/null | awk 'NR==1{print $2}' || true)"
if [[ -z "${MON_COUNT:-}" ]]; then
  # fallback: count connected outputs
  MON_COUNT="$(xrandr --query 2>/dev/null | grep -c " connected" || true)"
fi
MON_COUNT="${MON_COUNT:-1}"
log "MONITOR_COUNT=$MON_COUNT"

DUAL_X=655; DUAL_Y=404; SINGLE_X=645; SINGLE_Y=357

if [[ "$MON_COUNT" -ge 2 ]]; then
  MIC_RX="$DUAL_X"; MIC_RY="$DUAL_Y"
  log "MIC_PROFILE=dual rel=($MIC_RX,$MIC_RY)"
else
  MIC_RX="$SINGLE_X"; MIC_RY="$SINGLE_Y"
  log "MIC_PROFILE=single rel=($MIC_RX,$MIC_RY)"
fi

# --- Helper: safe sleep that respects deadline ---
safe_sleep() {
  local s="${1:-0.2}"
  # if we're already close to deadline, don't sleep long
  if ! deadline_ok; then
    return 0
  fi
  sleep "$s"
}

# --- Wait for page to "look loaded" (bounded) ---
# On Pi 4B you found you need ~3s+ before click. We'll do:
#  - wait until title contains "chatgpt" (or we hit a cap)
#  - then an extra settle delay (default 3.0s)
TITLE_CAP_SEC=6          # cap for waiting on title
POST_TITLE_SETTLE_SEC=3  # extra settle after title is "ChatGPT"
t_title_start="$(date +%s)"
got_chatgpt_title=0

while true; do
  if ! deadline_ok; then
    log "WARN: deadline reached during title-wait; clicking anyway"
    break
  fi

  title="$(xdotool getwindowname "$WID" 2>/dev/null || true)"
  if [[ -n "$title" ]]; then
    log "UI: title='$title'"
    if echo "$title" | tr '[:upper:]' '[:lower:]' | grep -q "chatgpt"; then
      got_chatgpt_title=1
      break
    fi
  fi

  now="$(date +%s)"
  if (( now - t_title_start >= TITLE_CAP_SEC )); then
    log "UI: title cap (${TITLE_CAP_SEC}s) reached; clicking anyway"
    break
  fi

  safe_sleep 0.5
done

if [[ "$got_chatgpt_title" -eq 1 ]]; then
  # Give the page time to finish laying out the mic button.
  # (This is the important part for Pi 4B stability.)
  safe_sleep "$POST_TITLE_SETTLE_SEC"
else
  # Still give a small minimum wait even if title heuristic failed.
  safe_sleep 3
fi

# --- Click ChatGPT mic button (relative-to-window coords) ---
log "MIC_CLICK: using rel=($MIC_RX,$MIC_RY) in window $WID"

# Ensure Chromium window is focused before clicking
xdotool windowactivate --sync "$WID" >/dev/null 2>&1 || true
safe_sleep 0.2

# First click
xdotool mousemove --sync --window "$WID" "$MIC_RX" "$MIC_RY" click 1 >/dev/null 2>&1 || true
safe_sleep 0.8

# --- Minimize so user only sees your Tk GUI ---
xdotool windowminimize "$WID" >/dev/null 2>&1 || true
log "Minimized window $WID after mic click"

exit 0
