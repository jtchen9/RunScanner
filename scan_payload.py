#!/usr/bin/env python3
"""
scan_payload.py

Build the payload uploaded by uploader.py to NMS.

This module MUST stay small and dependency-light.
Pi is dumb: it only packages local scan results + minimal metadata.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from config import LATEST_JSON_FILE


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    - ts_utc: upload build timestamp (UTC)
    - iface: wlan0, etc
    - entries: list of AP scan dicts from parse_iw.py
    """
    return {
        "scanner": scanner,
        "ts_utc": utc_iso(),
        "iface": iface,
        "entries": _read_latest_scan_entries(),
    }
