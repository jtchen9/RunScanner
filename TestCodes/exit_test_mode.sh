#!/usr/bin/env bash
set -euo pipefail

AUTOSTART_DIR="/home/pi/.config/autostart"
AUTOSTART_FILE="${AUTOSTART_DIR}/myscript.desktop"
AUTOSTART_DISABLED="${AUTOSTART_DIR}/myscript.desktop.disabled"

echo "[exit_test_mode] restoring GUI autostart (myscript.desktop)..."
mkdir -p "${AUTOSTART_DIR}"
if [[ -f "${AUTOSTART_DISABLED}" ]]; then
  mv -f "${AUTOSTART_DISABLED}" "${AUTOSTART_FILE}"
  echo "[exit_test_mode] restored: ${AUTOSTART_FILE}"
else
  echo "[exit_test_mode] ${AUTOSTART_DISABLED} not present (ok)"
fi

echo "[exit_test_mode] NOTE: services remain disabled/off."
echo "[exit_test_mode] If you want production mode back, run:"
echo "  sudo systemctl enable scanner-agent.service scanner-uploader.service"
echo "  sudo systemctl start  scanner-agent.service scanner-uploader.service"
echo "  # scanner-poller.service is usually started by command"
echo "[exit_test_mode] done."
