#!/usr/bin/env python3
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

HTTP_TIMEOUT_SEC = 6


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
        capabilities="ap,status,traffic,association,txpower",
    )

    scanner = (result.get("scanner") or "").strip()
    detail = (result.get("detail") or "").strip()
    http_code = int(result.get("http_code") or 0)

    if rc == 0:
        print(scanner)
        return 0

    if rc == 3:
        print("[ap_register] OFFLINE: no NMS reachable", file=sys.stderr)
        return rc

    if rc == 4:
        print(f"[ap_register] OFFLINE: {detail}", file=sys.stderr)
        return rc

    if rc == 5:
        print("[ap_register] ERROR: empty scanner name returned", file=sys.stderr)
        return rc

    if rc == 6:
        print(f"[ap_register] ERROR: cannot write scanner_name.txt: {detail}", file=sys.stderr)
        return rc

    if rc == 7:
        print(f"[ap_register] BLOCKED: {detail}", file=sys.stderr)
        return rc

    if rc == 8:
        print(f"[ap_register] ERROR http={http_code} body={detail}", file=sys.stderr)
        return rc

    print(f"[ap_register] ERROR: {detail or 'registration failed'}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
    