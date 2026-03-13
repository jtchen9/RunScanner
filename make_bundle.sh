#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Bundle metadata (EDIT HERE WHEN BUMPING VERSION)
# ------------------------------------------------------------
BUNDLE_ID="robotBundle2.0"
BASE_DIR="/opt/_RunScanner"
OUT_DIR="${BASE_DIR}/_bundle_build"
BUNDLE_DIR="${OUT_DIR}/${BUNDLE_ID}"
ZIP_NAME="${BUNDLE_ID}.zip"

echo "=== Building bundle: ${BUNDLE_ID} ==="

# ------------------------------------------------------------
# Safety checks
# ------------------------------------------------------------
cd "${BASE_DIR}"

REQUIRED_FILES=(
  # common
  common_log.py
  common_nms.py
  common_register.py
  config.py
  bundle_manager.py

  # robot runtime
  agent.py
  robot_agent.py
  robot_dispatch.py
  robot_agent_handlers.py
  robot_handlers_scan.py
  robot_handlers_av.py
  robot_handlers_audio.py
  robot_handlers_voice.py
  uploader.py
  robot_uploader.py
  register.py
  register_until_ok.sh

  # scan path
  scan_wifi.sh
  parse_iw.py
  scan_payload.py

  # GUI/runtime
  main.py
  windows.py

  # docs / notes
  scenario_commands.md
)

REQUIRED_DIRS=(
  av
  voice
  services
  autostart
)

for f in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: required file missing: ${f}"
    exit 1
  fi
done

for d in "${REQUIRED_DIRS[@]}"; do
  if [[ ! -d "${d}" ]]; then
    echo "ERROR: required directory missing: ${d}"
    exit 1
  fi
done

REQUIRED_SERVICE_FILES=(
  services/scanner-agent.service
  services/scanner-uploader.service
  services/scanner-poller.service
  services/scanner-voice.service
  services/scanner-avstream.service
  services/scanner-agent
)

for f in "${REQUIRED_SERVICE_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: required service-related file missing: ${f}"
    exit 1
  fi
done

REQUIRED_AUTOSTART_FILES=(
  autostart/myscript.desktop
)

for f in "${REQUIRED_AUTOSTART_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: required autostart file missing: ${f}"
    exit 1
  fi
done

# ------------------------------------------------------------
# Prepare staging directory
# ------------------------------------------------------------
rm -rf "${OUT_DIR}"
mkdir -p "${BUNDLE_DIR}"

# ------------------------------------------------------------
# Copy bundle contents
# ------------------------------------------------------------
echo "Copying files..."

for f in "${REQUIRED_FILES[@]}"; do
  cp "${f}" "${BUNDLE_DIR}/"
done

cp -a av "${BUNDLE_DIR}/"
cp -a voice "${BUNDLE_DIR}/"
cp -a services "${BUNDLE_DIR}/"
cp -a autostart "${BUNDLE_DIR}/"

# Optional extras if present
OPTIONAL_FILES=(
  common_ap_nms.py
  ap_register.py
  ap_register_until_ok.sh
  ap_agent.py
  ap_uploader.py
  ap_dispatch.py
  ap_handlers_status.py
  ap_handlers_traffic.py
)

for f in "${OPTIONAL_FILES[@]}"; do
  if [[ -f "${f}" ]]; then
    cp "${f}" "${BUNDLE_DIR}/"
  fi
done

# ------------------------------------------------------------
# Install hook
# ------------------------------------------------------------
cat > "${BUNDLE_DIR}/install.sh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "[install.sh] Applying robot bundle..."

BASE_DIR="/opt/_RunScanner"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART_DIR="/home/pi/.config/autostart"
SYSTEMD_DIR="/etc/systemd/system"
SUDOERS_DST="/etc/sudoers.d/scanner-agent"

REQUIRED_BUNDLE_ITEMS=(
  common_log.py
  common_nms.py
  common_register.py
  config.py
  bundle_manager.py

  agent.py
  robot_agent.py
  robot_dispatch.py
  robot_agent_handlers.py
  robot_handlers_scan.py
  robot_handlers_av.py
  robot_handlers_audio.py
  robot_handlers_voice.py
  uploader.py
  robot_uploader.py
  register.py
  register_until_ok.sh

  scan_wifi.sh
  parse_iw.py
  scan_payload.py

  main.py
  windows.py
  scenario_commands.md

  av
  voice
  services
  autostart
)

echo "[install.sh] Verifying bundle contents..."
for f in "${REQUIRED_BUNDLE_ITEMS[@]}"; do
  if [[ ! -e "${BUNDLE_DIR}/${f}" ]]; then
    echo "[install.sh] ERROR: missing bundle item: ${f}"
    exit 1
  fi
done

echo "[install.sh] Stopping robot services..."
for svc in \
  scanner-agent.service \
  scanner-uploader.service \
  scanner-poller.service \
  scanner-voice.service \
  scanner-avstream.service
do
  systemctl stop "${svc}" 2>/dev/null || true
  sudo -n systemctl stop "${svc}" 2>/dev/null || true
done

KEEP_RUNTIME_ITEMS=(
  bundles
  MoveOut
  TestCodes
  _bundle_build
  scanner_name.txt
  last_register.json
  nms_base.txt
  ap_traffic_config.json
)

echo "[install.sh] Cleaning runtime directory..."
mkdir -p "${BASE_DIR}"
cd "${BASE_DIR}"

for item in * .*; do
  [[ "${item}" == "." || "${item}" == ".." ]] && continue

  keep=false
  for k in "${KEEP_RUNTIME_ITEMS[@]}"; do
    if [[ "${item}" == "${k}" ]]; then
      keep=true
      break
    fi
  done

  if [[ "${keep}" == false ]]; then
    rm -rf "${item}"
  fi
done

echo "[install.sh] Copying payload into ${BASE_DIR}..."

for f in \
  common_log.py \
  common_nms.py \
  common_register.py \
  config.py \
  bundle_manager.py \
  agent.py \
  robot_agent.py \
  robot_dispatch.py \
  robot_agent_handlers.py \
  robot_handlers_scan.py \
  robot_handlers_av.py \
  robot_handlers_audio.py \
  robot_handlers_voice.py \
  uploader.py \
  robot_uploader.py \
  register.py \
  register_until_ok.sh \
  scan_wifi.sh \
  parse_iw.py \
  scan_payload.py \
  main.py \
  windows.py \
  scenario_commands.md
do
  cp -a "${BUNDLE_DIR}/${f}" "${BASE_DIR}/"
done

cp -a "${BUNDLE_DIR}/av" "${BASE_DIR}/"
cp -a "${BUNDLE_DIR}/voice" "${BASE_DIR}/"

# Optional AP files if present
for f in \
  common_ap_nms.py \
  ap_register.py \
  ap_register_until_ok.sh \
  ap_agent.py \
  ap_uploader.py \
  ap_dispatch.py \
  ap_handlers_status.py \
  ap_handlers_traffic.py
do
  if [[ -f "${BUNDLE_DIR}/${f}" ]]; then
    cp -a "${BUNDLE_DIR}/${f}" "${BASE_DIR}/"
  fi
done

echo "[install.sh] Fixing permissions..."
find "${BASE_DIR}" -type f -name "*.sh" -exec chmod +x {} \; || true
find "${BASE_DIR}" -type f -name "*.py" -exec chmod +x {} \; || true
chmod -R u+rwX "${BASE_DIR}/voice" || true
chmod -R u+rwX "${BASE_DIR}/av" || true

echo "[install.sh] Installing systemd service files..."
mkdir -p "${SYSTEMD_DIR}"

cp -a "${BUNDLE_DIR}/services/scanner-agent.service"    "${SYSTEMD_DIR}/"
cp -a "${BUNDLE_DIR}/services/scanner-uploader.service" "${SYSTEMD_DIR}/"
cp -a "${BUNDLE_DIR}/services/scanner-poller.service"   "${SYSTEMD_DIR}/"
cp -a "${BUNDLE_DIR}/services/scanner-voice.service"    "${SYSTEMD_DIR}/"
cp -a "${BUNDLE_DIR}/services/scanner-avstream.service" "${SYSTEMD_DIR}/"

echo "[install.sh] Installing sudoers rule..."
cp -a "${BUNDLE_DIR}/services/scanner-agent" "${SUDOERS_DST}"
chown root:root "${SUDOERS_DST}"
chmod 0440 "${SUDOERS_DST}"
visudo -c

echo "[install.sh] Installing autostart entry..."
mkdir -p "${AUTOSTART_DIR}"
cp -a "${BUNDLE_DIR}/autostart/myscript.desktop" "${AUTOSTART_DIR}/"

echo "[install.sh] Reloading systemd..."
systemctl daemon-reload
sudo -n systemctl daemon-reload 2>/dev/null || true

echo "[install.sh] Enabling robot services..."
for svc in \
  scanner-agent.service \
  scanner-uploader.service \
  scanner-poller.service \
  scanner-voice.service \
  scanner-avstream.service
do
  systemctl enable "${svc}" 2>/dev/null || true
  sudo -n systemctl enable "${svc}" 2>/dev/null || true
done

echo "[install.sh] Restarting robot services..."
for svc in \
  scanner-agent.service \
  scanner-uploader.service \
  scanner-poller.service \
  scanner-voice.service \
  scanner-avstream.service
do
  systemctl restart "${svc}" 2>/dev/null || true
  sudo -n systemctl restart "${svc}" 2>/dev/null || true
done

echo "[install.sh] Bundle install completed."
EOF

chmod +x "${BUNDLE_DIR}/install.sh"

# ------------------------------------------------------------
# Create ZIP
# ------------------------------------------------------------
cd "${OUT_DIR}"
rm -f "${ZIP_NAME}"

echo "Creating zip: ${ZIP_NAME}"
zip -r "${ZIP_NAME}" "${BUNDLE_ID}" > /dev/null

# ------------------------------------------------------------
# Final report
# ------------------------------------------------------------
echo "=== Bundle build complete ==="
echo "Output: ${OUT_DIR}/${ZIP_NAME}"
echo "Contents:"
zipinfo -1 "${ZIP_NAME}"
