#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from config import get_nms_base, BASE_DIR, local_ts


IPERF3_BIN = "/usr/bin/iperf3"
TRAFFIC_SESSIONS_FILE = BASE_DIR / "traffic_sessions.json"
TRAFFIC_LOG_DIR = BASE_DIR / "traffic_logs"

AC_TO_TOS = {
    "vo": 184,   # DSCP 46 << 2
    "vi": 136,   # DSCP 34 << 2
    "be": 0,     # DSCP 0  << 2
    "bk": 32,    # DSCP 8  << 2
}


def _ensure_dirs() -> None:
    TRAFFIC_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_sessions() -> Dict[str, Any]:
    try:
        if TRAFFIC_SESSIONS_FILE.exists():
            return json.loads(TRAFFIC_SESSIONS_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        pass
    return {}


def _save_sessions(data: Dict[str, Any]) -> None:
    tmp = TRAFFIC_SESSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TRAFFIC_SESSIONS_FILE)


def _parse_positive_int(name: str, value: Any, default: int = 1) -> Tuple[bool, int, str]:
    if value in (None, ""):
        return True, default, ""
    try:
        x = int(value)
        if x <= 0:
            return False, 0, f"{name} must be > 0"
        return True, x, ""
    except Exception:
        return False, 0, f"{name} must be an integer"


def _validate_required_str(name: str, value: Any) -> Tuple[bool, str]:
    s = str(value or "").strip()
    if not s:
        return False, f"{name} missing"
    return True, s


def _session_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cleanup_dead_sessions() -> None:
    data = _load_sessions()
    changed = False

    for session_id in list(data.keys()):
        try:
            pid = int(data[session_id].get("pid") or 0)
        except Exception:
            pid = 0

        if pid <= 0 or not _session_is_alive(pid):
            data.pop(session_id, None)
            changed = True

    if changed:
        _save_sessions(data)


def _finalize_log_file(
    *,
    tmp_log_path: Path,
    final_log_path: Path,
    cmd: list,
    session_id: str,
    scanner: str,
) -> None:
    try:
        raw_text = tmp_log_path.read_text(encoding="utf-8").strip()

        try:
            iperf_json = json.loads(raw_text) if raw_text else {}
        except Exception:
            iperf_json = {"raw_output": raw_text}

        final_obj = {
            "meta": {
                "session_id": session_id,
                "scanner": scanner,
                "cmd": " ".join(cmd),
                "cmd_list": cmd,
                "generated_at": local_ts(),
            },
            "iperf3": iperf_json,
        }

        final_log_path.write_text(
            json.dumps(final_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    except Exception as e:
        try:
            final_log_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "session_id": session_id,
                            "scanner": scanner,
                            "cmd": " ".join(cmd),
                            "cmd_list": cmd,
                            "generated_at": local_ts(),
                            "error": f"finalize_failed: {e}",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    finally:
        try:
            if tmp_log_path.exists():
                tmp_log_path.unlink()
        except Exception:
            pass


def _spawn_finalize_watcher(
    *,
    proc: subprocess.Popen,
    tmp_log_path: Path,
    final_log_path: Path,
    cmd: list,
    session_id: str,
    scanner: str,
) -> None:
    def _watch() -> None:
        try:
            proc.wait()
        except Exception:
            pass
        finally:
            _finalize_log_file(
                tmp_log_path=tmp_log_path,
                final_log_path=final_log_path,
                cmd=cmd,
                session_id=session_id,
                scanner=scanner,
            )

    th = threading.Thread(
        target=_watch,
        daemon=True,
        name=f"traffic-finalize-{session_id}",
    )
    th.start()


def exec_traffic_session_start(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    _ensure_dirs()
    _cleanup_dead_sessions()

    ok, session_id = _validate_required_str("session_id", args.get("session_id"))
    if not ok:
        return False, session_id

    target_ip_raw = (args.get("target_ip") or "").strip()
    if target_ip_raw:
        target_ip = target_ip_raw
    else:
        nms_base = get_nms_base()
        if not nms_base:
            return False, "target_ip missing and cannot discover NMS"

        try:
            parsed = urlparse(nms_base)
            target_ip = parsed.hostname or ""
        except Exception:
            target_ip = ""

        if not target_ip:
            return False, f"failed to extract IP from nms_base={nms_base}"

    target_port_raw = args.get("target_port", 5201)
    ok, target_port, err = _parse_positive_int("target_port", target_port_raw, default=5201)
    if not ok:
        return False, err

    ac = str(args.get("ac") or "").strip().lower()
    if ac not in AC_TO_TOS:
        return False, "ac must be one of: vo, vi, be, bk"

    protocol = str(args.get("protocol", "udp")).strip().lower()
    if protocol not in ("udp", "tcp"):
        return False, "protocol must be 'udp' or 'tcp'"

    ok, duration_sec, err = _parse_positive_int("duration_sec", args.get("duration_sec"))
    if not ok:
        return False, err

    ok, report_interval_sec, err = _parse_positive_int(
        "report_interval_sec",
        args.get("report_interval_sec", 60),
        default=60,
    )
    if not ok:
        return False, err

    ok, parallel, err = _parse_positive_int("parallel", args.get("parallel", 1), default=1)
    if not ok:
        return False, err

    # UDP requires bitrate and packet_size.
    # TCP does not require them, but packet_size may still be passed through if provided.
    bitrate = str(args.get("bitrate") or "").strip()
    if protocol == "udp":
        if not bitrate:
            return False, "bitrate missing"

    packet_size = None
    packet_size_raw = args.get("packet_size")
    if protocol == "udp":
        ok, packet_size, err = _parse_positive_int("packet_size", packet_size_raw)
        if not ok:
            return False, err
    else:
        if packet_size_raw not in (None, ""):
            ok, packet_size, err = _parse_positive_int("packet_size", packet_size_raw)
            if not ok:
                return False, err

    protocol = str(args.get("protocol", "udp")).strip().lower()
    if protocol not in ("udp", "tcp"):
        return False, "protocol must be 'udp' or 'tcp'"

    if not Path(IPERF3_BIN).exists():
        return False, f"iperf3 not found at {IPERF3_BIN}"

    sessions = _load_sessions()
    if session_id in sessions:
        try:
            old_pid = int(sessions[session_id].get("pid") or 0)
        except Exception:
            old_pid = 0

        if old_pid > 0 and _session_is_alive(old_pid):
            return False, f"session_id already running: {session_id}"

        sessions.pop(session_id, None)
        _save_sessions(sessions)

    tos = AC_TO_TOS[ac]
    log_path = TRAFFIC_LOG_DIR / f"{session_id}.json"
    tmp_log_path = TRAFFIC_LOG_DIR / f"{session_id}.raw.json"

    cmd = [
        IPERF3_BIN,
        "-c", target_ip,
    ]

    if protocol == "udp":
        cmd += [
            "-u",
            "-b", bitrate,
        ]

    cmd += [
        "-t", str(duration_sec),
        "-P", str(parallel),
    ]

    if packet_size is not None:
        cmd += [
            "-l", str(packet_size),
        ]

    cmd += [
        "--tos", str(tos),
        "-p", str(target_port),
        "-i", str(report_interval_sec),
        "-J",
    ]

    try:
        with tmp_log_path.open("w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                text=True,
            )
    except Exception as e:
        return False, f"failed to start iperf3: {type(e).__name__}: {e}"

    _spawn_finalize_watcher(
        proc=proc,
        tmp_log_path=tmp_log_path,
        final_log_path=log_path,
        cmd=cmd,
        session_id=session_id,
        scanner=scanner,
    )

    sessions = _load_sessions()
    sessions[session_id] = {
        "session_id": session_id,
        "pid": proc.pid,
        "scanner": scanner,
        "target_ip": target_ip,
        "target_port": target_port,
        "ac": ac,
        "tos": tos,
        "bitrate": bitrate,
        "duration_sec": duration_sec,
        "report_interval_sec": report_interval_sec,
        "parallel": parallel,
        "packet_size": packet_size,
        "protocol": protocol,
        "direction": "uplink",
        "log_path": str(log_path),
        "raw_log_path": str(tmp_log_path),
        "start_time": local_ts(),
        "cmd": cmd,
        "state": "running",
    }
    _save_sessions(sessions)

    detail = (
        f"started session_id={session_id} pid={proc.pid} "
        f"protocol={protocol} ac={ac} tos={tos} "
        f"bitrate={bitrate or '-'} duration={duration_sec} "
        f"report_interval={report_interval_sec} "
        f"parallel={parallel} packet_size={packet_size if packet_size is not None else '-'} "
        f"target={target_ip}:{target_port}"
    )    
    return True, detail


def exec_traffic_session_stop(args: Dict[str, Any]) -> Tuple[bool, str]:
    _cleanup_dead_sessions()

    ok, session_id = _validate_required_str("session_id", args.get("session_id"))
    if not ok:
        return False, session_id

    sessions = _load_sessions()
    item = sessions.get(session_id)
    if not item:
        return False, f"session_id not found: {session_id}"

    try:
        pid = int(item.get("pid") or 0)
    except Exception:
        pid = 0

    if pid <= 0:
        sessions.pop(session_id, None)
        _save_sessions(sessions)
        return False, f"invalid pid for session_id={session_id}"

    stopped = False
    try:
        os.killpg(pid, signal.SIGTERM)
        stopped = True
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except Exception:
            stopped = False

    sessions.pop(session_id, None)
    _save_sessions(sessions)

    if stopped:
        return True, f"stopped session_id={session_id} pid={pid}"

    return False, f"failed to stop session_id={session_id} pid={pid}"
