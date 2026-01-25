#!/usr/bin/env python3
"""
scan_payload.py

Build the payload uploaded by uploader.py to NMS.

This module MUST stay small and dependency-light.
Pi is dumb: it only packages local scan results + minimal metadata.

Time format MUST match NMS:
  TIME_FMT = "%Y-%m-%d-%H:%M:%S"
"""

import json
from typing import Any, Dict, List

from config import LATEST_JSON_FILE, TIME_FMT, local_ts


def _read_latest_scan_entries() -> List[Dict[str, Any]]:
    """
    Read /tmp/latest_scan.json (written by scan_wifi.sh + parse_iw.py).
    Returns [] if missing or invalid.
    """
    try:
        if not LATEST_JSON_FILE.exists():
            return []
        s = LATEST_JSON_FILE.read_text(encoding="utf-8").strip()
        if not s:
            return []
        obj = json.loads(s)
        return obj if isinstance(obj, list) else []
    except Exception:
        return []


def build_payload(scanner: str, iface: str) -> Dict[str, Any]:
    """
    Payload contract (Pi-side):
    - scanner: identity string
    - time: payload build timestamp (LOCAL time, TIME_FMT; matches NMS)
    - iface: wlan0, etc
    - entries: list of AP scan dicts from parse_iw.py
    - time_format: explicit (telemetry only)
    """
    return {
        "scanner": scanner,
        "time": local_ts(),
        "iface": iface,
        "entries": _read_latest_scan_entries(),
        "time_format": TIME_FMT,
    }
