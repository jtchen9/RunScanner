#!/usr/bin/env bash
set -euo pipefail

CFG="/home/pi/_RunScanner/av/av_stream_config.json"
LOG="/home/pi/_RunScanner/av/av_stream.log"

# Wait for config to exist (agent writes it before starting service)
if [[ ! -f "$CFG" ]]; then
  echo "$(date) ERROR: config missing: $CFG" >> "$LOG"
  exit 1
fi

# Parse JSON with python (avoid jq dependency)
read_json() {
  /usr/bin/python3 - << 'PY'
import json
p="/home/pi/_RunScanner/av/av_stream_config.json"
j=json.load(open(p,"r",encoding="utf-8"))
# print fields line-by-line in a stable order
keys=["server","port","path","transport","video_dev","audio_dev","size","fps"]
for k in keys:
    print(str(j.get(k,"")))
PY
}

mapfile -t LINES < <(read_json)
SERVER="${LINES[0]}"
PORT="${LINES[1]}"
PATHNAME="${LINES[2]}"
TRANSPORT="${LINES[3]}"
VIDEO_DEV="${LINES[4]}"
AUDIO_DEV="${LINES[5]}"
SIZE="${LINES[6]}"
FPS="${LINES[7]}"

# Defaults / guardrails
: "${SERVER:=6g-private.com}"
: "${PORT:=8554}"
: "${PATHNAME:=twin-scout-bravo}"
: "${TRANSPORT:=tcp}"
: "${VIDEO_DEV:=/dev/video0}"
: "${AUDIO_DEV:=plughw:1,0}"
: "${SIZE:=640x480}"
: "${FPS:=30}"

RTSP_URL="rtsp://${SERVER}:${PORT}/${PATHNAME}"

echo "$(date) START avstream -> ${RTSP_URL} (v=${VIDEO_DEV} a=${AUDIO_DEV} ${SIZE}@${FPS})" >> "$LOG"

# Important: keep ffmpeg in foreground (systemd tracks it)
exec /usr/bin/ffmpeg -hide_banner -loglevel info \
  -f v4l2 -framerate "${FPS}" -video_size "${SIZE}" -input_format mjpeg -i "${VIDEO_DEV}" \
  -f alsa -ac 1 -ar 48000 -i "${AUDIO_DEV}" \
  -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -pix_fmt yuv420p -b:v 1200k \
  -c:a libopus -b:a 64k -ar 48000 -ac 1 \
  -f rtsp -rtsp_transport "${TRANSPORT}" "${RTSP_URL}"
