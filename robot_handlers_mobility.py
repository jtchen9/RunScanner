#!/usr/bin/env python3
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from robot_mobility_motion import (
    move_forward,
    move_backward,
    turn_left,
    turn_right,
)

PYTHON = "/usr/bin/python3"
LOCATION_CAPTURE_SCRIPT = "/opt/_RunScanner/robot_mobility_location_capture.py"

STATE_PATH = Path("/tmp/mobility_state.json")

_LOCK = threading.Lock()


def _now_ts() -> float:
    return time.time()


def _default_state() -> Dict[str, Any]:
    return {
        "busy": False,
        "last_command": "",
        "last_command_args": {},
        "last_command_received_ts": 0.0,
        "last_command_finished_ts": 0.0,
        "last_exec_status": "",
        "last_error_code": "",
        "last_error_detail": "",
        "last_location_result": None,
        "pending_report": False,
    }


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return _default_state()


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_busy(command: str, args: Dict[str, Any]) -> None:
    state = _load_state()
    state["busy"] = True
    state["last_command"] = command
    state["last_command_args"] = args
    state["last_command_received_ts"] = _now_ts()
    state["last_exec_status"] = "accepted"
    state["last_error_code"] = ""
    state["last_error_detail"] = ""
    state["last_location_result"] = None
    state["pending_report"] = False
    _save_state(state)


def _finish_state(
    exec_status: str,
    error_code: str,
    error_detail: str,
    location_result: Dict[str, Any] | None,
) -> None:
    state = _load_state()
    state["busy"] = False
    state["last_command_finished_ts"] = _now_ts()
    state["last_exec_status"] = exec_status
    state["last_error_code"] = error_code
    state["last_error_detail"] = error_detail
    state["last_location_result"] = location_result
    state["pending_report"] = True
    _save_state(state)


def _fail_immediate(error_code: str, error_detail: str) -> Tuple[bool, str]:
    state = _load_state()
    state["last_exec_status"] = "error"
    state["last_error_code"] = error_code
    state["last_error_detail"] = error_detail
    state["pending_report"] = True
    _save_state(state)
    return False, f"{error_code} {error_detail}"


def _is_busy() -> bool:
    return bool(_load_state().get("busy", False))


def _run_location_capture() -> Tuple[bool, Dict[str, Any]]:
    cp = subprocess.run(
        [PYTHON, LOCATION_CAPTURE_SCRIPT],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )

    raw = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {
            "ok": False,
            "error": "location_capture_output_not_json",
            "raw_stdout": raw,
            "raw_stderr": err,
        }

    if cp.returncode != 0:
        if "error" not in data:
            data["error"] = err or raw or "location_capture_failed"
        return False, data

    return bool(data.get("ok", False)), data


def _parse_distance(args: Dict[str, Any]) -> Tuple[bool, float, str]:
    try:
        distance_m = float(args.get("distance_m"))
        return True, distance_m, ""
    except Exception:
        return False, 0.0, "BAD_COMMAND_ARGS missing_or_invalid distance_m"


def _parse_angle(args: Dict[str, Any]) -> Tuple[bool, float, str]:
    try:
        angle_deg = float(args.get("angle_deg"))
        return True, angle_deg, ""
    except Exception:
        return False, 0.0, "BAD_COMMAND_ARGS missing_or_invalid angle_deg"


def _execute_with_location(
    command_name: str,
    args: Dict[str, Any],
    motion_func,
    motion_value: float,
) -> Tuple[bool, str]:
    with _LOCK:
        if _is_busy():
            return _fail_immediate("MOBILITY_BUSY", f"{command_name} ignored while busy")

        _set_busy(command_name, args)

    ok_motion = False
    motion_detail = ""

    try:
        ok_motion, motion_detail = motion_func(motion_value)
        if not ok_motion:
            _finish_state(
                exec_status="error",
                error_code="MOVE_EXEC_FAIL" if "move" in command_name else "TURN_EXEC_FAIL",
                error_detail=motion_detail,
                location_result=None,
            )
            return False, motion_detail

        ok_loc, loc_data = _run_location_capture()
        if not ok_loc:
            _finish_state(
                exec_status="error",
                error_code="LOCATION_CAPTURE_FAIL",
                error_detail=loc_data.get("error", "location_capture_failed"),
                location_result=loc_data,
            )
            return False, f"LOCATION_CAPTURE_FAIL {loc_data.get('error', '')}".strip()

        apriltag = loc_data.get("apriltag") or {}
        if apriltag.get("ok") and int(apriltag.get("count", 0)) == 0:
            _finish_state(
                exec_status="error",
                error_code="NO_TAG_VISIBLE",
                error_detail="apriltag count=0",
                location_result=loc_data,
            )
            return False, "NO_TAG_VISIBLE apriltag count=0"

        _finish_state(
            exec_status="completed",
            error_code="",
            error_detail="",
            location_result=loc_data,
        )
        return True, motion_detail

    except Exception as e:
        _finish_state(
            exec_status="error",
            error_code="UNEXPECTED_EXCEPTION",
            error_detail=str(e),
            location_result=None,
        )
        return False, f"UNEXPECTED_EXCEPTION {e}"


def exec_mobility_move_forward(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok, distance_m, detail = _parse_distance(args)
    if not ok:
        return _fail_immediate("BAD_COMMAND_ARGS", detail)

    return _execute_with_location(
        command_name="mobility.move.forward",
        args=args,
        motion_func=move_forward,
        motion_value=distance_m,
    )


def exec_mobility_move_backward(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok, distance_m, detail = _parse_distance(args)
    if not ok:
        return _fail_immediate("BAD_COMMAND_ARGS", detail)

    return _execute_with_location(
        command_name="mobility.move.backward",
        args=args,
        motion_func=move_backward,
        motion_value=distance_m,
    )


def exec_mobility_turn_left(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok, angle_deg, detail = _parse_angle(args)
    if not ok:
        return _fail_immediate("BAD_COMMAND_ARGS", detail)

    return _execute_with_location(
        command_name="mobility.turn.left",
        args=args,
        motion_func=turn_left,
        motion_value=angle_deg,
    )


def exec_mobility_turn_right(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok, angle_deg, detail = _parse_angle(args)
    if not ok:
        return _fail_immediate("BAD_COMMAND_ARGS", detail)

    return _execute_with_location(
        command_name="mobility.turn.right",
        args=args,
        motion_func=turn_right,
        motion_value=angle_deg,
    )


def exec_mobility_report_location(args: Dict[str, Any]) -> Tuple[bool, str]:
    with _LOCK:
        if _is_busy():
            return _fail_immediate("MOBILITY_BUSY", "mobility.report.location ignored while busy")

        _set_busy("mobility.report.location", args)

    try:
        ok_loc, loc_data = _run_location_capture()
        if not ok_loc:
            _finish_state(
                exec_status="error",
                error_code="LOCATION_CAPTURE_FAIL",
                error_detail=loc_data.get("error", "location_capture_failed"),
                location_result=loc_data,
            )
            return False, f"LOCATION_CAPTURE_FAIL {loc_data.get('error', '')}".strip()

        apriltag = loc_data.get("apriltag") or {}
        if apriltag.get("ok") and int(apriltag.get("count", 0)) == 0:
            _finish_state(
                exec_status="error",
                error_code="NO_TAG_VISIBLE",
                error_detail="apriltag count=0",
                location_result=loc_data,
            )
            return False, "NO_TAG_VISIBLE apriltag count=0"

        _finish_state(
            exec_status="completed",
            error_code="",
            error_detail="",
            location_result=loc_data,
        )
        return True, "location_report_done"

    except Exception as e:
        _finish_state(
            exec_status="error",
            error_code="UNEXPECTED_EXCEPTION",
            error_detail=str(e),
            location_result=None,
        )
        return False, f"UNEXPECTED_EXCEPTION {e}"
    