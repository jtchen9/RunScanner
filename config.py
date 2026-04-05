#!/usr/bin/env python3
"""
Shared config for _RunScanner.

Single source of truth for:
- NMS discovery
- common paths
- time format helpers
"""

import os
import urllib.request
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
NMS_TIMEOUT_SEC = 3
NMS_PORT = 8000

# Final-resort fixed NMS address for the new routed architecture
FIXED_NMS_BASE = f"http://192.168.0.3:{NMS_PORT}"

# -------------------------
# Time (MUST match NMS)
# -------------------------

TIME_FMT: str = "%Y-%m-%d-%H:%M:%S"

def local_ts() -> str:
    return datetime.now().strftime(TIME_FMT)

# -------------------------
# bundle
# -------------------------

BUNDLES_DIR = BASE_DIR / "bundles"
ACTIVE_BUNDLE_FILE = BUNDLES_DIR / "active_bundle.txt"

def get_bundle_version() -> str:
    try:
        s = ACTIVE_BUNDLE_FILE.read_text(encoding="utf-8").strip()
        return s if s else "0"
    except Exception:
        return "0"

# ----------------
# NMS discovery
# ----------------

def _probe_nms(base: str) -> bool:
    try:
        r = urllib.request.get(f"{base}/health", timeout=NMS_TIMEOUT_SEC)
        return r.status_code == 200
    except Exception:
        return False

def _get_local_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def _scan_ip(ip: str) -> Optional[str]:
    base = f"http://{ip}:{NMS_PORT}"
    if _probe_nms(base):
        return base
    return None

def _scan_subnet_for_nms() -> Optional[str]:
    local_ip = _get_local_ip()
    if not local_ip:
        return None

    try:
        net = ipaddress.ip_network(local_ip + "/24", strict=False)
    except Exception:
        return None

    hosts = [str(ip) for ip in net.hosts()]

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
        futures = {executor.submit(_scan_ip, ip): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                executor.shutdown(cancel_futures=True)
                return result

    return None

def _write_nms_cache(base: str) -> None:
    try:
        NMS_CACHE_FILE.write_text(base, encoding="utf-8")
    except Exception:
        pass

def discover_nms_base(force: bool = False) -> Optional[str]:
    """
    Priority:
    1) cached value in nms_base.txt
    2) local subnet scan for port 8000
    3) final-resort fixed IP 192.168.0.3
    """

    # 1. cached nms_base.txt
    if not force and NMS_CACHE_FILE.exists():
        try:
            cached = NMS_CACHE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            cached = ""

        if cached and _probe_nms(cached):
            return cached

    # 2. local subnet scan
    found = _scan_subnet_for_nms()
    if found:
        _write_nms_cache(found)
        return found

    # 3. final resort fixed NMS IP
    if _probe_nms(FIXED_NMS_BASE):
        _write_nms_cache(FIXED_NMS_BASE)
        return FIXED_NMS_BASE

    return None

def get_nms_base() -> Optional[str]:
    return discover_nms_base(force=False)

# -------------------------
# System-wide endpoints
# -------------------------

WEB_SERVER = "6g-private.com"

# -------------------------
# Services + paths
# -------------------------

SERVICE_SCANNER_POLLER = "scanner-poller.service"
SERVICE_UPLOADER = "scanner-uploader.service"
SERVICE_AVSTREAM = "scanner-avstream.service"

MPV_BIN = "/usr/bin/mpv"
AUDIO_AO_DEFAULT = "alsa"
AUDIO_DEVICE_DEFAULT = "alsa/default"
AUDIO_VOLUME_DEFAULT = 90

SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"

def get_reg_iface() -> str:
    """
    Select the interface used for registration MAC.

    Rules:
    1) Use 'wan' if it exists (real AP)
    2) Otherwise use 'wlan0' if it exists (robot)
    3) Otherwise fall back to the first non-loopback interface
    """
    try:
        if Path("/sys/class/net/wan").exists():
            return "wan"

        if Path("/sys/class/net/wlan0").exists():
            return "wlan0"

        net_path = Path("/sys/class/net")
        for iface in net_path.iterdir():
            name = iface.name
            if name != "lo":
                return name
    except Exception:
        pass

    return "wlan0"


def get_mac_address() -> str:
    try:
        iface = get_reg_iface()
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip().lower()
    except Exception:
        return ""
    
LATEST_JSON_FILE = Path("/tmp/latest_scan.json")

AV_DIR = BASE_DIR / "av"
AV_CFG_FILE = AV_DIR / "av_stream_config.json"
AV_DEFAULT_SERVER = WEB_SERVER
AV_DEFAULT_RTSP_PORT = 8554
AV_DEFAULT_TRANSPORT = "tcp"
AV_DEFAULT_PATH_PREFIX = ""
AV_DEFAULT_VIDEO_DEV = "/dev/video0"
AV_DEFAULT_AUDIO_DEV = "plughw:1,0"
AV_DEFAULT_SIZE = "640x480"
AV_DEFAULT_FPS = 30
AV_LOG_FILE = AV_DIR / "av_stream.log"