#!/usr/bin/env python3
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

LATEST_JSON = Path("/tmp/latest_scan.json")

def utc_iso():
    return datetime.now(timezone.utc).isoformat()

def load_results():
    try:
        if not LATEST_JSON.exists():
            return []
        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def build_payload(scanner: str, iface: str = "wlan0"):
    results = load_results()
    return {
        "scanner": scanner,
        "host": socket.gethostname(),
        "ts_utc": utc_iso(),
        "iface": iface,
        "results": results,  # list of {bssid, ssid, freq, signal}
    }

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scanner", default="scanner1")
    ap.add_argument("--iface", default="wlan0")
    args = ap.parse_args()

    payload = build_payload(args.scanner, args.iface)
    print(json.dumps(payload, ensure_ascii=False))
