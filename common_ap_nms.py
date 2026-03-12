#!/usr/bin/env python3
from typing import Any, Dict, Tuple, Callable, Optional

import requests


def post_ap_poll(
    nms_base: str,
    scanner: str,
    body: Dict[str, Any],
    http_timeout_sec: int,
) -> Tuple[bool, Dict[str, Any]]:
    """
    POST AP status + poll commands in one regular 10-second channel.
    Returns (ok, payload).
    """
    url = f"{nms_base}/ap/poll/{scanner}"
    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
        if r.status_code != 200:
            return False, {"error": f"http {r.status_code}", "text": r.text[:300]}
        return True, r.json()
    except Exception as e:
        return False, {"error": f"exception {type(e).__name__}", "detail": str(e)[:300]}


def post_ap_traffic(
    nms_base: str,
    scanner: str,
    body: Dict[str, Any],
    http_timeout_sec: int,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Best-effort AP 60-second traffic report.
    """
    url = f"{nms_base}/ap/traffic/{scanner}"
    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
        if 200 <= r.status_code < 300:
            return True
        if log_func is not None:
            log_func(f"AP_TRAFFIC fail scanner={scanner} via={nms_base} status={r.status_code} body={r.text[:300]}")
        return False
    except Exception as e:
        if log_func is not None:
            log_func(f"AP_TRAFFIC exception scanner={scanner} via={nms_base}: {type(e).__name__}: {e}")
        return False
    