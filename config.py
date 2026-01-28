#!/usr/bin/env python3
"""
Shared config for _RunScanner.

Single source of truth for:
- NMS discovery
- common paths
- time format helpers
"""

import os
import requests
from pathlib import Path
from typing import Optional
from datetime import datetime

BASE_DIR = Path("/home/pi/_RunScanner")

# ------------------------------------------------------------------
# Time (MUST match NMS)
# ------------------------------------------------------------------

# ONE official time format everywhere (Pi <-> NMS)
TIME_FMT: str = "%Y-%m-%d-%H:%M:%S"

def local_ts() -> str:
    """Return current local time string in TIME_FMT."""
    return datetime.now().strftime(TIME_FMT)

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

    Priority:
    1) cached value (if still alive)
    2) ordered NMS_CANDIDATES probe
    """
    if not force and NMS_CACHE_FILE.exists():
        cached = NMS_CACHE_FILE.read_text().strip()
        if cached and _probe_nms(cached):
            return cached

    for base in NMS_CANDIDATES:
        if _probe_nms(base):
            try:
                NMS_CACHE_FILE.write_text(base, encoding="utf-8")
            except Exception:
                pass
            return base

    return None

def get_nms_base() -> Optional[str]:
    """Return active NMS base URL, or None if unavailable."""
    return discover_nms_base(force=False)


SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"

# ------------------------------------------------------------------
# System-wide endpoints (shared across the entire system)
# ------------------------------------------------------------------

WEB_SERVER = "6g-private.com"

# ------------------------------------------------------------------
# Services (systemd) + systemctl paths
# ------------------------------------------------------------------

SERVICE_SCANNER_POLLER = "scanner-poller.service"
SERVICE_UPLOADER = "scanner-uploader.service"
SERVICE_AVSTREAM = "scanner-avstream.service"

# ------------------------------------------------------------------
# Audio playback defaults (known-good on your Pi)
# ------------------------------------------------------------------

MPV_BIN = "/usr/bin/mpv"
AUDIO_AO_DEFAULT = "alsa"
AUDIO_DEVICE_DEFAULT = "alsa/default"
AUDIO_VOLUME_DEFAULT = 90

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

# ------------------------------------------------------------------
# Audio / Video (AV)
# ------------------------------------------------------------------

AV_DIR = BASE_DIR / "av"
AV_CFG_FILE = AV_DIR / "av_stream_config.json"

SERVICE_AVSTREAM = "scanner-avstream.service"

# Default streaming target (can be overridden by command args)
AV_DEFAULT_SERVER = WEB_SERVER
AV_DEFAULT_RTSP_PORT = 8554
AV_DEFAULT_TRANSPORT = "tcp"     # tcp|udp (we default tcp)
AV_DEFAULT_PATH_PREFIX = ""      # optional prefix, usually empty

# Default capture devices
AV_DEFAULT_VIDEO_DEV = "/dev/video0"
AV_DEFAULT_AUDIO_DEV = "plughw:1,0"

# Default capture format
AV_DEFAULT_SIZE = "640x480"
AV_DEFAULT_FPS = 30

# Logging (if runner/service writes logs here)
AV_LOG_FILE = AV_DIR / "av_stream.log"
