#!/usr/bin/env python3
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import (
    BASE_DIR,
    get_nms_base,
    get_reg_iface,
    SCANNER_NAME_FILE,
    LAST_REGISTER_FILE,
)

def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def write_last_register(status: str, detail: str = "", http_code: int = 0,
                        scanner: str = "", mac: str = "", ip: str = ""):
    payload = {
        "ts_utc": utc_iso(),
        "status": status,          # ok | blocked | offline | error
        "detail": detail,
        "http_code": http_code,
        "scanner": scanner,
        "mac": mac,
        "ip": ip,
    }
    try:
        LAST_REGISTER_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_mac_from_sysfs(iface: str) -> str:
    p = Path(f"/sys/class/net/{iface}/address")
    if p.exists():
        return p.read_text(encoding="utf-8").strip().lower()
    return ""

def get_ip_best_effort() -> str:
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["bash", "-lc", "ip route get 1.1.1.1 | awk '{print $7; exit}'"],
            text=True
        ).strip()
        if out and not out.startswith("127."):
            return out
    except Exception:
        pass

    return ""

def main():
    # Ensure base dir exists (safe)
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    nms_base = get_nms_base()
    iface = get_reg_iface()

    mac = get_mac_from_sysfs(iface)
    if not mac:
        write_last_register("error", f"Cannot read MAC for iface={iface}", 0, "", "", "")
        print(f"[register] ERROR: cannot read MAC for iface={iface}", file=sys.stderr)
        return 2

    ip = get_ip_best_effort()

    url = f"{nms_base}/registry/register"
    body = {
        "mac": mac,
        "ip": ip or None,
        "scanner_version": None,
        "capabilities": "scan",
    }

    try:
        r = requests.post(url, json=body, timeout=6)
    except Exception as e:
        write_last_register("offline", f"POST failed: {e}", 0, "", mac, ip)
        print(f"[register] OFFLINE: {e}", file=sys.stderr)
        return 3

    if r.status_code == 200:
        scanner = (r.text or "").strip()
        if not scanner:
            write_last_register("error", "Empty scanner name returned", 200, "", mac, ip)
            print("[register] ERROR: empty scanner name returned", file=sys.stderr)
            return 4

        try:
            tmp = SCANNER_NAME_FILE.with_suffix(".txt.tmp")
            tmp.write_text(scanner + "\n", encoding="utf-8")
            tmp.replace(SCANNER_NAME_FILE)
        except Exception as e:
            write_last_register("error", f"Failed to write scanner_name.txt: {e}", 200, scanner, mac, ip)
            print(f"[register] ERROR: cannot write scanner_name.txt: {e}", file=sys.stderr)
            return 5

        write_last_register("ok", "registered", 200, scanner, mac, ip)
        print(scanner)
        return 0

    if r.status_code == 403:
        write_last_register("blocked", (r.text or "")[:200], 403, "", mac, ip)
        print(f"[register] BLOCKED: {r.text}", file=sys.stderr)
        return 6

    write_last_register("error", (r.text or "")[:200], r.status_code, "", mac, ip)
    print(f"[register] ERROR http={r.status_code} body={(r.text or '')[:200]}", file=sys.stderr)
    return 7

if __name__ == "__main__":
    sys.exit(main())
