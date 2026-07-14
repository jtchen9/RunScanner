#!/usr/bin/env python3
# python3 robot_mobility_move_calibration.py --distance 0.4 --move-profile bump_crossing -b
# 
# To debug:
# sudo i2cdetect -y 1
# python3 - << 'PY'
# from robot_mobility_vl53l1x import read_distance_mm
# for i in range(10):
#     print(i, read_distance_mm())
# PY

import argparse
import csv
import json
import re
import sys
from datetime import datetime

from robot_mobility_motion import (
    move_forward,
    move_backward,
)


DIST_RE = re.compile(r"distance_m=([-+]?\d+(?:\.\d+)?)")
CRUISE_RE = re.compile(r"cruise_time=([-+]?\d+(?:\.\d+)?)")


def parse_distance(detail: str):
    m = DIST_RE.search(detail or "")
    if not m:
        return None
    return float(m.group(1))


def parse_cruise_time(detail: str):
    m = CRUISE_RE.search(detail or "")
    if not m:
        return None
    return float(m.group(1))


def run_move_calibration(distance_m: float,
                         trial: str = "",
                         rep: str = "",
                         move_profile: str = "default") -> dict:

    if distance_m == 0:
        return {
            "ok": False,
            "trial": trial,
            "rep": rep,
            "cmd_distance_m": distance_m,
            "direction": "none",
            "move_profile": move_profile,
            "reported_distance_m": None,
            "cruise_time_sec": None,
            "status": "BAD_COMMAND_ARGS",
            "detail": "distance_m cannot be 0",
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    if distance_m > 0:
        direction = "forward"
        ok, detail = move_forward(abs(distance_m), move_profile=move_profile)
    else:
        direction = "backward"
        ok, detail = move_backward(abs(distance_m), move_profile=move_profile)

    reported_distance = parse_distance(detail)
    cruise_time = parse_cruise_time(detail)

    return {
        "ok": bool(ok),
        "trial": trial,
        "rep": rep,
        "cmd_distance_m": distance_m,
        "direction": direction,
        "move_profile": move_profile,
        "reported_distance_m": reported_distance,
        "cruise_time_sec": cruise_time,
        "status": "ok" if ok else "error",
        "detail": detail,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


def print_brief(result: dict) -> None:
    print(
        f"trial={result.get('trial')} "
        f"rep={result.get('rep')} "
        f"cmd={result.get('cmd_distance_m'):.3f} "
        f"dir={result.get('direction')} "
        f"profile={result.get('move_profile')} "
        f"ok={result.get('ok')} "
        f"reported={result.get('reported_distance_m')} "
        f"cruise={result.get('cruise_time_sec')} "
        f"status={result.get('status')}"
    )


def print_csv_row(result: dict, header: bool = False) -> None:
    fields = [
        "trial",
        "rep",
        "cmd_distance_m",
        "direction",
        "move_profile",
        "ok",
        "reported_distance_m",
        "cruise_time_sec",
        "status",
        "detail",
        "time",
    ]

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=fields,
        extrasaction="ignore",
    )

    if header:
        writer.writeheader()

    writer.writerow(result)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--distance",
        type=float,
        required=True,
        help="Signed command distance: positive=forward negative=backward",
    )

    ap.add_argument("--trial", default="")
    ap.add_argument("--rep", default="")

    ap.add_argument(
        "--move-profile",
        choices=["default", "bump_crossing"],
        default="default",
        help="Movement profile passed to robot_mobility_motion",
    )

    ap.add_argument(
        "-b",
        "--brief",
        action="store_true",
        help="Brief one-line output",
    )

    ap.add_argument(
        "--csv",
        action="store_true",
        help="CSV row output",
    )

    ap.add_argument(
        "--header",
        action="store_true",
        help="Print CSV header",
    )

    args = ap.parse_args()

    result = run_move_calibration(
        distance_m=args.distance,
        trial=args.trial,
        rep=args.rep,
        move_profile=args.move_profile,
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
