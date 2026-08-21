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
import copy
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
from config import MOTOR_MOVE_DISTANCE_MODEL
from robot_mobility_calibration_registry import MobilityCalibrationSnapshot


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
BUMP_POSITIVE_Y_START = (9.06, 4.30, 90.0)
BUMP_NEGATIVE_Y_START = (9.06, 6.10, 270.0)
BUMP_DEFAULT_COMMAND_DISTANCE_M = 2.0
DEFAULT_FORWARD_KICK_RIGHT_SPEED = 40
DEFAULT_FORWARD_KICK_LEFT_SPEED = 40

# These session values are installed into every calibration-only snapshot after
# the startup phase is accepted or skipped. Production snapshots come from the
# registry loader instead.
SESSION_FORWARD_KICK_RIGHT_SPEED = DEFAULT_FORWARD_KICK_RIGHT_SPEED
SESSION_FORWARD_KICK_LEFT_SPEED = DEFAULT_FORWARD_KICK_LEFT_SPEED

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
    "right_kick_speed",
    "left_kick_speed",
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
        if number < 0.0:
            print("Enter a non-negative distance in centimetres.")
            continue
        return number / 100.0


def _prompt_buck_voltage(previous: Optional[Dict[str, object]]) -> float:
    previous_value = (
        previous.get("buck_voltage_v", DEFAULT_BUCK_VOLTAGE_V)
        if isinstance(previous, dict)
        else DEFAULT_BUCK_VOLTAGE_V
    )
    try:
        default_value = float(previous_value)
    except (TypeError, ValueError):
        default_value = DEFAULT_BUCK_VOLTAGE_V

    while True:
        answer = input(
            f"Measured motor buck voltage for this calibration [{default_value:.1f} V]: "
        ).strip()
        if not answer:
            return default_value
        try:
            value = float(answer)
        except ValueError:
            print("Enter a finite positive voltage.")
            continue
        if not math.isfinite(value) or value <= 0.0:
            print("Enter a finite positive voltage.")
            continue
        return value


def _prompt_bump_distance(direction_label: str, default_value: float) -> float:
    while True:
        answer = input(
            f"Command distance for {direction_label} [{default_value:.3f} m]: "
        ).strip()
        if not answer:
            return default_value
        try:
            value = float(answer)
        except ValueError:
            print("Enter a finite positive distance in metres.")
            continue
        if not math.isfinite(value) or not (0.0 < value <= motion.MAX_MOVE_DISTANCE_M):
            print(
                "Enter a distance greater than zero and no more than "
                f"{motion.MAX_MOVE_DISTANCE_M:.3f} m."
            )
            continue
        return value


def _prompt_motor_speed(prompt: str, default_value: int) -> int:
    while True:
        answer = input(f"{prompt} [{default_value}]: ").strip()
        if not answer:
            return default_value
        try:
            value = int(answer)
        except ValueError:
            print("Enter an integer motor speed from 0 through 100.")
            continue
        if 0 <= value <= 100:
            return value
        print("Enter an integer motor speed from 0 through 100.")


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
    scanner: str,
    raw_motor_distance_m: float,
    gz_bias: float,
) -> Tuple[bool, str]:
    requested = max(raw_motor_distance_m, motion.MIN_MOVE_DISTANCE_M)
    return motion._run_move(
        forward=True,
        distance_m=requested,
        calibration=_calibration_bootstrap_snapshot(scanner, gz_bias),
        calibration_gz_bias=gz_bias,
        motor_distance_override=raw_motor_distance_m,
    )


def _execute_candidate_calibrated(
    scanner: str,
    desired_distance_m: float,
    cmd_a: float,
    cmd_b: float,
    gz_bias: float,
) -> Tuple[bool, str]:
    motor_distance = max(0.0, cmd_a * float(desired_distance_m) + cmd_b)
    return motion._run_move(
        forward=True,
        distance_m=desired_distance_m,
        calibration=_calibration_bootstrap_snapshot(
            scanner,
            gz_bias,
            cmd_a=cmd_a,
            cmd_b=cmd_b,
        ),
        calibration_gz_bias=gz_bias,
        motor_distance_override=motor_distance,
    )


def _calibration_bootstrap_snapshot(
    scanner: str,
    gz_bias: float,
    *,
    cmd_a: Optional[float] = None,
    cmd_b: Optional[float] = None,
):
    """Build a calibration-only snapshot without consulting production state.

    The strict production loader must continue to reject robots that have no
    accepted registry entry.  Guided calibration is the one explicit bootstrap
    path: raw trials use the generic starting distance model, while candidate
    verification may supply the newly fitted coefficients.
    """
    model = MOTOR_MOVE_DISTANCE_MODEL
    effective_cmd_a = float(model["cmd_a"]) if cmd_a is None else float(cmd_a)
    effective_cmd_b = float(model["cmd_b"]) if cmd_b is None else float(cmd_b)
    return MobilityCalibrationSnapshot(
        scanner=scanner,
        gz_bias=float(gz_bias),
        cmd_a=effective_cmd_a,
        cmd_b=effective_cmd_b,
        source="calibration_bootstrap",
        warning="calibration_bootstrap_not_for_production",
        forward_kick_right_speed=SESSION_FORWARD_KICK_RIGHT_SPEED,
        forward_kick_left_speed=SESSION_FORWARD_KICK_LEFT_SPEED,
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
        ok, detail = _execute_raw(scanner, raw_motor_distance_m, gz_bias)
    else:
        if desired_distance_m is None or cmd_a is None or cmd_b is None:
            raise RuntimeError("candidate verification parameters are incomplete")
        ok, detail = _execute_candidate_calibrated(
            scanner,
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


def _finite_number(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _previous_gz_bias(previous: Optional[Dict[str, object]]) -> Optional[float]:
    if not isinstance(previous, dict):
        return None
    return _finite_number(previous.get("gz_bias"))


def _previous_startup_values(
    previous: Optional[Dict[str, object]],
) -> Optional[Tuple[int, int]]:
    if not isinstance(previous, dict):
        return None
    move_startup = previous.get("move_startup")
    if not isinstance(move_startup, dict):
        return None
    forward = move_startup.get("forward")
    if not isinstance(forward, dict):
        return None
    right = _finite_number(forward.get("right_kick_speed"))
    left = _finite_number(forward.get("left_kick_speed"))
    if right is None or left is None or not right.is_integer() or not left.is_integer():
        return None
    right_int = int(right)
    left_int = int(left)
    if not (0 <= right_int <= 100 and 0 <= left_int <= 100):
        return None
    return right_int, left_int


def _previous_distance_values(
    previous: Optional[Dict[str, object]],
) -> Optional[Tuple[Dict[str, float], float]]:
    if not isinstance(previous, dict):
        return None
    model = previous.get("distance_model")
    short_move = previous.get("short_move")
    if not isinstance(model, dict) or not isinstance(short_move, dict):
        return None
    values = {name: _finite_number(model.get(name)) for name in
              ("actual_a", "actual_b", "cmd_a", "cmd_b")}
    kick_distance_m = _finite_number(short_move.get("kick_distance_m"))
    if any(value is None for value in values.values()) or kick_distance_m is None:
        return None
    if values["actual_a"] <= 0.0 or values["cmd_a"] <= 0.0 or kick_distance_m < 0.0:
        return None
    return ({name: float(value) for name, value in values.items()}, kick_distance_m)


def _previous_bump_values(
    previous: Optional[Dict[str, object]],
) -> Optional[Dict[str, float]]:
    if not isinstance(previous, dict):
        return None
    bump = previous.get("bump_crossing")
    if not isinstance(bump, dict):
        return None
    result: Dict[str, float] = {}
    for direction in ("positive_y", "negative_y"):
        direction_entry = bump.get(direction)
        if not isinstance(direction_entry, dict):
            return None
        value = _finite_number(direction_entry.get("command_distance_m"))
        if value is None or not (0.0 < value <= motion.MAX_MOVE_DISTANCE_M):
            return None
        result[direction] = value
    return result


def _prompt_skip_phase(phase_label: str, existing_summary: Optional[str]) -> bool:
    print("\n============================================================")
    print(f"{phase_label} — PHASE SELECTION")
    print("============================================================")
    if existing_summary is None:
        print("No complete existing calibration is available; this phase cannot be skipped.")
        return False
    print(f"Value retained if skipped: {existing_summary}")
    while True:
        answer = input(
            "Keep this value and skip this phase? [y/N]: "
        ).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            print(f"Keeping the current {phase_label} value unchanged.")
            return True
        print("Enter Y to keep the existing value, or N to run this phase.")


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
            calibration=_calibration_bootstrap_snapshot(scanner, candidate),
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


def _prompt_startup_trial_decision() -> str:
    while True:
        answer = input(
            "Is the startup direction satisfactory? [y/N/q]: "
        ).strip().lower()
        if answer in {"y", "yes"}:
            return "accept"
        if answer in {"", "n", "no", "r", "retry"}:
            return "retry"
        if answer in {"q", "quit", "cancel"}:
            return "cancel"
        print("Enter Y to accept, N to adjust and retry, or Q to cancel.")


def _run_startup_stage(
    scanner: str,
    previous: Optional[Dict[str, object]],
) -> Dict[str, object]:
    old_values = _previous_startup_values(previous)
    right_speed = (
        old_values[0] if old_values is not None
        else DEFAULT_FORWARD_KICK_RIGHT_SPEED
    )
    left_speed = (
        old_values[1] if old_values is not None
        else DEFAULT_FORWARD_KICK_LEFT_SPEED
    )
    attempt = 1

    print("\n============================================================")
    print("FORWARD STARTUP BALANCE CALIBRATION")
    print("============================================================")
    print(
        f"Each trial runs only the {motion.MOVE_KICK_TIME_SEC:.2f}-second "
        "forward kick, then stops."
    )
    print("Judge the immediate heading change, not the distance travelled.")
    print("If the robot twists left, increase RIGHT or reduce LEFT.")
    print("If the robot twists right, increase LEFT or reduce RIGHT.")

    while True:
        print(f"\nStartup trial {attempt}")
        right_speed = _prompt_motor_speed("Right-side kick speed", right_speed)
        left_speed = _prompt_motor_speed("Left-side kick speed", left_speed)
        input(
            "Place the robot on the marked heading and clear the short path, "
            "then press Enter: "
        )
        print("Starting in 3 seconds...")
        time.sleep(3.0)
        ok, detail = motion._run_forward_startup_trial(right_speed, left_speed)
        row = {
            "recorded_at_utc": _utc_now(),
            "scanner": scanner,
            "phase": "forward_startup_balance",
            "round": 1,
            "attempt": attempt,
            "accepted": False,
            "execution_ok": ok,
            "execution_detail": detail,
            "right_kick_speed": right_speed,
            "left_kick_speed": left_speed,
        }
        print(f"Startup trial {'completed' if ok else 'failed'}: {detail}")
        if not ok:
            _append_row(row)
            answer = input("Retry this startup trial? [Y/n]: ").strip().lower()
            if answer in {"", "y", "yes"}:
                attempt += 1
                continue
            raise RuntimeError("startup calibration stopped after failed movement")

        decision = _prompt_startup_trial_decision()
        row["accepted"] = decision == "accept"
        _append_row(row)
        if decision == "cancel":
            raise RuntimeError("startup calibration cancelled before acceptance")
        if decision == "accept":
            return {
                "accepted_trial": attempt,
                "right_kick_speed": right_speed,
                "left_kick_speed": left_speed,
                "kick_time_sec": motion.MOVE_KICK_TIME_SEC,
                "quality": {
                    "status": "pass",
                    "manual_startup_heading_accepted": True,
                },
            }
        attempt += 1


def _write_registry(
    scanner: str,
    buck_voltage_v: float,
    startup_stage: Optional[Dict[str, object]],
    gz_stage: Optional[Dict[str, object]],
    distance_stage: Optional[Dict[str, object]],
    bump_stage: Optional[Dict[str, object]],
) -> None:
    registry = _load_registry()
    robots = registry["robots"]
    assert isinstance(robots, dict)

    if REGISTRY_PATH.exists():
        backup = REGISTRY_PATH.with_name(
            f"{REGISTRY_PATH.stem}.{_filename_timestamp()}.bak.json"
        )
        shutil.copy2(REGISTRY_PATH, backup)

    old_entry = robots.get(scanner)
    entry: Dict[str, object] = (
        copy.deepcopy(old_entry) if isinstance(old_entry, dict) else {}
    )
    entry["buck_voltage_v"] = buck_voltage_v
    old_quality = entry.get("quality")
    quality: Dict[str, object] = (
        copy.deepcopy(old_quality) if isinstance(old_quality, dict) else {}
    )

    if startup_stage is not None:
        entry["move_startup"] = {
            "forward": {
                "right_kick_speed": int(startup_stage["right_kick_speed"]),
                "left_kick_speed": int(startup_stage["left_kick_speed"]),
            },
        }
        quality["move_startup"] = copy.deepcopy(startup_stage["quality"])

    if gz_stage is not None:
        entry["gz_bias"] = float(gz_stage["gz_bias"])
        quality["gz_bias"] = copy.deepcopy(gz_stage["quality"])

    if distance_stage is not None:
        fit = distance_stage["regression"]
        assert isinstance(fit, dict)
        kick_distance_m = float(distance_stage["kick_distance_m"])
        entry["distance_model"] = {
            "actual_a": fit["actual_a"],
            "actual_b": fit["actual_b"],
            "cmd_a": fit["cmd_a"],
            "cmd_b": fit["cmd_b"],
        }
        entry["short_move"] = {
            "kick_distance_m": kick_distance_m,
            "skip_threshold_m": kick_distance_m / 2.0,
        }
        quality["distance"] = copy.deepcopy(distance_stage["quality"])

    if bump_stage is not None:
        entry["bump_crossing"] = {
            "positive_y": {
                "command_distance_m": bump_stage["positive_y_command_distance_m"],
                "start": {"x": 9.06, "y": 4.30, "heading_deg": 90.0},
            },
            "negative_y": {
                "command_distance_m": bump_stage["negative_y_command_distance_m"],
                "start": {"x": 9.06, "y": 6.10, "heading_deg": 270.0},
            },
        }
        quality["bump_crossing"] = copy.deepcopy(bump_stage["quality"])

    phase_statuses = [
        value.get("status") for key, value in quality.items()
        if key in {"move_startup", "gz_bias", "distance", "bump_crossing"}
        and isinstance(value, dict)
    ]
    if phase_statuses:
        quality["status"] = (
            "pass" if all(status == "pass" for status in phase_statuses) else "review"
        )
    entry["quality"] = quality
    entry["calibrated_at_utc"] = _utc_now()
    entry["source"] = str(ACTIVE_CSV_PATH)
    entry["production_loader_enabled"] = True
    robots[scanner] = entry

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


def _previous_bump_distance(
    previous: Optional[Dict[str, object]],
    direction: str,
) -> float:
    try:
        bump = previous["bump_crossing"]  # type: ignore[index]
        direction_entry = bump[direction]  # type: ignore[index]
        value = float(direction_entry["command_distance_m"])  # type: ignore[index]
        if math.isfinite(value) and 0.0 < value <= motion.MAX_MOVE_DISTANCE_M:
            return value
    except (KeyError, TypeError, ValueError):
        pass
    return BUMP_DEFAULT_COMMAND_DISTANCE_M


def _run_bump_trial(
    scanner: str,
    direction: str,
    start: Tuple[float, float, float],
    default_distance_m: float,
    gz_bias: float,
    cmd_a: float,
    cmd_b: float,
    round_number: int,
) -> Tuple[float, bool, str]:
    x, y, heading_deg = start
    print("\n------------------------------------------------------------")
    print(f"BUMP CROSSING — {direction}")
    print(
        f"Place the robot at ({x:.2f}, {y:.2f}) facing {heading_deg:.0f} degrees."
    )
    distance_m = _prompt_bump_distance(direction, default_distance_m)
    input("Confirm placement and clear the path, then press Enter to start: ")
    print("Starting in 3 seconds...")
    time.sleep(3.0)
    calibration = _calibration_bootstrap_snapshot(
        scanner,
        gz_bias,
        cmd_a=cmd_a,
        cmd_b=cmd_b,
    )
    ok, detail = motion._run_move(
        forward=True,
        distance_m=distance_m,
        move_profile=(
            "bump_crossing_up"
            if direction == "positive_y"
            else "bump_crossing_down"
        ),
        calibration=calibration,
        calibration_gz_bias=gz_bias,
    )
    _append_row({
        "recorded_at_utc": _utc_now(),
        "scanner": scanner,
        "phase": f"bump_crossing_{direction}",
        "round": round_number,
        "attempt": 1,
        "accepted": False,
        "desired_distance_m": distance_m,
        "gz_bias": gz_bias,
        "execution_ok": ok,
        "execution_detail": detail,
    })
    print(f"Movement {'completed' if ok else 'failed'}: {detail}")
    return distance_m, ok, detail


def _prompt_bump_pair_decision(both_executed: bool) -> str:
    while True:
        if both_executed:
            answer = input(
                "Are both bump-crossing command distances satisfactory? [Y/n/q]: "
            ).strip().lower()
            if answer in {"", "y", "yes"}:
                return "accept"
        else:
            answer = input(
                "A movement failed. Repeat both trials or cancel? [R/q]: "
            ).strip().lower()
        if answer in {"n", "no", "r", "repeat"}:
            return "repeat"
        if answer in {"q", "quit", "cancel"}:
            return "cancel"
        print("Enter Y to accept both, N/R to repeat both, or Q to cancel.")


def _run_bump_crossing_stage(
    scanner: str,
    previous: Optional[Dict[str, object]],
    gz_bias: float,
    cmd_a: float,
    cmd_b: float,
) -> Dict[str, object]:
    positive_default = _previous_bump_distance(previous, "positive_y")
    negative_default = _previous_bump_distance(previous, "negative_y")
    round_number = 1
    while True:
        print("\n============================================================")
        print(f"BUMP-CROSSING CALIBRATION — ROUND {round_number}")
        print("============================================================")
        positive, positive_ok, positive_detail = _run_bump_trial(
            scanner, "positive_y", BUMP_POSITIVE_Y_START, positive_default,
            gz_bias, cmd_a, cmd_b, round_number,
        )
        negative, negative_ok, negative_detail = _run_bump_trial(
            scanner, "negative_y", BUMP_NEGATIVE_Y_START, negative_default,
            gz_bias, cmd_a, cmd_b, round_number,
        )
        decision = _prompt_bump_pair_decision(positive_ok and negative_ok)
        if decision == "cancel":
            raise RuntimeError("bump-crossing calibration cancelled")
        if decision == "accept":
            return {
                "accepted_round": round_number,
                "positive_y_command_distance_m": positive,
                "negative_y_command_distance_m": negative,
                "positive_y_execution_detail": positive_detail,
                "negative_y_execution_detail": negative_detail,
                "quality": {"status": "pass", "manual_pair_accepted": True},
            }
        positive_default = positive
        negative_default = negative
        round_number += 1


def _run_session(scanner: str) -> str:
    global SESSION_FORWARD_KICK_RIGHT_SPEED
    global SESSION_FORWARD_KICK_LEFT_SPEED

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
        print(f"Previous buck voltage:     {previous.get('buck_voltage_v')} V")
        if isinstance(old_model, dict):
            print("Previous distance cmd_a/b: "
                  f"{old_model.get('cmd_a')}, {old_model.get('cmd_b')}")

    buck_voltage_v = _prompt_buck_voltage(previous)
    print(f"Calibration buck voltage:  {buck_voltage_v:.3f} V")

    old_startup = _previous_startup_values(previous)
    if old_startup is None:
        startup_values = (
            DEFAULT_FORWARD_KICK_RIGHT_SPEED,
            DEFAULT_FORWARD_KICK_LEFT_SPEED,
        )
        startup_summary = (
            f"legacy/default right={startup_values[0]}, left={startup_values[1]}"
        )
    else:
        startup_values = old_startup
        startup_summary = f"saved right={old_startup[0]}, left={old_startup[1]}"
    skip_startup = _prompt_skip_phase("FORWARD STARTUP BALANCE", startup_summary)
    startup_stage: Optional[Dict[str, object]] = None
    if skip_startup:
        startup_right_speed, startup_left_speed = startup_values
    else:
        startup_stage = _run_startup_stage(scanner, previous)
        startup_right_speed = int(startup_stage["right_kick_speed"])
        startup_left_speed = int(startup_stage["left_kick_speed"])
    SESSION_FORWARD_KICK_RIGHT_SPEED = startup_right_speed
    SESSION_FORWARD_KICK_LEFT_SPEED = startup_left_speed

    old_gz_bias = _previous_gz_bias(previous)
    skip_gz = _prompt_skip_phase(
        "GZ_BIAS",
        None if old_gz_bias is None else f"GZ_BIAS={old_gz_bias:+.9f}",
    )
    gz_stage: Optional[Dict[str, object]] = None
    if skip_gz:
        assert old_gz_bias is not None
        gz_bias = old_gz_bias
    else:
        gz_stage = _run_gz_bias_stage(scanner, previous)
        gz_bias = float(gz_stage["gz_bias"])

    old_distance = _previous_distance_values(previous)
    distance_summary = None
    if old_distance is not None:
        old_fit, old_kick = old_distance
        distance_summary = (
            f"cmd_a={old_fit['cmd_a']:.12f}, cmd_b={old_fit['cmd_b']:.12f}, "
            f"kick={old_kick:.3f} m"
        )
    skip_distance = _prompt_skip_phase("DISTANCE", distance_summary)
    distance_stage: Optional[Dict[str, object]] = None
    if skip_distance:
        assert old_distance is not None
        fit, kick_distance_m = old_distance
    else:
        print("\nNext stage overview:")
        print("Distance trial order: " + " -> ".join(
            f"{value:.2f} m" for value in RAW_SEQUENCE_M
        ))
        print(f"Then one {VERIFY_DISTANCE_M:.2f} m verification movement.")
        distance_stage = _run_distance_stage(scanner, gz_bias)
        fit = distance_stage["regression"]
        assert isinstance(fit, dict)
        kick_distance_m = float(distance_stage["kick_distance_m"])

    old_bump = _previous_bump_values(previous)
    bump_summary = None
    if old_bump is not None:
        bump_summary = (
            f"+Y={old_bump['positive_y']:.3f} m, "
            f"-Y={old_bump['negative_y']:.3f} m"
        )
    skip_bump = _prompt_skip_phase("BUMP CROSSING", bump_summary)
    bump_stage: Optional[Dict[str, object]] = None
    if skip_bump:
        assert old_bump is not None
        bump_values = old_bump
    else:
        bump_stage = _run_bump_crossing_stage(
            scanner,
            previous,
            gz_bias,
            float(fit["cmd_a"]),
            float(fit["cmd_b"]),
        )
        bump_values = {
            "positive_y": float(bump_stage["positive_y_command_distance_m"]),
            "negative_y": float(bump_stage["negative_y_command_distance_m"]),
        }
    comparison = _comparison(previous, gz_bias, fit)
    phase_actions = {
        "move_startup": "skipped" if skip_startup else "calibrated",
        "gz_bias": "skipped" if skip_gz else "calibrated",
        "distance": "skipped" if skip_distance else "calibrated",
        "bump_crossing": "skipped" if skip_bump else "calibrated",
    }
    combined_quality: Dict[str, object] = {"phase_actions": phase_actions}
    new_statuses: List[str] = []
    for name, stage in (("move_startup", startup_stage),
                        ("gz_bias", gz_stage), ("distance", distance_stage),
                        ("bump_crossing", bump_stage)):
        if stage is not None:
            stage_quality = stage["quality"]
            combined_quality[name] = stage_quality
            if isinstance(stage_quality, dict):
                new_statuses.append(str(stage_quality.get("status")))
    combined_quality["status"] = (
        "pass" if new_statuses and all(value == "pass" for value in new_statuses)
        else "unchanged" if not new_statuses else "review"
    )
    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(), "scanner": scanner,
        "phase_actions": phase_actions,
        "move_startup_calibration": startup_stage or {"status": "skipped"},
        "gz_bias_calibration": gz_stage or {"status": "skipped"},
        "distance_calibration": distance_stage or {"status": "skipped"},
        "bump_crossing_calibration": bump_stage or {"status": "skipped"},
        "combined_quality": combined_quality,
        "candidate": {
            "buck_voltage_v": buck_voltage_v,
            "move_startup": {
                "forward": {
                    "right_kick_speed": startup_right_speed,
                    "left_kick_speed": startup_left_speed,
                },
            },
            "gz_bias": gz_bias, "distance_model": fit,
            "short_move": {"kick_distance_m": kick_distance_m,
                           "skip_threshold_m": kick_distance_m / 2.0},
            "bump_crossing": {
                "positive_y": {
                    "command_distance_m": bump_values["positive_y"]
                },
                "negative_y": {
                    "command_distance_m": bump_values["negative_y"]
                },
            },
        },
        "comparison_with_previous": comparison,
        "session_csv": str(ACTIVE_CSV_PATH),
    }
    _write_result(result)

    print("\n============================================================")
    print("OVERALL CALIBRATION CONCLUSION")
    print("============================================================")
    print(f"Combined quality:          {str(combined_quality['status']).upper()}")
    print(f"Buck voltage:              {buck_voltage_v:.3f} V")
    print(
        "Forward kick right/left:   "
        f"{startup_right_speed} / {startup_left_speed}"
    )
    print(f"Accepted GZ_BIAS:          {gz_bias:+.9f}")
    print(f"Phase actions:             {phase_actions}")
    if gz_stage is not None:
        print("GZ calibration method:     static seed + accepted 3 m physical walk")
        print(
            "GZ raw sample spread:      "
            f"{gz_stage['static_measurement']['raw_sample_spread_deg_per_sec']:.9f} deg/s"
        )
        print(f"Accepted GZ walking trial: {gz_stage['accepted_trial']}")
    else:
        print("GZ calibration method:     existing registry value retained")
    print(f"Distance cmd_a/b:          {fit['cmd_a']:.12f}, {fit['cmd_b']:.12f}")
    if distance_stage is not None:
        print(f"Distance fit RMSE:         {fit['rmse_m'] * 100:.2f} cm")
        print(f"Distance fit R-squared:    {fit['r_squared']:.6f}")
    else:
        print("Distance calibration:      existing registry values retained")
    print(f"Kick-only distance:        {kick_distance_m * 100:.2f} cm")
    print(f"Short-move skip below:     {kick_distance_m * 50:.2f} cm")
    print(
        "Bump +Y/-Y command:       "
        f"{bump_values['positive_y']:.3f} / "
        f"{bump_values['negative_y']:.3f} m"
    )
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
        _write_registry(
            scanner,
            buck_voltage_v,
            startup_stage,
            gz_stage,
            distance_stage,
            bump_stage,
        )
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
