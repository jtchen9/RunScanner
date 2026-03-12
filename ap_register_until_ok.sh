#!/usr/bin/env bash
set -euo pipefail

PY="/usr/bin/python3"
REG="/opt/_RunScanner/ap_register.py"
NAME_FILE="/opt/_RunScanner/scanner_name.txt"

while true; do
  if $PY "$REG"; then
    if [[ -f "$NAME_FILE" ]]; then
      ASSIGNED_NAME="$(cat "$NAME_FILE" | tr -d '[:space:]')"
      CURRENT_HOST="$(hostnamectl --static)"

      if [[ -n "$ASSIGNED_NAME" && "$CURRENT_HOST" != "$ASSIGNED_NAME" ]]; then
        echo "Hostname mismatch: $CURRENT_HOST -> $ASSIGNED_NAME"
        sudo hostnamectl set-hostname "$ASSIGNED_NAME"
        echo "Hostname changed. Rebooting..."
        sudo reboot
        exit 0
      fi
    fi

    exit 0
  fi

  sleep 2
done
