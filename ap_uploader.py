#!/usr/bin/env python3
import os
import time

from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    local_ts,
)

from common_log import append_log_line
from common_ap_nms import post_ap_traffic
from ap_handlers_traffic import is_traffic_enabled, get_ap_traffic_report

INTERVAL_SEC = int(os.getenv("AP_UPLOAD_INTERVAL_SEC", "60"))
HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "10"))

LOG_PATH = BASE_DIR / "ap_uploader.log"


def log(msg: str) -> None:
    append_log_line(
        log_path=LOG_PATH,
        msg=msg,
        ts_func=local_ts,
        ensure_parent=True,
        also_print=True,
    )


def read_scanner_name() -> str:
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def main() -> None:
    log(f"ap_uploader started interval={INTERVAL_SEC}s")

    while True:
        try:
            if not is_traffic_enabled():
                log("skip ap traffic upload: disabled")
                time.sleep(INTERVAL_SEC)
                continue

            scanner = read_scanner_name()
            if not scanner:
                log("skip ap traffic upload: scanner_name.txt missing/empty")
                time.sleep(INTERVAL_SEC)
                continue

            nms_base = get_nms_base()
            if not nms_base:
                log("skip ap traffic upload: offline (no NMS reachable)")
                time.sleep(INTERVAL_SEC)
                continue

            body = get_ap_traffic_report()
            body["device_name"] = scanner

            ok = post_ap_traffic(
                nms_base=nms_base,
                scanner=scanner,
                body=body,
                http_timeout_sec=HTTP_TIMEOUT_SEC,
                log_func=log,
            )
            if ok:
                log(f"AP_TRAFFIC ok scanner={scanner} via={nms_base} records={len(body.get('records') or [])}")

        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")

        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
    