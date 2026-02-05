#!/usr/bin/env python3
import json
import socket
import subprocess
import sys
from typing import Dict, Any

import requests

from config import (
    BASE_DIR,
    get_nms_base,
    get_reg_iface,
    get_bundle_version,
    get_mac_address,
    SCANNER_NAME_FILE,
    LAST_REGISTER_FILE,
    TIME_FMT,
    local_ts,
)
VOICE_CFG = BASE_DIR / "voice" / "voice_config.json"
HTTP_TIMEOUT_SEC = 6


def update_voice_llm_session(scanner: str) -> None:
    try:
        p = VOICE_CFG
        cfg = {}
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8") or "{}")

        llm = cfg.get("llm") or {}
        llm["session_id"] = scanner
        cfg["llm"] = llm

        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass

# ------------------------------------------------------------------
# Persistence (telemetry only; NOT control)
# ------------------------------------------------------------------

def write_last_register(
    status: str,
    detail: str = "",
    http_code: int = 0,
    scanner: str = "",
    mac: str = "",
    ip: str = "",
) -> None:
    """
    Persist last registration attempt for local debugging/inspection only.

    NOTE:
    - Telemetry only
    - Time format MUST match NMS (TIME_FMT)
    """
    payload: Dict[str, Any] = {
        "time": local_ts(),
        "status": status,          # ok | blocked | offline | error
        "detail": detail,
        "http_code": http_code,
        "scanner": scanner,
        "mac": mac,
        "ip": ip,
        "time_format": TIME_FMT,
    }
    try:
        LAST_REGISTER_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# ------------------------------------------------------------------
# Network helpers
# ------------------------------------------------------------------

def get_ip_best_effort() -> str:
    """
    Best-effort local IP discovery.
    Returns empty string if not available.
    """
    # 1) hostname -> IP (may be 127.x)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # 2) routing-based
    try:
        out = subprocess.check_output(
            ["bash", "-lc", "ip route get 1.1.1.1 | awk '{print $7; exit}'"],
            text=True,
        ).strip()
        if out and not out.startswith("127."):
            return out
    except Exception:
        pass

    return ""


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> int:
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    iface = get_reg_iface()
    mac = get_mac_address(iface)
    if not mac:
        write_last_register(
            status="error",
            detail=f"Cannot read MAC for iface={iface}",
        )
        print(f"[register] ERROR: cannot read MAC for iface={iface}", file=sys.stderr)
        return 2

    nms_base = get_nms_base()
    ip = get_ip_best_effort()

    if not nms_base:
        write_last_register(
            status="offline",
            detail="No NMS reachable (discovery failed)",
            mac=mac,
            ip=ip,
        )
        print("[register] OFFLINE: no NMS reachable", file=sys.stderr)
        return 3

    url = f"{nms_base}/registry/register"
    body = {
        "mac": mac,
        "ip": ip or None,
        # TELEMETRY ONLY — NMS treats this as informational
        "scanner_version": get_bundle_version(),
        "capabilities": "scan",
    }

    try:
        r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
    except Exception as e:
        write_last_register(
            status="offline",
            detail=f"POST failed: {e}",
            mac=mac,
            ip=ip,
        )
        print(f"[register] OFFLINE: {e}", file=sys.stderr)
        return 4

    if r.status_code == 200:
        scanner = (r.text or "").strip()
        if not scanner:
            write_last_register(
                status="error",
                detail="Empty scanner name returned",
                http_code=200,
                mac=mac,
                ip=ip,
            )
            print("[register] ERROR: empty scanner name returned", file=sys.stderr)
            return 5

        try:
            tmp = SCANNER_NAME_FILE.with_suffix(".tmp")
            tmp.write_text(scanner + "\n", encoding="utf-8")
            tmp.replace(SCANNER_NAME_FILE)
        except Exception as e:
            write_last_register(
                status="error",
                detail=f"Failed to write scanner_name.txt: {e}",
                http_code=200,
                scanner=scanner,
                mac=mac,
                ip=ip,
            )
            print(f"[register] ERROR: cannot write scanner_name.txt: {e}", file=sys.stderr)
            return 6
        
        update_voice_llm_session(scanner)
        write_last_register(
            status="ok",
            detail=f"registered via {nms_base}",
            http_code=200,
            scanner=scanner,
            mac=mac,
            ip=ip,
        )
        print(scanner)
        return 0

    if r.status_code == 403:
        write_last_register(
            status="blocked",
            detail=(r.text or "")[:200],
            http_code=403,
            mac=mac,
            ip=ip,
        )
        print(f"[register] BLOCKED: {r.text}", file=sys.stderr)
        return 7

    write_last_register(
        status="error",
        detail=(r.text or "")[:200],
        http_code=r.status_code,
        mac=mac,
        ip=ip,
    )
    print(f"[register] ERROR http={r.status_code} body={(r.text or '')[:200]}", file=sys.stderr)
    return 8


if __name__ == "__main__":
    sys.exit(main())
