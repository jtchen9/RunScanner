#!/usr/bin/env python3
import json
from typing import Any, Dict, Tuple, Callable, Optional

import requests


def fetch_commands(
    nms_base: str,
    scanner: str,
    poll_limit: int,
    http_timeout_sec: int,
    av_streaming: Optional[int] = None,
    mobility_report: Optional[Dict[str, Any]] = None,
    status_report: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Poll NMS for due commands using POST.

    Body:
      {
        "limit": int,
        "av_streaming": 0/1,
        "status_report": {...} | null,
        "mobility_report": {...} | null
      }
    """
    url = f"{nms_base}/cmd/poll/{scanner}"

    body: Dict[str, Any] = {
        "limit": poll_limit,
        "av_streaming": av_streaming,
        "status_report": status_report,
        "mobility_report": mobility_report,
    }

    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
        if r.status_code != 200:
            return False, {"error": f"http {r.status_code}", "text": r.text[:200]}
        return True, r.json()
    except Exception as e:
        return False, {"error": f"exception {type(e).__name__}", "detail": str(e)[:200]}
    

def ack_command(
    nms_base: str,
    scanner: str,
    cmd_id: str,
    status: str,
    detail: str,
    http_timeout_sec: int,
    ts_func: Callable[[], str],
    log_func: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Best-effort ACK. Never raises.
    """
    url = f"{nms_base}/cmd/ack/{scanner}"
    body = {
        "cmd_id": cmd_id,
        "status": status,
        "detail": detail,
        "finished_at": ts_func(),
    }
    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
        if r.status_code != 200 and log_func is not None:
            log_func(f"ACK fail cmd_id={cmd_id} http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        if log_func is not None:
            log_func(f"ACK exception cmd_id={cmd_id} {type(e).__name__}: {e}")


def parse_args_json(s: str) -> Dict[str, Any]:
    """
    NMS stores args_json as JSON text. Device parses it into dict.
    """
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
    