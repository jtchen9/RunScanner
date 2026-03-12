#!/usr/bin/env python3
import os
import time
import subprocess

from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    local_ts,
)

from common_log import append_log_line
from common_nms import ack_command
from common_ap_nms import post_ap_poll
from ap_handlers_status import get_ap_status
from ap_dispatch import dispatch

REGISTER_PY = BASE_DIR / "ap_register.py"
LOG_PATH = BASE_DIR / "ap_agent.log"

POLL_INTERVAL_SEC = int(os.getenv("AP_POLL_INTERVAL_SEC", "10"))
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
    log(f"ap_agent started poll={POLL_INTERVAL_SEC}s")

    while True:
        scanner = read_scanner_name()
        if not scanner:
            log("scanner_name.txt missing/empty; attempt AP registration")
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

        status_obj = get_ap_status()
        status_obj["device_name"] = scanner      
        status_body = {
            "time": local_ts(),
            "status": status_obj,
        }

        ok, payload = post_ap_poll(
            nms_base=nms_base,
            scanner=scanner,
            body=status_body,
            http_timeout_sec=HTTP_TIMEOUT_SEC,
        )
        if not ok:
            log(f"AP poll fail scanner={scanner} via={nms_base} {payload}")
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
                log(f"bad AP command item: {item}")
                continue

            fields = fields or {}
            cmd_id = (fields.get("cmd_id") or "").strip()
            action = (fields.get("action") or "").strip()
            execute_at = (fields.get("execute_at") or "").strip()

            if not cmd_id:
                log(f"skip AP command without cmd_id xid={xid} action={action}")
                continue

            log(f"AP_EXEC cmd_id={cmd_id} action={action} execute_at={execute_at} xid={xid}")

            status, detail = dispatch(fields)
            log(f"AP_RESULT cmd_id={cmd_id} status={status} detail={detail}")

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
    