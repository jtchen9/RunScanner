#!/bin/sh
set -e

# ------------------------------------------------------------
# Bundle install for OpenWrt/prplOS AP
# ------------------------------------------------------------
BASE_DIR="/opt/_RunScanner"
LOG_FILE="${BASE_DIR}/bundle_apply.log"

mkdir -p "${BASE_DIR}"

log_step() {
  echo "[install.sh] $(date '+%F %T') $*" >> "${LOG_FILE}"
}

log_step "START AP bundle install (OpenWrt)"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
INIT_DIR="/etc/init.d"

TOP_LEVEL_FILES="
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
  ap_mcs_sampler_daemon.sh
"

REQUIRED_DIRS="services"

log_step "Verifying bundle contents"

for f in ${TOP_LEVEL_FILES}; do
  if [ ! -f "${BUNDLE_DIR}/${f}" ]; then
    log_step "ERROR missing file: ${f}"
    exit 1
  fi
done

for d in ${REQUIRED_DIRS}; do
  if [ ! -d "${BUNDLE_DIR}/${d}" ]; then
    log_step "ERROR missing directory: ${d}"
    exit 1
  fi
done

STOP_SERVICES="ap-uploader"

log_step "Stopping AP services"

for svc in ${STOP_SERVICES}; do
  if [ -f "/etc/init.d/${svc}" ]; then
    log_step "Stopping $svc"
    /etc/init.d/${svc} stop >> "${LOG_FILE}" 2>&1 || true
  else
    log_step "Service ${svc} not installed yet, skipping stop"
  fi
done

KEEP_RUNTIME_ITEMS="
  _bundle_build
  bundles
  last_register.json
  nms_base.txt
  scanner_name.txt
  TestCodes
  bundle_apply.log
"

log_step "Cleaning runtime directory"

mkdir -p "${BASE_DIR}"
cd "${BASE_DIR}"

for item in *; do
  [ "${item}" = "." ] && continue
  [ "${item}" = ".." ] && continue

  keep=0
  for k in ${KEEP_RUNTIME_ITEMS}; do
    if [ "${item}" = "${k}" ]; then
      keep=1
      break
    fi
  done

  if [ ${keep} -eq 0 ]; then
    log_step "Removing ${item}"
    rm -rf "${item}"
  fi
done

log_step "Copying payload"

for f in ${TOP_LEVEL_FILES}; do
  cp -a "${BUNDLE_DIR}/${f}" "${BASE_DIR}/"
done

for d in ${REQUIRED_DIRS}; do
  cp -a "${BUNDLE_DIR}/${d}" "${BASE_DIR}/"
done

log_step "Fixing permissions"

find "${BASE_DIR}" -type f -name "*.sh" -exec chmod +x {} \; >> "${LOG_FILE}" 2>&1 || true
find "${BASE_DIR}" -type f -name "*.py" -exec chmod +x {} \; >> "${LOG_FILE}" 2>&1 || true

log_step "Installing init.d service scripts"

if [ -f "${BUNDLE_DIR}/services/ap-agent.init" ]; then
  cp -a "${BUNDLE_DIR}/services/ap-agent.init" "${INIT_DIR}/ap-agent"
  chmod +x "${INIT_DIR}/ap-agent"
  log_step "Installed ap-agent init script"
fi

if [ -f "${BUNDLE_DIR}/services/ap-uploader.init" ]; then
  cp -a "${BUNDLE_DIR}/services/ap-uploader.init" "${INIT_DIR}/ap-uploader"
  chmod +x "${INIT_DIR}/ap-uploader"
  log_step "Installed ap-uploader init script"
fi

if [ -f "${BUNDLE_DIR}/services/ap-mcs-sampler.init" ]; then
  cp -a "${BUNDLE_DIR}/services/ap-mcs-sampler.init" "${INIT_DIR}/ap-mcs-sampler"
  chmod +x "${INIT_DIR}/ap-mcs-sampler"
  log_step "Installed ap-mcs-sampler init script"
fi

log_step "Enabling services"

ENABLE_SERVICES="ap-agent ap-uploader ap-mcs-sampler"

for svc in ${ENABLE_SERVICES}; do
  log_step "Enable $svc"
  /etc/init.d/${svc} enable >> "${LOG_FILE}" 2>&1 || true
done

log_step "AP bundle install completed successfully"
log_step "Starting services"

for svc in ${ENABLE_SERVICES}; do
  log_step "Starting $svc"
  /etc/init.d/${svc} start >> "${LOG_FILE}" 2>&1 || true
done

log_step "Installation complete. Services started."
