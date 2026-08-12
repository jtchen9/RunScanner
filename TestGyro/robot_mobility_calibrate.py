#!/usr/bin/env python3
"""One-command guided mobility calibration for one AutoLab robot.

The operator runs this script once and enters only:

* measured forward distance in centimetres;
* signed lateral shift in centimetres (right positive);
* whether the current measurement should be retried.

This first version writes a per-robot calibration registry but does not change
the production loader.  Production continues using its existing configuration
until the registry integration is reviewed separately.
"""

from __future__ import annotations

import csv
import json
import math
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


# Fixed quick sequence.  Non-monotonic order reduces correlation between test
# distance and gradual battery/motor heating.
RAW_SEQUENCE_M = (1.00, 0.00, 2.00, 0.20, 0.50)
VERIFY_DISTANCE_M = 1.00

STATIONARY_SAMPLE_SEC = 5.0
STATIONARY_DISCARD_SEC = 1.0
STATIONARY_SAMPLE_DT_SEC = 0.02
STATIONARY_TRIM_FRACTION = 0.10

MIN_R_SQUARED = 0.995
MAX_FIT_RMSE_M = 0.05
MAX_VERIFY_DISTANCE_ERROR_M = 0.05
MAX_VERIFY_LATERAL_SHIFT_M = 0.05

CALIBRATION_DIR = SCRIPT_DIR / "calibration"
ACTIVE_CSV_PATH = CALIBRATION_DIR / "current_calibration.csv"
ARCHIVE_DIR = CALIBRATION_DIR / "archive"
REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"

CSV_FIELDS = (
    "recorded_at_utc",
    "scanner",
    "phase",
    "attempt",
    "accepted",
    "raw_motor_distance_m",
    "desired_distance_m",
    "actual_forward_distance_m",
    "actual_lateral_shift_m",
    "gz_bias",
    "execution_ok",
    "execution_detail",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filename_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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


def _prompt_retry() -> bool:
    while True:
        value = input(
            "Retry this measurement? [y/N] "
            "(choose N only after repositioning the robot): "
        ).strip().lower()
        if value in {"", "n", "no"}:
            return False
        if value in {"y", "yes"}:
            return True
        print("Enter Y to retry, or N to accept and continue.")


def _trimmed_mean(values: List[float], fraction: float) -> float:
    if not values:
        raise RuntimeError("no gyro samples collected")
    ordered = sorted(values)
    trim_count = int(len(ordered) * fraction)
    if trim_count > 0 and (2 * trim_count) < len(ordered):
        ordered = ordered[trim_count:-trim_count]
    return statistics.fmean(ordered)


def _measure_stationary_gz_bias() -> Tuple[float, float, int]:
    ok, imu, detail = motion._imu_begin()
    if not ok:
        raise RuntimeError(f"IMU initialization failed: {detail}")

    print(f"Measuring stationary gyro for {STATIONARY_SAMPLE_SEC:.0f} seconds...")
    samples: List[float] = []
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= STATIONARY_SAMPLE_SEC:
            break
        _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
        if elapsed >= STATIONARY_DISCARD_SEC and math.isfinite(float(gz)):
            samples.append(float(gz))
        time.sleep(STATIONARY_SAMPLE_DT_SEC)

    if len(samples) < 20:
        raise RuntimeError(f"too few usable gyro samples: {len(samples)}")
    bias = _trimmed_mean(samples, STATIONARY_TRIM_FRACTION)
    spread = statistics.pstdev(samples)
    return bias, spread, len(samples)


def _execute_raw(raw_motor_distance_m: float) -> Tuple[bool, str]:
    original = motion.apply_motor_move_calibration
    requested = max(raw_motor_distance_m, motion.MIN_MOVE_DISTANCE_M)
    motion.apply_motor_move_calibration = lambda _distance: raw_motor_distance_m
    try:
        return motion.move_forward(requested)
    finally:
        motion.apply_motor_move_calibration = original


def _execute_candidate_calibrated(
    desired_distance_m: float,
    cmd_a: float,
    cmd_b: float,
) -> Tuple[bool, str]:
    original = motion.apply_motor_move_calibration

    def candidate_calibration(distance_m: float) -> float:
        value = cmd_a * float(distance_m) + cmd_b
        return max(0.0, value)

    motion.apply_motor_move_calibration = candidate_calibration
    try:
        return motion.move_forward(desired_distance_m)
    finally:
        motion.apply_motor_move_calibration = original


def _measure_one(
    scanner: str,
    phase: str,
    attempt: int,
    gz_bias: float,
    raw_motor_distance_m: Optional[float] = None,
    desired_distance_m: Optional[float] = None,
    cmd_a: Optional[float] = None,
    cmd_b: Optional[float] = None,
) -> Tuple[bool, Optional[Tuple[float, float]]]:
    if raw_motor_distance_m is not None:
        label = f"raw motor distance {raw_motor_distance_m:.2f} m"
    else:
        label = f"candidate calibrated distance {desired_distance_m:.2f} m"

    print(f"\n{label} — attempt {attempt}")
    print("Starting in 3 seconds...")
    time.sleep(3.0)

    if raw_motor_distance_m is not None:
        ok, detail = _execute_raw(raw_motor_distance_m)
    else:
        if desired_distance_m is None or cmd_a is None or cmd_b is None:
            raise RuntimeError("candidate verification parameters are incomplete")
        ok, detail = _execute_candidate_calibrated(
            desired_distance_m,
            cmd_a,
            cmd_b,
        )

    row: Dict[str, object] = {
        "recorded_at_utc": _utc_now(),
        "scanner": scanner,
        "phase": phase,
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
        retry = _prompt_retry()
        row["accepted"] = False
        _append_row(row)
        if retry:
            return True, None
        raise RuntimeError("calibration stopped after failed movement")

    actual_forward_m = _prompt_float_cm("Actual forward distance (cm): ")
    actual_lateral_m = _prompt_float_cm(
        "Lateral shift (cm, right positive): "
    )
    retry = _prompt_retry()

    row["actual_forward_distance_m"] = actual_forward_m
    row["actual_lateral_shift_m"] = actual_lateral_m
    row["accepted"] = not retry
    _append_row(row)

    if retry:
        return True, None
    return False, (actual_forward_m, actual_lateral_m)


def _collect_raw_point(
    scanner: str,
    raw_motor_distance_m: float,
    gz_bias: float,
) -> Tuple[float, float]:
    attempt = 1
    while True:
        retry, measurement = _measure_one(
            scanner=scanner,
            phase="raw_fit",
            attempt=attempt,
            gz_bias=gz_bias,
            raw_motor_distance_m=raw_motor_distance_m,
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


def _write_registry(
    scanner: str,
    gz_bias: float,
    fit: Dict[str, float],
) -> None:
    registry = _load_registry()
    robots = registry["robots"]
    assert isinstance(robots, dict)

    if REGISTRY_PATH.exists():
        backup = REGISTRY_PATH.with_name(
            f"{REGISTRY_PATH.stem}.{_filename_timestamp()}.bak.json"
        )
        shutil.copy2(REGISTRY_PATH, backup)

    robots[scanner] = {
        "buck_voltage_v": 9.0,
        "gz_bias": gz_bias,
        "distance_model": {
            "actual_a": fit["actual_a"],
            "actual_b": fit["actual_b"],
            "cmd_a": fit["cmd_a"],
            "cmd_b": fit["cmd_b"],
        },
        "calibrated_at_utc": _utc_now(),
        "source": str(ACTIVE_CSV_PATH),
        "production_loader_enabled": False,
    }

    temporary = REGISTRY_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(REGISTRY_PATH)


def main() -> int:
    scanner = _scanner_name()
    _archive_active_csv(scanner)

    print("============================================================")
    print(f"Mobility calibration: {scanner}")
    print("Place the robot at the start mark on a clear straight path.")
    input("Keep it stationary and press Enter to begin: ")

    gz_bias, gz_spread, sample_count = _measure_stationary_gz_bias()
    motion.GZ_BIAS = gz_bias

    raw_points: List[Tuple[float, float]] = []
    lateral_points: List[float] = []
    for raw_distance in RAW_SEQUENCE_M:
        actual_forward, actual_lateral = _collect_raw_point(
            scanner,
            raw_distance,
            gz_bias,
        )
        raw_points.append((raw_distance, actual_forward))
        lateral_points.append(actual_lateral)

    fit = _linear_fit(raw_points)

    verification_attempt = 1
    while True:
        retry, verification = _measure_one(
            scanner=scanner,
            phase="candidate_verification",
            attempt=verification_attempt,
            gz_bias=gz_bias,
            desired_distance_m=VERIFY_DISTANCE_M,
            cmd_a=fit["cmd_a"],
            cmd_b=fit["cmd_b"],
        )
        if not retry and verification is not None:
            break
        verification_attempt += 1

    verify_forward_m, verify_lateral_m = verification
    verify_error_m = abs(verify_forward_m - VERIFY_DISTANCE_M)

    checks = {
        "r_squared": fit["r_squared"] >= MIN_R_SQUARED,
        "fit_rmse": fit["rmse_m"] <= MAX_FIT_RMSE_M,
        "verify_distance": verify_error_m <= MAX_VERIFY_DISTANCE_ERROR_M,
        "verify_lateral": abs(verify_lateral_m) <= MAX_VERIFY_LATERAL_SHIFT_M,
    }
    passed = all(checks.values())

    if passed:
        _write_registry(scanner, gz_bias, fit)

    print("\n============================================================")
    if passed:
        print(f"PASS — {scanner} calibration recorded")
    else:
        print(f"REVIEW — {scanner} registry was not changed")
    print("============================================================")
    print(f"GZ_BIAS:                 {gz_bias:.9f}")
    print(f"Gyro sample spread:      {gz_spread:.6f} deg/s ({sample_count} samples)")
    print(f"Distance actual_a/b:     {fit['actual_a']:.12f}, {fit['actual_b']:.12f}")
    print(f"Distance cmd_a/b:        {fit['cmd_a']:.12f}, {fit['cmd_b']:.12f}")
    print(f"Fit RMSE:                {fit['rmse_m'] * 100:.2f} cm")
    print(f"Fit R-squared:           {fit['r_squared']:.6f}")
    print(f"1 m verification error:  {verify_error_m * 100:.2f} cm")
    print(f"1 m lateral shift:       {verify_lateral_m * 100:+.2f} cm")
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("Failed checks:            " + ", ".join(failed))
    print(f"Session CSV:              {ACTIVE_CSV_PATH}")
    if passed:
        print(f"Candidate registry:       {REGISTRY_PATH}")
        print("Production loader:        not enabled in this interface trial")
    return 0 if passed else 1


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
