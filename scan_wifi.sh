#!/usr/bin/env bash
# AX210 diagnostic replacement. Keep capture_wifi.py beside this file.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/capture_wifi.py" "${1:-once}"
