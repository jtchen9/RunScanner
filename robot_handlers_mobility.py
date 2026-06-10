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

# Small settle gap between consecutive sub-actions in turn-move-turn
TURN_MOVE_TURN_GAP_SEC = 0.2

ANGLE_EPS_DEG = 0.1

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


def _parse_signed_angle(args: Dict[str, Any], field_name: str) -> Tuple[bool, float, str]:
    try:
        angle_deg = float(args.get(field_name))
        return True, angle_deg, ""
    except Exception:
        return False, 0.0, f"BAD_COMMAND_ARGS missing_or_invalid {field_name}"


def _zero_small_angle(angle_deg: float) -> float:
    try:
        a = float(angle_deg)
    except Exception:
        return 0.0
    return 0.0 if abs(a) < ANGLE_EPS_DEG else a


def _run_signed_turn(angle_deg: float) -> Tuple[bool, str]:
    """
    Positive angle => left turn
    Negative angle => right turn
    Near-zero angle => no-op success
    """
    angle_deg = _zero_small_angle(angle_deg)

    if angle_deg > 0:
        return turn_left(abs(angle_deg))

    if angle_deg < 0:
        return turn_right(abs(angle_deg))

    return True, "turn_noop near_zero_angle"


def _run_move_direction(forward: bool, distance_m: float) -> Tuple[bool, str]:
    return move_forward(distance_m) if forward else move_backward(distance_m)


def _finalize_with_location() -> Tuple[bool, str]:
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
    return True, "location_capture_done"


def _execute_turn_only(
    command_name: str,
    args: Dict[str, Any],
    angle_deg: float,
) -> Tuple[bool, str]:
    with _LOCK:
        if _is_busy():
            return _fail_immediate("MOBILITY_BUSY", f"{command_name} ignored while busy")
        _set_busy(command_name, args)

    try:
        ok_turn, turn_detail = _run_signed_turn(angle_deg)
        if not ok_turn:
            _finish_state(
                exec_status="error",
                error_code="TURN_EXEC_FAIL",
                error_detail=turn_detail,
                location_result=None,
            )
            return False, turn_detail

        ok_final, final_detail = _finalize_with_location()
        if not ok_final:
            return False, final_detail

        return True, turn_detail

    except Exception as e:
        _finish_state(
            exec_status="error",
            error_code="UNEXPECTED_EXCEPTION",
            error_detail=str(e),
            location_result=None,
        )
        return False, f"UNEXPECTED_EXCEPTION {e}"


def _execute_turn_move_turn(
    command_name: str,
    args: Dict[str, Any],
    forward: bool,
    pre_angle: float,
    distance_m: float,
    post_angle: float,
) -> Tuple[bool, str]:
    with _LOCK:
        if _is_busy():
            return _fail_immediate("MOBILITY_BUSY", f"{command_name} ignored while busy")
        _set_busy(command_name, args)

        print(
            f"[mobility] TMT received command={command_name} "
            f"pre_angle={pre_angle!r} distance_m={distance_m!r} post_angle={post_angle!r}",
            flush=True,
        )

    try:
        # 1) pre-turn
        ok_pre, pre_detail = _run_signed_turn(pre_angle)
        print(f"[mobility] TMT pre_turn result ok={ok_pre} detail={pre_detail!r}", flush=True)

        if not ok_pre:
            _finish_state(
                exec_status="error",
                error_code="TURN_EXEC_FAIL",
                error_detail=f"pre_turn_failed: {pre_detail}",
                location_result=None,
            )
            return False, f"pre_turn_failed: {pre_detail}"

        time.sleep(TURN_MOVE_TURN_GAP_SEC)

        # 2) move
        ok_move, move_detail = _run_move_direction(forward=forward, distance_m=distance_m)
        if not ok_move:
            if move_detail.startswith("COLLISION_BLOCKED_AT_START"):
                err_code = "COLLISION_BLOCKED_AT_START"
            elif move_detail.startswith("COLLISION_STOP_DURING_MOVE"):
                err_code = "COLLISION_STOP_DURING_MOVE"
            elif move_detail.startswith("TOF_SENSOR_FAIL"):
                err_code = "TOF_SENSOR_FAIL"
            else:
                err_code = "MOVE_EXEC_FAIL"

            _finish_state(
                exec_status="error",
                error_code=err_code,
                error_detail=move_detail,
                location_result=None,
            )
            return False, move_detail


        time.sleep(TURN_MOVE_TURN_GAP_SEC)

        # 3) post-turn
        ok_post, post_detail = _run_signed_turn(post_angle)
        print(f"[mobility] TMT post_turn result ok={ok_post} detail={post_detail!r}", flush=True)
        
        if not ok_post:
            _finish_state(
                exec_status="error",
                error_code="TURN_EXEC_FAIL",
                error_detail=f"post_turn_failed: {post_detail}",
                location_result=None,
            )
            return False, f"post_turn_failed: {post_detail}"

        # 4) one location capture at the very end
        ok_final, final_detail = _finalize_with_location()
        if not ok_final:
            return False, final_detail

        direction = "forward" if forward else "backward"
        return True, (
            f"turn_move_turn_{direction}_done "
            f"pre_angle={pre_angle:.3f} "
            f"distance_m={distance_m:.3f} "
            f"post_angle={post_angle:.3f}"
        )

    except Exception as e:
        _finish_state(
            exec_status="error",
            error_code="UNEXPECTED_EXCEPTION",
            error_detail=str(e),
            location_result=None,
        )
        return False, f"UNEXPECTED_EXCEPTION {e}"


def exec_mobility_turn(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok, angle_deg, detail = _parse_signed_angle(args, "angle_deg")
    if not ok:
        return _fail_immediate("BAD_COMMAND_ARGS", detail)

    return _execute_turn_only(
        command_name="mobility.turn",
        args=args,
        angle_deg=angle_deg,
    )


def exec_mobility_turn_move_turn_forward(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok_pre, pre_angle, detail_pre = _parse_signed_angle(args, "pre_angle")
    if not ok_pre:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_pre)

    ok_dist, distance_m, detail_dist = _parse_distance(args)
    if not ok_dist:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_dist)

    ok_post, post_angle, detail_post = _parse_signed_angle(args, "post_angle")
    if not ok_post:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_post)

    pre_angle = _zero_small_angle(pre_angle)
    post_angle = _zero_small_angle(post_angle)

    return _execute_turn_move_turn(
        command_name="mobility.turn_move_turn.forward",
        args=args,
        forward=True,
        pre_angle=pre_angle,
        distance_m=distance_m,
        post_angle=post_angle,
    )


def exec_mobility_turn_move_turn_backward(args: Dict[str, Any]) -> Tuple[bool, str]:
    ok_pre, pre_angle, detail_pre = _parse_signed_angle(args, "pre_angle")
    if not ok_pre:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_pre)

    ok_dist, distance_m, detail_dist = _parse_distance(args)
    if not ok_dist:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_dist)

    ok_post, post_angle, detail_post = _parse_signed_angle(args, "post_angle")
    if not ok_post:
        return _fail_immediate("BAD_COMMAND_ARGS", detail_post)

    pre_angle = _zero_small_angle(pre_angle)
    post_angle = _zero_small_angle(post_angle)

    return _execute_turn_move_turn(
        command_name="mobility.turn_move_turn.backward",
        args=args,
        forward=False,
        pre_angle=pre_angle,
        distance_m=distance_m,
        post_angle=post_angle,
    )


def exec_mobility_report_location(args: Dict[str, Any]) -> Tuple[bool, str]:
    with _LOCK:
        if _is_busy():
            return _fail_immediate("MOBILITY_BUSY", "mobility.report.location ignored while busy")
        _set_busy("mobility.report.location", args)

    try:
        ok_final, final_detail = _finalize_with_location()
        if not ok_final:
            return False, final_detail
        return True, "location_report_done"

    except Exception as e:
        _finish_state(
            exec_status="error",
            error_code="UNEXPECTED_EXCEPTION",
            error_detail=str(e),
            location_result=None,
        )
        return False, f"UNEXPECTED_EXCEPTION {e}"
