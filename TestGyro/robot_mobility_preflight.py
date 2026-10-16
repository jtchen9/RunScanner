#!/usr/bin/env python3
"""Operator-assisted robot health gate before long mobility experiments.

This program exercises the same calibration loader and motion primitives used
by production.  It never modifies calibration.  Physical wheel, distance and
lateral-motion checks remain operator-confirmed because the robot has no wheel
encoders or independent ground-truth position sensor.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion
from robot_mobility_calibration_registry import (
    MobilityCalibrationSnapshot,
    load_mobility_calibration,
)


SCANNER_NAME_PATH = ROBOT_ROOT / "scanner_name.txt"
LOCATION_CAPTURE_SCRIPT = ROBOT_ROOT / "robot_mobility_location_capture.py"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "preflight"

GYRO_MEASURE_SEC = 5.0
GYRO_IGNORE_SEC = 1.0
GYRO_BIAS_DELTA_LIMIT = 0.15
GYRO_SPREAD_LIMIT = 0.20
MOTOR_HAT_TRIALS = 5
MOTOR_HAT_TRIAL_GAP_SEC = 0.20
LIFTED_WHEEL_RUN_SEC = 1.0
TURN_CYCLES = 4
TURN_SETTLE_SEC = 1.0

FORWARD_DISTANCE_M = 1.0
FORWARD_DISTANCE_LOW_CM = 90.0
FORWARD_DISTANCE_HIGH_CM = 110.0
FORWARD_HEADING_LIMIT_DEG = 5.0
FORWARD_LATERAL_LIMIT_CM = 10.0

BUMP_DISTANCE_LOW_CM = 185.0
BUMP_DISTANCE_HIGH_CM = 215.0


class PreflightFailure(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _yes_no(label: str, *, default_yes: bool = False) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        answer = input(f"{label} {suffix}: ").strip().lower()
        if not answer:
            return default_yes
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter Y or N.")


def _prompt_float(label: str, *, low: float, high: float) -> float:
    while True:
        answer = input(f"{label}: ").strip()
        try:
            value = float(answer)
        except ValueError:
            print("Enter a number.")
            continue
        if math.isfinite(value) and low <= value <= high:
            return value
        print(f"Enter a value from {low:g} through {high:g}.")


def _record(
    report: Dict[str, Any],
    name: str,
    passed: bool,
    **details: Any,
) -> None:
    report["checks"].append(
        {
            "name": name,
            "status": "pass" if passed else "fail",
            **details,
        }
    )
    print(f"{'PASS' if passed else 'FAIL'}: {name}")


def _require(
    report: Dict[str, Any],
    name: str,
    passed: bool,
    **details: Any,
) -> None:
    _record(report, name, passed, **details)
    if not passed:
        raise PreflightFailure(name)


def _save_report(report: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scanner = str(report.get("scanner") or "unknown").replace("/", "_")
    path = output_dir / f"preflight_{scanner}_{stamp}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _check_calibration(
    report: Dict[str, Any],
) -> MobilityCalibrationSnapshot:
    calibration = load_mobility_calibration()
    details: Dict[str, Any] = {
        "model": calibration.drivetrain_model,
        "gz_bias": calibration.gz_bias,
        "detail": calibration.detail(),
    }
    if calibration.uses_balanced_timed_v2:
        details.update(
            {
                "forward_speeds": list(calibration.move_speeds("default")),
                "forward_seconds_per_meter": calibration.move_seconds_per_meter(
                    "default"
                ),
                "turn_speed": calibration.turn_speed,
                "turn_ccw_stop_margin_deg": calibration.turn_ccw_stop_margin_deg,
                "turn_cw_stop_margin_deg": calibration.turn_cw_stop_margin_deg,
            }
        )
    _require(report, "production calibration loads", True, **details)
    return calibration


def _check_motor_hat(report: Dict[str, Any], *, label: str) -> None:
    attempts: List[Dict[str, Any]] = []
    passed = True
    for index in range(1, MOTOR_HAT_TRIALS + 1):
        ok, motor, detail = motion._motor_begin()
        attempts.append({"attempt": index, "ok": ok, "detail": detail})
        if motor is not None:
            motion._safe_stop(motor)
        if not ok:
            passed = False
        if index < MOTOR_HAT_TRIALS:
            time.sleep(MOTOR_HAT_TRIAL_GAP_SEC)
    _require(report, label, passed, attempts=attempts)


def _measure_gyro(calibration: MobilityCalibrationSnapshot) -> Dict[str, Any]:
    ok, imu, detail = motion._imu_begin()
    if not ok:
        raise PreflightFailure(detail)
    samples: List[float] = []
    started = time.monotonic()
    while time.monotonic() - started < GYRO_MEASURE_SEC:
        elapsed = time.monotonic() - started
        _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
        value = float(gz)
        if elapsed >= GYRO_IGNORE_SEC and math.isfinite(value):
            samples.append(value)
        time.sleep(0.02)
    if len(samples) < 20:
        raise PreflightFailure(f"too few valid gyro samples: {len(samples)}")
    ordered = sorted(samples)
    trim = int(len(ordered) * 0.10)
    trimmed = ordered[trim:-trim] if trim else ordered
    mean = statistics.fmean(trimmed)
    spread = statistics.pstdev(samples)
    return {
        "samples": len(samples),
        "mean_deg_per_sec": mean,
        "spread_deg_per_sec": spread,
        "stored_bias_deg_per_sec": calibration.gz_bias,
        "bias_delta_deg_per_sec": mean - calibration.gz_bias,
    }


def _check_gyro(
    report: Dict[str, Any],
    calibration: MobilityCalibrationSnapshot,
    *,
    label: str,
) -> None:
    result = _measure_gyro(calibration)
    passed = (
        abs(result["bias_delta_deg_per_sec"]) <= GYRO_BIAS_DELTA_LIMIT
        and result["spread_deg_per_sec"] <= GYRO_SPREAD_LIMIT
    )
    _require(
        report,
        label,
        passed,
        bias_delta_limit=GYRO_BIAS_DELTA_LIMIT,
        spread_limit=GYRO_SPREAD_LIMIT,
        **result,
    )


def _run_location_capture() -> Tuple[bool, Dict[str, Any]]:
    if not LOCATION_CAPTURE_SCRIPT.is_file():
        return False, {"error": f"missing {LOCATION_CAPTURE_SCRIPT}"}
    completed = subprocess.run(
        [sys.executable, str(LOCATION_CAPTURE_SCRIPT)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    try:
        data = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        data = {
            "error": "location_capture_output_not_json",
            "stdout": stdout,
            "stderr": stderr,
        }
    passed = completed.returncode == 0 and bool(data.get("ok", False))
    return passed, {
        "returncode": completed.returncode,
        "result": data,
        "stderr": stderr,
    }


def _check_location_pipeline(report: Dict[str, Any], *, label: str) -> None:
    attempts: List[Dict[str, Any]] = []
    passed = True
    for _index in range(2):
        ok, detail = _run_location_capture()
        attempts.append({"ok": ok, **detail})
        passed = passed and ok
    _require(report, label, passed, attempts=attempts)


def _lifted_wheel_check(
    report: Dict[str, Any],
    calibration: MobilityCalibrationSnapshot,
) -> None:
    right_speed, left_speed = (
        calibration.move_speeds("default")
        if calibration.uses_balanced_timed_v2
        else (motion.MOVE_CRUISE_SPEED, motion.MOVE_CRUISE_SPEED)
    )
    print("\nLift Alpha so every wheel is clear of the floor.")
    input("Hold it securely and press Enter for a one-second forward wheel test: ")
    ok, motor, detail = motion._motor_begin()
    if not ok:
        _require(report, "lifted wheel direction", False, detail=detail)
        return
    try:
        motion._safe_stop(motor)
        motor.motor_movement([motor.M1], motor.CCW, right_speed)
        motor.motor_movement([motor.M2], motor.CW, left_speed)
        time.sleep(LIFTED_WHEEL_RUN_SEC)
    finally:
        motion._safe_stop(motor)
    passed = _yes_no(
        "Did all four wheels start immediately and rotate forward together?"
    )
    _require(
        report,
        "lifted wheel direction",
        passed,
        right_speed=right_speed,
        left_speed=left_speed,
        duration_sec=LIFTED_WHEEL_RUN_SEC,
    )


def _short_move_check(report: Dict[str, Any]) -> None:
    input("Place the robot on clear flat floor and press Enter for a 0.10-m move: ")
    ok, detail = motion.move_forward(0.10)
    operator_ok = ok and _yes_no(
        "Did all wheels start immediately with no hesitation, fighting, or abnormal sound?"
    )
    _require(report, "0.10-m startup move", ok and operator_ok, detail=detail)


def _one_metre_check(report: Dict[str, Any]) -> None:
    input("Return to the marked flat-floor start and press Enter for a 1.00-m move: ")
    started = time.monotonic()
    ok, detail = motion.move_forward(FORWARD_DISTANCE_M)
    elapsed = time.monotonic() - started
    if not ok:
        _require(report, "1.00-m flat-floor move", False, detail=detail)
        return
    distance_cm = _prompt_float(
        "Measured forward distance (cm)", low=0.0, high=500.0
    )
    heading_deg = _prompt_float(
        "Final heading error (degrees; CCW/left is positive)",
        low=-180.0,
        high=180.0,
    )
    lateral_cm = _prompt_float(
        "Lateral displacement (cm; left is positive)",
        low=-500.0,
        high=500.0,
    )
    smooth = _yes_no("Was motion smooth, without visible zig-zag or hesitation?")
    passed = (
        FORWARD_DISTANCE_LOW_CM <= distance_cm <= FORWARD_DISTANCE_HIGH_CM
        and abs(heading_deg) <= FORWARD_HEADING_LIMIT_DEG
        and abs(lateral_cm) <= FORWARD_LATERAL_LIMIT_CM
        and smooth
    )
    _require(
        report,
        "1.00-m flat-floor move",
        passed,
        detail=detail,
        elapsed_sec=elapsed,
        measured_distance_cm=distance_cm,
        heading_error_deg_ccw_positive=heading_deg,
        lateral_displacement_cm_left_positive=lateral_cm,
        smooth=smooth,
        limits={
            "distance_cm": [FORWARD_DISTANCE_LOW_CM, FORWARD_DISTANCE_HIGH_CM],
            "absolute_heading_error_deg": FORWARD_HEADING_LIMIT_DEG,
            "absolute_lateral_displacement_cm": FORWARD_LATERAL_LIMIT_CM,
        },
    )


def _turn_check(report: Dict[str, Any]) -> None:
    input(
        f"Clear the rotation area and press Enter for {TURN_CYCLES} alternating "
        "+90/-90-degree cycles: "
    )
    turns: List[Dict[str, Any]] = []
    for cycle in range(1, TURN_CYCLES + 1):
        for angle in (90.0, -90.0):
            ok, detail = motion.turn_signed(angle)
            turns.append(
                {"cycle": cycle, "requested_angle_deg": angle, "ok": ok, "detail": detail}
            )
            print(
                f"Cycle {cycle}/{TURN_CYCLES} angle {angle:+.0f}: "
                f"{'OK' if ok else 'FAILED'}"
            )
            if not ok:
                _require(report, "alternating turns", False, turns=turns)
                return
            time.sleep(TURN_SETTLE_SEC)
    operator_ok = _yes_no(
        "Did every turn start smoothly with all wheels engaged and no abnormal sound?"
    )
    _require(report, "alternating turns", operator_ok, turns=turns)


def _bump_direction_check(
    report: Dict[str, Any],
    calibration: MobilityCalibrationSnapshot,
    *,
    direction: str,
    profile: str,
    label: str,
) -> None:
    distance_m = calibration.bump_crossing_distance(direction)
    input(f"Place the robot for {label} and press Enter to run {distance_m:.2f} m: ")
    ok, detail = motion.move_forward(distance_m, move_profile=profile)
    if not ok:
        _require(report, f"bump {label}", False, detail=detail)
        return
    measured_cm = _prompt_float(
        "Measured forward distance (cm)", low=0.0, high=500.0
    )
    cleared = _yes_no(
        "Did all wheels clear the ramp without stall, fighting, or manual assistance?"
    )
    passed = (
        BUMP_DISTANCE_LOW_CM <= measured_cm <= BUMP_DISTANCE_HIGH_CM and cleared
    )
    _require(
        report,
        f"bump {label}",
        passed,
        detail=detail,
        measured_distance_cm=measured_cm,
        cleared_without_assistance=cleared,
        distance_limits_cm=[BUMP_DISTANCE_LOW_CM, BUMP_DISTANCE_HIGH_CM],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a non-calibrating robot-side mobility preflight."
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="run calibration, HAT, gyro, and location checks without movement",
    )
    parser.add_argument(
        "--with-bump",
        action="store_true",
        help="include one production crossing in each direction",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory for timestamped JSON reports",
    )
    args = parser.parse_args()
    if args.static_only and args.with_bump:
        parser.error("--static-only and --with-bump cannot be combined")

    scanner = SCANNER_NAME_PATH.read_text(encoding="utf-8").strip()
    report: Dict[str, Any] = {
        "schema_version": 1,
        "started_at_utc": _utc_now(),
        "scanner": scanner,
        "mode": (
            "static_only"
            if args.static_only
            else "with_bump"
            if args.with_bump
            else "standard"
        ),
        "checks": [],
        "status": "running",
    }

    print("=" * 60)
    print("ROBOT MOBILITY PREFLIGHT")
    print("=" * 60)
    print(f"Robot: {scanner}")
    print(f"Mode:  {report['mode']}")
    print("Calibration will not be changed.")
    if not _yes_no("Start preflight?"):
        print("Cancelled.")
        return 0

    exit_code = 0
    try:
        calibration = _check_calibration(report)
        _check_motor_hat(report, label="motor HAT repeated communication")
        print("Keep the robot completely stationary for the gyro check.")
        _check_gyro(report, calibration, label="stationary gyro")
        _check_location_pipeline(report, label="location/camera pipeline")

        if not args.static_only:
            _lifted_wheel_check(report, calibration)
            _short_move_check(report)
            _one_metre_check(report)
            _turn_check(report)
            if args.with_bump:
                _bump_direction_check(
                    report,
                    calibration,
                    direction="positive_y",
                    profile="bump_crossing_up",
                    label="inner-to-outer",
                )
                _bump_direction_check(
                    report,
                    calibration,
                    direction="negative_y",
                    profile="bump_crossing_down",
                    label="outer-to-inner",
                )
            _check_motor_hat(report, label="post-motion motor HAT communication")
            print("Keep the robot completely stationary for the final gyro check.")
            _check_gyro(report, calibration, label="post-motion stationary gyro")
            _check_location_pipeline(report, label="post-motion location/camera pipeline")

        report["status"] = "pass"
    except PreflightFailure as exc:
        report["status"] = "fail"
        report["failure"] = str(exc)
        exit_code = 1
    except KeyboardInterrupt:
        report["status"] = "cancelled"
        report["failure"] = "operator cancelled"
        exit_code = 130
    except Exception as exc:
        report["status"] = "error"
        report["failure"] = f"{type(exc).__name__}: {exc}"
        exit_code = 2
    finally:
        report["finished_at_utc"] = _utc_now()
        path = _save_report(report, args.output_dir)
        print("\n" + "=" * 60)
        print(f"PREFLIGHT {report['status'].upper()}")
        print(f"Report: {path}")
        print("=" * 60)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
