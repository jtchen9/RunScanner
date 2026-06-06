#!/usr/bin/env python3
import json
import socket
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests
from config import get_ax210_iface
MOBILITY_CAPTURE_SCRIPT = "/opt/_RunScanner/robot_mobility_location_capture.py"

def get_registration_mobility_report() -> Optional[Dict[str, Any]]:
    """
    Return robot mobility report for registration if this device supports it.

    Robot:
      /opt/_RunScanner/robot_mobility_location_capture.py exists
      -> run it and return JSON result

    Non-mobile device / AP:
      script does not exist
      -> return None, so registration payload is unchanged
    """
    script = Path(MOBILITY_CAPTURE_SCRIPT)

    if not script.exists():
        return None

    try:
        cp = subprocess.run(
            ["/usr/bin/python3", str(script)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )

        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()

        if out:
            try:
                data = json.loads(out)
            except Exception:
                return {
                    "ok": False,
                    "stage": "registration_mobility_report",
                    "error": "mobility_report_not_json",
                    "returncode": cp.returncode,
                    "raw_stdout": out[:500],
                    "raw_stderr": err[:500],
                }

            data["registration_mobility_report"] = True
            data["returncode"] = cp.returncode
            return data

        return {
            "ok": False,
            "stage": "registration_mobility_report",
            "error": "mobility_report_empty_stdout",
            "returncode": cp.returncode,
            "raw_stderr": err[:500],
        }

    except Exception as e:
        return {
            "ok": False,
            "stage": "registration_mobility_report",
            "error": f"{type(e).__name__}: {e}",
        }

def write_last_register(
    last_register_file,
    time_fmt: str,
    ts_func,
    status: str,
    detail: str = "",
    http_code: int = 0,
    scanner: str = "",
    mac: str = "",
    ip: str = "",
) -> None:
    """
    Persist last registration attempt for local debugging/inspection only.

    NOTE:
    - Telemetry only
    - Time format MUST match NMS (TIME_FMT)
    """
    payload: Dict[str, Any] = {
        "time": ts_func(),
        "status": status,          # ok | blocked | offline | error
        "detail": detail,
        "http_code": http_code,
        "scanner": scanner,
        "mac": mac,
        "ip": ip,
        "time_format": time_fmt,
    }
    try:
        last_register_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

def _get_ipv4_of_iface(iface: str) -> str:
    """
    Return the first non-loopback IPv4 of iface, else "".
    """
    try:
        out = subprocess.check_output(
            [
                "bash",
                "-lc",
                f"ip -4 addr show dev {iface} | awk '/inet / {{print $2}}' | cut -d/ -f1 | head -n 1",
            ],
            text=True,
        ).strip()
        if out and not out.startswith("127."):
            return out
    except Exception:
        pass
    return ""


def get_ip_best_effort() -> str:
    """
    IP reported to NMS for reverse-direction iperf3 traffic.

    Policy:
      1) Prefer AX210 interface (iwlwifi)
      2) If AX210 exists but has no IPv4 yet, return ""
      3) Do NOT fall back to wlan0, because wlan0 is control plane
         and must not be advertised as iperf3 data-plane IP
    """
    ax_iface = get_ax210_iface()
    if not ax_iface:
        return ""

    return _get_ipv4_of_iface(ax_iface)


def perform_registration(
    *,
    base_dir,
    get_nms_base,
    get_bundle_version,
    get_mac_address,
    scanner_name_file,
    last_register_file,
    time_fmt: str,
    ts_func,
    http_timeout_sec: int,
    capabilities: str = "scan",
) -> Tuple[int, Dict[str, Any]]:
    """
    Reusable device registration core.

    Returns:
      (rc, result_dict)

    result_dict may include:
      - scanner
      - llm_weblink
      - mac
      - ip
      - nms_base
      - http_code
      - detail
    """
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    mac = get_mac_address()
    if not mac:
        write_last_register(
            last_register_file=last_register_file,
            time_fmt=time_fmt,
            ts_func=ts_func,
            status="error",
            detail="Cannot read MAC for iface",
        )
        return 2, {
            "status": "error",
            "detail": "Cannot read MAC for iface",
            "mac": "",
            "ip": "",
            "scanner": "",
            "llm_weblink": "",
            "nms_base": "",
            "http_code": 0,
        }

    nms_base = get_nms_base()
    ip = get_ip_best_effort()

    if not nms_base:
        write_last_register(
            last_register_file=last_register_file,
            time_fmt=time_fmt,
            ts_func=ts_func,
            status="offline",
            detail="No NMS reachable (discovery failed)",
            mac=mac,
            ip=ip,
        )
        return 3, {
            "status": "offline",
            "detail": "No NMS reachable (discovery failed)",
            "mac": mac,
            "ip": ip,
            "scanner": "",
            "llm_weblink": "",
            "nms_base": "",
            "http_code": 0,
        }

    url = f"{nms_base}/registry/register"
    mobility_report = get_registration_mobility_report()

    body = {
        "mac": mac,
        "ip": ip or None,
        "scanner_version": get_bundle_version(),
        "capabilities": capabilities,
    }

    if mobility_report is not None:
        body["mobility_report_at_registration"] = mobility_report

    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
    except Exception as e:
        detail = f"POST failed: {e}"
        write_last_register(
            last_register_file=last_register_file,
            time_fmt=time_fmt,
            ts_func=ts_func,
            status="offline",
            detail=detail,
            mac=mac,
            ip=ip,
        )
        return 4, {
            "status": "offline",
            "detail": detail,
            "mac": mac,
            "ip": ip,
            "scanner": "",
            "llm_weblink": "",
            "nms_base": nms_base,
            "http_code": 0,
        }

    if r.status_code == 200:
        scanner = ""
        llm_weblink = ""
        tailscaled_state_b64 = ""

        try:
            data = r.json()
            if isinstance(data, dict):
                scanner = (data.get("scanner") or "").strip()
                llm_weblink = (data.get("llm_weblink") or "").strip()
                tailscaled_state_b64 = (data.get("tailscaled_state_b64") or "").strip()
        except Exception:
            pass

        if not scanner:
            scanner = (r.text or "").strip()

        if not scanner:
            write_last_register(
                last_register_file=last_register_file,
                time_fmt=time_fmt,
                ts_func=ts_func,
                status="error",
                detail="Empty scanner name returned",
                http_code=200,
                mac=mac,
                ip=ip,
            )
            return 5, {
                "status": "error",
                "detail": "Empty scanner name returned",
                "mac": mac,
                "ip": ip,
                "scanner": "",
                "llm_weblink": "",
                "tailscaled_state_b64": "",
                "nms_base": nms_base,
                "http_code": 200,
            }

        try:
            tmp_file = scanner_name_file.with_suffix(".tmp")
            tmp_file.write_text(scanner + "\n", encoding="utf-8")
            tmp_file.replace(scanner_name_file)
        except Exception as e:
            detail = f"Failed to write scanner_name.txt: {e}"
            write_last_register(
                last_register_file=last_register_file,
                time_fmt=time_fmt,
                ts_func=ts_func,
                status="error",
                detail=detail,
                http_code=200,
                scanner=scanner,
                mac=mac,
                ip=ip,
            )
            return 6, {
                "status": "error",
                "detail": detail,
                "mac": mac,
                "ip": ip,
                "scanner": scanner,
                "llm_weblink": llm_weblink,
                "tailscaled_state_b64": tailscaled_state_b64,
                "nms_base": nms_base,
                "http_code": 200,
            }

        write_last_register(
            last_register_file=last_register_file,
            time_fmt=time_fmt,
            ts_func=ts_func,
            status="ok",
            detail=f"registered via {nms_base}",
            http_code=200,
            scanner=scanner,
            mac=mac,
            ip=ip,
        )
        return 0, {
            "status": "ok",
            "detail": f"registered via {nms_base}",
            "mac": mac,
            "ip": ip,
            "scanner": scanner,
            "llm_weblink": llm_weblink,
            "tailscaled_state_b64": tailscaled_state_b64,
            "nms_base": nms_base,
            "http_code": 200,
        }

    if r.status_code == 403:
        detail = (r.text or "")[:200]
        write_last_register(
            last_register_file=last_register_file,
            time_fmt=time_fmt,
            ts_func=ts_func,
            status="blocked",
            detail=detail,
            http_code=403,
            mac=mac,
            ip=ip,
        )
        return 7, {
            "status": "blocked",
            "detail": detail,
            "mac": mac,
            "ip": ip,
            "scanner": "",
            "llm_weblink": "",
            "nms_base": nms_base,
            "http_code": 403,
        }

    detail = (r.text or "")[:200]
    write_last_register(
        last_register_file=last_register_file,
        time_fmt=time_fmt,
        ts_func=ts_func,
        status="error",
        detail=detail,
        http_code=r.status_code,
        mac=mac,
        ip=ip,
    )
    return 8, {
        "status": "error",
        "detail": detail,
        "mac": mac,
        "ip": ip,
        "scanner": "",
        "llm_weblink": "",
        "nms_base": nms_base,
        "http_code": r.status_code,
    }
