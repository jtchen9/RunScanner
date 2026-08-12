#!/usr/bin/env python3
"""One-command guided mobility calibration for one AutoLab robot.

The operator runs this script once and enters only:

* measured forward distance in centimetres;
* signed lateral shift in centimetres (right positive);
* whether the current measurement should be kept or retried.

Accepted results are written to the per-robot production calibration registry.
The motion layer reads the latest PASS result for its own scanner name.
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
INITIAL_GZ_TEST_DISTANCE_M = 1.50
INITIAL_GZ_BIAS_OFFSETS = (0.00, -0.10, 0.10)

STATIONARY_SAMPLE_SEC = 5.0
STATIONARY_DISCARD_SEC = 1.0
STATIONARY_SAMPLE_DT_SEC = 0.02
STATIONARY_TRIM_FRACTION = 0.10

MIN_R_SQUARED = 0.995
MAX_FIT_RMSE_M = 0.05
MAX_VERIFY_DISTANCE_ERROR_M = 0.05
MAX_VERIFY_LATERAL_SHIFT_M = 0.05
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
) -> Tuple[bool, Optional[Tuple[float, float]]]:
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
    actual_lateral_m = _prompt_float_cm(
        "Lateral shift (cm, right positive): "
    )
    retry = _prompt_retry(next_experiment)

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
    next_experiment: str,
    phase: str = "raw_fit",
) -> Tuple[float, float]:
    attempt = 1
    while True:
        retry, measurement = _measure_one(
            scanner=scanner,
            phase=phase,
            attempt=attempt,
            gz_bias=gz_bias,
            raw_motor_distance_m=raw_motor_distance_m,
            next_experiment=next_experiment,
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


def _first_time_gz_bias(
    scanner: str,
    stationary_bias: float,
) -> float:
    """Choose the least-drifting of three first-time bias candidates."""
    candidates: List[Tuple[float, float]] = []
    biases = [stationary_bias + offset for offset in INITIAL_GZ_BIAS_OFFSETS]
    for index, bias in enumerate(biases):
        next_text = (
            f"GZ_BIAS {biases[index + 1]:.6f}, "
            f"raw {INITIAL_GZ_TEST_DISTANCE_M:.2f} m"
            if index + 1 < len(biases)
            else f"raw motor distance {RAW_SEQUENCE_M[0]:.2f} m"
        )
        _forward, lateral = _collect_raw_point(
            scanner=scanner,
            raw_motor_distance_m=INITIAL_GZ_TEST_DISTANCE_M,
            gz_bias=bias,
            next_experiment=next_text,
            phase="initial_gz_selection",
        )
        candidates.append((bias, lateral))

    selected_bias, _selected_lateral = min(
        candidates,
        key=lambda item: (abs(item[1]), abs(item[0] - stationary_bias)),
    )
    print(f"Initial GZ_BIAS selected: {selected_bias:.9f}")
    return selected_bias


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
            print(
                "Previous distance cmd_a/b: "
                f"{old_model.get('cmd_a')}, {old_model.get('cmd_b')}"
            )
    print("Place the robot at the start mark on a clear straight path.")
    input("Keep it stationary and press Enter to begin: ")

    stationary_bias, gz_spread, sample_count = _measure_stationary_gz_bias()
    if previous is None:
        gz_bias = _first_time_gz_bias(scanner, stationary_bias)
    else:
        try:
            gz_bias = float(previous["gz_bias"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("previous registry GZ_BIAS is invalid") from exc
        print(f"Using previous GZ_BIAS: {gz_bias:.9f}")
    raw_points: List[Tuple[float, float]] = []
    lateral_points: List[float] = []
    kick_distance_m: Optional[float] = None
    for index, raw_distance in enumerate(RAW_SEQUENCE_M):
        next_text = (
            f"raw motor distance {RAW_SEQUENCE_M[index + 1]:.2f} m"
            if index + 1 < len(RAW_SEQUENCE_M)
            else f"candidate calibrated distance {VERIFY_DISTANCE_M:.2f} m"
        )
        actual_forward, actual_lateral = _collect_raw_point(
            scanner=scanner,
            raw_motor_distance_m=raw_distance,
            gz_bias=gz_bias,
            next_experiment=next_text,
        )
        raw_points.append((raw_distance, actual_forward))
        lateral_points.append(actual_lateral)
        if raw_distance == 0.0:
            kick_distance_m = actual_forward

    if kick_distance_m is None:
        raise RuntimeError("accepted kick-only measurement is missing")

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
            next_experiment="calibration quality summary",
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
    comparison = _comparison(previous, gz_bias, fit)
    quality: Dict[str, object] = {
        "status": "pass" if passed else "review",
        "checks": checks,
        "fit_rmse_m": fit["rmse_m"],
        "r_squared": fit["r_squared"],
        "verification_distance_error_m": verify_error_m,
        "verification_lateral_shift_m": verify_lateral_m,
        "maximum_raw_lateral_shift_m": max(abs(x) for x in lateral_points),
        "stationary_gz_bias": stationary_bias,
        "stationary_gz_spread": gz_spread,
        "stationary_sample_count": sample_count,
    }
    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(),
        "scanner": scanner,
        "quality": quality,
        "candidate": {
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
        },
        "comparison_with_previous": comparison,
        "session_csv": str(ACTIVE_CSV_PATH),
    }
    _write_result(result)

    print("\n============================================================")
    print(f"QUALITY: {'PASS' if passed else 'REVIEW'}")
    print("============================================================")
    print(f"GZ_BIAS:                 {gz_bias:.9f}")
    print(f"Stationary gyro estimate:{stationary_bias: .9f}")
    print(f"Gyro sample spread:      {gz_spread:.6f} deg/s ({sample_count} samples)")
    print(f"Distance actual_a/b:     {fit['actual_a']:.12f}, {fit['actual_b']:.12f}")
    print(f"Distance cmd_a/b:        {fit['cmd_a']:.12f}, {fit['cmd_b']:.12f}")
    print(f"Fit RMSE:                {fit['rmse_m'] * 100:.2f} cm")
    print(f"Fit R-squared:           {fit['r_squared']:.6f}")
    print(f"1 m verification error:  {verify_error_m * 100:.2f} cm")
    print(f"1 m lateral shift:       {verify_lateral_m * 100:+.2f} cm")
    print(f"Kick-only distance:      {kick_distance_m * 100:.2f} cm")
    print(f"Short-move skip below:   {kick_distance_m * 50:.2f} cm")
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("Failed checks:            " + ", ".join(failed))
    if comparison.get("available"):
        print("Previous comparison:      " + (
            "MAJOR DIFFERENCE" if comparison.get("major_difference") else "no major difference"
        ))
        print(
            "GZ_BIAS old/new:          "
            f"{comparison.get('previous_gz_bias')} / {gz_bias:.9f}"
        )
        for name in ("actual_a", "actual_b", "cmd_a", "cmd_b"):
            change = comparison.get(f"{name}_change_percent")
            if change is not None:
                print(f"{name} change:             {float(change):+.2f}%")
    else:
        print("Previous comparison:      first calibration")
    print(f"Session CSV:              {ACTIVE_CSV_PATH}")
    print(f"Quality report:           {RESULT_PATH}")

    action = _prompt_final_action(previous is not None)
    if action == "update":
        _write_registry(scanner, gz_bias, fit, quality, kick_distance_m)
        print(f"Registry updated:         {REGISTRY_PATH}")
        print("Production loader:        enabled for the next motion primitive")
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
