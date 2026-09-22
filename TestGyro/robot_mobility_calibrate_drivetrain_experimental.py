#!/usr/bin/env python3
"""Non-production drivetrain calibration for experimental motors and wheels.

This tool deliberately does NOT update robot_mobility_calibration.json.  It is
intended to characterize a new drivetrain without changing the established
calibration workflow used by the rest of the fleet.

Stages are intentionally separated:
  1. stationary gyro bias measurement (sensor property),
  2. forward kick balance (startup mechanical transient),
  3. heading-hold tuning with a fixed gyro bias (controller property).

The accepted candidate is written only to:
  TestGyro/calibration/experimental_drivetrain_result.json
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion
from robot_mobility_calibration_registry import MobilityCalibrationSnapshot


RESULT_PATH = SCRIPT_DIR / "calibration" / "experimental_drivetrain_result.json"

STATIONARY_SAMPLE_SEC = 5.0
STATIONARY_DISCARD_SEC = 1.0
STATIONARY_SAMPLE_DT_SEC = 0.02
STATIONARY_TRIM_FRACTION = 0.10

DEFAULT_KICK_RIGHT_SPEED = 40
DEFAULT_KICK_LEFT_SPEED = 40
DEFAULT_HEADING_KP = 0.40
DEFAULT_MAX_CORRECTION = 4.0
DEFAULT_DEADBAND_DEG = 0.50
DEFAULT_RAW_TRIAL_DISTANCE_M = 3.00

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scanner_name() -> str:
    path = ROBOT_ROOT / "scanner_name.txt"
    scanner = path.read_text(encoding="utf-8").strip()
    if not scanner:
        raise RuntimeError(f"empty robot identity in {path}")
    return scanner


def _prompt_float(label: str, default: float, minimum: float, maximum: float) -> float:
    while True:
        answer = input(f"{label} [{default:g}]: ").strip()
        if not answer:
            return float(default)
        try:
            value = float(answer)
        except ValueError:
            print("Enter a number.")
            continue
        if math.isfinite(value) and minimum <= value <= maximum:
            return value
        print(f"Enter a finite value from {minimum:g} through {maximum:g}.")


def _prompt_int(label: str, default: int, minimum: int, maximum: int) -> int:
    return int(round(_prompt_float(label, float(default), float(minimum), float(maximum))))


def _yes_no(label: str, default_yes: bool = False) -> bool:
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


def _trimmed_mean(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    trim = int(len(ordered) * fraction)
    if trim and trim * 2 < len(ordered):
        ordered = ordered[trim:-trim]
    return statistics.fmean(ordered)


def _measure_stationary_gz() -> Dict[str, float | int]:
    ok, imu, detail = motion._imu_begin()
    if not ok:
        raise RuntimeError(f"IMU initialization failed: {detail}")

    samples: List[float] = []
    start = time.monotonic()
    while time.monotonic() - start < STATIONARY_SAMPLE_SEC:
        elapsed = time.monotonic() - start
        _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
        value = float(gz)
        if elapsed >= STATIONARY_DISCARD_SEC and math.isfinite(value):
            samples.append(value)
        time.sleep(STATIONARY_SAMPLE_DT_SEC)

    if len(samples) < 20:
        raise RuntimeError(f"too few usable gyro samples: {len(samples)}")
    return {
        "gz_bias": _trimmed_mean(samples, STATIONARY_TRIM_FRACTION),
        "raw_spread_deg_per_sec": statistics.pstdev(samples),
        "sample_count": len(samples),
    }


def _run_startup_balance() -> Dict[str, int]:
    print("\nFORWARD STARTUP BALANCE")
    print("This stage runs only the short forward kick and then stops.")
    print("If it twists right, increase LEFT or reduce RIGHT.")
    print("If it twists left, increase RIGHT or reduce LEFT.")

    right = DEFAULT_KICK_RIGHT_SPEED
    left = DEFAULT_KICK_LEFT_SPEED
    attempt = 1
    while True:
        print(f"\nStartup attempt {attempt}")
        right = _prompt_int("Right-side kick speed", right, 0, 100)
        left = _prompt_int("Left-side kick speed", left, 0, 100)
        input("Clear the short path and press Enter to run the kick: ")
        ok, detail = motion._run_forward_startup_trial(right, left)
        print(detail)
        if not ok:
            if _yes_no("Retry this stage?", default_yes=True):
                attempt += 1
                continue
            raise RuntimeError("startup balance cancelled after movement failure")
        if _yes_no("Accept this startup balance?"):
            return {"right_kick_speed": right, "left_kick_speed": left}
        attempt += 1


def _snapshot(scanner: str, gz_bias: float, right_kick: int, left_kick: int):
    return MobilityCalibrationSnapshot(
        scanner=scanner,
        gz_bias=gz_bias,
        cmd_a=1.0,
        cmd_b=0.0,
        source="experimental_drivetrain_calibration",
        warning="test_only_production_calibration_unchanged",
        forward_kick_right_speed=right_kick,
        forward_kick_left_speed=left_kick,
        turn_ccw_stop_margin_deg=0.0,
        turn_cw_stop_margin_deg=0.0,
    )


def _detail_value(detail: str, name: str) -> float:
    match = re.search(rf"(?:^|\s){re.escape(name)}=({_NUMBER})(?:\s|$)", detail)
    if match is None:
        raise RuntimeError(f"movement result is missing {name}")
    return float(match.group(1))


def _run_heading_trial(
    scanner: str,
    gz_bias: float,
    kick: Dict[str, int],
    kp: float,
    max_correction: float,
    deadband_deg: float,
    raw_distance_m: float,
) -> Dict[str, object]:
    old_values = (
        motion.HEADING_HOLD_KP,
        motion.HEADING_HOLD_MAX_CORRECTION,
        motion.HEADING_HOLD_DEADBAND_DEG,
    )
    try:
        motion.HEADING_HOLD_KP = kp
        motion.HEADING_HOLD_MAX_CORRECTION = max_correction
        motion.HEADING_HOLD_DEADBAND_DEG = deadband_deg
        ok, detail = motion._run_move(
            forward=True,
            distance_m=raw_distance_m,
            calibration=_snapshot(
                scanner,
                gz_bias,
                kick["right_kick_speed"],
                kick["left_kick_speed"],
            ),
            calibration_gz_bias=gz_bias,
            motor_distance_override=raw_distance_m,
        )
    finally:
        (
            motion.HEADING_HOLD_KP,
            motion.HEADING_HOLD_MAX_CORRECTION,
            motion.HEADING_HOLD_DEADBAND_DEG,
        ) = old_values

    result: Dict[str, object] = {
        "ok": ok,
        "detail": detail,
        "kp": kp,
        "max_correction": max_correction,
        "deadband_deg": deadband_deg,
        "raw_trial_distance_m": raw_distance_m,
    }
    if ok:
        result["final_yaw_deg"] = _detail_value(detail, "final_yaw_deg")
        result["max_abs_yaw_deg"] = _detail_value(detail, "max_abs_yaw_deg")
    return result


def _run_heading_tuning(
    scanner: str,
    gz_bias: float,
    kick: Dict[str, int],
) -> Dict[str, object]:
    print("\nCRUISE HEADING-HOLD TUNING")
    print("GZ_BIAS remains fixed at the stationary measurement.")
    print("Judge persistent drift and visible right-left oscillation separately.")

    kp = DEFAULT_HEADING_KP
    max_correction = DEFAULT_MAX_CORRECTION
    deadband = DEFAULT_DEADBAND_DEG
    raw_distance = DEFAULT_RAW_TRIAL_DISTANCE_M
    trials: List[Dict[str, object]] = []
    attempt = 1

    while True:
        print(f"\nHeading trial {attempt}")
        kp = _prompt_float("Heading Kp", kp, 0.0, 5.0)
        max_correction = _prompt_float(
            "Maximum speed correction", max_correction, 0.0, 30.0
        )
        deadband = _prompt_float("Yaw deadband (degrees)", deadband, 0.0, 10.0)
        raw_distance = _prompt_float(
            "Raw trial distance", raw_distance, 0.10, motion.MAX_MOVE_DISTANCE_M
        )
        print(
            "This is an uncalibrated raw-duration trial; the physical distance "
            "may differ from its label."
        )
        input("Clear the path, mark the initial heading, and press Enter: ")
        result = _run_heading_trial(
            scanner,
            gz_bias,
            kick,
            kp,
            max_correction,
            deadband,
            raw_distance,
        )
        trials.append(result)
        print(str(result["detail"]))
        if result["ok"]:
            print(
                f"final_yaw={float(result['final_yaw_deg']):+.3f} deg; "
                f"max_abs_yaw={float(result['max_abs_yaw_deg']):.3f} deg"
            )
            print("Accept only if the path has no clear S-curve or persistent drift.")
            if _yes_no("Accept these heading-hold settings?"):
                return {
                    "kp": kp,
                    "max_correction": max_correction,
                    "deadband_deg": deadband,
                    "accepted_trial": attempt,
                    "trials": trials,
                }
        elif not _yes_no("Movement failed. Retry?", default_yes=True):
            raise RuntimeError("heading tuning cancelled after movement failure")
        attempt += 1


def _write_result(result: Dict[str, object]) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)


def main() -> int:
    scanner = _scanner_name()
    print("============================================================")
    print(f"EXPERIMENTAL DRIVETRAIN CALIBRATION: {scanner}")
    print("============================================================")
    print("Production calibration and shared source files will not be changed.")
    print(f"Motion cruise speed currently loaded: {motion.MOVE_CRUISE_SPEED}")
    print(f"Bump cruise speed currently loaded:   {motion.MOVE_BUMP_CROSSING_CRUISE_SPEED}")

    input("Keep Alpha completely stationary and press Enter for gyro sampling: ")
    stationary = _measure_stationary_gz()
    gz_bias = float(stationary["gz_bias"])
    print(f"Stationary GZ_BIAS: {gz_bias:+.9f} deg/s")
    print(f"Raw spread:         {float(stationary['raw_spread_deg_per_sec']):.9f} deg/s")
    print("This value is fixed; walking behavior will not be used to alter it.")

    kick = _run_startup_balance()
    heading = _run_heading_tuning(scanner, gz_bias, kick)

    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(),
        "scanner": scanner,
        "status": "experimental_only",
        "production_registry_updated": False,
        "loaded_motion_constants": {
            "move_cruise_speed": motion.MOVE_CRUISE_SPEED,
            "bump_cruise_speed": motion.MOVE_BUMP_CROSSING_CRUISE_SPEED,
            "move_sec_per_meter": motion.MOVE_SEC_PER_METER,
        },
        "stationary_gyro": stationary,
        "forward_startup": kick,
        "heading_hold": heading,
    }
    _write_result(result)

    print("\nEXPERIMENTAL RESULT")
    print(f"GZ_BIAS:                 {gz_bias:+.9f}")
    print(
        "Startup right/left:       "
        f"{kick['right_kick_speed']} / {kick['left_kick_speed']}"
    )
    print(f"Heading Kp:              {float(heading['kp']):.3f}")
    print(f"Maximum correction:      {float(heading['max_correction']):.3f}")
    print(f"Deadband:                {float(heading['deadband_deg']):.3f} deg")
    print(f"Result file:             {RESULT_PATH}")
    print("Production registry:     unchanged")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled; production calibration was not changed.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nEXPERIMENTAL CALIBRATION FAILED: {exc}", file=sys.stderr)
        print("Production calibration was not changed.", file=sys.stderr)
        raise SystemExit(2)
