#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# Bundle metadata (EDIT HERE WHEN BUMPING VERSION)
# ------------------------------------------------------------
BUNDLE_ID="robotBundle1.0"
BASE_DIR="/home/pi/_RunScanner"
OUT_DIR="${BASE_DIR}/_bundle_build"
BUNDLE_DIR="${OUT_DIR}/${BUNDLE_ID}"
ZIP_NAME="${BUNDLE_ID}.zip"

echo "=== Building bundle: ${BUNDLE_ID} ==="

# ------------------------------------------------------------
# Safety checks
# ------------------------------------------------------------
cd "${BASE_DIR}"

REQUIRED_FILES=(
  agent.py
  bundle_manager.py
  config.py
  main.py
  parse_iw.py
  register.py
  scan_payload.py
  scan_wifi.sh
  scenario_commands.md
  uploader.py
  windows.py
)

for f in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: required file missing: ${f}"
    exit 1
  fi
done

# ------------------------------------------------------------
# Prepare staging directory
# ------------------------------------------------------------
rm -rf "${OUT_DIR}"
mkdir -p "${BUNDLE_DIR}"

# ------------------------------------------------------------
# Copy upgradeable application files ONLY
# ------------------------------------------------------------
echo "Copying files..."

cp agent.py               "${BUNDLE_DIR}/"
cp bundle_manager.py      "${BUNDLE_DIR}/"
cp config.py              "${BUNDLE_DIR}/"
cp main.py                "${BUNDLE_DIR}/"
cp parse_iw.py            "${BUNDLE_DIR}/"
cp register.py            "${BUNDLE_DIR}/"
cp scan_payload.py        "${BUNDLE_DIR}/"
cp scan_wifi.sh           "${BUNDLE_DIR}/"
cp scenario_commands.md   "${BUNDLE_DIR}/"
cp uploader.py            "${BUNDLE_DIR}/"
cp windows.py             "${BUNDLE_DIR}/"

# ------------------------------------------------------------
# Install hook (optional but recommended)
# ------------------------------------------------------------
cat > "${BUNDLE_DIR}/install.sh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "[install.sh] Applying robot bundle..."

BASE_DIR="/home/pi/_RunScanner"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"

REQUIRED_BUNDLE_FILES=(
  agent.py
  bundle_manager.py
  config.py
  main.py
  parse_iw.py
  register.py
  scan_payload.py
  scan_wifi.sh
  scenario_commands.md
  uploader.py
  windows.py
)

KEEP_RUNTIME_FILES=(
  bundles
  make_bundle.sh
  MoveOut
  nms_base.txt
  scanner_name.txt
  TestCodes
)

echo "[install.sh] Verifying bundle contents..."
for f in "${REQUIRED_BUNDLE_FILES[@]}"; do
  if [[ ! -f "${BUNDLE_DIR}/${f}" ]]; then
    echo "[install.sh] ERROR: missing bundle file: ${f}"
    exit 1
  fi
done

echo "[install.sh] Cleaning runtime directory..."
cd "${BASE_DIR}"

for item in * .*; do
  [[ "${item}" == "." || "${item}" == ".." ]] && continue

  keep=false
  for k in "${KEEP_RUNTIME_FILES[@]}"; do
    if [[ "${item}" == "${k}" ]]; then
      keep=true
      break
    fi
  done

  if [[ "${keep}" == false ]]; then
    rm -rf "${item}"
  fi
done

echo "[install.sh] Copying bundle files into runtime..."
for f in "${REQUIRED_BUNDLE_FILES[@]}"; do
  cp "${BUNDLE_DIR}/${f}" "${BASE_DIR}/"
done

echo "[install.sh] Fixing permissions..."
chmod +x scan_wifi.sh || true
chmod +x *.py || true

echo "[install.sh] Bundle install completed."
EOF

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
