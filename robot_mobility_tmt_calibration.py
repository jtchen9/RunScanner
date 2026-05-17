#!/usr/bin/env python3
# python3 robot_mobility_tmt_calibration.py --pre 90 --distance 0.5 --post -90 -b
import argparse
import json
import re
import time
from datetime import datetime

from robot_mobility_motion import (
    turn_left,
    turn_right,
    move_forward,
    move_backward,
)

YAW_RE = re.compile(r"measured_yaw_deg=([-+]?\d+(?:\.\d+)?)")


def parse_raw_yaw(detail: str):
    m = YAW_RE.search(detail or "")
    return float(m.group(1)) if m else None


def run_turn(angle_deg: float):
    if angle_deg == 0:
        return True, {
            "cmd_angle_deg": 0.0,
            "direction": "none",
            "raw_yaw_deg": 0.0,
            "actual_turn_deg": 0.0,
            "error_deg": 0.0,
            "detail": "turn_skipped",
        }

    if angle_deg > 0:
        ok, detail = turn_left(abs(angle_deg))
    else:
        ok, detail = turn_right(abs(angle_deg))

    raw_yaw = parse_raw_yaw(detail)

    # robot_mobility_motion convention:
    # left turn => raw yaw negative
    # right turn => raw yaw positive
    # convert to command convention:
    # positive = left, negative = right
    actual_turn = -raw_yaw if raw_yaw is not None else None
    error = actual_turn - angle_deg if actual_turn is not None else None

    return ok, {
        "cmd_angle_deg": angle_deg,
        "direction": "left" if angle_deg > 0 else "right",
        "raw_yaw_deg": raw_yaw,
        "actual_turn_deg": actual_turn,
        "error_deg": error,
        "detail": detail,
    }


def run_move(direction: str, distance_m: float):
    if direction == "forward":
        ok, detail = move_forward(distance_m)
    elif direction == "backward":
        ok, detail = move_backward(distance_m)
    else:
        return False, {
            "direction": direction,
            "distance_m": distance_m,
            "detail": "BAD_COMMAND_ARGS direction must be forward/backward",
        }

    return ok, {
        "direction": direction,
        "distance_m": distance_m,
        "detail": detail,
    }


def run_tmt(pre_angle: float, distance_m: float, post_angle: float,
            direction: str, gap_sec: float):
    result = {
        "ok": False,
        "time": datetime.now().isoformat(timespec="seconds"),
        "pre_angle_deg": pre_angle,
        "distance_m": distance_m,
        "post_angle_deg": post_angle,
        "direction": direction,
        "gap_sec": gap_sec,
        "pre_turn": None,
        "move": None,
        "post_turn": None,
        "failed_stage": "",
        "status": "error",
    }

    ok_pre, pre = run_turn(pre_angle)
    result["pre_turn"] = pre
    if not ok_pre:
        result["failed_stage"] = "pre_turn"
        return result

    time.sleep(gap_sec)

    ok_move, move = run_move(direction, distance_m)
    result["move"] = move
    if not ok_move:
        result["failed_stage"] = "move"
        return result

    time.sleep(gap_sec)

    ok_post, post = run_turn(post_angle)
    result["post_turn"] = post
    if not ok_post:
        result["failed_stage"] = "post_turn"
        return result

    result["ok"] = True
    result["status"] = "ok"
    return result


def print_brief(r: dict):
    pre = r.get("pre_turn") or {}
    post = r.get("post_turn") or {}
    mv = r.get("move") or {}

    print(
        f"ok={r.get('ok')} "
        f"dir={r.get('direction')} "
        f"pre={r.get('pre_angle_deg')} "
        f"pre_actual={pre.get('actual_turn_deg')} "
        f"dist={r.get('distance_m')} "
        f"post={r.get('post_angle_deg')} "
        f"post_actual={post.get('actual_turn_deg')} "
        f"failed={r.get('failed_stage') or '-'}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", type=float, required=True)
    ap.add_argument("--distance", type=float, required=True)
    ap.add_argument("--post", type=float, required=True)
    ap.add_argument("--direction", choices=["forward", "backward"], default="forward")
    ap.add_argument("--gap-sec", type=float, default=0.3)
    ap.add_argument("-b", "--brief", action="store_true")
    args = ap.parse_args()

    result = run_tmt(
        pre_angle=args.pre,
        distance_m=args.distance,
        post_angle=args.post,
        direction=args.direction,
        gap_sec=args.gap_sec,
    )

    if args.brief:
        print_brief(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()