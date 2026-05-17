#!/usr/bin/env python3
import argparse
import csv
import json
import re
import sys
from datetime import datetime

from robot_mobility_motion import turn_left, turn_right


MEASURED_RE = re.compile(r"measured_yaw_deg=([-+]?\d+(?:\.\d+)?)")


def parse_measured_yaw(detail: str):
    m = MEASURED_RE.search(detail or "")
    if not m:
        return None
    return float(m.group(1))


def run_turn_calibration(angle_deg: float, trial: str = "", rep: str = "") -> dict:
    """
    Command convention:
      +angle = left turn
      -angle = right turn

    Internal gyro convention in robot_mobility_motion.py:
      left turn produces negative measured_yaw_deg
      right turn produces positive measured_yaw_deg

    Therefore:
      actual_turn_deg = -measured_yaw_deg
    so that positive means left, negative means right.
    """

    if angle_deg == 0:
        return {
            "ok": False,
            "trial": trial,
            "rep": rep,
            "cmd_angle_deg": angle_deg,
            "direction": "none",
            "raw_measured_yaw_deg": None,
            "actual_turn_deg": None,
            "error_deg": None,
            "status": "BAD_COMMAND_ARGS",
            "detail": "angle_deg cannot be 0",
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    if angle_deg > 0:
        direction = "left"
        ok, detail = turn_left(abs(angle_deg))
    else:
        direction = "right"
        ok, detail = turn_right(abs(angle_deg))

    raw_yaw = parse_measured_yaw(detail)

    if raw_yaw is None:
        actual_turn = None
        error = None
    else:
        actual_turn = -raw_yaw
        error = actual_turn - angle_deg

    return {
        "ok": bool(ok),
        "trial": trial,
        "rep": rep,
        "cmd_angle_deg": angle_deg,
        "direction": direction,
        "raw_measured_yaw_deg": raw_yaw,
        "actual_turn_deg": actual_turn,
        "error_deg": error,
        "status": "ok" if ok else "error",
        "detail": detail,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


def print_csv_row(result: dict, header: bool = False) -> None:
    fields = [
        "trial",
        "rep",
        "cmd_angle_deg",
        "direction",
        "ok",
        "raw_measured_yaw_deg",
        "actual_turn_deg",
        "error_deg",
        "status",
        "detail",
        "time",
    ]

    writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
    if header:
        writer.writeheader()
    writer.writerow(result)


def print_brief(result: dict) -> None:
    print(
        f"trial={result.get('trial')} "
        f"rep={result.get('rep')} "
        f"cmd={result.get('cmd_angle_deg'):.1f} "
        f"dir={result.get('direction')} "
        f"ok={result.get('ok')} "
        f"raw_yaw={result.get('raw_measured_yaw_deg')} "
        f"actual={result.get('actual_turn_deg')} "
        f"err={result.get('error_deg')} "
        f"status={result.get('status')}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--angle", type=float, required=True,
                    help="Signed command angle: positive=left, negative=right")
    ap.add_argument("--trial", default="", help="Trial ID, e.g. T1-01")
    ap.add_argument("--rep", default="", help="Repetition number")
    ap.add_argument("-b", "--brief", action="store_true",
                    help="Brief one-line output")
    ap.add_argument("--csv", action="store_true",
                    help="CSV row output")
    ap.add_argument("--header", action="store_true",
                    help="Print CSV header before row")
    args = ap.parse_args()

    result = run_turn_calibration(
        angle_deg=args.angle,
        trial=args.trial,
        rep=args.rep,
    )

    if args.csv:
        print_csv_row(result, header=args.header)
    elif args.brief:
        print_brief(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
    