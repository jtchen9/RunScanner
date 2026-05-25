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
from robot_agent_handlers import (
    get_av_streaming_flag,
    get_mobility_report_payload,
    mark_mobility_report_sent,
)
from robot_status_report import build_status_report
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

        mobility_report = get_mobility_report_payload()
        status_report = build_status_report()

        ok, payload = fetch_commands(
            nms_base=nms_base,
            scanner=scanner,
            poll_limit=POLL_LIMIT,
            http_timeout_sec=HTTP_TIMEOUT_SEC,
            av_streaming=get_av_streaming_flag(),
            mobility_report=mobility_report,
            status_report=status_report,
        )
        if not ok:
            log(f"poll fail scanner={scanner} via={nms_base} {payload}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Option B:
        # once command poll succeeds, clear the pending mobility report
        # so outdated tag/location info will not keep interfering upstream.
        if mobility_report:
            mark_mobility_report_sent()

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
            log(f"CMD_FIELDS cmd_id={cmd_id} fields={fields}")

            if action == "bundle.apply":
                # Disruptive command: ACK first so NMS will not keep re-issuing it.
                pre_status = "ok"
                pre_detail = "bundle.apply accepted; robot will install bundle and reboot"

                ack_command(
                    nms_base=nms_base,
                    scanner=scanner,
                    cmd_id=cmd_id,
                    status=pre_status,
                    detail=pre_detail,
                    http_timeout_sec=HTTP_TIMEOUT_SEC,
                    ts_func=local_ts,
                    log_func=log,
                )
                log(f"PRE-ACK cmd_id={cmd_id} status={pre_status} detail={pre_detail}")
                log(f"BUNDLE: begin apply after pre-ack cmd_id={cmd_id}")
                
                status, detail = dispatch(
                    nms_base=nms_base,
                    scanner=scanner,
                    cmd_fields=fields,
                    http_timeout_sec=HTTP_TIMEOUT_SEC,
                    log_func=log,
                )
                log(f"RESULT cmd_id={cmd_id} status={status} detail={detail}")

            else:
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
    