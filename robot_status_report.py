#!/usr/bin/env python3
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional


LATEST_SCAN_FILE = Path("/tmp/latest_scan.json")
VOICE_CONFIG_FILE = Path("/home/pi/_RunScanner/voice/voice_config.json")
STT_ECHO_FILE = Path("/home/pi/_RunScanner/voice/stt_echo.txt")


def _run(cmd: list[str], timeout: float = 1.0) -> str:
    try:
        return subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return ""


def wifi_freq_to_band(freq_mhz: int) -> str:
    if 2400 <= freq_mhz < 2500:
        return "2g"
    if 5000 <= freq_mhz < 5925:
        return "5g"
    if 5925 <= freq_mhz < 7125:
        return "6g"
    return ""


def wifi_freq_to_channel(freq_mhz: int) -> Optional[int]:
    if freq_mhz == 2484:
        return 14
    if 2412 <= freq_mhz <= 2472:
        return int((freq_mhz - 2407) / 5)
    if 5000 <= freq_mhz < 5925:
        return int((freq_mhz - 5000) / 5)
    if 5955 <= freq_mhz <= 7115:
        return int((freq_mhz - 5950) / 5)
    return None


def get_wifi_status(iface: str = "wlan0") -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "iface": iface,
        "connected": False,
        "assoc_bssid": "",
        "ssid": "",
        "freq_mhz": None,
        "channel": None,
        "band": "",
    }

    out = _run(["iw", "dev", iface, "link"], timeout=1.0)
    if not out or "Not connected" in out:
        return status

    status["connected"] = True

    m = re.search(r"Connected to\s+([0-9a-fA-F:]{17})", out)
    if m:
        status["assoc_bssid"] = m.group(1).lower()

    m = re.search(r"SSID:\s*(.+)", out)
    if m:
        status["ssid"] = m.group(1).strip()

    m = re.search(r"freq:\s*(\d+)", out)
    if m:
        freq = int(m.group(1))
        status["freq_mhz"] = freq
        status["channel"] = wifi_freq_to_channel(freq)
        status["band"] = wifi_freq_to_band(freq)

    return status


def get_scan_status() -> Dict[str, Any]:
    active = _run(["systemctl", "is-active", "scanner-poller.service"], timeout=1.0).strip()

    last_scan_age_sec = None
    latest_scan_exists = LATEST_SCAN_FILE.exists()

    if latest_scan_exists:
        try:
            last_scan_age_sec = max(0, int(time.time() - LATEST_SCAN_FILE.stat().st_mtime))
        except Exception:
            last_scan_age_sec = None

    return {
        "scanning": active == "active",
        "scanner_poller_service": active or "unknown",
        "latest_scan_exists": latest_scan_exists,
        "last_scan_age_sec": last_scan_age_sec,
    }


def get_voice_status() -> Dict[str, Any]:
    active = _run(["systemctl", "is-active", "scanner-voice.service"], timeout=1.0).strip()

    voice_mode = ""
    try:
        cfg = json.loads(VOICE_CONFIG_FILE.read_text(encoding="utf-8"))
        voice_mode = str(cfg.get("mode") or "")
    except Exception:
        pass

    stt_echo_age_sec = None
    if STT_ECHO_FILE.exists():
        try:
            stt_echo_age_sec = max(0, int(time.time() - STT_ECHO_FILE.stat().st_mtime))
        except Exception:
            stt_echo_age_sec = None

    return {
        "voice_service": active or "unknown",
        "voice_ready": active == "active",
        "voice_mode": voice_mode,
        "stt_echo_exists": STT_ECHO_FILE.exists(),
        "stt_echo_age_sec": stt_echo_age_sec,
    }


def build_status_report() -> Dict[str, Any]:
    iface = detect_active_wifi_iface()

    wifi_status = get_wifi_status(iface) if iface else {
        "iface": "",
        "connected": False,
        "assoc_bssid": "",
        "ssid": "",
        "freq_mhz": None,
        "channel": None,
        "band": "",
    }

    return {
        "wifi_status": wifi_status,
        "scan_status": get_scan_status(),
        "voice_status": get_voice_status(),
    }


def detect_active_wifi_iface() -> Optional[str]:
    """
    Return the first connected Wi-Fi interface.
    Prefer AX210 (non-wlan0) if both exist.
    """
    out = _run(["iw", "dev"], timeout=1.0)
    if not out:
        return None

    ifaces = re.findall(r"Interface\s+(\S+)", out)

    # Prefer non-wlan0 (AX210)
    for iface in ifaces:
        if iface != "wlan0":
            link = _run(["iw", "dev", iface, "link"], timeout=1.0)
            if "Connected to" in link:
                return iface

    # fallback to wlan0
    for iface in ifaces:
        if iface == "wlan0":
            link = _run(["iw", "dev", iface, "link"], timeout=1.0)
            if "Connected to" in link:
                return iface

    return None

