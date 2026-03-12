#!/usr/bin/env bash
set -euo pipefail

PY="/usr/bin/python3"
REG="/opt/_RunScanner/register.py"
NAME_FILE="/opt/_RunScanner/scanner_name.txt"

while true; do
  if $PY "$REG"; then
    # Registration succeeded

    if [[ -f "$NAME_FILE" ]]; then
      SCANNER_NAME="$(cat "$NAME_FILE" | tr -d '[:space:]')"

      if [[ "$SCANNER_NAME" == twin-scout-* ]]; then
        SHORT_NAME="${SCANNER_NAME#twin-scout-}"
        CURRENT_HOST="$(hostnamectl --static)"

        if [[ "$CURRENT_HOST" != "$SHORT_NAME" ]]; then
          echo "Hostname mismatch: $CURRENT_HOST -> $SHORT_NAME"
          sudo hostnamectl set-hostname "$SHORT_NAME"
          echo "Hostname changed. Rebooting..."
          sudo reboot
          exit 0
        fi
      fi
    fi

    # Hostname already correct
    exit 0
  fi

  sleep 2
done