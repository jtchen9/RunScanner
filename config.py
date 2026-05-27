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
NMS_TIMEOUT_SEC = 10  # Increased from 2 to 10 for slow NMS responses
NMS_PORT = 8000

# Final-resort fixed NMS address for the new routed architecture
# FIXED_NMS_BASE = f"http://10.145.49.65:{NMS_PORT}"
FIXED_NMS_BASE = f"http://192.168.11.51:{NMS_PORT}"

# ------------------------------------------------------------------
# Time (MUST match NMS)
# ------------------------------------------------------------------

# ONE official time format everywhere (Pi <-> NMS)
TIME_FMT: str = "%Y-%m-%d-%H:%M:%S"

def local_ts() -> str:
    """Return current local time string in TIME_FMT."""
    return datetime.now().strftime(TIME_FMT)

# ------------------------------------------------------------------
# bundle
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# NMS discovery
# ------------------------------------------------------------------
def _probe_nms(base: str) -> bool:
    """Return True if NMS /health responds."""
    try:
        req = urllib.request.Request(f"{base}/health")
        with urllib.request.urlopen(req, timeout=NMS_TIMEOUT_SEC) as response:
            return response.status == 200
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

def _write_nms_cache(base: str) -> None:
    try:
        NMS_CACHE_FILE.write_text(base, encoding="utf-8")
    except Exception:
        pass

def discover_nms_base(force: bool = False) -> Optional[str]:
    """
    Discover reachable NMS and cache it.

    Policy:
    1) Try cached nms_base.txt
    2) Fall back directly to fixed NMS base
    3) No subnet scan
    """

    # 1. cached
    if not force and NMS_CACHE_FILE.exists():
        try:
            cached = NMS_CACHE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            cached = ""

        if cached and _probe_nms(cached):
            return cached

    if _probe_nms(FIXED_NMS_BASE):
        _write_nms_cache(FIXED_NMS_BASE)
        return FIXED_NMS_BASE

    return None

def get_nms_base() -> Optional[str]:
    return discover_nms_base(force=False)

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

def get_reg_iface() -> str:
    from pathlib import Path
    import os

    if Path("/sys/class/net/wan").exists():
        return "wan"

    for iface in os.listdir("/sys/class/net"):
        if iface == "lo" or "." in iface:
            continue
        driver_link = f"/sys/class/net/{iface}/device/driver"
        target = os.path.realpath(driver_link) if os.path.islink(driver_link) else ""
        if target.endswith("iwlwifi"):
            continue
        if iface.startswith("wlan") or iface.startswith("wl"):
            return iface

    return "wlan0"


def get_ax210_iface() -> str:
    """
    Detect AX210 interface via iwlwifi driver.
    Returns interface name or "" if not found.
    """
    try:
        import os
        for iface in os.listdir("/sys/class/net"):
            if iface in ("lo", "wlan0"):
                continue

            driver_link = f"/sys/class/net/{iface}/device/driver"
            if os.path.islink(driver_link):
                target = os.path.realpath(driver_link)
                if target.endswith("iwlwifi"):
                    return iface
    except Exception:
        pass

    return ""


def get_mac_address() -> str:
    try:
        iface = get_reg_iface()
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


# ------------------------------------------------------------------
# Camera roles / AprilTag calibration profiles
# ------------------------------------------------------------------
# Single source of truth for front/rear camera device and AprilTag intrinsics.
# Front = new 108-degree 2K webcam, captured at 1280x720.
# Rear  = Logitech C270, captured at 640x480.
CAMERA_ROLE_FRONT = "front"
CAMERA_ROLE_REAR = "rear"

CAMERA_FRONT_VIDEO_DEV = "/dev/v4l/by-id/usb-webcamvendor_webcamproduct_YGR80PU1200.23071717-video-index0"
CAMERA_REAR_VIDEO_DEV = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_200901010001-video-index0"

APRILTAG_CAMERA_PROFILES = {
    CAMERA_ROLE_FRONT: {
        "camera_role": CAMERA_ROLE_FRONT,
        "video_dev": CAMERA_FRONT_VIDEO_DEV,
        "width": 1280,
        "height": 720,
        "fx": 857.2,
        "fy": 851.8,
        "cx": 640.0,
        "cy": 360.0,
        "tag_size_m": 0.10,
    },
    CAMERA_ROLE_REAR: {
        "camera_role": CAMERA_ROLE_REAR,
        "video_dev": CAMERA_REAR_VIDEO_DEV,
        "width": 640,
        "height": 480,
        "fx": 554.0,
        "fy": 554.0,
        "cx": 320.0,
        "cy": 240.0,
        "tag_size_m": 0.10,
    },
}

def get_apriltag_camera_profile(camera_role: str = CAMERA_ROLE_FRONT) -> dict:
    """
    Return AprilTag/camera profile for a named camera role.
    Defaults to front camera to match the current navigation-primary camera.
    """
    role = (camera_role or CAMERA_ROLE_FRONT).strip().lower()
    if role not in APRILTAG_CAMERA_PROFILES:
        raise ValueError(f"bad camera_role={camera_role}; expected front|rear")
    return APRILTAG_CAMERA_PROFILES[role].copy()

