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

# One single source of truth for top-level files
TOP_LEVEL_FILES=(
  agent.py
  bundle_manager.py
  common_log.py
  common_nms.py
  common_register.py
  config.py
  main.py
  make_bundle.sh
  parse_iw.py
  register.py
  register_until_ok.sh
  robot_agent.py
  robot_agent_handlers.py
  robot_dispatch.py
  robot_handlers_audio.py
  robot_handlers_av.py
  robot_handlers_scan.py
  robot_handlers_voice.py
  robot_uploader.py
  scan_payload.py
  scan_wifi.sh
  uploader.py
  windows.py
)

REQUIRED_DIRS=(
  autostart
  av
  services
  voice
)

REQUIRED_SERVICE_FILES=(
  services/scanner-agent
  services/scanner-agent.service
  services/scanner-avstream.service
  services/scanner-poller.service
  services/scanner-uploader.service
  services/scanner-voice.service
)

REQUIRED_AUTOSTART_FILES=(
  autostart/myscript.desktop
)

for f in "${TOP_LEVEL_FILES[@]}"; do
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

for f in "${REQUIRED_SERVICE_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: required service-related file missing: ${f}"
    exit 1
  fi
done

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

for f in "${TOP_LEVEL_FILES[@]}"; do
  cp "${f}" "${BUNDLE_DIR}/"
done

for d in "${REQUIRED_DIRS[@]}"; do
  cp -a "${d}" "${BUNDLE_DIR}/"
done

# Optional extras if present
OPTIONAL_FILES=(
  ap_agent.py
  ap_dispatch.py
  ap_handlers_status.py
  ap_handlers_traffic.py
  ap_register.py
  ap_register_until_ok.sh
  ap_uploader.py
  common_ap_nms.py
)

for f in "${OPTIONAL_FILES[@]}"; do
  if [[ -f "${f}" ]]; then
    cp "${f}" "${BUNDLE_DIR}/"
  fi
done

# ------------------------------------------------------------
# Build install.sh from the same source lists
# ------------------------------------------------------------
TOP_LEVEL_FILES_STR=""
for f in "${TOP_LEVEL_FILES[@]}"; do
  TOP_LEVEL_FILES_STR+="  ${f}"$'\n'
done

REQUIRED_DIRS_STR=""
for d in "${REQUIRED_DIRS[@]}"; do
  REQUIRED_DIRS_STR+="  ${d}"$'\n'
done

cat > "${BUNDLE_DIR}/install.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Bundle install tracing
# ------------------------------------------------------------
LOG_FILE="/opt/_RunScanner/bundle_apply.log"

log_step() {
  echo "[install.sh] \$(date '+%F %T') \$*" >> "\${LOG_FILE}"
}

log_step "START bundle install"

echo "[install.sh] Applying robot bundle..."

BASE_DIR="/opt/_RunScanner"
BUNDLE_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
AUTOSTART_DIR="/home/pi/.config/autostart"
SYSTEMD_DIR="/etc/systemd/system"
SUDOERS_DST="/etc/sudoers.d/scanner-agent"

TOP_LEVEL_FILES=(
${TOP_LEVEL_FILES_STR})

REQUIRED_DIRS=(
${REQUIRED_DIRS_STR})

log_step "Verifying bundle contents"

for f in "\${TOP_LEVEL_FILES[@]}"; do
  if [[ ! -f "\${BUNDLE_DIR}/\${f}" ]]; then
    log_step "ERROR missing file: \${f}"
    exit 1
  fi
done

for d in "\${REQUIRED_DIRS[@]}"; do
  if [[ ! -d "\${BUNDLE_DIR}/\${d}" ]]; then
    log_step "ERROR missing directory: \${d}"
    exit 1
  fi
done

log_step "Stopping robot services"

for svc in \
  scanner-uploader.service \
  scanner-poller.service \
  scanner-voice.service \
  scanner-avstream.service
do
  log_step "Stopping \$svc"
  systemctl stop "\${svc}" >> "\${LOG_FILE}" 2>&1 || true
  sudo -n systemctl stop "\${svc}" >> "\${LOG_FILE}" 2>&1 || true
done

KEEP_RUNTIME_ITEMS=(
  _bundle_build
  ap_traffic_config.json
  bundles
  last_register.json
  MoveOut
  nms_base.txt
  scanner_name.txt
  TestCodes
)

log_step "Cleaning runtime directory"

mkdir -p "\${BASE_DIR}"
cd "\${BASE_DIR}"

for item in * .*; do
  [[ "\${item}" == "." || "\${item}" == ".." ]] && continue

  keep=false
  for k in "\${KEEP_RUNTIME_ITEMS[@]}"; do
    if [[ "\${item}" == "\${k}" ]]; then
      keep=true
      break
    fi
  done

  if [[ "\${keep}" == false ]]; then
    log_step "Removing \${item}"
    rm -rf "\${item}"
  fi
done

log_step "Copying payload"

for f in "\${TOP_LEVEL_FILES[@]}"; do
  cp -a "\${BUNDLE_DIR}/\${f}" "\${BASE_DIR}/"
done

for d in "\${REQUIRED_DIRS[@]}"; do
  cp -a "\${BUNDLE_DIR}/\${d}" "\${BASE_DIR}/"
done

log_step "Copying optional AP files"

for f in \
  ap_agent.py \
  ap_dispatch.py \
  ap_handlers_status.py \
  ap_handlers_traffic.py \
  ap_register.py \
  ap_register_until_ok.sh \
  ap_uploader.py \
  common_ap_nms.py
do
  if [[ -f "\${BUNDLE_DIR}/\${f}" ]]; then
    cp -a "\${BUNDLE_DIR}/\${f}" "\${BASE_DIR}/"
  fi
done

log_step "Fixing permissions"

find "\${BASE_DIR}" -type f -name "*.sh" -exec chmod +x {} \; >> "\${LOG_FILE}" 2>&1 || true
find "\${BASE_DIR}" -type f -name "*.py" -exec chmod +x {} \; >> "\${LOG_FILE}" 2>&1 || true
chmod -R u+rwX "\${BASE_DIR}/voice" >> "\${LOG_FILE}" 2>&1 || true
chmod -R u+rwX "\${BASE_DIR}/av" >> "\${LOG_FILE}" 2>&1 || true

log_step "Installing systemd service files"

sudo -n mkdir -p "\${SYSTEMD_DIR}" >> "\${LOG_FILE}" 2>&1

sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-agent.service"    "\${SYSTEMD_DIR}/" >> "\${LOG_FILE}" 2>&1
sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-uploader.service" "\${SYSTEMD_DIR}/" >> "\${LOG_FILE}" 2>&1
sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-poller.service"   "\${SYSTEMD_DIR}/" >> "\${LOG_FILE}" 2>&1
sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-voice.service"    "\${SYSTEMD_DIR}/" >> "\${LOG_FILE}" 2>&1
sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-avstream.service" "\${SYSTEMD_DIR}/" >> "\${LOG_FILE}" 2>&1

log_step "Installing sudoers rule"

sudo -n cp -a "\${BUNDLE_DIR}/services/scanner-agent" "\${SUDOERS_DST}" >> "\${LOG_FILE}" 2>&1
sudo -n chown root:root "\${SUDOERS_DST}" >> "\${LOG_FILE}" 2>&1
sudo -n chmod 0440 "\${SUDOERS_DST}" >> "\${LOG_FILE}" 2>&1
sudo -n visudo -c >> "\${LOG_FILE}" 2>&1

log_step "Installing autostart entry"

mkdir -p "\${AUTOSTART_DIR}"
cp -a "\${BUNDLE_DIR}/autostart/myscript.desktop" "\${AUTOSTART_DIR}/"

log_step "Reloading systemd"

sudo -n systemctl daemon-reload >> "\${LOG_FILE}" 2>&1

log_step "Enabling services"

for svc in \
  scanner-agent.service \
  scanner-uploader.service \
  scanner-poller.service \
  scanner-voice.service \
  scanner-avstream.service
do
  log_step "Enable \$svc"
  sudo -n systemctl enable "\${svc}" >> "\${LOG_FILE}" 2>&1
done

log_step "Bundle install completed successfully"
log_step "Rebooting system"

sync
sleep 2
sudo -n /usr/sbin/reboot >> "\${LOG_FILE}" 2>&1 || sudo -n /sbin/reboot >> "\${LOG_FILE}" 2>&1 || reboot
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
