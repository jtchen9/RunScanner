#!/usr/bin/env python3
"""
scanner agent (headless): polls NMS for commands, executes, and ACKs.

Step 3 scope:
- Identity: read scanner_name.txt; if missing, run register.py and retry.
- NMS discovery: via config.get_nms_base() (failover + caching).
- Poll: GET /cmd/poll/{scanner}
- Execute: scan.start / scan.stop / scan.once
- Ack: POST /cmd/ack/{scanner}

Future: add robot/video/audio actions via the same dispatch table.
"""

import os
import time
import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, List
from bundle_manager import apply_bundle

import requests

from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
)

REGISTER_PY = BASE_DIR / "register.py"
LOG_PATH = BASE_DIR / "agent.log"

SCAN_SCRIPT = str(BASE_DIR / "scan_wifi.sh")
SERVICE_NAME_SCAN = "scanner-poller.service"
SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"

# Runtime tuning
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))
POLL_LIMIT = int(os.getenv("POLL_LIMIT", "10"))
HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "10"))
REGISTER_RETRY_SEC = int(os.getenv("REGISTER_RETRY_SEC", "10"))
OFFLINE_RETRY_SEC = int(os.getenv("OFFLINE_RETRY_SEC", "5"))


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"[{utc_iso()}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_scanner_name() -> str:
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def run_register_once() -> None:
    """Best-effort registration attempt. Never raise."""
    try:
        subprocess.run(
            ["/usr/bin/python3", str(REGISTER_PY)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=12,
        )
    except Exception:
        pass


def _run_systemctl(args: List[str]) -> Tuple[bool, str, str]:
    """Run systemctl. Try without sudo first; if that fails, retry with sudo -n."""
    try:
        cp = subprocess.run(
            [SYSTEMCTL] + args,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return True, (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except subprocess.CalledProcessError as e1:
        try:
            cp2 = subprocess.run(
                [SUDO, "-n", SYSTEMCTL] + args,
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            return True, (cp2.stdout or "").strip(), (cp2.stderr or "").strip()
        except subprocess.CalledProcessError as e2:
            return False, (e2.stdout or "").strip(), (e2.stderr or e1.stderr or "").strip()


def exec_scan_start() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["start", SERVICE_NAME_SCAN])
    return (True, "started scanner-poller.service") if ok else (False, f"start failed: {err or out}")


def exec_scan_stop() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["stop", SERVICE_NAME_SCAN])
    return (True, "stopped scanner-poller.service") if ok else (False, f"stop failed: {err or out}")


def exec_scan_once() -> Tuple[bool, str]:
    """Run one scan immediately (does not rely on systemd service)."""
    try:
        cp = subprocess.run(
            ["/usr/bin/bash", SCAN_SCRIPT, "once"],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=40,
        )
        if cp.returncode == 0:
            return True, "scan_once ok"
        return False, f"scan_once rc={cp.returncode} stderr={((cp.stderr or '')[:200]).strip()}"
    except Exception as e:
        return False, f"scan_once exception: {type(e).__name__}: {e}"


def fetch_commands(nms_base: str, scanner: str) -> Tuple[bool, Dict[str, Any]]:
    """Returns (ok, payload). ok=False means network/parse error."""
    url = f"{nms_base}/cmd/poll/{scanner}"
    try:
        r = requests.get(url, params={"limit": POLL_LIMIT}, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            return False, {"error": f"http {r.status_code}", "text": r.text[:200]}
        return True, r.json()
    except Exception as e:
        return False, {"error": f"exception {type(e).__name__}", "detail": str(e)[:200]}


def ack_command(nms_base: str, scanner: str, cmd_id: str, status: str, detail: str) -> None:
    """Best-effort ACK. Never raise."""
    url = f"{nms_base}/cmd/ack/{scanner}"
    body = {
        "cmd_id": cmd_id,
        "status": status,
        "detail": detail,
        "finished_at": utc_iso(),
    }
    try:
        r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            log(f"ACK fail cmd_id={cmd_id} http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        log(f"ACK exception cmd_id={cmd_id} {type(e).__name__}: {e}")


def parse_args_json(s: str) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def dispatch(nms_base: str, scanner: str, cmd_fields: Dict[str, Any]) -> Tuple[str, str]:
    """Execute one command. Returns (status, detail) where status in {'ok','error'}."""
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    _args = parse_args_json(cmd_fields.get("args_json") or "")

    if category and category != "scan":
        return "error", f"unsupported category={category}"

    if action == "scan.start":
        ok, detail = exec_scan_start()
        return ("ok" if ok else "error"), detail

    if action == "scan.stop":
        ok, detail = exec_scan_stop()
        return ("ok" if ok else "error"), detail

    if action == "scan.once":
        ok, detail = exec_scan_once()
        return ("ok" if ok else "error"), detail

    if action == "bundle.apply":
        bundle_id = (_args.get("bundle_id") or "").strip()
        if not bundle_id:
            bundle_id = (cmd_fields.get("bundle_id") or "").strip()
        if not bundle_id:
            return "error", "missing bundle_id"

        ok, detail, prev = apply_bundle(nms_base, bundle_id)
        status = "ok" if ok else "error"

        report_bundle_result(nms_base, scanner, bundle_id, prev, status, detail)
        return status, detail

    return "error", f"unknown action={action}"


def report_bundle_result(nms_base: str, scanner: str, bundle_id: str, prev_bundle_id: str,
                         status: str, detail: str) -> None:
    """
    Best-effort report to NMS bundle management endpoint.
    Does not replace /cmd/ack; it's for bundle tracking.
    """
    url = f"{nms_base}/bootstrap/report/{scanner}"
    body = {
        "scanner": scanner,
        "bundle_id": bundle_id,
        "prev_bundle_id": prev_bundle_id,
        "status": status,   # ok | error
        "detail": detail,
        "ts_utc": utc_iso(),
    }
    try:
        r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            log(f"BOOTSTRAP report fail http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        log(f"BOOTSTRAP report exception: {type(e).__name__}: {e}")


def main() -> None:
    log(f"agent started poll={POLL_INTERVAL_SEC}s limit={POLL_LIMIT}")

    while True:
        # 1) Ensure identity
        scanner = read_scanner_name()
        if not scanner:
            log("scanner_name.txt missing/empty; attempt registration")
            run_register_once()
            scanner = read_scanner_name()
            if not scanner:
                log(f"still unassigned; retry in {REGISTER_RETRY_SEC}s")
                time.sleep(REGISTER_RETRY_SEC)
                continue

        # 2) Ensure NMS is reachable
        nms_base = get_nms_base()
        if not nms_base:
            log(f"offline: no NMS reachable; retry in {OFFLINE_RETRY_SEC}s")
            time.sleep(OFFLINE_RETRY_SEC)
            continue

        # 3) Poll
        ok, payload = fetch_commands(nms_base, scanner)
        if not ok:
            log(f"poll fail scanner={scanner} via={nms_base} {payload}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        cmds = payload.get("commands") or []
        if not cmds:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # cmds: [[xid, fields], ...]
        for item in cmds:
            try:
                xid, fields = item
            except Exception:
                log(f"bad command item: {item}")
                continue

            fields = fields or {}
            cmd_id = fields.get("cmd_id") or ""
            action = fields.get("action") or ""
            execute_at = fields.get("execute_at") or ""

            if not cmd_id:
                log(f"skip command without cmd_id xid={xid} action={action}")
                continue

            log(f"EXEC cmd_id={cmd_id} action={action} execute_at={execute_at} xid={xid}")

            status, detail = dispatch(nms_base, scanner, fields)
            log(f"RESULT cmd_id={cmd_id} status={status} detail={detail}")

            ack_command(nms_base, scanner, cmd_id, status, detail)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
