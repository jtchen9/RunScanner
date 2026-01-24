#!/usr/bin/env python3
"""
Shared config for _RunScanner.

Single source of truth for:
- NMS discovery
- common paths
"""

import os
import json
import socket
import requests
from pathlib import Path
from typing import Optional

BASE_DIR = Path("/home/pi/_RunScanner")

# ------------------------------------------------------------------
# NMS discovery
# ------------------------------------------------------------------

# Ordered preference list
NMS_CANDIDATES = [
    "http://192.168.137.3:8000",  # primary (normal lab)
    "http://192.168.137.1:8000",  # fallback (dev / laptop)
]

NMS_CACHE_FILE = BASE_DIR / "nms_base.txt"
NMS_TIMEOUT_SEC = 3
BUNDLES_DIR = BASE_DIR / "bundles"
ACTIVE_BUNDLE_FILE = BUNDLES_DIR / "active_bundle.txt"  # written by bundle_manager.py

def get_bundle_version() -> str:
    """
    Return current bundle version/id from bundles/active_bundle.txt.

    Operational policy:
    - SD-clone image should ship with a valid version like "robotBundle1.0".
    - "0" is reserved as a fallback meaning: unknown/uninitialized (should be rare).
    """
    try:
        s = ACTIVE_BUNDLE_FILE.read_text(encoding="utf-8").strip()
        return s if s else "0"
    except Exception:
        return "0"


def _probe_nms(base: str) -> bool:
    """Return True if NMS /health responds."""
    try:
        r = requests.get(f"{base}/health", timeout=NMS_TIMEOUT_SEC)
        return r.status_code == 200
    except Exception:
        return False


def discover_nms_base(force: bool = False) -> Optional[str]:
    """
    Discover reachable NMS and cache it.
    """
    if not force and NMS_CACHE_FILE.exists():
        cached = NMS_CACHE_FILE.read_text().strip()
        if cached and _probe_nms(cached):
            return cached

    for base in NMS_CANDIDATES:
        if _probe_nms(base):
            NMS_CACHE_FILE.write_text(base)
            return base

    return None


def get_nms_base() -> Optional[str]:
    """
    Return active NMS base URL, or None if unavailable.
    """
    return discover_nms_base(force=False)


# ------------------------------------------------------------------
# Registration / identity
# ------------------------------------------------------------------

SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"

REG_IFACE_DEFAULT = "wlan0"


def get_reg_iface() -> str:
    return os.getenv("REG_IFACE", REG_IFACE_DEFAULT)


def get_mac_address(iface: str) -> str:
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip().lower()
    except Exception:
        return ""


# ------------------------------------------------------------------
# Scan data
# ------------------------------------------------------------------

LATEST_JSON_FILE = Path("/tmp/latest_scan.json")
