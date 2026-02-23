#!/usr/bin/env bash
set -euo pipefail

PY="/usr/bin/python3"
REG="/home/pi/_RunScanner/register.py"

# Policy: keep trying until success.
# Backoff: 2s per attempt (cheap and stable)
# Optional: add a max attempts if you ever want "give up after X".

while true; do
  $PY "$REG" && exit 0
  sleep 2
done