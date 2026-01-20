#!/usr/bin/env python3
import os, time, json
from pathlib import Path
from datetime import datetime, timezone
import requests

# ---- Config (keep simple for now) ----
NMS_BASE = os.getenv("NMS_BASE", "http://192.168.137.3:8000")  # <-- change if needed
SCANNER  = os.getenv("SCANNER", "scanner1")
IFACE    = os.getenv("IFACE", "wlan0")
INTERVAL = int(os.getenv("UPLOAD_INTERVAL_SEC", "60"))

PAYLOAD_BUILDER = "/home/pi/_RunScanner/build_scan_payload.py"
LOG_PATH = Path("/home/pi/_RunScanner/uploader.log")

LATEST_JSON = Path("/tmp/latest_scan.json")
WAIT_SCAN_MAX_SEC = int(os.getenv("WAIT_SCAN_MAX_SEC", "10"))  # small boot grace

def utc_iso():
    return datetime.now(timezone.utc).isoformat()

def log(msg: str):
    line = f"[{utc_iso()}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def build_payload():
    # Import builder inline to avoid subprocess overhead
    from build_scan_payload import build_payload as _build
    return _build(SCANNER, IFACE)

def post_once():
    payload = build_payload()
    url = f"{NMS_BASE}/ingest/{SCANNER}"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/octet-stream"}

    try:
        r = requests.post(url, data=body, headers=headers, timeout=8)
        if 200 <= r.status_code < 300:
            log(f"UPLOAD ok status={r.status_code} bytes={len(body)}")
            return True
        log(f"UPLOAD fail status={r.status_code} body={r.text[:200]}")
        return False
    except Exception as e:
        log(f"UPLOAD exception: {e}")
        return False

def wait_for_scan_file(max_wait_sec: int) -> bool:
    """
    Wait until /tmp/latest_scan.json exists and is non-empty.
    Returns True if ready, False if timeout.
    """
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            if LATEST_JSON.exists() and LATEST_JSON.stat().st_size > 2:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def main():
    log(f"uploader started NMS_BASE={NMS_BASE} SCANNER={SCANNER} IFACE={IFACE} INTERVAL={INTERVAL}s")
    while True:
        try:
            if not wait_for_scan_file(WAIT_SCAN_MAX_SEC):
                log(f"skip upload: {LATEST_JSON} not ready (empty/missing) after {WAIT_SCAN_MAX_SEC}s")
            else:
                post_once()
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
