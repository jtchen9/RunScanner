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
    # get_reg_iface,
    get_bundle_version,
    get_mac_address,
    SCANNER_NAME_FILE,
    LAST_REGISTER_FILE,
    TIME_FMT,
    local_ts,
)
from common_register import perform_registration


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
    
def update_voice_llm_weblink(llm_weblink: str) -> None:
    """
    Update voice_config.json so llm_browser_start.sh uses this link.
    """
    if not llm_weblink:
        return
    try:
        p = VOICE_CFG
        cfg = {}
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8") or "{}")

        llm_browser = cfg.get("llm_browser") or {}
        llm_browser["url"] = llm_weblink
        cfg["llm_browser"] = llm_browser

        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> int:
    rc, result = perform_registration(
        base_dir=BASE_DIR,
        get_nms_base=get_nms_base,
        get_bundle_version=get_bundle_version,
        get_mac_address=get_mac_address,
        scanner_name_file=SCANNER_NAME_FILE,
        last_register_file=LAST_REGISTER_FILE,
        time_fmt=TIME_FMT,
        ts_func=local_ts,
        http_timeout_sec=HTTP_TIMEOUT_SEC,
        capabilities="scan",
    )

    scanner = (result.get("scanner") or "").strip()
    llm_weblink = (result.get("llm_weblink") or "").strip()
    detail = (result.get("detail") or "").strip()
    http_code = int(result.get("http_code") or 0)

    if rc == 0:
        update_voice_llm_session(scanner)
        if llm_weblink:
            update_voice_llm_weblink(llm_weblink)
        print(scanner)
        return 0

    if rc == 3:
        print("[register] OFFLINE: no NMS reachable", file=sys.stderr)
        return rc

    if rc == 4:
        print(f"[register] OFFLINE: {detail}", file=sys.stderr)
        return rc

    if rc == 5:
        print("[register] ERROR: empty scanner name returned", file=sys.stderr)
        return rc

    if rc == 6:
        print(f"[register] ERROR: cannot write scanner_name.txt: {detail}", file=sys.stderr)
        return rc

    if rc == 7:
        print(f"[register] BLOCKED: {detail}", file=sys.stderr)
        return rc

    if rc == 8:
        print(f"[register] ERROR http={http_code} body={detail}", file=sys.stderr)
        return rc

    print(f"[register] ERROR: {detail or 'registration failed'}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
