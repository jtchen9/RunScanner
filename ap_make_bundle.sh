#!/usr/bin/env bash
set -euo pipefail

# ---------------------------
# AP bundle builder for real APs (OpenWrt / prplOS)
# ---------------------------

BUNDLE_ID="apBundle1.0"
BASE_DIR="/opt/_RunScanner"
OUT_DIR="${BASE_DIR}/_bundle_build"
BUNDLE_DIR="${OUT_DIR}/${BUNDLE_ID}"
ZIP_NAME="${BUNDLE_ID}.zip"

echo "=== Building AP bundle: ${BUNDLE_ID} ==="

cd "${BASE_DIR}"

# --------------------------
# Authoritative AP bundle contents
# --------------------------

TOP_LEVEL_FILES=(
  ap_agent.py
  ap_dispatch.py
  ap_handlers_status.py
  ap_handlers_traffic.py
  ap_register.py
  ap_register_until_ok.sh
  ap_uploader.py
  bundle_manager.py
  common_ap_nms.py
  common_log.py
  common_nms.py
  common_register.py
  config.py
  ap_make_bundle.sh
  ap_installer.sh
  ap_mcs_sampler_daemon
)

OPTIONAL_TOP_LEVEL_FILES=(
  check_sampler_status.sh
)

REQUIRED_DIRS=(
  services
)

REQUIRED_SERVICE_FILES=(
  services/ap-agent.init
  services/ap-uploader.init
  services/ap-mcs-sampler.init
)

OPTIONAL_DIRS=(
  TestCodes
)

# --------------------------
# Verify bundle contents
# --------------------------

echo "Verifying required files..."

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
    echo "ERROR: required service/init file missing: ${f}"
    exit 1
  fi
done

echo "All required files are present."

# ---------------------
# Prepare staging directory
# ---------------------

echo "Preparing staging directory..."
rm -rf "${OUT_DIR}"
mkdir -p "${BUNDLE_DIR}"

# ---------------------
# Copy bundle contents
# ---------------------

echo "Copying top-level files..."
for f in "${TOP_LEVEL_FILES[@]}"; do
  cp -a "${f}" "${BUNDLE_DIR}/"
done

echo "Copying optional top-level files if present..."
for f in "${OPTIONAL_TOP_LEVEL_FILES[@]}"; do
  if [[ -f "${f}" ]]; then
    cp -a "${f}" "${BUNDLE_DIR}/"
  fi
done

echo "Copying required directories..."
for d in "${REQUIRED_DIRS[@]}"; do
  cp -a "${d}" "${BUNDLE_DIR}/"
done

echo "Copying optional directories if present..."
for d in "${OPTIONAL_DIRS[@]}"; do
  if [[ -d "${d}" ]]; then
    cp -a "${d}" "${BUNDLE_DIR}/"
  fi
done

# --------------------------
# Create installer entrypoint
# --------------------------

echo "Creating install.sh from ap_installer.sh..."
cp -a "${BUNDLE_DIR}/ap_installer.sh" "${BUNDLE_DIR}/install.sh"
chmod +x "${BUNDLE_DIR}/install.sh"

# --------------------------
# Fix permissions inside bundle
# --------------------------

echo "Fixing permissions..."
find "${BUNDLE_DIR}" -type f -name "*.sh" -exec chmod +x {} \;
find "${BUNDLE_DIR}" -type f -name "*.py" -exec chmod +x {} \;
find "${BUNDLE_DIR}/services" -type f -name "*.init" -exec chmod +x {} \;

# --------------------------
# Build ZIP
# --------------------------

echo "Creating zip bundle..."
cd "${OUT_DIR}"
rm -f "${ZIP_NAME}"
zip -qr "${ZIP_NAME}" "${BUNDLE_ID}"

# ---------------------------
# Final report
# ---------------------------

echo
echo "=== AP bundle build complete ==="
echo "Bundle directory : ${BUNDLE_DIR}"
echo "Bundle zip       : ${OUT_DIR}/${ZIP_NAME}"
echo "Installer inside : ${BUNDLE_ID}/install.sh"
echo
echo "Included required service/init files:"
for f in "${REQUIRED_SERVICE_FILES[@]}"; do
  echo "  - ${f}"
done

echo
echo "Included top-level files:"
for f in "${TOP_LEVEL_FILES[@]}"; do
  echo "  - ${f}"
done

for f in "${OPTIONAL_TOP_LEVEL_FILES[@]}"; do
  if [[ -f "${BASE_DIR}/${f}" ]]; then
    echo "  - ${f}"
  fi
done

echo
echo "Done."