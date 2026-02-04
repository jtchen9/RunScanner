#!/usr/bin/env python3
"""
One-shot integration test for Pi-facing NMS APIs.

Operator-side script:
- Uses operator-only underscore APIs to set up test environment (reset, whitelist, bundle, commands)
- Exercises Pi-facing endpoints in order
- Pauses at each step so you can stop at the first problem and paste output

Test order:
  0) POST /admin/_reset                     (operator-only)  [best-effort]
  1) GET  /health                           (Pi-facing)
  2) POST /registry/_whitelist_upsert       (operator-only)  [upsert BOTH known MACs]
  3) POST /registry/register                (Pi-facing)
  4) POST /ingest/{scanner}                 (Pi-facing)
  5) (optional) bundle upload: POST /bootstrap/_bundle      (operator-only)
  6) (optional) bundle download: GET /bootstrap/bundle/{id} (Pi-facing)
  7) (optional) bundle telemetry: POST /bootstrap/report/{scanner} (Pi-facing)
  8) (optional) enqueue command: POST /cmd/_enqueue/{scanner}      (operator-only)
  9) (optional) poll: GET /cmd/poll/{scanner}              (Pi-facing)
 10) (optional) ack:  POST /cmd/ack/{scanner}              (Pi-facing)
"""

import sys
import json
import time
import hashlib
import zipfile
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


# =============================================================================
# Globals (will be assigned in __main__)
# =============================================================================
NMS_BASE: str

WL_SCANNER01: str
WL_MAC01: str
WL_SCANNER02: str
WL_MAC02: str

TEST_SCANNER: str
TEST_MAC: str
TEST_IP: str

DO_BUNDLE_TEST: bool
BUNDLE_ID: str
BUNDLE_SRC_DIR: str
BUNDLE_ZIP_PATH: str

DO_COMMAND_TEST: bool
CMD_ACTION: str
CMD_ARGS_JSON: str

HTTP_TIMEOUT: float

BUNDLE_FILES = [
    "agent.py",
    "bundle_manager.py",
    "config.py",
    "main.py",
    "parse_iw.py",
    "register.py",
    "scan_payload.py",
    "scan_wifi.sh",
    "scenario_commands.md",
    "uploader.py",
    "windows.py",
    "install.sh",   # optional; included if present
]


# =============================================================================
# Helpers
# =============================================================================

def die(msg: str, code: int = 2) -> None:
    print(f"\n[FATAL] {msg}\n")
    sys.exit(code)

def pause(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    input("Press ENTER to continue (or Ctrl+C to stop) ... ")

def jprint(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def req(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{NMS_BASE}{path}"
    return requests.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)

def ok_or_dump(r: requests.Response) -> None:
    print(f"[HTTP] {r.request.method} {r.url}")
    print(f"[HTTP] status={r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            jprint(r.json())
        except Exception:
            print((r.text or "")[:1500])
    else:
        t = (r.text or "")
        if t:
            print(t[:1500])
        else:
            print(f"[HTTP] (no text) content-length={len(r.content or b'')}")

def _is_valid_mac(mac: str) -> bool:
    parts = mac.split(":")
    return len(parts) == 6 and all(len(p) == 2 for p in parts)

def admin_reset_best_effort() -> None:
    """
    POST /admin/_reset.

    Operator-only API.
    Requires explicit confirmation to prevent accidental data loss.
    """
    body = {
        "confirm": "RESET",
        "keep_whitelist": True,
        "keep_bundles": True,
        "keep_autoflush_flag": True,
    }

    r = req("POST", "/admin/_reset", json=body)
    ok_or_dump(r)

    if r.status_code != 200:
        die("/admin/_reset failed (expected 200 OK)")

    print("[OK] admin reset succeeded")

def get_health() -> Dict[str, Any]:
    r = req("GET", "/health")
    ok_or_dump(r)
    if r.status_code != 200:
        die("health check failed")
    j = r.json()
    if not isinstance(j, dict):
        die("health JSON not dict")
    return j

def op_whitelist_upsert_two() -> None:
    body = {
        "items": [
            {"scanner": WL_SCANNER01, "mac": WL_MAC01},
            {"scanner": WL_SCANNER02, "mac": WL_MAC02},
        ]
    }
    r = req("POST", "/registry/_whitelist_upsert", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("whitelist upsert failed")

def pi_register(mac: str, ip: Optional[str], scanner_version: str) -> str:
    body = {
        "mac": mac,
        "ip": ip,
        "scanner_version": scanner_version,  # telemetry only
        "capabilities": "scan",              # telemetry only
    }
    r = req("POST", "/registry/register", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("Pi register failed")
    scanner = (r.text or "").strip()
    if not scanner:
        die("Pi register returned empty scanner name")
    return scanner

def pi_ingest(scanner: str, payload_obj: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    r = req(
        "POST",
        f"/ingest/{scanner}",
        data=raw,
        headers={"Content-Type": "application/octet-stream"},
    )
    ok_or_dump(r)
    if r.status_code != 200:
        die("Pi ingest failed")
    j = r.json()
    if not isinstance(j, dict):
        die("ingest response not dict")
    return j

def op_bundle_upload(bundle_id: str, zip_path: str) -> Dict[str, Any]:
    p = Path(zip_path)
    if not p.exists():
        die(f"bundle zip not found: {p}")

    with p.open("rb") as f:
        files = {"bundle": (p.name, f, "application/zip")}
        r = req("POST", "/bootstrap/_bundle", params={"bundle_id": bundle_id}, files=files)

    ok_or_dump(r)
    if r.status_code != 200:
        die("bundle upload failed")
    try:
        return r.json()
    except Exception:
        return {"status": "ok"}

def pi_bundle_download(bundle_id: str) -> bytes:
    r = req("GET", f"/bootstrap/bundle/{bundle_id}")
    print(f"[HTTP] GET {r.url} status={r.status_code} content-type={r.headers.get('content-type','')}")
    if r.status_code != 200:
        ok_or_dump(r)
        die("bundle download failed")
    data = r.content or b""
    print(f"[OK] downloaded bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
    return data

def pi_bootstrap_report(scanner: str, installed_version: str) -> Dict[str, Any]:
    r = req("POST", f"/bootstrap/report/{scanner}", json={"installed_version": installed_version})
    ok_or_dump(r)
    if r.status_code != 200:
        die("bootstrap report failed")
    return r.json()

def op_cmd_enqueue(scanner: str, execute_at: str, category: str, action: str, args_json_text: str) -> str:
    try:
        _ = json.loads(args_json_text) if args_json_text else {}
    except Exception as e:
        die(f"CMD_ARGS_JSON is not valid JSON: {e}")

    body = {
        "category": category,
        "action": action,
        "execute_at": execute_at,
        "args_json_text": args_json_text,
    }
    r = req("POST", f"/cmd/_enqueue/{scanner}", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("cmd enqueue failed")
    j = r.json()
    cmd_id = (j.get("cmd_id") or "").strip()
    if not cmd_id:
        die("enqueue returned no cmd_id")
    return cmd_id

def pi_cmd_poll(scanner: str, limit: int = 5) -> Dict[str, Any]:
    r = req("GET", f"/cmd/poll/{scanner}", params={"limit": limit})
    ok_or_dump(r)
    if r.status_code != 200:
        die("cmd poll failed")
    j = r.json()
    if not isinstance(j, dict):
        die("poll response not dict")
    return j

def pi_cmd_ack(scanner: str, cmd_id: str) -> Dict[str, Any]:
    body = {
        "cmd_id": cmd_id,
        "status": "ok",
        "finished_at": None,  # server stamps if None/absent
        "detail": "one-shot test ack (operator script)",
    }
    r = req("POST", f"/cmd/ack/{scanner}", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("cmd ack failed")
    return r.json()

def build_bundle_zip_from_dir(src_dir: str, bundle_id: str) -> str:
    """
    Create a zip file:
      ZIP contains a top-level folder named {bundle_id}/...

    The zip is stored in /home/pi/_RunScanner/bundles
    so it can be reused later.
    """
    src = Path(src_dir)
    if not src.exists() or not src.is_dir():
        die(f"BUNDLE_SRC_DIR not a directory: {src}")

    bundle_store = Path("/home/pi/_RunScanner/bundles")
    bundle_store.mkdir(parents=True, exist_ok=True)

    out_zip = bundle_store / f"{bundle_id}.zip"
    if out_zip.exists():
        out_zip.unlink()

    missing = [f for f in BUNDLE_FILES if f != "install.sh" and not (src / f).exists()]
    if missing:
        die(f"Bundle build: missing required files in {src}: {missing}")

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in BUNDLE_FILES:
            p = src / name
            if not p.exists():
                continue
            arcname = f"{bundle_id}/{name}"
            zf.write(p, arcname=arcname)

    print(f"[OK] built bundle zip: {out_zip} bytes={out_zip.stat().st_size}")
    return str(out_zip)

def resolve_bundle_zip_path() -> Optional[str]:
    """
    Decide how to obtain bundle zip:
    - if BUNDLE_ZIP_PATH is non-empty -> use it
    - else if BUNDLE_SRC_DIR is non-empty -> build zip from it
    - else -> None
    """
    if BUNDLE_ZIP_PATH:
        p = Path(BUNDLE_ZIP_PATH)
        if not p.exists():
            die(f"BUNDLE_ZIP_PATH not found: {p}")
        return str(p)

    if BUNDLE_SRC_DIR:
        return build_bundle_zip_from_dir(BUNDLE_SRC_DIR, BUNDLE_ID)

    return None

def _run_sh(path: Path) -> None:
    subprocess.run(
        ["/usr/bin/bash", str(path)],
        check=False,
        capture_output=False,   # show output live
        text=True,
    )

# =============================================================================
# Main flow
# =============================================================================

def main() -> int:   
    _run_sh(ENTER_SH)
    try:
        if not _is_valid_mac(WL_MAC01) or not _is_valid_mac(WL_MAC02) or not _is_valid_mac(TEST_MAC):
            die("MAC format invalid. Expected like 2c:cf:67:...")

        pause(f"STEP 0: POST /admin/_reset (operator-only)  NMS_BASE={NMS_BASE}")
        admin_reset_best_effort()

        pause("STEP 1: GET /health (Pi-facing)")
        health = get_health()
        server_now = health.get("time")
        time_fmt = health.get("time_format")
        print(f"[INFO] server_now={server_now} time_format={time_fmt}")
        if not isinstance(server_now, str) or not server_now:
            die("health missing 'time'")

        pause("STEP 2: POST /registry/_whitelist_upsert (operator-only)  (upsert BOTH known MACs)")
        op_whitelist_upsert_two()

        pause("STEP 3: POST /registry/register (Pi-facing) using TEST_MAC")
        assigned = pi_register(TEST_MAC, TEST_IP, scanner_version=BUNDLE_ID)
        print(f"[OK] register assigned scanner={assigned}")
        if assigned != TEST_SCANNER:
            print(f"[WARN] assigned scanner differs from TEST_SCANNER: expected={TEST_SCANNER} got={assigned}")

        pause("STEP 4: POST /ingest/{scanner} (Pi-facing) with opaque bytes")
        payload_obj = {
            "scanner": assigned,
            "time": server_now,  # opaque for NMS
            "note": f"one-shot test payload ({BUNDLE_ID})",
            "entries": [{"bssid": "00:11:22:33:44:55", "ssid": "TEST", "freq": 2412, "signal": -45.0}],
        }
        ingest = pi_ingest(assigned, payload_obj)
        print(f"[OK] ingest queued_in={ingest.get('queued_in')} bytes={ingest.get('bytes')} sha256={ingest.get('sha256')}")

        if DO_BUNDLE_TEST:
            pause("STEP 5: Bundle upload (operator-only) POST /bootstrap/_bundle  (build zip if configured)")
            zip_path = resolve_bundle_zip_path()
            if zip_path:
                op_bundle_upload(BUNDLE_ID, zip_path)
            else:
                print("[WARN] No BUNDLE_ZIP_PATH and no BUNDLE_SRC_DIR. Skipping upload step.")

            pause("STEP 6: Bundle download (Pi-facing) GET /bootstrap/bundle/{bundle_id}")
            _ = pi_bundle_download(BUNDLE_ID)

            pause("STEP 7: Bundle telemetry (Pi-facing) POST /bootstrap/report/{scanner}")
            _ = pi_bootstrap_report(assigned, BUNDLE_ID)

        if DO_COMMAND_TEST:
            pause("STEP 8: Enqueue one due command (operator-only) POST /cmd/_enqueue/{scanner}")
            cmd_id = op_cmd_enqueue(
                scanner=assigned,
                execute_at=server_now,      # guarantee due
                category="scan",
                action=CMD_ACTION,
                args_json_text=CMD_ARGS_JSON,
            )
            print(f"[OK] enqueued cmd_id={cmd_id} action={CMD_ACTION} args={CMD_ARGS_JSON}")

            pause("STEP 9: Poll commands (Pi-facing) GET /cmd/poll/{scanner}")
            poll = pi_cmd_poll(assigned, limit=10)
            cmds = poll.get("commands") or []
            if not cmds:
                die("poll returned no commands (expected >=1).")
            xid, fields = cmds[0]
            got_cmd_id = (fields or {}).get("cmd_id") or cmd_id
            print(f"[OK] poll returned xid={xid} cmd_id={got_cmd_id}")

            pause("STEP 10: ACK command (Pi-facing) POST /cmd/ack/{scanner}")
            _ = pi_cmd_ack(assigned, got_cmd_id)

        pause("DONE: All selected Pi-facing APIs exercised.")
        return 0
    finally:
        _run_sh(EXIT_SH)

if __name__ == "__main__":
    # -------------------------------------------------------------------------
    # You only change these two lines at office vs home:
    # -------------------------------------------------------------------------
    # TEST_SCANNER = "twin-scout-alpha"
    # TEST_MAC = "2c:cf:67:d0:67:f3"
    TEST_SCANNER = "twin-scout-bravo"
    TEST_MAC = "2c:cf:67:3f:7b:51"
    # TEST_SCANNER = "twin-scout-charlie"
    # TEST_MAC = "2c:cf:67:d0:67:82"

    # -------------------------------------------------------------------------
    # Fixed defaults (do NOT change unless you intend to change the test)
    # -------------------------------------------------------------------------
    NMS_BASE = "http://192.168.137.1:8000"

    TEST_DIR = Path("/home/pi/_RunScanner/TestCodes")
    ENTER_SH = TEST_DIR / "enter_test_mode.sh"
    EXIT_SH  = TEST_DIR / "exit_test_mode.sh"

    WL_SCANNER01 = "twin-scout-alpha"
    WL_MAC01 = "2c:cf:67:d0:67:f3"

    WL_SCANNER02 = "twin-scout-bravo"
    WL_MAC02 = "2c:cf:67:3f:7b:51"
    
    WL_SCANNER03 = "twin-scout-charlie"
    WL_MAC03 = "2c:cf:67:d0:67:82"

    TEST_IP = "192.168.137.2"   # fixed as requested

    DO_BUNDLE_TEST = True
    BUNDLE_ID = "robotBundle1.0"

    # If you want the script to build zip automatically, set BUNDLE_SRC_DIR to a folder.
    # Or set BUNDLE_ZIP_PATH to an existing zip. Leaving both empty means: skip upload, still test download/report.
    BUNDLE_SRC_DIR = "/home/pi/_RunScanner"      
    BUNDLE_ZIP_PATH = ""     

    DO_COMMAND_TEST = True
    CMD_ACTION = "scan.once"
    CMD_ARGS_JSON = "{}"

    HTTP_TIMEOUT = 12.0

    raise SystemExit(main())
