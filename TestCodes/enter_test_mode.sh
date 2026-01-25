#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/home/pi/_RunScanner"
AUTOSTART_DIR="/home/pi/.config/autostart"
AUTOSTART_FILE="${AUTOSTART_DIR}/myscript.desktop"
AUTOSTART_DISABLED="${AUTOSTART_DIR}/myscript.desktop.disabled"

echo "[enter_test_mode] stopping services..."
sudo systemctl stop scanner-agent.service 2>/dev/null || true
sudo systemctl stop scanner-uploader.service 2>/dev/null || true
sudo systemctl stop scanner-poller.service 2>/dev/null || true

echo "[enter_test_mode] disabling services..."
sudo systemctl disable scanner-agent.service 2>/dev/null || true
sudo systemctl disable scanner-uploader.service 2>/dev/null || true
sudo systemctl disable scanner-poller.service 2>/dev/null || true

echo "[enter_test_mode] disabling GUI autostart (myscript.desktop)..."
mkdir -p "${AUTOSTART_DIR}"
if [[ -f "${AUTOSTART_FILE}" ]]; then
  mv -f "${AUTOSTART_FILE}" "${AUTOSTART_DISABLED}"
  echo "[enter_test_mode] moved to: ${AUTOSTART_DISABLED}"
else
  echo "[enter_test_mode] ${AUTOSTART_FILE} not present (ok)"
fi

echo "[enter_test_mode] done."
echo "[enter_test_mode] service states:"
systemctl is-enabled scanner-agent.service 2>/dev/null || true
systemctl is-enabled scanner-uploader.service 2>/dev/null || true
systemctl is-enabled scanner-poller.service 2>/dev/null || true
systemctl is-active scanner-agent.service 2>/dev/null || true
systemctl is-active scanner-uploader.service 2>/dev/null || true
systemctl is-active scanner-poller.service 2>/dev/null || true
