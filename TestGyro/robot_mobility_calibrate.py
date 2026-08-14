#!/usr/bin/env python3
"""One-command guided mobility calibration for one AutoLab robot.

The tool first measures stationary GZ_BIAS for five seconds and uses that
measurement only as the initial candidate for repeated 3 m straight-walking
trials.  The operator accepts the GZ_BIAS that produces a satisfactory physical
path.  It then asks only for measured forward distance during distance
calibration.  The production registry is changed only after both stages are
accepted and the operator approves the conclusion.

The former seven-movement zero-crossing experiment remains below as a disabled
diagnostic function so it can be tested again without reconstructing it.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion


# Fixed sequence. Non-monotonic order reduces correlation between test distance
# and gradual battery/motor heating. The zero point measures kick-only travel.
RAW_SEQUENCE_M = (1.00, 0.00, 2.00, 0.20, 1.50, 0.50, 1.80, 0.10, 1.20, 0.30)
VERIFY_DISTANCE_M = 1.00
GZ_TEST_DISTANCE_M = 0.50
GZ_BIAS_SEQUENCE = (0.0, -9.0, 9.0, -6.0, 6.0, -3.0, 3.0)
STATIONARY_SAMPLE_SEC = 5.0
STATIONARY_DISCARD_SEC = 1.0
STATIONARY_SAMPLE_DT_SEC = 0.02
STATIONARY_TRIM_FRACTION = 0.10
GZ_TUNING_DISTANCE_M = 3.00
GZ_TUNING_CLEARANCE_M = 3.50

MIN_R_SQUARED = 0.995
MAX_FIT_RMSE_M = 0.05
MAX_VERIFY_DISTANCE_ERROR_M = 0.05
MIN_GZ_R_SQUARED = 0.90
MAX_GZ_REGRESSION_RMSE_DEG = 3.0
PREFERRED_GZ_ZERO_MIN = -3.0
PREFERRED_GZ_ZERO_MAX = 3.0
DEFAULT_BUCK_VOLTAGE_V = 8.2

CALIBRATION_DIR = SCRIPT_DIR / "calibration"
ACTIVE_CSV_PATH = CALIBRATION_DIR / "current_calibration.csv"
ARCHIVE_DIR = CALIBRATION_DIR / "archive"
REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"
RESULT_PATH = CALIBRATION_DIR / "calibration_result.json"

CSV_FIELDS = (
    "recorded_at_utc",
    "scanner",
    "phase",
    "round",
    "attempt",
    "accepted",
    "trial_gz_bias",
    "final_yaw_deg",
    "max_abs_yaw_deg",
    "raw_motor_distance_m",
    "desired_distance_m",
    "actual_forward_distance_m",
    "gz_bias",
    "execution_ok",
    "execution_detail",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filename_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _scanner_name() -> str:
    path = ROBOT_ROOT / "scanner_name.txt"
    try:
        scanner = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    if not scanner:
        raise RuntimeError(f"empty robot identity in {path}")
    return scanner


def _archive_active_csv(scanner: str) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not ACTIVE_CSV_PATH.exists():
        return
    archive_path = (
        ARCHIVE_DIR
        / f"{scanner}-{_filename_timestamp()}-calibration.csv"
    )
    shutil.move(str(ACTIVE_CSV_PATH), str(archive_path))


def _append_row(row: Dict[str, object]) -> None:
    write_header = not ACTIVE_CSV_PATH.exists()
    with ACTIVE_CSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def _prompt_float_cm(prompt: str) -> float:
    while True:
        value = input(prompt).strip()
        try:
            number = float(value)
        except ValueError:
            print("Enter a number in centimetres.")
            continue
        if not math.isfinite(number):
            print("Enter a finite number in centimetres.")
            continue
        return number / 100.0


def _prompt_retry(next_experiment: str) -> bool:
    print(f"Next experiment: {next_experiment}")
    while True:
        value = input(
            "Keep this measurement? [Y/n] "
            "(choose Y only after repositioning for the next experiment): "
        ).strip().lower()
        if value in {"", "y", "yes"}:
            return False
        if value in {"n", "no"}:
            return True
        print("Enter Y to keep this measurement, or N to discard and retry it.")


def _execute_raw(
    raw_motor_distance_m: float,
    gz_bias: float,
) -> Tuple[bool, str]:
    requested = max(raw_motor_distance_m, motion.MIN_MOVE_DISTANCE_M)
    return motion._run_move(
        forward=True,
        distance_m=requested,
        calibration_gz_bias=gz_bias,
        motor_distance_override=raw_motor_distance_m,
    )


def _execute_candidate_calibrated(
    desired_distance_m: float,
    cmd_a: float,
    cmd_b: float,
    gz_bias: float,
) -> Tuple[bool, str]:
    motor_distance = max(0.0, cmd_a * float(desired_distance_m) + cmd_b)
    return motion._run_move(
        forward=True,
        distance_m=desired_distance_m,
        calibration_gz_bias=gz_bias,
        motor_distance_override=motor_distance,
    )


def _measure_one(
    scanner: str,
    phase: str,
    attempt: int,
    gz_bias: float,
    raw_motor_distance_m: Optional[float] = None,
    desired_distance_m: Optional[float] = None,
    cmd_a: Optional[float] = None,
    cmd_b: Optional[float] = None,
    next_experiment: str = "calibration summary",
    round_number: int = 1,
) -> Tuple[bool, Optional[float]]:
    if raw_motor_distance_m is not None:
        label = f"raw motor distance {raw_motor_distance_m:.2f} m"
    else:
        label = f"candidate calibrated distance {desired_distance_m:.2f} m"

    print(f"\n{label} — attempt {attempt}")
    print("Starting in 3 seconds...")
    time.sleep(3.0)

    if raw_motor_distance_m is not None:
        ok, detail = _execute_raw(raw_motor_distance_m, gz_bias)
    else:
        if desired_distance_m is None or cmd_a is None or cmd_b is None:
            raise RuntimeError("candidate verification parameters are incomplete")
        ok, detail = _execute_candidate_calibrated(
            desired_distance_m,
            cmd_a,
            cmd_b,
            gz_bias,
        )

    row: Dict[str, object] = {
        "recorded_at_utc": _utc_now(),
        "scanner": scanner,
        "phase": phase,
        "round": round_number,
        "attempt": attempt,
        "accepted": False,
        "raw_motor_distance_m": "" if raw_motor_distance_m is None else raw_motor_distance_m,
        "desired_distance_m": "" if desired_distance_m is None else desired_distance_m,
        "gz_bias": gz_bias,
        "execution_ok": ok,
        "execution_detail": detail,
    }

    if not ok:
        print(f"Movement failed: {detail}")
        retry = _prompt_retry(next_experiment)
        row["accepted"] = False
        _append_row(row)
        if retry:
            return True, None
        raise RuntimeError("calibration stopped after failed movement")

    actual_forward_m = _prompt_float_cm("Actual forward distance (cm): ")
    retry = _prompt_retry(next_experiment)

    row["actual_forward_distance_m"] = actual_forward_m
    row["accepted"] = not retry
    _append_row(row)

    if retry:
        return True, None
    return False, actual_forward_m


def _collect_raw_point(
    scanner: str,
    raw_motor_distance_m: float,
    gz_bias: float,
    next_experiment: str,
    phase: str = "raw_fit",
    round_number: int = 1,
) -> float:
    attempt = 1
    while True:
        retry, measurement = _measure_one(
            scanner=scanner,
            phase=phase,
            attempt=attempt,
            gz_bias=gz_bias,
            raw_motor_distance_m=raw_motor_distance_m,
            next_experiment=next_experiment,
            round_number=round_number,
        )
        if not retry and measurement is not None:
            return measurement
        attempt += 1


def _linear_fit(points: List[Tuple[float, float]]) -> Dict[str, float]:
    if len(points) < 2:
        raise RuntimeError("at least two accepted measurements are required")
    mean_x = statistics.fmean(x for x, _y in points)
    mean_y = statistics.fmean(y for _x, y in points)
    sxx = sum((x - mean_x) ** 2 for x, _y in points)
    if sxx <= 0.0:
        raise RuntimeError("raw test distances must not all be identical")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    actual_a = sxy / sxx
    actual_b = mean_y - actual_a * mean_x
    if actual_a <= 0.0:
        raise RuntimeError(f"invalid fitted distance slope: {actual_a}")
    residuals = [y - (actual_a * x + actual_b) for x, y in points]
    rmse = math.sqrt(statistics.fmean(r * r for r in residuals))
    syy = sum((y - mean_y) ** 2 for _x, y in points)
    r_squared = 1.0 - sum(r * r for r in residuals) / syy if syy else 1.0
    return {
        "actual_a": actual_a,
        "actual_b": actual_b,
        "cmd_a": 1.0 / actual_a,
        "cmd_b": -actual_b / actual_a,
        "rmse_m": rmse,
        "r_squared": r_squared,
    }


def _load_registry() -> Dict[str, object]:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "robots": {}}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"cannot read registry {REGISTRY_PATH}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("robots"), dict):
        raise RuntimeError(f"invalid registry structure in {REGISTRY_PATH}")
    return data


def _previous_entry(registry: Dict[str, object], scanner: str) -> Optional[Dict[str, object]]:
    robots = registry.get("robots")
    if not isinstance(robots, dict):
        return None
    entry = robots.get(scanner)
    return entry if isinstance(entry, dict) else None


_DETAIL_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _detail_number(detail: str, name: str) -> float:
    match = re.search(rf"(?:^|\s){re.escape(name)}=({_DETAIL_NUMBER})(?:\s|$)", detail)
    if match is None:
        raise RuntimeError(f"movement detail is missing {name}")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise RuntimeError(f"movement detail has non-finite {name}")
    return value


def _ordinary_line_fit(points: List[Tuple[float, float]]) -> Dict[str, float]:
    if len(points) < 2:
        raise RuntimeError("at least two regression points are required")
    mean_x = statistics.fmean(x for x, _y in points)
    mean_y = statistics.fmean(y for _x, y in points)
    sxx = sum((x - mean_x) ** 2 for x, _y in points)
    if sxx <= 0.0:
        raise RuntimeError("regression inputs must not all be identical")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in points]
    rmse = math.sqrt(statistics.fmean(value * value for value in residuals))
    syy = sum((y - mean_y) ** 2 for _x, y in points)
    r_squared = 1.0 - sum(value * value for value in residuals) / syy if syy else 1.0
    if slope == 0.0:
        raise RuntimeError("GZ_BIAS regression slope is zero")
    return {
        "intercept_deg": intercept,
        "slope_deg_per_bias": slope,
        "zero_crossing_gz_bias": -intercept / slope,
        "rmse_deg": rmse,
        "r_squared": r_squared,
    }


def _gz_quality(points: List[Tuple[float, float]], fit: Dict[str, float]) -> Dict[str, object]:
    observed = [yaw for _bias, yaw in points]
    zero = fit["zero_crossing_gz_bias"]
    checks = {
        "negative_slope": fit["slope_deg_per_bias"] < 0.0,
        "observed_sign_crossing": min(observed) < 0.0 < max(observed),
        "zero_inside_test_range": min(GZ_BIAS_SEQUENCE) <= zero <= max(GZ_BIAS_SEQUENCE),
        "r_squared": fit["r_squared"] >= MIN_GZ_R_SQUARED,
        "regression_rmse": fit["rmse_deg"] <= MAX_GZ_REGRESSION_RMSE_DEG,
    }
    return {
        "status": "pass" if all(checks.values()) else "review",
        "checks": checks,
        "preferred_central_region": PREFERRED_GZ_ZERO_MIN <= zero <= PREFERRED_GZ_ZERO_MAX,
    }


def _prompt_accept_stage(stage_name: str) -> bool:
    while True:
        answer = input(f"Accept this {stage_name} result or retry this stage? [A/r]: ").strip().lower()
        if answer in {"", "a", "accept"}:
            return True
        if answer in {"r", "retry"}:
            return False
        print("Enter A to accept this result or R to repeat this stage.")


def _prompt_gz_candidate(static_gz_bias: float) -> float:
    """Ask for the next physical-walk candidate; Enter selects the static seed."""
    while True:
        answer = input(
            f"GZ_BIAS for next 3 m trial [{static_gz_bias:+.9f}], or Q to cancel: "
        ).strip()
        if not answer:
            return static_gz_bias
        if answer.lower() in {"q", "quit", "cancel"}:
            raise RuntimeError("GZ_BIAS calibration cancelled before acceptance")
        try:
            value = float(answer)
        except ValueError:
            print("Enter a finite GZ_BIAS number, press Enter for the static value, or Q.")
            continue
        if not math.isfinite(value):
            print("Enter a finite GZ_BIAS number.")
            continue
        return value


def _prompt_gz_trial_decision() -> str:
    while True:
        answer = input("Is this 3 m path satisfactory? [Y/n/q]: ").strip().lower()
        if answer in {"", "y", "yes", "accept"}:
            return "accept"
        if answer in {"n", "no", "retry"}:
            return "retry"
        if answer in {"q", "quit", "cancel"}:
            return "cancel"
        print("Enter Y to accept, N to test another GZ_BIAS, or Q to cancel.")


def _trimmed_mean(values: List[float], fraction: float) -> float:
    if not values:
        raise RuntimeError("no gyro samples collected")
    ordered = sorted(float(value) for value in values)
    trim_count = int(len(ordered) * fraction)
    if trim_count > 0 and 2 * trim_count < len(ordered):
        ordered = ordered[trim_count:-trim_count]
    return statistics.fmean(ordered)


def _measure_stationary_gz_bias() -> Tuple[float, float, int]:
    ok, imu, detail = motion._imu_begin()
    if not ok:
        raise RuntimeError(f"IMU initialization failed: {detail}")

    samples: List[float] = []
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= STATIONARY_SAMPLE_SEC:
            break
        _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
        value = float(gz)
        if elapsed >= STATIONARY_DISCARD_SEC and math.isfinite(value):
            samples.append(value)
        time.sleep(STATIONARY_SAMPLE_DT_SEC)

    if len(samples) < 20:
        raise RuntimeError(f"too few usable gyro samples: {len(samples)}")
    return (
        _trimmed_mean(samples, STATIONARY_TRIM_FRACTION),
        statistics.pstdev(samples),
        len(samples),
    )


def _run_gz_bias_stage(
    scanner: str,
    previous: Optional[Dict[str, object]],
) -> Dict[str, object]:
    print("\n============================================================")
    print("GZ_BIAS CALIBRATION — STATIC SEED AND 3 m WALKING TRIALS")
    print("============================================================")
    print("First, the robot must remain completely stationary for five seconds.")
    print("The first second is discarded; the highest and lowest 10%")
    print("of the remaining samples are removed before averaging.")
    input("Keep the robot stationary and press Enter to begin: ")

    static_gz_bias, spread, sample_count = _measure_stationary_gz_bias()
    previous_bias = previous.get("gz_bias") if isinstance(previous, dict) else None
    _append_row({
        "recorded_at_utc": _utc_now(), "scanner": scanner,
        "phase": "gz_bias_stationary_seed", "round": 1, "attempt": 1,
        "accepted": True, "gz_bias": static_gz_bias, "execution_ok": True,
        "execution_detail": (
            f"stationary_seed_gz_bias={static_gz_bias:+.9f} "
            f"raw_spread={spread:.9f}_deg/s sample_count={sample_count} "
            f"discard_sec={STATIONARY_DISCARD_SEC:.1f} "
            f"trim_fraction={STATIONARY_TRIM_FRACTION:.2f}"
        ),
    })

    print("\nSTATIC GZ_BIAS MEASUREMENT")
    print(f"Previous GZ_BIAS:       {previous_bias if previous_bias is not None else 'none'}")
    print(f"Static GZ_BIAS:         {static_gz_bias:+.9f}")
    print(f"Raw sample spread:      {spread:.9f} deg/s")
    print(f"Usable sample count:    {sample_count}")
    print("The static value is only the initial candidate, not the final result.")
    print("Judge each candidate from the complete physical path of a 3 m walk.")

    trials: List[Dict[str, object]] = []
    candidate = _prompt_gz_candidate(static_gz_bias)
    attempt = 1
    while True:
        print("\n------------------------------------------------------------")
        print(f"GZ_BIAS WALKING TRIAL {attempt}")
        print(f"Candidate GZ_BIAS:      {candidate:+.9f}")
        print(f"Next test:              move forward {GZ_TUNING_DISTANCE_M:.2f} m")
        print(
            f"Reposition the robot and ensure at least {GZ_TUNING_CLEARANCE_M:.2f} m "
            "ahead is clear."
        )
        ready = input("Press Enter to start, or Q to cancel: ").strip().lower()
        if ready in {"q", "quit", "cancel"}:
            raise RuntimeError("GZ_BIAS calibration cancelled before acceptance")

        print("Starting in 3 seconds...")
        time.sleep(3.0)
        ok, detail = motion._run_move(
            forward=True,
            distance_m=GZ_TUNING_DISTANCE_M,
            calibration_gz_bias=candidate,
        )
        trial: Dict[str, object] = {
            "trial": attempt, "gz_bias": candidate,
            "distance_m": GZ_TUNING_DISTANCE_M,
            "execution_ok": ok, "execution_detail": detail,
        }
        row: Dict[str, object] = {
            "recorded_at_utc": _utc_now(), "scanner": scanner,
            "phase": "gz_bias_physical_walk", "round": 1,
            "attempt": attempt, "accepted": False,
            "trial_gz_bias": candidate,
            "desired_distance_m": GZ_TUNING_DISTANCE_M,
            "gz_bias": candidate, "execution_ok": ok,
            "execution_detail": detail,
        }
        if not ok:
            print(f"Movement failed: {detail}")
            _append_row(row)
            trials.append(trial)
            decision = input("Retry the same GZ_BIAS trial? [Y/n/q]: ").strip().lower()
            if decision in {"q", "quit", "cancel"}:
                raise RuntimeError("GZ_BIAS calibration cancelled after failed movement")
            if decision in {"n", "no"}:
                candidate = _prompt_gz_candidate(static_gz_bias)
            attempt += 1
            continue

        print(f"Movement completed: {detail}")
        decision = _prompt_gz_trial_decision()
        accepted = decision == "accept"
        row["accepted"] = accepted
        trial["accepted"] = accepted
        _append_row(row)
        trials.append(trial)
        if decision == "cancel":
            raise RuntimeError("GZ_BIAS calibration cancelled before acceptance")
        if accepted:
            quality: Dict[str, object] = {
                "status": "pass",
                "checks": {
                    "finite_gz_bias": math.isfinite(candidate),
                    "minimum_stationary_sample_count": sample_count >= 20,
                    "physical_3m_walk_accepted": True,
                },
            }
            print("\nGZ_BIAS CALIBRATION RESULT")
            print(f"Accepted GZ_BIAS:       {candidate:+.9f}")
            print(f"Accepted walking trial: {attempt}")
            print("This accepted result will be used during distance calibration.")
            return {
                "method": "stationary_seed_manual_3m_walk",
                "accepted_round": 1, "accepted_trial": attempt,
                "gz_bias": candidate,
                "static_measurement": {
                    "gz_bias": static_gz_bias,
                    "sample_duration_sec": STATIONARY_SAMPLE_SEC,
                    "discard_sec": STATIONARY_DISCARD_SEC,
                    "trim_fraction": STATIONARY_TRIM_FRACTION,
                    "raw_sample_spread_deg_per_sec": spread,
                    "usable_sample_count": sample_count,
                },
                "trials": trials, "quality": quality,
            }
        candidate = _prompt_gz_candidate(static_gz_bias)
        attempt += 1


def _run_gz_bias_stage_seven_movement_disabled(
    scanner: str,
    previous: Optional[Dict[str, object]],
) -> Dict[str, object]:
    """Disabled diagnostic retained for possible future comparison.

    This function is intentionally not called by the production calibration
    workflow.  It contains the former seven self-walking zero-crossing method.
    """
    round_number = 1
    while True:
        print("\n============================================================")
        print(f"GZ_BIAS CALIBRATION — ROUND {round_number}")
        print("============================================================")
        print("Seven automatic 0.50 m movements will run in this order:")
        print("GZ_BIAS: " + ", ".join(f"{value:+g}" for value in GZ_BIAS_SEQUENCE))
        print("Total commanded travel: 3.50 m.")
        print("No manual distance or lateral-shift measurement is required.")
        input("Confirm the open movement area is clear, then press Enter: ")

        points: List[Tuple[float, float]] = []
        measurements: List[Dict[str, object]] = []
        for index, trial_bias in enumerate(GZ_BIAS_SEQUENCE, start=1):
            attempt = 1
            while True:
                print(f"\nGZ trial {index}/7: bias={trial_bias:+.1f}; starting in 3 seconds...")
                time.sleep(3.0)
                ok, detail = motion._run_move(
                    forward=True,
                    distance_m=GZ_TEST_DISTANCE_M,
                    calibration_gz_bias=trial_bias,
                )
                row: Dict[str, object] = {
                    "recorded_at_utc": _utc_now(), "scanner": scanner,
                    "phase": "gz_bias_regression", "round": round_number,
                    "attempt": attempt, "accepted": False,
                    "trial_gz_bias": trial_bias, "desired_distance_m": GZ_TEST_DISTANCE_M,
                    "gz_bias": trial_bias, "execution_ok": ok,
                    "execution_detail": detail,
                }
                try:
                    if not ok:
                        raise RuntimeError(detail)
                    final_yaw = _detail_number(detail, "final_yaw_deg")
                    max_abs_yaw = _detail_number(detail, "max_abs_yaw_deg")
                except Exception as exc:
                    print(f"GZ trial failed: {exc}")
                    _append_row(row)
                    retry = input("Retry this movement? [Y/n]: ").strip().lower()
                    if retry in {"", "y", "yes"}:
                        attempt += 1
                        continue
                    raise RuntimeError("GZ_BIAS calibration stopped after failed movement") from exc
                row.update({"accepted": True, "final_yaw_deg": final_yaw,
                            "max_abs_yaw_deg": max_abs_yaw})
                _append_row(row)
                points.append((trial_bias, final_yaw))
                measurements.append({"trial_gz_bias": trial_bias,
                                     "final_yaw_deg": final_yaw,
                                     "max_abs_yaw_deg": max_abs_yaw})
                print(f"final_yaw_deg={final_yaw:+.3f}; max_abs_yaw_deg={max_abs_yaw:.3f}")
                break

        fit = _ordinary_line_fit(points)
        quality = _gz_quality(points, fit)
        old_bias = previous.get("gz_bias") if isinstance(previous, dict) else None
        print("\nGZ_BIAS CALIBRATION RESULT")
        print(f"Regression: final_yaw = {fit['intercept_deg']:+.6f} "
              f"{fit['slope_deg_per_bias']:+.6f} * GZ_BIAS")
        print(f"Zero crossing:           {fit['zero_crossing_gz_bias']:+.9f}")
        print(f"R-squared:              {fit['r_squared']:.6f}")
        print(f"Regression RMSE:        {fit['rmse_deg']:.3f} deg")
        print(f"Quality:                {str(quality['status']).upper()}")
        if old_bias is not None:
            print(f"Previous GZ_BIAS:       {old_bias}")
        failed = [name for name, passed in quality["checks"].items() if not passed]
        if failed:
            print("Failed checks:           " + ", ".join(failed))
        if not quality["preferred_central_region"]:
            print("Notice: zero crossing is outside the preferred [-3,+3] region.")
        if _prompt_accept_stage("GZ_BIAS calibration"):
            return {"accepted_round": round_number, "measurements": measurements,
                    "regression": fit, "quality": quality}
        round_number += 1


def _percent_change(current: float, previous: float) -> Optional[float]:
    if previous == 0.0:
        return None
    return 100.0 * (current - previous) / abs(previous)


def _comparison(
    previous: Optional[Dict[str, object]],
    gz_bias: float,
    fit: Dict[str, float],
) -> Dict[str, object]:
    if previous is None:
        return {"available": False, "major_difference": False}
    old_model = previous.get("distance_model")
    if not isinstance(old_model, dict):
        old_model = {}
    try:
        old_gz = float(previous.get("gz_bias"))
    except (TypeError, ValueError):
        old_gz = math.nan

    values: Dict[str, object] = {
        "available": True,
        "previous_gz_bias": old_gz,
        "gz_bias_change": gz_bias - old_gz if math.isfinite(old_gz) else None,
    }
    major = math.isfinite(old_gz) and abs(gz_bias - old_gz) >= 0.05
    for name in ("actual_a", "actual_b", "cmd_a", "cmd_b"):
        try:
            old_value = float(old_model[name])
        except (KeyError, TypeError, ValueError):
            values[f"previous_{name}"] = None
            values[f"{name}_change_percent"] = None
            continue
        values[f"previous_{name}"] = old_value
        change = _percent_change(fit[name], old_value)
        values[f"{name}_change_percent"] = change
        if name in {"actual_a", "cmd_a"} and change is not None and abs(change) >= 5.0:
            major = True
        if name in {"actual_b", "cmd_b"} and abs(fit[name] - old_value) >= 0.03:
            major = True
    values["major_difference"] = major
    return values


def _write_result(result: Dict[str, object]) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)


def _prompt_final_action(has_previous: bool) -> str:
    while True:
        if has_previous:
            prompt = "Update registry, keep previous, or repeat? [u/K/r]: "
        else:
            prompt = "Create registry entry or repeat? [u/r]: "
        value = input(prompt).strip().lower()
        if value in {"u", "update"}:
            return "update"
        if has_previous and value in {"", "k", "keep"}:
            return "keep"
        if value in {"r", "repeat"}:
            return "repeat"
        print("Enter U to update, K to keep the previous entry, or R to repeat.")


def _write_registry(
    scanner: str,
    gz_bias: float,
    fit: Dict[str, float],
    quality: Dict[str, object],
    kick_distance_m: float,
) -> None:
    registry = _load_registry()
    robots = registry["robots"]
    assert isinstance(robots, dict)

    if REGISTRY_PATH.exists():
        backup = REGISTRY_PATH.with_name(
            f"{REGISTRY_PATH.stem}.{_filename_timestamp()}.bak.json"
        )
        shutil.copy2(REGISTRY_PATH, backup)

    previous = robots.get(scanner)
    if isinstance(previous, dict):
        buck_voltage_v = previous.get("buck_voltage_v", DEFAULT_BUCK_VOLTAGE_V)
    else:
        buck_voltage_v = DEFAULT_BUCK_VOLTAGE_V

    robots[scanner] = {
        "buck_voltage_v": buck_voltage_v,
        "gz_bias": gz_bias,
        "distance_model": {
            "actual_a": fit["actual_a"],
            "actual_b": fit["actual_b"],
            "cmd_a": fit["cmd_a"],
            "cmd_b": fit["cmd_b"],
        },
        "short_move": {
            "kick_distance_m": kick_distance_m,
            "skip_threshold_m": kick_distance_m / 2.0,
        },
        "calibrated_at_utc": _utc_now(),
        "source": str(ACTIVE_CSV_PATH),
        "quality": quality,
        "production_loader_enabled": True,
    }

    temporary = REGISTRY_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(REGISTRY_PATH)


def _run_distance_stage(scanner: str, gz_bias: float) -> Dict[str, object]:
    round_number = 1
    while True:
        print("\n============================================================")
        print(f"DISTANCE CALIBRATION — ROUND {round_number}")
        print("============================================================")
        print("Ten raw motor-distance trials will run in this order:")
        print("  " + " -> ".join(f"{value:.2f} m" for value in RAW_SEQUENCE_M))
        print(f"A calibrated {VERIFY_DISTANCE_M:.2f} m verification follows the fit.")
        print("For each trial, enter only the measured forward distance.")
        print("The tool will show the next trial before you keep the current measurement.")
        input("Place the robot for the first distance trial, then press Enter: ")

        raw_points: List[Tuple[float, float]] = []
        kick_distance_m: Optional[float] = None
        for index, raw_distance in enumerate(RAW_SEQUENCE_M):
            next_text = (
                f"raw motor distance {RAW_SEQUENCE_M[index + 1]:.2f} m"
                if index + 1 < len(RAW_SEQUENCE_M)
                else f"candidate calibrated distance {VERIFY_DISTANCE_M:.2f} m"
            )
            actual_forward = _collect_raw_point(
                scanner=scanner, raw_motor_distance_m=raw_distance,
                gz_bias=gz_bias, next_experiment=next_text,
                round_number=round_number,
            )
            raw_points.append((raw_distance, actual_forward))
            if raw_distance == 0.0:
                kick_distance_m = actual_forward
        if kick_distance_m is None:
            raise RuntimeError("accepted kick-only measurement is missing")

        fit = _linear_fit(raw_points)
        verification_attempt = 1
        while True:
            retry, verification = _measure_one(
                scanner=scanner, phase="candidate_verification",
                attempt=verification_attempt, gz_bias=gz_bias,
                desired_distance_m=VERIFY_DISTANCE_M,
                cmd_a=fit["cmd_a"], cmd_b=fit["cmd_b"],
                next_experiment="distance calibration result",
                round_number=round_number,
            )
            if not retry and verification is not None:
                break
            verification_attempt += 1

        verify_error_m = abs(verification - VERIFY_DISTANCE_M)
        checks = {
            "r_squared": fit["r_squared"] >= MIN_R_SQUARED,
            "fit_rmse": fit["rmse_m"] <= MAX_FIT_RMSE_M,
            "verify_distance": verify_error_m <= MAX_VERIFY_DISTANCE_ERROR_M,
        }
        quality: Dict[str, object] = {
            "status": "pass" if all(checks.values()) else "review",
            "checks": checks, "fit_rmse_m": fit["rmse_m"],
            "r_squared": fit["r_squared"],
            "verification_distance_error_m": verify_error_m,
        }
        print("\nDISTANCE CALIBRATION RESULT")
        print(f"Distance actual_a/b:    {fit['actual_a']:.12f}, {fit['actual_b']:.12f}")
        print(f"Distance cmd_a/b:       {fit['cmd_a']:.12f}, {fit['cmd_b']:.12f}")
        print(f"Fit RMSE:              {fit['rmse_m'] * 100:.2f} cm")
        print(f"Fit R-squared:         {fit['r_squared']:.6f}")
        print(f"1 m verification error:{verify_error_m * 100: .2f} cm")
        print(f"Kick-only distance:    {kick_distance_m * 100:.2f} cm")
        print(f"Short-move skip below: {kick_distance_m * 50:.2f} cm")
        print(f"Quality:               {str(quality['status']).upper()}")
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            print("Failed checks:          " + ", ".join(failed))
        if _prompt_accept_stage("distance calibration"):
            return {"accepted_round": round_number, "raw_points": raw_points,
                    "regression": fit, "verification_distance_m": verification,
                    "kick_distance_m": kick_distance_m, "quality": quality}
        round_number += 1


def _run_session(scanner: str) -> str:
    _archive_active_csv(scanner)
    registry = _load_registry()
    previous = _previous_entry(registry, scanner)
    print("============================================================")
    print(f"Mobility calibration: {scanner}")
    if previous is None:
        print("Previous robot calibration: none")
    else:
        old_model = previous.get("distance_model") or {}
        print(f"Previous GZ_BIAS:          {previous.get('gz_bias')}")
        if isinstance(old_model, dict):
            print("Previous distance cmd_a/b: "
                  f"{old_model.get('cmd_a')}, {old_model.get('cmd_b')}")

    gz_stage = _run_gz_bias_stage(scanner, previous)
    gz_bias = float(gz_stage["gz_bias"])
    print("\nNext stage overview:")
    print("Distance trial order: " + " -> ".join(f"{value:.2f} m" for value in RAW_SEQUENCE_M))
    print(f"Then one {VERIFY_DISTANCE_M:.2f} m verification movement.")
    distance_stage = _run_distance_stage(scanner, gz_bias)
    fit = distance_stage["regression"]
    kick_distance_m = float(distance_stage["kick_distance_m"])
    comparison = _comparison(previous, gz_bias, fit)
    combined_quality = {
        "status": "pass" if (
            gz_stage["quality"]["status"] == "pass"
            and distance_stage["quality"]["status"] == "pass"
        ) else "review",
        "gz_bias": gz_stage["quality"],
        "distance": distance_stage["quality"],
    }
    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(), "scanner": scanner,
        "gz_bias_calibration": gz_stage,
        "distance_calibration": distance_stage,
        "combined_quality": combined_quality,
        "candidate": {
            "gz_bias": gz_bias, "distance_model": fit,
            "short_move": {"kick_distance_m": kick_distance_m,
                           "skip_threshold_m": kick_distance_m / 2.0},
        },
        "comparison_with_previous": comparison,
        "session_csv": str(ACTIVE_CSV_PATH),
    }
    _write_result(result)

    print("\n============================================================")
    print("OVERALL CALIBRATION CONCLUSION")
    print("============================================================")
    print(f"Combined quality:          {str(combined_quality['status']).upper()}")
    print(f"Accepted GZ_BIAS:          {gz_bias:+.9f}")
    print("GZ calibration method:     static seed + accepted 3 m physical walk")
    print(
        "GZ raw sample spread:      "
        f"{gz_stage['static_measurement']['raw_sample_spread_deg_per_sec']:.9f} deg/s"
    )
    print(f"Accepted GZ walking trial: {gz_stage['accepted_trial']}")
    print(f"Distance cmd_a/b:          {fit['cmd_a']:.12f}, {fit['cmd_b']:.12f}")
    print(f"Distance fit RMSE:         {fit['rmse_m'] * 100:.2f} cm")
    print(f"Distance fit R-squared:    {fit['r_squared']:.6f}")
    print(f"Kick-only distance:        {kick_distance_m * 100:.2f} cm")
    print(f"Short-move skip below:     {kick_distance_m * 50:.2f} cm")
    if comparison.get("available"):
        print("Previous comparison:       " + (
            "MAJOR DIFFERENCE" if comparison.get("major_difference") else "no major difference"))
        print(f"GZ_BIAS old/new:           {comparison.get('previous_gz_bias')} / {gz_bias:+.9f}")
    else:
        print("Previous comparison:       first calibration")
    print(f"Session CSV:               {ACTIVE_CSV_PATH}")
    print(f"Quality report:            {RESULT_PATH}")

    action = _prompt_final_action(previous is not None)
    if action == "update":
        _write_registry(scanner, gz_bias, fit, combined_quality, kick_distance_m)
        print(f"Registry updated:          {REGISTRY_PATH}")
        print("Production loader:         enabled for the next motion primitive")
    elif action == "keep":
        print("Previous registry entry retained.")
    return action


def main() -> int:
    scanner = _scanner_name()
    while True:
        action = _run_session(scanner)
        if action != "repeat":
            return 0
        print("\nRepeating calibration with a fresh active session...")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCalibration cancelled; production calibration was not changed.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nCALIBRATION FAILED: {exc}", file=sys.stderr)
        print("Production calibration was not changed.", file=sys.stderr)
        raise SystemExit(2)
