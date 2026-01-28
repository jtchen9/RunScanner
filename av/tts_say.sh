#!/usr/bin/env bash
set -euo pipefail

# usage:
#   tts_say.sh "hello world" [lead_silence_ms] [volume]
TEXT="${1:-}"
LEAD_MS="${2:-300}"
VOL="${3:-90}"

RAW="/tmp/tts_raw.wav"
PAD="/tmp/tts_pad.wav"
OUT="/tmp/tts_padded.wav"

rm -f "$RAW" "$PAD" "$OUT"

# 1) TTS -> RAW wav (espeak-ng is lightweight and usually available)
# (If your Pi uses another backend later, swap this line only.)
/usr/bin/espeak-ng -w "$RAW" "$TEXT"

# 2) Make a short silence wav
/usr/bin/ffmpeg -hide_banner -loglevel error \
  -f lavfi -i "anullsrc=r=48000:cl=mono" -t "$(python3 - <<PY
ms=int(${LEAD_MS}); print(ms/1000.0)
PY
)" \
  -c:a pcm_s16le "$PAD"

# 3) Concatenate silence + speech
/usr/bin/ffmpeg -hide_banner -loglevel error \
  -i "$PAD" -i "$RAW" \
  -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1[a]" -map "[a]" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$OUT"

# 4) Play
/usr/bin/mpv --ao=alsa --audio-device=alsa/default --no-video --volume="$VOL" "$OUT" >/dev/null 2>&1
