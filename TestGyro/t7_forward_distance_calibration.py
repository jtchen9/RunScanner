#!/usr/bin/env python3
"""Production-equivalent forward-distance calibration for AutoLab robots.

Examples
--------
Test the currently deployed end-to-end calibration for a requested 1 metre::

    python3 t7_forward_distance_calibration.py run \
        --mode calibrated --distance-m 1.0 --buck-voltage-v 9.0

Collect a raw calibration point.  In raw mode, --distance-m is the motor
distance used to calculate cruise time; the configured distance regression is
bypassed, while all other deployed forward-motion behavior remains active::

    python3 t7_forward_distance_calibration.py run \
        --mode raw --distance-m 0.50 --buck-voltage-v 9.0

Measure the fixed kick alone::

    python3 t7_forward_distance_calibration.py run \
        --mode raw --distance-m 0 --buck-voltage-v 9.0

Fit raw points after measurements have been collected::

    python3 t7_forward_distance_calibration.py fit \
        --min-motor-distance-m 0.15

The script deliberately calls robot_mobility_motion.move_forward() rather than
duplicating its motor algorithm.  Thus kick timing, cruise timing, heading
hold, ToF handling, motor mapping, and deployed constants remain identical.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion


DEFAULT_CSV_PATH = SCRIPT_DIR / "distance_calibration.csv"
DETAIL_FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")

CSV_FIELDS = [
    "recorded_at_utc",
    "scanner",
    "mode",
    "input_distance_m",
    "requested_distance_passed_to_motion_m",
    "motor_distance_m",
    "kick_time_sec",
    "cruise_time_sec",
    "total_powered_time_sec",
    "kick_speed",
    "cruise_speed",
    "gz_bias",
    "buck_voltage_v",
    "heading_hold_kp",
    "heading_hold_max_correction",
    "heading_hold_deadband_deg",
    "reported_final_yaw_deg",
    "reported_max_abs_yaw_deg",
    "actual_forward_distance_m",
    "actual_lateral_shift_m",
    "actual_heading_change_deg",
    "execution_ok",
    "execution_detail",
    "notes",
]


def _scanner_name() -> str:
    path = ROBOT_ROOT / "scanner_name.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _parse_detail(detail: str) -> Dict[str, str]:
    return {key: value for key, value in DETAIL_FIELD_RE.findall(detail or "")}


def _optional_float(prompt: str) -> Optional[float]:
    while True:
        value = input(prompt).strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            print("Enter a number, or press Enter to leave this field blank.")


def _append_csv(path: Path, row: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def _validate_run_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.distance_m) or args.distance_m < 0.0:
        raise ValueError("--distance-m must be finite and >= 0")
    if args.distance_m > motion.MAX_MOVE_DISTANCE_M:
        raise ValueError(
            f"--distance-m must be <= {motion.MAX_MOVE_DISTANCE_M:.3f}"
        )
    if args.mode == "calibrated" and args.distance_m < motion.MIN_MOVE_DISTANCE_M:
        raise ValueError(
            "calibrated mode requires --distance-m >= "
            f"{motion.MIN_MOVE_DISTANCE_M:.3f}"
        )
    if args.gz_bias is not None and not math.isfinite(args.gz_bias):
        raise ValueError("--gz-bias must be finite")


def _run(args: argparse.Namespace) -> int:
    _validate_run_args(args)

    if args.gz_bias is not None:
        motion.GZ_BIAS = float(args.gz_bias)

    input_distance_m = float(args.distance_m)
    requested_for_motion_m = input_distance_m
    original_calibration = motion.apply_motor_move_calibration

    if args.mode == "raw":
        raw_motor_distance_m = input_distance_m

        # _run_move validates the public requested distance before applying
        # calibration.  Use the minimum legal request for the special raw-zero
        # kick-only test, while forcing calibrated motor distance to zero.
        requested_for_motion_m = max(
            raw_motor_distance_m,
            motion.MIN_MOVE_DISTANCE_M,
        )
        motion.apply_motor_move_calibration = (
            lambda _requested_m: raw_motor_distance_m
        )
        predicted_motor_distance_m = raw_motor_distance_m
    else:
        predicted_motor_distance_m = float(
            original_calibration(requested_for_motion_m)
        )

    predicted_cruise_time_sec = (
        predicted_motor_distance_m * motion.MOVE_SEC_PER_METER
    )
    predicted_total_powered_time_sec = (
        motion.MOVE_KICK_TIME_SEC + predicted_cruise_time_sec
    )

    print("============================================================")
    print("Production-equivalent forward calibration run")
    print("============================================================")
    print(f"Mode:                         {args.mode}")
    print(f"Input distance:               {input_distance_m:.6f} m")
    print(
        "Distance passed to move:      "
        f"{requested_for_motion_m:.6f} m"
    )
    print(
        "Motor distance for timing:    "
        f"{predicted_motor_distance_m:.6f} m"
    )
    print(
        "Kick/cruise/total time:       "
        f"{motion.MOVE_KICK_TIME_SEC:.3f} / "
        f"{predicted_cruise_time_sec:.3f} / "
        f"{predicted_total_powered_time_sec:.3f} s"
    )
    print(
        "Kick/cruise speed:            "
        f"{motion.MOVE_KICK_SPEED} / {motion.MOVE_CRUISE_SPEED}"
    )
    print(f"GZ_BIAS:                      {motion.GZ_BIAS}")
    if args.dry_run:
        print("Dry run requested; motors were not started.")
        motion.apply_motor_move_calibration = original_calibration
        return 0

    if not args.yes:
        answer = input(
            "Confirm the path is clear and run one forward movement [y/N]: "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled; motors were not started.")
            motion.apply_motor_move_calibration = original_calibration
            return 2

    try:
        ok, detail = motion.move_forward(requested_for_motion_m)
    finally:
        motion.apply_motor_move_calibration = original_calibration

    print(f"Execution ok:                 {ok}")
    print(f"Execution detail:             {detail}")

    parsed = _parse_detail(detail)
    row: Dict[str, object] = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scanner": _scanner_name(),
        "mode": args.mode,
        "input_distance_m": input_distance_m,
        "requested_distance_passed_to_motion_m": requested_for_motion_m,
        "motor_distance_m": parsed.get(
            "motor_distance_m",
            predicted_motor_distance_m,
        ),
        "kick_time_sec": parsed.get(
            "kick_time",
            motion.MOVE_KICK_TIME_SEC,
        ),
        "cruise_time_sec": parsed.get(
            "cruise_time",
            predicted_cruise_time_sec,
        ),
        "total_powered_time_sec": predicted_total_powered_time_sec,
        "kick_speed": motion.MOVE_KICK_SPEED,
        "cruise_speed": parsed.get(
            "cruise_speed",
            motion.MOVE_CRUISE_SPEED,
        ),
        "gz_bias": motion.GZ_BIAS,
        "buck_voltage_v": "" if args.buck_voltage_v is None else args.buck_voltage_v,
        "heading_hold_kp": motion.HEADING_HOLD_KP,
        "heading_hold_max_correction": motion.HEADING_HOLD_MAX_CORRECTION,
        "heading_hold_deadband_deg": motion.HEADING_HOLD_DEADBAND_DEG,
        "reported_final_yaw_deg": parsed.get("final_yaw_deg", ""),
        "reported_max_abs_yaw_deg": parsed.get("max_abs_yaw_deg", ""),
        "execution_ok": ok,
        "execution_detail": detail,
        "notes": args.notes or "",
    }

    if args.no_record:
        print("CSV recording disabled by --no-record.")
        return 0 if ok else 1

    if ok:
        print("\nMeasure displacement relative to the intended straight path.")
        row["actual_forward_distance_m"] = _optional_float(
            "Actual forward displacement in metres (blank to omit): "
        )
        row["actual_lateral_shift_m"] = _optional_float(
            "Signed lateral shift in metres; right positive (blank to omit): "
        )
        row["actual_heading_change_deg"] = _optional_float(
            "Actual final heading change in degrees (blank to omit): "
        )

    csv_path = Path(args.csv).expanduser().resolve()
    _append_csv(csv_path, row)
    print(f"Recorded: {csv_path}")
    return 0 if ok else 1


def _read_fit_points(
    csv_path: Path,
    min_motor_distance_m: float,
    max_motor_distance_m: Optional[float],
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("mode", "")).strip().lower() != "raw":
                continue
            if str(row.get("execution_ok", "")).strip().lower() not in {
                "true",
                "1",
                "yes",
            }:
                continue
            try:
                motor_distance = float(row["motor_distance_m"])
                actual_distance = float(row["actual_forward_distance_m"])
            except (KeyError, TypeError, ValueError):
                continue
            if motor_distance < min_motor_distance_m:
                continue
            if (
                max_motor_distance_m is not None
                and motor_distance > max_motor_distance_m
            ):
                continue
            points.append((motor_distance, actual_distance))
    return points


def _linear_fit(points: Iterable[Tuple[float, float]]) -> Dict[str, float]:
    values = list(points)
    n = len(values)
    if n < 2:
        raise ValueError("at least two usable raw measurement rows are required")

    mean_x = sum(x for x, _y in values) / n
    mean_y = sum(y for _x, y in values) / n
    sxx = sum((x - mean_x) ** 2 for x, _y in values)
    if sxx <= 0.0:
        raise ValueError("raw motor-distance values must not all be identical")

    sxy = sum((x - mean_x) * (y - mean_y) for x, y in values)
    actual_a = sxy / sxx
    actual_b = mean_y - actual_a * mean_x
    if actual_a <= 0.0:
        raise ValueError(f"fitted slope must be positive, got {actual_a}")

    residuals = [y - (actual_a * x + actual_b) for x, y in values]
    rmse = math.sqrt(sum(r * r for r in residuals) / n)
    syy = sum((y - mean_y) ** 2 for _x, y in values)
    r_squared = 1.0 - (sum(r * r for r in residuals) / syy) if syy > 0 else 1.0

    return {
        "n": float(n),
        "actual_a": actual_a,
        "actual_b": actual_b,
        "cmd_a": 1.0 / actual_a,
        "cmd_b": -actual_b / actual_a,
        "rmse_m": rmse,
        "r_squared": r_squared,
    }


def _fit(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv).expanduser().resolve()
    points = _read_fit_points(
        csv_path,
        args.min_motor_distance_m,
        args.max_motor_distance_m,
    )
    result = _linear_fit(points)

    print("============================================================")
    print("Raw motor-distance calibration fit")
    print("============================================================")
    print(f"CSV:          {csv_path}")
    print(f"Points:       {int(result['n'])}")
    print(
        "Actual model: actual_distance_m = "
        f"{result['actual_a']:.15g} * motor_distance_m + "
        f"{result['actual_b']:.15g}"
    )
    print(
        "Inverse model: motor_distance_m = "
        f"{result['cmd_a']:.15g} * desired_distance_m + "
        f"{result['cmd_b']:.15g}"
    )
    print(f"RMSE:         {result['rmse_m']:.6f} m")
    print(f"R-squared:    {result['r_squared']:.6f}")
    print("\nSuggested configuration block:")
    print("MOTOR_MOVE_DISTANCE_MODEL = " + json.dumps(
        {
            "actual_a": result["actual_a"],
            "actual_b": result["actual_b"],
            "cmd_a": result["cmd_a"],
            "cmd_b": result["cmd_b"],
            "source": f"{csv_path.name} production-equivalent raw fit",
        },
        indent=4,
    ))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run one forward movement")
    run_parser.add_argument(
        "--mode",
        choices=("calibrated", "raw"),
        required=True,
        help="use deployed calibration, or bypass it with a raw motor distance",
    )
    run_parser.add_argument(
        "--distance-m",
        type=float,
        required=True,
        help="desired distance in calibrated mode; motor distance in raw mode",
    )
    run_parser.add_argument("--gz-bias", type=float, default=None)
    run_parser.add_argument("--buck-voltage-v", type=float, default=None)
    run_parser.add_argument("--notes", default="")
    run_parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH))
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the final path-clear confirmation",
    )
    run_parser.add_argument(
        "--no-record",
        action="store_true",
        help="do not prompt for measurements or append CSV",
    )
    run_parser.set_defaults(func=_run)

    fit_parser = subparsers.add_parser("fit", help="fit recorded raw points")
    fit_parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH))
    fit_parser.add_argument("--min-motor-distance-m", type=float, default=0.0)
    fit_parser.add_argument("--max-motor-distance-m", type=float, default=None)
    fit_parser.set_defaults(func=_fit)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
