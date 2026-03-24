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
import socket
import ipaddress
import concurrent.futures

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
BASE_DIR = Path("/opt/_RunScanner")
NMS_CACHE_FILE = BASE_DIR / "nms_base.txt"
NMS_TIMEOUT_SEC = 2
NMS_PORT = 8000

# --------------
# Time (MUST match NMS)
# --------------

# ONE official time format everywhere (Pi <-> NMS)
TIME_FMT: str = "%Y-%m-%d-%H:%M:%S"

def local_ts() -> str:
    """Return current local time string in TIME_FMT."""
    return datetime.now().strftime(TIME_FMT)

# --------------
# bundle
# --------------
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

# ----------------
# NMS discovery
# ----------------
def _probe_nms(base: str) -> bool:
    """Return True if NMS /health responds."""
    try:
        r = requests.get(f"{base}/health", timeout=NMS_TIMEOUT_SEC)
        return r.status_code == 200
    except Exception:
        return False

def _get_local_ip() -> Optional[str]:
    """
    Get current primary IP (routing-based).
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def _scan_ip(ip: str) -> Optional[str]:
    """
    Try probing a single IP for NMS.
    """
    base = f"http://{ip}:{NMS_PORT}"
    if _probe_nms(base):
        return base
    return None

def _scan_subnet_for_nms() -> Optional[str]:
    """
    Scan local /24 subnet for port 8000 NMS.
    Uses thread pool for speed.
    """
    local_ip = _get_local_ip()
    if not local_ip:
        return None

    try:
        net = ipaddress.ip_network(local_ip + "/24", strict=False)
    except Exception:
        return None

    # skip network/broadcast
    hosts = [str(ip) for ip in net.hosts()]

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
        futures = {executor.submit(_scan_ip, ip): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                executor.shutdown(cancel_futures=True)
                return result

    return None

def discover_nms_base(force: bool = False) -> Optional[str]:
    """
    Discover reachable NMS and cache it.

    Priority:
    1) cached value (if still alive)
    2) subnet scan
    """

    # 1. cached
    if not force and NMS_CACHE_FILE.exists():
        cached = NMS_CACHE_FILE.read_text().strip()
        if cached and _probe_nms(cached):
            return cached

    # 2. subnet scan
    found = _scan_subnet_for_nms()
    if found:
        try:
            NMS_CACHE_FILE.write_text(found, encoding="utf-8")
        except Exception:
            pass
        return found

    return None

def get_nms_base() -> Optional[str]:
    return discover_nms_base(force=False)

# ----------------
# System-wide endpoints (shared across the entire system)
# ----------------

WEB_SERVER = "6g-private.com"

# -----------------
# Services (systemd) + systemctl paths
# ------------------

SERVICE_SCANNER_POLLER = "scanner-poller.service"
SERVICE_UPLOADER = "scanner-uploader.service"
SERVICE_AVSTREAM = "scanner-avstream.service"

# ----------------
# Audio playback defaults (known-good on your Pi)
# ----------------

MPV_BIN = "/usr/bin/mpv"
AUDIO_AO_DEFAULT = "alsa"
AUDIO_DEVICE_DEFAULT = "alsa/default"
AUDIO_VOLUME_DEFAULT = 90

# ------------------# Registration / identity
# -----------------

SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"

REG_IFACE_DEFAULT = "wlan0"

# def get_reg_iface() -> str:
#     return os.getenv("REG_IFACE", REG_IFACE_DEFAULT)

def get_mac_address() -> str:
    try:
        with open(f"/sys/class/net/{REG_IFACE_DEFAULT}/address") as f:
            return f.read().strip().lower()
    except Exception:
        return ""

# ------------------
# Scan data
# ------------------

LATEST_JSON_FILE = Path("/tmp/latest_scan.json")

# ------------------
# Audio / Video (AV)
# ------------------

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
