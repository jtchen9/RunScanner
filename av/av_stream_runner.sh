#!/usr/bin/env bash
set -euo pipefail

CFG="/opt/_RunScanner/av/av_stream_config.json"
LOG="/opt/_RunScanner/av/av_stream.log"

FRONT_VIDEO="/dev/v4l/by-id/usb-webcamvendor_webcamproduct_YGR80PU1200.23071717-video-index0"
FRONT_AUDIO="plughw:CARD=webcamproduct,DEV=0"

REAR_VIDEO="/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_200901010001-video-index0"

if [[ ! -f "$CFG" ]]; then
  echo "$(date) ERROR: config missing: $CFG" >> "$LOG"
  exit 1
fi

read_json() {
  /usr/bin/python3 - << 'PY'
import json
p="/opt/_RunScanner/av/av_stream_config.json"
j=json.load(open(p,"r",encoding="utf-8"))
keys=[
  "server","port","path","transport",
  "video_dev","audio_dev","size","fps",
  "camera_role","scanner","audio_enabled"
]
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
CAMERA_ROLE="${LINES[8]}"
SCANNER="${LINES[9]}"
AUDIO_ENABLED="${LINES[10]}"

: "${SERVER:=6g-private.com}"
: "${PORT:=8554}"
: "${TRANSPORT:=tcp}"
: "${FPS:=30}"
: "${CAMERA_ROLE:=legacy}"

# ----------------------------
# Role-based camera selection
# ----------------------------
if [[ "$CAMERA_ROLE" == "front" ]]; then
  : "${SCANNER:=twin-scout-julia}"
  : "${PATHNAME:=${SCANNER}}"
  VIDEO_DEV="$FRONT_VIDEO"
  AUDIO_DEV="$FRONT_AUDIO"
  SIZE="${SIZE:-1280x720}"
  AUDIO_ENABLED="${AUDIO_ENABLED:-true}"
  VIDEO_MODE="front_h264_copy"

elif [[ "$CAMERA_ROLE" == "rear" ]]; then
  : "${SCANNER:=twin-scout-julia}"
  : "${PATHNAME:=${SCANNER}-rear}"
  VIDEO_DEV="$REAR_VIDEO"
  AUDIO_DEV=""
  SIZE="${SIZE:-640x480}"
  AUDIO_ENABLED="${AUDIO_ENABLED:-false}"
  VIDEO_MODE="rear_yuyv_x264"

else
  # Backward-compatible legacy behavior
  : "${PATHNAME:=twin-scout-julia}"
  : "${VIDEO_DEV:=/dev/video0}"
  : "${AUDIO_DEV:=plughw:1,0}"
  : "${SIZE:=640x480}"
  AUDIO_ENABLED="${AUDIO_ENABLED:-true}"
  VIDEO_MODE="legacy_mjpeg_x264"
fi

RTSP_URL="rtsp://${SERVER}:${PORT}/${PATHNAME}"

echo "$(date) START avstream role=${CAMERA_ROLE} mode=${VIDEO_MODE} -> ${RTSP_URL} (v=${VIDEO_DEV} a=${AUDIO_DEV} ${SIZE}@${FPS} audio=${AUDIO_ENABLED})" >> "$LOG"

# ----------------------------
# Build ffmpeg command
# ----------------------------
if [[ "$VIDEO_MODE" == "front_h264_copy" ]]; then
  CMD=(
    /usr/bin/ffmpeg -hide_banner -loglevel info
    -f v4l2 -framerate "$FPS" -video_size "$SIZE" -input_format h264 -i "$VIDEO_DEV"
  )

  if [[ "$AUDIO_ENABLED" == "true" && -n "$AUDIO_DEV" ]]; then
    CMD+=(
      -f alsa -ac 1 -ar 48000 -i "$AUDIO_DEV"
      -c:v copy
      -c:a libopus -b:a 64k -ar 48000 -ac 1
    )
  else
    CMD+=(
      -c:v copy
      -an
    )
  fi

elif [[ "$VIDEO_MODE" == "rear_yuyv_x264" ]]; then
  CMD=(
    /usr/bin/ffmpeg -hide_banner -loglevel info
    -f v4l2 -framerate "$FPS" -video_size "$SIZE" -input_format yuyv422 -i "$VIDEO_DEV"
    -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline
    -pix_fmt yuv420p -b:v 500k
    -an
  )

else
  CMD=(
    /usr/bin/ffmpeg -hide_banner -loglevel info
    -f v4l2 -framerate "$FPS" -video_size "$SIZE" -input_format mjpeg -i "$VIDEO_DEV"
  )

  if [[ "$AUDIO_ENABLED" == "true" && -n "$AUDIO_DEV" ]]; then
    CMD+=(
      -f alsa -ac 1 -ar 48000 -i "$AUDIO_DEV"
      -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline
      -pix_fmt yuv420p -b:v 1200k
      -c:a libopus -b:a 64k -ar 48000 -ac 1
    )
  else
    CMD+=(
      -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline
      -pix_fmt yuv420p -b:v 1200k
      -an
    )
  fi
fi

CMD+=(
  -f rtsp -rtsp_transport "$TRANSPORT" "$RTSP_URL"
)

exec "${CMD[@]}"
