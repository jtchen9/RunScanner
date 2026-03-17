#!/usr/bin/env bash
set -euo pipefail

PY="/usr/bin/python3"
REG="/opt/_RunScanner/register.py"

PENDING_HOST_FILE="/opt/_RunScanner/pending_hostname.txt"
PENDING_TS_MODE_FILE="/opt/_RunScanner/pending_tailscaled_mode.txt"
PENDING_TS_B64_FILE="/opt/_RunScanner/pending_tailscaled_state.b64"

TS_STATE_FILE="/var/lib/tailscale/tailscaled.state"

while true; do
  set +e
  $PY "$REG"
  RC=$?
  set -e

  if [[ "$RC" -eq 0 ]]; then
    exit 0
  fi

  # Identity transition required:
  #   mode=replace -> decode/install new tailscaled.state
  #   mode=delete  -> remove old tailscaled.state to avoid IP conflict
  #
  # Safe order:
  # 1) stop tailscaled
  # 2) replace/delete state
  # 3) change hostname
  # 4) start tailscaled
  # 5) reboot (robot path)
  if [[ "$RC" -eq 11 ]]; then
    if [[ ! -f "$PENDING_HOST_FILE" || ! -f "$PENDING_TS_MODE_FILE" ]]; then
      echo "Pending identity transition files missing"
      sleep 2
      continue
    fi

    SHORT_NAME="$(tr -d '[:space:]' < "$PENDING_HOST_FILE")"
    MODE="$(tr -d '[:space:]' < "$PENDING_TS_MODE_FILE")"

    if [[ -z "$SHORT_NAME" ]]; then
      echo "Pending hostname empty"
      sleep 2
      continue
    fi

    if [[ "$MODE" != "replace" && "$MODE" != "delete" ]]; then
      echo "Invalid pending tailscale mode: $MODE"
      sleep 2
      continue
    fi

    echo "Stopping tailscaled..."
    sudo systemctl stop tailscaled || true

    if [[ "$MODE" == "replace" ]]; then
      if [[ ! -f "$PENDING_TS_B64_FILE" ]]; then
        echo "Pending tailscaled_state_b64 missing for replace mode"
        sleep 2
        continue
      fi

      TMP_STATE="$(mktemp)"

      echo "Decoding tailscaled state..."
      if ! base64 -d "$PENDING_TS_B64_FILE" > "$TMP_STATE"; then
        echo "Failed to decode tailscaled_state_b64"
        rm -f "$TMP_STATE"
        sleep 2
        continue
      fi

      echo "Installing new tailscaled.state..."
      sudo cp "$TMP_STATE" "$TS_STATE_FILE"
      sudo chmod 600 "$TS_STATE_FILE"
      rm -f "$TMP_STATE"

    else
      echo "No tailscaled_state_b64 from NMS; deleting old tailscaled.state to avoid IP conflict..."
      sudo bash -lc 'rm -f /var/lib/tailscale/tailscaled.state*'
    fi

    echo "Changing hostname to $SHORT_NAME ..."
    sudo hostnamectl set-hostname "$SHORT_NAME"

    echo "Starting tailscaled..."
    sudo systemctl start tailscaled || true

    echo "Identity transition completed. Rebooting..."
    sudo reboot
    exit 0
  fi

  sleep 2
done