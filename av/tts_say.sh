#!/usr/bin/env bash
set -euo pipefail

# usage:
#   tts_say.sh "hello world" [lead_silence_ms] [mpv_volume] [rate] [amp]
TEXT="${1:-}"
LEAD_MS="${2:-300}"
MPV_VOL="${3:-120}"     # mpv gain; 100 = nominal, >100 boosts
RATE="${4:-140}"        # espeak-ng speed
AMP="${5:-200}"         # espeak-ng amplitude 0-200

RAW="/tmp/tts_raw.wav"
PAD="/tmp/tts_pad.wav"
OUT="/tmp/tts_padded.wav"

# ---- call trace (keep!) ----
echo "[$(date '+%F %T')] tts_say.sh uid=$(id -u) user=$(id -un) TEXT_LEN=${#TEXT} ARGS=$*" >> /tmp/tts_calls.log

rm -f "$RAW" "$PAD" "$OUT"

# 1) TTS -> wav
/usr/bin/espeak-ng -s "$RATE" -a "$AMP" -w "$RAW" "$TEXT"

# 2) lead-in silence
/usr/bin/ffmpeg -hide_banner -loglevel error \
  -f lavfi -i "anullsrc=r=48000:cl=mono" \
  -t "$(python3 - <<PY
ms=int(${LEAD_MS}); print(ms/1000.0)
PY
)" \
  -c:a pcm_s16le "$PAD"

# 3) concat silence + speech
/usr/bin/ffmpeg -hide_banner -loglevel error \
  -i "$PAD" -i "$RAW" \
  -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1[a]" -map "[a]" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$OUT"

# 4) PLAYBACK: FORCE ALSA DEFAULT (robust vs PipeWire HDMI sink reorder)
# IMPORTANT: do NOT auto-select pulse/pipewire here.
exec /usr/bin/mpv \
  --ao=alsa \
  --audio-device=alsa/default \
  --no-video \
  --volume="$MPV_VOL" \
  "$OUT" >/dev/null 2>&1
