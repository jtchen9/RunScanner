#!/usr/bin/env python3
"""
Shared config for _RunScanner.

Goal: avoid duplicating NMS_BASE and other common settings across scripts/services.
"""

import os
from pathlib import Path

BASE_DIR = Path("/home/pi/_RunScanner")

# ---- NMS ----
# Environment variable overrides are always allowed.
NMS_BASE_DEFAULT = "http://192.168.137.3:8000"

def get_nms_base() -> str:
    """
    Return NMS base URL.
    Priority:
      1) env NMS_BASE
      2) NMS_BASE_DEFAULT
    """
    return (os.getenv("NMS_BASE", NMS_BASE_DEFAULT) or NMS_BASE_DEFAULT).strip().rstrip("/")


# ---- Registration ----
REG_IFACE_DEFAULT = "wlan0"

def get_reg_iface() -> str:
    return (os.getenv("REG_IFACE", REG_IFACE_DEFAULT) or REG_IFACE_DEFAULT).strip()


# ---- Common state files ----
SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"
LATEST_JSON_FILE = Path("/tmp/latest_scan.json")
