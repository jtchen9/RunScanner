#!/usr/bin/env python3
"""
robot agent (headless): polls NMS for commands, executes, and ACKs.
"""

import os
import time
import subprocess

from common_log import append_log_line
from common_nms import fetch_commands, ack_command
from robot_dispatch import dispatch
from robot_agent_handlers import get_av_streaming_flag

from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    local_ts,
)

REGISTER_PY = BASE_DIR / "register.py"
LOG_PATH = BASE_DIR / "agent.log"

# Runtime tuning
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "10"))
POLL_LIMIT = int(os.getenv("POLL_LIMIT", "20"))
HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "10"))
REGISTER_RETRY_SEC = int(os.getenv("REGISTER_RETRY_SEC", "10"))
OFFLINE_RETRY_SEC = int(os.getenv("OFFLINE_RETRY_SEC", "5"))


def log(msg: str) -> None:
    append_log_line(
        log_path=LOG_PATH,
        msg=msg,
        ts_func=local_ts,
        ensure_parent=False,
        also_print=True,
    )


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


def main() -> None:
    log(f"agent started poll={POLL_INTERVAL_SEC}s limit={POLL_LIMIT}")

    while True:
        scanner = read_scanner_name()
        if not scanner:
            log("scanner_name.txt missing/empty; attempt registration")
            run_register_once()
            scanner = read_scanner_name()
            if not scanner:
                log(f"still unassigned; retry in {REGISTER_RETRY_SEC}s")
                time.sleep(REGISTER_RETRY_SEC)
                continue

        nms_base = get_nms_base()
        if not nms_base:
            log(f"offline: no NMS reachable; retry in {OFFLINE_RETRY_SEC}s")
            time.sleep(OFFLINE_RETRY_SEC)
            continue

        ok, payload = fetch_commands(
            nms_base=nms_base,
            scanner=scanner,
            poll_limit=POLL_LIMIT,
            http_timeout_sec=HTTP_TIMEOUT_SEC,
            av_streaming=get_av_streaming_flag(),
        )
        if not ok:
            log(f"poll fail scanner={scanner} via={nms_base} {payload}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        cmds = payload.get("commands") or []
        if not cmds:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        for item in cmds:
            try:
                xid, fields = item
            except Exception:
                log(f"bad command item: {item}")
                continue

            fields = fields or {}
            cmd_id = (fields.get("cmd_id") or "").strip()
            action = (fields.get("action") or "").strip()
            execute_at = (fields.get("execute_at") or "").strip()

            if not cmd_id:
                log(f"skip command without cmd_id xid={xid} action={action}")
                continue

            log(f"EXEC cmd_id={cmd_id} action={action} execute_at={execute_at} xid={xid}")

            status, detail = dispatch(
                nms_base=nms_base,
                scanner=scanner,
                cmd_fields=fields,
                http_timeout_sec=HTTP_TIMEOUT_SEC,
                log_func=log,
            )
            log(f"RESULT cmd_id={cmd_id} status={status} detail={detail}")

            ack_command(
                nms_base=nms_base,
                scanner=scanner,
                cmd_id=cmd_id,
                status=status,
                detail=detail,
                http_timeout_sec=HTTP_TIMEOUT_SEC,
                ts_func=local_ts,
                log_func=log,
            )

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    