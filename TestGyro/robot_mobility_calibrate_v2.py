#!/usr/bin/env python3
"""Calibrate the balanced timed drivetrain without legacy kick fitting.

This workflow is for the all-metal drivetrain established on Alpha.  It keeps
the legacy calibration fields in the registry for rollback, but production
motion selects the new profile only when ``drivetrain.model`` is
``balanced_timed_v2``.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion
from robot_mobility_calibration_registry import (
    DRIVETRAIN_MODEL_BALANCED_TIMED_V2,
    MobilityCalibrationSnapshot,
    load_mobility_calibration,
)


REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"
SCANNER_NAME_PATH = ROBOT_ROOT / "scanner_name.txt"
RESULT_PATH = SCRIPT_DIR / "calibration" / "drivetrain_v2_result.json"

DEFAULT_RIGHT_SPEED = 30
DEFAULT_LEFT_SPEED = 29
DEFAULT_BUMP_RIGHT_SPEED = 40
DEFAULT_BUMP_LEFT_SPEED = 39
DEFAULT_TURN_SPEED = 30
DEFAULT_FORWARD_TRIAL_SEC = 9.0
DEFAULT_BUMP_TRIAL_SEC = 12.0
DEFAULT_MINIMUM_DISTANCE_M = 0.10
DEFAULT_HEADING_KP = 0.30
DEFAULT_HEADING_DEADBAND_DEG = 2.0
DEFAULT_HEADING_MAX_CORRECTION = 2.0
TURN_COUNT = 10
TURN_SETTLE_SEC = 1.0
BUMP_TARGET_DISTANCE_M = 2.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_float(label: str, default: float, low: float, high: float) -> float:
    while True:
        answer = input(f"{label} [{default:g}]: ").strip()
        if not answer:
            return float(default)
        try:
            value = float(answer)
        except ValueError:
            print("Enter a number.")
            continue
        if math.isfinite(value) and low <= value <= high:
            return value
        print(f"Enter a value from {low:g} through {high:g}.")


def _prompt_int(label: str, default: int, low: int, high: int) -> int:
    return int(round(_prompt_float(label, float(default), float(low), float(high))))


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


def _measure_stationary_gz(seconds: float = 5.0) -> Tuple[float, float, int]:
    ok, imu, detail = motion._imu_begin()
    if not ok:
        raise RuntimeError(f"IMU initialization failed: {detail}")
    samples: List[float] = []
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        elapsed = time.monotonic() - started
        _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
        if elapsed >= 1.0 and math.isfinite(float(gz)):
            samples.append(float(gz))
        time.sleep(0.02)
    if len(samples) < 20:
        raise RuntimeError(f"too few gyro samples: {len(samples)}")
    ordered = sorted(samples)
    trim = int(len(ordered) * 0.10)
    trimmed = ordered[trim:-trim] if trim else ordered
    return statistics.fmean(trimmed), statistics.pstdev(samples), len(samples)


def _candidate(
    production: MobilityCalibrationSnapshot,
    *,
    gz_bias: float,
    right_speed: int,
    left_speed: int,
    seconds_per_meter: float,
    turn_speed: int,
    ccw_margin: float,
    cw_margin: float,
    positive_y_seconds_per_meter: float,
    negative_y_seconds_per_meter: float,
    positive_y_right_speed: int = DEFAULT_BUMP_RIGHT_SPEED,
    positive_y_left_speed: int = DEFAULT_BUMP_LEFT_SPEED,
    negative_y_right_speed: int = DEFAULT_BUMP_RIGHT_SPEED,
    negative_y_left_speed: int = DEFAULT_BUMP_LEFT_SPEED,
) -> MobilityCalibrationSnapshot:
    return replace(
        production,
        source="drivetrain_v2_calibration",
        warning="calibration_candidate_registry_unchanged",
        gz_bias=gz_bias,
        drivetrain_model=DRIVETRAIN_MODEL_BALANCED_TIMED_V2,
        forward_right_speed=right_speed,
        forward_left_speed=left_speed,
        forward_seconds_per_meter=seconds_per_meter,
        minimum_move_distance_m=DEFAULT_MINIMUM_DISTANCE_M,
        heading_hold_kp=DEFAULT_HEADING_KP,
        heading_hold_deadband_deg=DEFAULT_HEADING_DEADBAND_DEG,
        heading_hold_max_correction=DEFAULT_HEADING_MAX_CORRECTION,
        turn_speed=turn_speed,
        turn_ccw_stop_margin_deg=ccw_margin,
        turn_cw_stop_margin_deg=cw_margin,
        bump_positive_y_distance_m=BUMP_TARGET_DISTANCE_M,
        bump_negative_y_distance_m=BUMP_TARGET_DISTANCE_M,
        bump_positive_y_seconds_per_meter=positive_y_seconds_per_meter,
        bump_negative_y_seconds_per_meter=negative_y_seconds_per_meter,
        bump_positive_y_right_speed=positive_y_right_speed,
        bump_positive_y_left_speed=positive_y_left_speed,
        bump_negative_y_right_speed=negative_y_right_speed,
        bump_negative_y_left_speed=negative_y_left_speed,
        bump_positive_y_gz_bias=None,
        bump_negative_y_gz_bias=None,
    )


def _run_forward_for_seconds(
    calibration: MobilityCalibrationSnapshot,
    seconds: float,
    profile: str = "default",
) -> None:
    rate = calibration.move_seconds_per_meter(profile)
    ok, detail = motion._run_move(
        forward=True,
        distance_m=max(DEFAULT_MINIMUM_DISTANCE_M, seconds / rate),
        move_profile=profile,
        calibration=calibration,
        motor_distance_override=seconds / rate,
    )
    print(detail)
    if not ok:
        raise RuntimeError(detail)


def _run_turns(
    calibration: MobilityCalibrationSnapshot,
    *,
    physical_ccw: bool,
    count: int,
) -> None:
    direction = "LEFT/CCW" if physical_ccw else "RIGHT/CW"
    print(f"Running {count} x 90-degree {direction} turns...")
    for index in range(1, count + 1):
        result = motion._run_turn_measured(
            left=not physical_ccw,
            angle_deg=90.0,
            calibration=calibration,
        )
        print(
            f"Turn {index}/{count}: {'OK' if result.ok else 'FAILED'}, "
            f"gyro={result.measured_yaw_deg:+.3f} deg"
        )
        if not result.ok:
            raise RuntimeError(result.detail)
        if index < count:
            time.sleep(TURN_SETTLE_SEC)


def _calibrate_stationary_gz() -> Dict[str, object]:
    print("\n=== 1. STATIONARY GYRO BIAS ===")
    print("Keep the robot completely stationary.")
    means: List[float] = []
    spreads: List[float] = []
    counts: List[int] = []
    for index in range(1, 4):
        print(f"Measurement {index}/3 starts in 3 seconds...")
        time.sleep(3.0)
        mean, spread, count = _measure_stationary_gz()
        means.append(mean)
        spreads.append(spread)
        counts.append(count)
        print(f"GZ_BIAS={mean:+.9f}, spread={spread:.9f}, samples={count}")
    value = statistics.fmean(means)
    between = statistics.pstdev(means)
    print(f"Accepted mean GZ_BIAS: {value:+.9f} deg/s")
    print(f"Between-test spread:   {between:.9f} deg/s")
    if between > 0.10:
        raise RuntimeError("stationary gyro measurements are not repeatable")
    return {
        "gz_bias": value,
        "measurement_means": means,
        "measurement_spreads": spreads,
        "sample_counts": counts,
        "between_test_spread": between,
    }


def _calibrate_forward_balance(
    production: MobilityCalibrationSnapshot,
    gz_bias: float,
) -> Tuple[int, int]:
    print("\n=== 2. FORWARD BALANCE ===")
    right = production.forward_right_speed or DEFAULT_RIGHT_SPEED
    left = production.forward_left_speed or DEFAULT_LEFT_SPEED
    while True:
        right = _prompt_int("Right/M1 base PWM", right, 1, 100)
        left = _prompt_int("Left/M2 base PWM", left, 1, 100)
        candidate = _candidate(
            production,
            gz_bias=gz_bias,
            right_speed=right,
            left_speed=left,
            seconds_per_meter=9.0,
            turn_speed=DEFAULT_TURN_SPEED,
            ccw_margin=0.0,
            cw_margin=0.0,
            positive_y_seconds_per_meter=9.0,
            negative_y_seconds_per_meter=9.0,
        )
        input("Mark the start, clear the path, and press Enter for a 5-second trial: ")
        _run_forward_for_seconds(candidate, 5.0)
        if _yes_no("Accept this smoothness and left/right balance?"):
            return right, left


def _calibrate_forward_timing(
    production: MobilityCalibrationSnapshot,
    gz_bias: float,
    right_speed: int,
    left_speed: int,
) -> Dict[str, object]:
    print("\n=== 3. FORWARD SECONDS PER METRE ===")
    distances: List[float] = []
    trial_sec = DEFAULT_FORWARD_TRIAL_SEC
    candidate = _candidate(
        production,
        gz_bias=gz_bias,
        right_speed=right_speed,
        left_speed=left_speed,
        seconds_per_meter=9.0,
        turn_speed=DEFAULT_TURN_SPEED,
        ccw_margin=0.0,
        cw_margin=0.0,
        positive_y_seconds_per_meter=9.0,
        negative_y_seconds_per_meter=9.0,
    )
    for index in range(1, 3):
        input(f"Place at the start and press Enter for timing trial {index}/2: ")
        _run_forward_for_seconds(candidate, trial_sec)
        cm = _prompt_float("Measured forward distance (cm)", 100.0, 1.0, 500.0)
        distances.append(cm / 100.0)
    seconds_per_meter = (trial_sec * len(distances)) / sum(distances)
    candidate = replace(candidate, forward_seconds_per_meter=seconds_per_meter)
    print(f"Candidate timing: {seconds_per_meter:.6f} s/m")
    input("Place at the start and press Enter for the 1.0-m verification: ")
    ok, detail = motion._run_move(
        forward=True,
        distance_m=1.0,
        calibration=candidate,
    )
    print(detail)
    if not ok:
        raise RuntimeError(detail)
    verification_cm = _prompt_float(
        "Measured verification distance (cm)", 100.0, 1.0, 500.0
    )
    if not _yes_no("Accept the forward timing?", default_yes=True):
        raise RuntimeError("forward timing was not accepted")
    return {
        "trial_seconds": trial_sec,
        "measured_distances_m": distances,
        "seconds_per_meter": seconds_per_meter,
        "verification_distance_m": verification_cm / 100.0,
    }


def _calibrate_turning(
    production: MobilityCalibrationSnapshot,
    gz_bias: float,
    right_speed: int,
    left_speed: int,
    seconds_per_meter: float,
) -> Dict[str, object]:
    print("\n=== 4. TURNING ===")
    turn_speed = _prompt_int(
        "Turn PWM", production.turn_speed or DEFAULT_TURN_SPEED, 1, 100
    )
    zero = _candidate(
        production,
        gz_bias=gz_bias,
        right_speed=right_speed,
        left_speed=left_speed,
        seconds_per_meter=seconds_per_meter,
        turn_speed=turn_speed,
        ccw_margin=0.0,
        cw_margin=0.0,
        positive_y_seconds_per_meter=9.0,
        negative_y_seconds_per_meter=9.0,
    )
    input("Mark the heading and press Enter for ten zero-margin CCW turns: ")
    _run_turns(zero, physical_ccw=True, count=TURN_COUNT)
    ccw_total_error = _prompt_float(
        "CCW cumulative error in degrees (+overshoot, -undershoot)",
        0.0,
        -90.0,
        90.0,
    )
    input("Mark the heading and press Enter for ten zero-margin CW turns: ")
    _run_turns(zero, physical_ccw=False, count=TURN_COUNT)
    cw_total_error = _prompt_float(
        "CW cumulative error in degrees (+overshoot, -undershoot)",
        0.0,
        -90.0,
        90.0,
    )
    ccw_margin = ccw_total_error / TURN_COUNT
    cw_margin = cw_total_error / TURN_COUNT
    compensated = replace(
        zero,
        turn_ccw_stop_margin_deg=ccw_margin,
        turn_cw_stop_margin_deg=cw_margin,
    )
    print(f"Candidate CCW/CW margins: {ccw_margin:.3f} / {cw_margin:.3f} deg")
    input("Mark the heading and press Enter for ten compensated CCW turns: ")
    _run_turns(compensated, physical_ccw=True, count=TURN_COUNT)
    ccw_verify = _prompt_float(
        "Compensated CCW cumulative error (degrees)", 0.0, -90.0, 90.0
    )
    input("Mark the heading and press Enter for ten compensated CW turns: ")
    _run_turns(compensated, physical_ccw=False, count=TURN_COUNT)
    cw_verify = _prompt_float(
        "Compensated CW cumulative error (degrees)", 0.0, -90.0, 90.0
    )
    if not _yes_no("Accept the turning calibration?", default_yes=True):
        raise RuntimeError("turning calibration was not accepted")
    return {
        "speed": turn_speed,
        "ccw_stop_margin_deg": ccw_margin,
        "cw_stop_margin_deg": cw_margin,
        "zero_margin_ccw_total_error_deg": ccw_total_error,
        "zero_margin_cw_total_error_deg": cw_total_error,
        "verification_ccw_total_error_deg": ccw_verify,
        "verification_cw_total_error_deg": cw_verify,
    }


def _bump_candidate(
    production: MobilityCalibrationSnapshot,
    *,
    gz_bias: float,
    right_speed: int,
    left_speed: int,
    seconds_per_meter: float,
    turn_speed: int,
    ccw_margin: float,
    cw_margin: float,
    positive_y: Dict[str, float],
    negative_y: Dict[str, float],
) -> MobilityCalibrationSnapshot:
    return _candidate(
        production,
        gz_bias=gz_bias,
        right_speed=right_speed,
        left_speed=left_speed,
        seconds_per_meter=seconds_per_meter,
        turn_speed=turn_speed,
        ccw_margin=ccw_margin,
        cw_margin=cw_margin,
        positive_y_seconds_per_meter=positive_y["seconds_per_meter"],
        negative_y_seconds_per_meter=negative_y["seconds_per_meter"],
        positive_y_right_speed=int(positive_y["right_speed"]),
        positive_y_left_speed=int(positive_y["left_speed"]),
        negative_y_right_speed=int(negative_y["right_speed"]),
        negative_y_left_speed=int(negative_y["left_speed"]),
    )


def _bump_trial(
    calibration: MobilityCalibrationSnapshot,
    *,
    direction: str,
    profile: str,
    settings: Dict[str, float],
) -> None:
    label = "+Y inner-to-outer" if direction == "positive_y" else "-Y outer-to-inner"
    print(f"\nBump timing trial: {label}")
    print(
        f"Bump right/left PWM: {int(settings['right_speed'])}/"
        f"{int(settings['left_speed'])}"
    )
    input(
        f"Place Alpha for {label} and press Enter for a "
        f"{DEFAULT_BUMP_TRIAL_SEC:g}-second trial: "
    )
    _run_forward_for_seconds(calibration, DEFAULT_BUMP_TRIAL_SEC, profile)
    if not _yes_no("Did Alpha clear the ramp without a stall?", default_yes=True):
        raise RuntimeError(
            f"{direction} bump trial stalled; timing was not calculated"
        )
    measured_cm = _prompt_float("Measured forward distance (cm)", 190.0, 20.0, 400.0)
    rate = DEFAULT_BUMP_TRIAL_SEC / (measured_cm / 100.0)
    settings["trial_distance_m"] = measured_cm / 100.0
    settings["seconds_per_meter"] = rate
    print(f"Candidate bump timing: {rate:.6f} s/m")


def _bump_verification(
    calibration: MobilityCalibrationSnapshot,
    *,
    direction: str,
    profile: str,
    settings: Dict[str, float],
) -> None:
    label = "+Y inner-to-outer" if direction == "positive_y" else "-Y outer-to-inner"
    print(f"\nBump verification: {label}")
    input(f"Place Alpha for {label} and press Enter for the calibrated 2.0-m verification: ")
    ok, detail = motion._run_move(
        forward=True,
        distance_m=BUMP_TARGET_DISTANCE_M,
        move_profile=profile,
        calibration=calibration,
    )
    print(detail)
    if not ok:
        raise RuntimeError(detail)
    if not _yes_no("Did Alpha clear the ramp without a stall?", default_yes=True):
        raise RuntimeError(
            f"{direction} bump verification stalled; calibration was rejected"
        )
    verify_cm = _prompt_float("Measured verification distance (cm)", 200.0, 20.0, 400.0)
    if not _yes_no(f"Accept {label} bump timing?", default_yes=True):
        raise RuntimeError(f"{direction} bump timing was not accepted")
    settings["verification_distance_m"] = verify_cm / 100.0


def _write_results(scanner: str, results: Dict[str, object]) -> None:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entry = document["robots"][scanner]
    entry["gz_bias"] = results["gyro"]["gz_bias"]
    entry["calibrated_at_utc"] = _utc_now()
    entry["source"] = str(RESULT_PATH)
    entry["drivetrain"] = {
        "model": DRIVETRAIN_MODEL_BALANCED_TIMED_V2,
        "forward": {
            "right_speed": results["forward_balance"]["right_speed"],
            "left_speed": results["forward_balance"]["left_speed"],
            "seconds_per_meter": results["forward_timing"]["seconds_per_meter"],
            "minimum_distance_m": DEFAULT_MINIMUM_DISTANCE_M,
        },
        "heading_hold": {
            "kp": DEFAULT_HEADING_KP,
            "deadband_deg": DEFAULT_HEADING_DEADBAND_DEG,
            "max_correction": DEFAULT_HEADING_MAX_CORRECTION,
        },
        "turning": {
            "speed": results["turning"]["speed"],
            "ccw_stop_margin_deg": results["turning"]["ccw_stop_margin_deg"],
            "cw_stop_margin_deg": results["turning"]["cw_stop_margin_deg"],
        },
        "bump_crossing": {
            "positive_y": {
                "distance_m": BUMP_TARGET_DISTANCE_M,
                "seconds_per_meter": results["bump_crossing"]["positive_y"]["seconds_per_meter"],
                "right_speed": int(results["bump_crossing"]["positive_y"]["right_speed"]),
                "left_speed": int(results["bump_crossing"]["positive_y"]["left_speed"]),
            },
            "negative_y": {
                "distance_m": BUMP_TARGET_DISTANCE_M,
                "seconds_per_meter": results["bump_crossing"]["negative_y"]["seconds_per_meter"],
                "right_speed": int(results["bump_crossing"]["negative_y"]["right_speed"]),
                "left_speed": int(results["bump_crossing"]["negative_y"]["left_speed"]),
            },
        },
    }
    entry["turning"] = {
        "ccw_stop_margin_deg": results["turning"]["ccw_stop_margin_deg"],
        "cw_stop_margin_deg": results["turning"]["cw_stop_margin_deg"],
    }
    old_bump = entry.get("bump_crossing")
    bump = copy.deepcopy(old_bump) if isinstance(old_bump, dict) else {}
    for direction in ("positive_y", "negative_y"):
        old_direction = bump.get(direction)
        direction_entry = (
            copy.deepcopy(old_direction) if isinstance(old_direction, dict) else {}
        )
        direction_entry["command_distance_m"] = BUMP_TARGET_DISTANCE_M
        direction_entry["gz_bias"] = results["gyro"]["gz_bias"]
        bump[direction] = direction_entry
    entry["bump_crossing"] = bump
    quality = entry.get("quality")
    if not isinstance(quality, dict):
        quality = {}
    quality["drivetrain_v2"] = {"status": "pass", "manual_acceptance": True}
    quality["status"] = "pass"
    entry["quality"] = quality

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result_document = {
        "schema_version": 1,
        "generated_at_utc": _utc_now(),
        "scanner": scanner,
        **results,
    }
    RESULT_PATH.write_text(
        json.dumps(result_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary = REGISTRY_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(REGISTRY_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate the balanced timed drivetrain v2 profile."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and display the active profile without moving the robot",
    )
    args = parser.parse_args()
    scanner = SCANNER_NAME_PATH.read_text(encoding="utf-8").strip()
    production = load_mobility_calibration()
    print("============================================================")
    print("BALANCED TIMED DRIVETRAIN V2 CALIBRATION")
    print("============================================================")
    print(f"Robot: {scanner}")
    print("No forward kick, turn kick, moving-bias tuning, or cmd_a/cmd_b fitting.")
    print("The production registry is changed only after final confirmation.")
    if args.check:
        print(f"Active model: {production.drivetrain_model}")
        print(production.detail())
        if production.uses_balanced_timed_v2:
            print(
                "Forward right/left PWM: "
                f"{production.forward_right_speed}/{production.forward_left_speed}"
            )
            print(
                "Forward seconds/metre:  "
                f"{production.forward_seconds_per_meter:.6f}"
            )
            print(
                "Bump +Y/-Y seconds/metre: "
                f"{production.bump_positive_y_seconds_per_meter:.6f}/"
                f"{production.bump_negative_y_seconds_per_meter:.6f}"
            )
            print(
                "Bump +Y right/left PWM: "
                f"{production.bump_positive_y_right_speed}/"
                f"{production.bump_positive_y_left_speed}"
            )
            print(
                "Bump -Y right/left PWM: "
                f"{production.bump_negative_y_right_speed}/"
                f"{production.bump_negative_y_left_speed}"
            )
        return 0
    if not _yes_no("Start calibration?"):
        return 0

    gyro: Dict[str, object] = {"gz_bias": production.gz_bias, "skipped": True}
    if _yes_no("Calibrate stationary gyro bias?"):
        gyro = _calibrate_stationary_gz()

    right_speed = production.forward_right_speed or DEFAULT_RIGHT_SPEED
    left_speed = production.forward_left_speed or DEFAULT_LEFT_SPEED
    balance_skipped = True
    if _yes_no("Calibrate flat-floor forward PWM balance?"):
        right_speed, left_speed = _calibrate_forward_balance(
            production, float(gyro["gz_bias"])
        )
        balance_skipped = False

    forward_timing: Dict[str, object] = {
        "seconds_per_meter": production.forward_seconds_per_meter or 9.0,
        "skipped": True,
    }
    if _yes_no("Calibrate flat-floor forward distance timing?"):
        forward_timing = _calibrate_forward_timing(
            production,
            float(gyro["gz_bias"]),
            right_speed,
            left_speed,
        )

    turning: Dict[str, object] = {
        "speed": production.turn_speed or DEFAULT_TURN_SPEED,
        "ccw_stop_margin_deg": production.turn_ccw_stop_margin_deg,
        "cw_stop_margin_deg": production.turn_cw_stop_margin_deg,
        "skipped": True,
    }
    if _yes_no("Calibrate turning?"):
        turning = _calibrate_turning(
            production,
            float(gyro["gz_bias"]),
            right_speed,
            left_speed,
            float(forward_timing["seconds_per_meter"]),
        )

    positive_y: Dict[str, float] = {
        "distance_m": BUMP_TARGET_DISTANCE_M,
        "seconds_per_meter": production.bump_positive_y_seconds_per_meter or 9.58,
        "right_speed": float(
            production.bump_positive_y_right_speed or DEFAULT_BUMP_RIGHT_SPEED
        ),
        "left_speed": float(
            production.bump_positive_y_left_speed or DEFAULT_BUMP_LEFT_SPEED
        ),
    }
    negative_y: Dict[str, float] = {
        "distance_m": BUMP_TARGET_DISTANCE_M,
        "seconds_per_meter": production.bump_negative_y_seconds_per_meter or 9.40,
        "right_speed": float(
            production.bump_negative_y_right_speed or DEFAULT_BUMP_RIGHT_SPEED
        ),
        "left_speed": float(
            production.bump_negative_y_left_speed or DEFAULT_BUMP_LEFT_SPEED
        ),
    }
    if _yes_no("Calibrate bump crossing?"):
        positive_y["right_speed"] = float(
            _prompt_int(
                "+Y inner-to-outer right/M1 PWM",
                int(positive_y["right_speed"]),
                1,
                100,
            )
        )
        positive_y["left_speed"] = float(
            _prompt_int(
                "+Y inner-to-outer left/M2 PWM",
                int(positive_y["left_speed"]),
                1,
                100,
            )
        )
        negative_y["right_speed"] = float(
            _prompt_int(
                "-Y outer-to-inner right/M1 PWM",
                int(negative_y["right_speed"]),
                1,
                100,
            )
        )
        negative_y["left_speed"] = float(
            _prompt_int(
                "-Y outer-to-inner left/M2 PWM",
                int(negative_y["left_speed"]),
                1,
                100,
            )
        )

        candidate = _bump_candidate(
            production,
            gz_bias=float(gyro["gz_bias"]),
            right_speed=right_speed,
            left_speed=left_speed,
            seconds_per_meter=float(forward_timing["seconds_per_meter"]),
            turn_speed=int(turning["speed"]),
            ccw_margin=float(turning["ccw_stop_margin_deg"]),
            cw_margin=float(turning["cw_stop_margin_deg"]),
            positive_y=positive_y,
            negative_y=negative_y,
        )
        # Trial order avoids manually carrying Alpha back across the ramp.
        _bump_trial(candidate, direction="positive_y", profile="bump_crossing_up", settings=positive_y)
        candidate = _bump_candidate(
            production,
            gz_bias=float(gyro["gz_bias"]),
            right_speed=right_speed,
            left_speed=left_speed,
            seconds_per_meter=float(forward_timing["seconds_per_meter"]),
            turn_speed=int(turning["speed"]),
            ccw_margin=float(turning["ccw_stop_margin_deg"]),
            cw_margin=float(turning["cw_stop_margin_deg"]),
            positive_y=positive_y,
            negative_y=negative_y,
        )
        _bump_trial(candidate, direction="negative_y", profile="bump_crossing_down", settings=negative_y)
        candidate = _bump_candidate(
            production,
            gz_bias=float(gyro["gz_bias"]),
            right_speed=right_speed,
            left_speed=left_speed,
            seconds_per_meter=float(forward_timing["seconds_per_meter"]),
            turn_speed=int(turning["speed"]),
            ccw_margin=float(turning["ccw_stop_margin_deg"]),
            cw_margin=float(turning["cw_stop_margin_deg"]),
            positive_y=positive_y,
            negative_y=negative_y,
        )
        _bump_verification(candidate, direction="positive_y", profile="bump_crossing_up", settings=positive_y)
        _bump_verification(candidate, direction="negative_y", profile="bump_crossing_down", settings=negative_y)
    results: Dict[str, object] = {
        "gyro": gyro,
        "forward_balance": {
            "right_speed": right_speed,
            "left_speed": left_speed,
            "skipped": balance_skipped,
        },
        "forward_timing": forward_timing,
        "turning": turning,
        "bump_crossing": {
            "positive_y": positive_y,
            "negative_y": negative_y,
        },
    }
    print("\nCalibration complete. Review the summary:")
    print(json.dumps(results, indent=2, sort_keys=True))
    if not _yes_no("Write this calibration to the production registry?"):
        print("Cancelled; production registry was not changed.")
        return 0
    _write_results(scanner, results)
    print(f"Saved result:   {RESULT_PATH}")
    print(f"Updated registry: {REGISTRY_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Motors were stopped by the active motion primitive.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nCALIBRATION FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
