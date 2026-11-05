#!/usr/bin/env bash
# Run after git pull: sudo bash tools/install_wifi_recovery.sh
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root_dir"
if [[ $EUID -ne 0 ]]; then
    echo 'Run: sudo bash tools/install_wifi_recovery.sh' >&2
    exit 1
fi
for cmd in python3 nmcli ip wpa_cli visudo install systemctl cmp; do
    command -v "$cmd" >/dev/null || { echo "Missing required command: $cmd" >&2; exit 1; }
done
# Validate all inputs before stopping the agent or replacing installed files.
python3 -I - <<'PY'
import ast
from pathlib import Path
for name in ('robot_network_recovery.py', 'tools/scanner-wifi-activate.py'):
    ast.parse(Path(name).read_text(), filename=name)
PY
visudo -cf tools/scanner-agent-wifi-recovery.sudoers
systemctl cat scanner-agent >/dev/null
stopped=0
helper_tmp=''
sudoers_tmp=''
cleanup() {
    [[ -z "$helper_tmp" ]] || rm -f -- "$helper_tmp"
    [[ -z "$sudoers_tmp" ]] || rm -f -- "$sudoers_tmp"
}
failed() {
    echo 'Installation failed; do not start an experiment.' >&2
    if [[ $stopped == 1 ]]; then
        echo 'scanner-agent was stopped. Fix the reported error and rerun this installer.' >&2
    fi
}
trap cleanup EXIT
trap failed ERR
install -d -o root -g root -m 755 /usr/local/libexec
helper_tmp=$(mktemp /usr/local/libexec/.scanner-wifi-activate.XXXXXX)
sudoers_tmp=$(mktemp /etc/sudoers.d/.scanner-agent-wifi-recovery.XXXXXX)
install -o root -g root -m 755 tools/scanner-wifi-activate.py "$helper_tmp"
install -o root -g root -m 440 tools/scanner-agent-wifi-recovery.sudoers "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
systemctl stop scanner-agent
stopped=1
mv -f -- "$helper_tmp" /usr/local/libexec/scanner-wifi-activate
helper_tmp=''
mv -f -- "$sudoers_tmp" /etc/sudoers.d/scanner-agent-wifi-recovery
sudoers_tmp=''
cmp tools/scanner-wifi-activate.py /usr/local/libexec/scanner-wifi-activate
cmp tools/scanner-agent-wifi-recovery.sudoers /etc/sudoers.d/scanner-agent-wifi-recovery
systemctl start scanner-agent
stopped=0
systemctl is-active --quiet scanner-agent
systemctl status scanner-agent --no-pager -l
echo 'Wi-Fi recovery installation complete. This does not certify network recovery.'
