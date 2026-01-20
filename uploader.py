#!/usr/bin/env python3
import os, time, json
from pathlib import Path
from datetime import datetime, timezone
import requests

from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    LATEST_JSON_FILE,
)

# ---- Config (keep simple for now) ----
IFACE    = os.getenv("IFACE", "wlan0")
INTERVAL = int(os.getenv("UPLOAD_INTERVAL_SEC", "60"))

LOG_PATH = BASE_DIR / "uploader.log"
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

def read_scanner_name() -> str:
    # Allow env override for debugging, but default to assigned name file
    env = (os.getenv("SCANNER", "") or "").strip()
    if env:
        return env
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def build_payload(scanner: str):
    # Import builder inline to avoid subprocess overhead
    from build_scan_payload import build_payload as _build
    return _build(scanner, IFACE)

def post_once():
    nms_base = get_nms_base()
    scanner = read_scanner_name()
    if not scanner:
        log("skip upload: scanner_name.txt missing/empty (not registered yet)")
        return False

    payload = build_payload(scanner)
    url = f"{nms_base}/ingest/{scanner}"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/octet-stream"}

    try:
        r = requests.post(url, data=body, headers=headers, timeout=8)
        if 200 <= r.status_code < 300:
            log(f"UPLOAD ok scanner={scanner} status={r.status_code} bytes={len(body)}")
            return True
        log(f"UPLOAD fail scanner={scanner} status={r.status_code} body={r.text[:200]}")
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
            if LATEST_JSON_FILE.exists() and LATEST_JSON_FILE.stat().st_size > 2:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

def main():
    log(f"uploader started NMS_BASE={get_nms_base()} IFACE={IFACE} INTERVAL={INTERVAL}s")
    while True:
        try:
            if not wait_for_scan_file(WAIT_SCAN_MAX_SEC):
                log(f"skip upload: {str(LATEST_JSON_FILE)} not ready (empty/missing) after {WAIT_SCAN_MAX_SEC}s")
            else:
                post_once()
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
