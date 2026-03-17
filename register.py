#!/usr/bin/env python3
import json
import subprocess
import sys

from config import (
    BASE_DIR,
    get_nms_base,
    get_bundle_version,
    get_mac_address,
    SCANNER_NAME_FILE,
    LAST_REGISTER_FILE,
    TIME_FMT,
    local_ts,
)
from common_register import perform_registration


VOICE_CFG = BASE_DIR / "voice" / "voice_config.json"
PENDING_HOSTNAME_FILE = BASE_DIR / "pending_hostname.txt"
PENDING_TS_MODE_FILE = BASE_DIR / "pending_tailscaled_mode.txt"   # replace | delete
PENDING_TS_STATE_B64_FILE = BASE_DIR / "pending_tailscaled_state.b64"
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


def get_current_hostname() -> str:
    try:
        cp = subprocess.run(
            ["/usr/bin/hostnamectl", "--static"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (cp.stdout or "").strip()
    except Exception:
        return ""


def write_pending_identity_transition(short_name: str, tailscaled_state_b64: str) -> None:
    """
    If tailscaled_state_b64 is non-empty:
      mode = replace
      write pending_tailscaled_state.b64
    Else:
      mode = delete
      remove any stale pending_tailscaled_state.b64
    """
    tmp1 = PENDING_HOSTNAME_FILE.with_suffix(".tmp")
    tmp1.write_text(short_name + "\n", encoding="utf-8")
    tmp1.replace(PENDING_HOSTNAME_FILE)

    mode = "replace" if tailscaled_state_b64 else "delete"
    tmp2 = PENDING_TS_MODE_FILE.with_suffix(".tmp")
    tmp2.write_text(mode + "\n", encoding="utf-8")
    tmp2.replace(PENDING_TS_MODE_FILE)

    if tailscaled_state_b64:
        tmp3 = PENDING_TS_STATE_B64_FILE.with_suffix(".tmp")
        tmp3.write_text(tailscaled_state_b64 + "\n", encoding="utf-8")
        tmp3.replace(PENDING_TS_STATE_B64_FILE)
    else:
        try:
            if PENDING_TS_STATE_B64_FILE.exists():
                PENDING_TS_STATE_B64_FILE.unlink()
        except Exception:
            pass


def clear_pending_identity_transition() -> None:
    for p in (
        PENDING_HOSTNAME_FILE,
        PENDING_TS_MODE_FILE,
        PENDING_TS_STATE_B64_FILE,
    ):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


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
    tailscaled_state_b64 = (result.get("tailscaled_state_b64") or "").strip()
    detail = (result.get("detail") or "").strip()
    http_code = int(result.get("http_code") or 0)

    if rc == 0:
        if scanner.startswith("twin-scout-"):
            short_name = scanner[len("twin-scout-"):].strip()
            current_host = get_current_hostname()

            if short_name and current_host and current_host != short_name:
                try:
                    write_pending_identity_transition(short_name, tailscaled_state_b64)
                except Exception as e:
                    print(
                        f"[register] ERROR: failed to write pending identity transition files: {e}",
                        file=sys.stderr,
                    )
                    return 10

                print(scanner)
                return 11

        clear_pending_identity_transition()
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