#!/usr/bin/env python3
"""Measure useful GZ_BIAS resolution without changing production calibration.

Run this diagnostic on one robot before choosing the stopping resolution for
the production GZ_BIAS calibration algorithm.  It compares symmetric low/high
bias values around the robot's current production GZ_BIAS.  Three balanced
pairs are collected at each candidate separation.

The tool writes only diagnostic CSV/JSON files.  It never modifies
robot_mobility_calibration.json.
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
from typing import Dict, List, Mapping, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))


TEST_DISTANCE_M = 1.50
BIAS_STEPS = (0.10000, 0.05000, 0.02500, 0.01250, 0.00625)
PAIR_ORDERS = (("low", "high"), ("high", "low"), ("low", "high"))

# A bias separation is called distinguishable only when its median lateral
# effect clears both an absolute physical threshold and the observed paired
# repeatability noise.  These are diagnostic classification thresholds, not
# production calibration acceptance thresholds.
MIN_USEFUL_LATERAL_EFFECT_M = 0.020
MIN_NOISE_SIGMA_M = 0.005
SIGNAL_TO_NOISE_REQUIRED = 2.0
MIN_SAME_SIGN_PAIRS = 3

REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"
SCANNER_NAME_PATH = ROBOT_ROOT / "scanner_name.txt"
OUTPUT_DIR = SCRIPT_DIR / "gz_bias_resolution"
ACTIVE_CSV_PATH = OUTPUT_DIR / "current_resolution.csv"
RESULT_PATH = OUTPUT_DIR / "resolution_result.json"
ARCHIVE_DIR = OUTPUT_DIR / "archive"

CSV_FIELDS = (
    "recorded_at_utc",
    "scanner",
    "step_index",
    "bias_step",
    "pair_index",
    "pair_order",
    "side",
    "trial_gz_bias",
    "accepted",
    "actual_forward_distance_m",
    "actual_lateral_shift_m",
    "execution_ok",
    "execution_detail",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _finite(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} is not numeric") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{name} is not finite")
    return number


def _load_identity_and_calibration() -> Tuple[str, float, float, float, str]:
    try:
        scanner = SCANNER_NAME_PATH.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise RuntimeError(f"cannot read {SCANNER_NAME_PATH}: {exc}") from exc
    if not scanner:
        raise RuntimeError(f"empty robot identity in {SCANNER_NAME_PATH}")

    try:
        document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"cannot read {REGISTRY_PATH}: {exc}") from exc
    robots = document.get("robots") if isinstance(document, dict) else None
    entry = robots.get(scanner) if isinstance(robots, dict) else None
    if not isinstance(entry, dict):
        raise RuntimeError(f"no calibration registry entry for {scanner}")
    if entry.get("production_loader_enabled") is not True:
        raise RuntimeError(f"production calibration is not enabled for {scanner}")
    model = entry.get("distance_model")
    if not isinstance(model, dict):
        raise RuntimeError(f"distance_model is missing for {scanner}")

    center = _finite(entry.get("gz_bias"), "registry gz_bias")
    cmd_a = _finite(model.get("cmd_a"), "registry cmd_a")
    cmd_b = _finite(model.get("cmd_b"), "registry cmd_b")
    if cmd_a <= 0.0:
        raise RuntimeError("registry cmd_a must be positive")
    calibrated_at = str(entry.get("calibrated_at_utc") or "")
    return scanner, center, cmd_a, cmd_b, calibrated_at


def _archive_previous(scanner: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in (ACTIVE_CSV_PATH, RESULT_PATH):
        if path.exists():
            destination = ARCHIVE_DIR / (
                f"{scanner}-{_timestamp()}-{path.name}"
            )
            shutil.move(str(path), str(destination))


def _append_csv(row: Mapping[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not ACTIVE_CSV_PATH.exists()
    with ACTIVE_CSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in CSV_FIELDS})


def _prompt_cm(prompt: str) -> float:
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


def _keep_measurement() -> bool:
    while True:
        value = input("Keep this measurement? [Y/n]: ").strip().lower()
        if value in {"", "y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter Y to keep this measurement or N to retry it.")


def _execute_move(
    *,
    gz_bias: float,
    cmd_a: float,
    cmd_b: float,
) -> Tuple[bool, str]:
    # Import only for a real movement.  Pure analysis tests can import this
    # module without Raspberry Pi motor, IMU, or ToF dependencies.
    import robot_mobility_motion as motion

    motor_distance = max(0.0, cmd_a * TEST_DISTANCE_M + cmd_b)
    return motion._run_move(
        forward=True,
        distance_m=TEST_DISTANCE_M,
        calibration_gz_bias=gz_bias,
        motor_distance_override=motor_distance,
    )


def _collect_trial(
    *,
    scanner: str,
    step_index: int,
    bias_step: float,
    pair_index: int,
    pair_order: str,
    side: str,
    trial_bias: float,
    cmd_a: float,
    cmd_b: float,
) -> Tuple[float, float]:
    attempt = 1
    while True:
        print("\n------------------------------------------------------------")
        print(
            f"Step {step_index}/{len(BIAS_STEPS)}, "
            f"pair {pair_index}/{len(PAIR_ORDERS)}, {side.upper()}"
        )
        print(f"Bias separation:          {bias_step:.5f}")
        print(f"Trial GZ_BIAS:            {trial_bias:.9f}")
        print(f"Forward distance command: {TEST_DISTANCE_M:.2f} m")
        print(f"Attempt:                  {attempt}")
        input(
            "Place the robot at the common start mark and heading; "
            "press Enter when ready: "
        )
        print("Starting in 3 seconds...")
        time.sleep(3.0)

        ok, detail = _execute_move(
            gz_bias=trial_bias,
            cmd_a=cmd_a,
            cmd_b=cmd_b,
        )
        base_row: Dict[str, object] = {
            "recorded_at_utc": _utc_now(),
            "scanner": scanner,
            "step_index": step_index,
            "bias_step": bias_step,
            "pair_index": pair_index,
            "pair_order": pair_order,
            "side": side,
            "trial_gz_bias": trial_bias,
            "accepted": False,
            "execution_ok": ok,
            "execution_detail": detail,
        }
        if not ok:
            print(f"Movement failed: {detail}")
            _append_csv(base_row)
            while True:
                answer = input("Retry this movement? [Y/n]: ").strip().lower()
                if answer in {"", "y", "yes"}:
                    attempt += 1
                    break
                if answer in {"n", "no"}:
                    raise RuntimeError("resolution experiment stopped after movement failure")
                print("Enter Y to retry or N to stop.")
            continue

        forward_m = _prompt_cm("Actual forward distance (cm): ")
        lateral_m = _prompt_cm("Lateral shift (cm, right positive): ")
        keep = _keep_measurement()
        base_row.update(
            {
                "accepted": keep,
                "actual_forward_distance_m": forward_m,
                "actual_lateral_shift_m": lateral_m,
            }
        )
        _append_csv(base_row)
        if keep:
            return forward_m, lateral_m
        attempt += 1


def _median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("cannot calculate median of empty values")
    return float(statistics.median(values))


def _robust_sigma(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = _median(values)
    mad = _median([abs(value - center) for value in values])
    return 1.4826 * mad


def analyze_step(
    *,
    bias_step: float,
    pairs: Sequence[Mapping[str, float]],
) -> Dict[str, object]:
    if len(pairs) < 3:
        raise ValueError("three low/high pairs are required")
    lows = [_finite(pair["low"], "low lateral shift") for pair in pairs]
    highs = [_finite(pair["high"], "high lateral shift") for pair in pairs]
    paired_changes = [high - low for low, high in zip(lows, highs)]
    effect = _median(paired_changes)
    effect_sign = 1 if effect > 0.0 else (-1 if effect < 0.0 else 0)
    same_sign = sum(
        1
        for change in paired_changes
        if (1 if change > 0.0 else (-1 if change < 0.0 else 0)) == effect_sign
    )
    paired_noise_sigma = _robust_sigma(paired_changes)
    comparison_noise = max(paired_noise_sigma, MIN_NOISE_SIGMA_M)
    snr = abs(effect) / comparison_noise
    distinguishable = (
        effect_sign != 0
        and same_sign >= MIN_SAME_SIGN_PAIRS
        and abs(effect) >= MIN_USEFUL_LATERAL_EFFECT_M
        and snr >= SIGNAL_TO_NOISE_REQUIRED
    )
    return {
        "bias_step": float(bias_step),
        "low_lateral_shifts_m": lows,
        "high_lateral_shifts_m": highs,
        "paired_lateral_changes_m": paired_changes,
        "median_low_lateral_m": _median(lows),
        "median_high_lateral_m": _median(highs),
        "median_paired_lateral_change_m": effect,
        "paired_change_noise_sigma_m": paired_noise_sigma,
        "signal_to_noise_ratio": snr,
        "same_sign_pair_count": same_sign,
        "pair_count": len(pairs),
        "distinguishable": distinguishable,
    }


def recommend_resolution(levels: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    ordered = sorted(levels, key=lambda level: float(level["bias_step"]), reverse=True)
    finest: Mapping[str, object] | None = None
    first_noise_dominated: Mapping[str, object] | None = None
    contiguous = True
    for level in ordered:
        if contiguous and bool(level.get("distinguishable")):
            finest = level
            continue
        if contiguous:
            first_noise_dominated = level
        contiguous = False

    if finest is None:
        return {
            "status": "NO_TESTED_STEP_DISTINGUISHABLE",
            "recommended_gz_bias_resolution": None,
            "corresponding_lateral_resolution_m": None,
            "first_noise_dominated_bias_step": (
                float(first_noise_dominated["bias_step"])
                if first_noise_dominated is not None
                else None
            ),
        }
    return {
        "status": "RESOLUTION_FOUND",
        "recommended_gz_bias_resolution": float(finest["bias_step"]),
        "corresponding_lateral_resolution_m": abs(
            float(finest["median_paired_lateral_change_m"])
        ),
        "first_noise_dominated_bias_step": (
            float(first_noise_dominated["bias_step"])
            if first_noise_dominated is not None
            else None
        ),
    }


def _write_result(result: Mapping[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)


def main() -> int:
    scanner, center, cmd_a, cmd_b, calibrated_at = _load_identity_and_calibration()
    _archive_previous(scanner)

    print("============================================================")
    print("GZ_BIAS resolution experiment")
    print("============================================================")
    print(f"Robot:                       {scanner}")
    print(f"Production center GZ_BIAS:   {center:.9f}")
    print(f"Fixed movement distance:     {TEST_DISTANCE_M:.2f} m")
    print(f"Candidate bias separations:  {', '.join(f'{x:.5f}' for x in BIAS_STEPS)}")
    print(f"Accepted movement count:     {len(BIAS_STEPS) * len(PAIR_ORDERS) * 2}")
    print("Production registry writes:  DISABLED")
    print()
    print("Every trial must start from the same mark and heading.")
    print("Measure lateral shift with right positive and left negative.")
    input("Press Enter to begin: ")

    analyses: List[Dict[str, object]] = []
    for step_index, bias_step in enumerate(BIAS_STEPS, start=1):
        low_bias = center - bias_step / 2.0
        high_bias = center + bias_step / 2.0
        pairs: List[Dict[str, float]] = []
        print("\n============================================================")
        print(f"BIAS SEPARATION {bias_step:.5f}")
        print(f"LOW={low_bias:.9f}, HIGH={high_bias:.9f}")
        print("============================================================")
        for pair_index, order in enumerate(PAIR_ORDERS, start=1):
            pair_values: Dict[str, float] = {}
            order_text = "->".join(order)
            for side in order:
                trial_bias = low_bias if side == "low" else high_bias
                _forward, lateral = _collect_trial(
                    scanner=scanner,
                    step_index=step_index,
                    bias_step=bias_step,
                    pair_index=pair_index,
                    pair_order=order_text,
                    side=side,
                    trial_bias=trial_bias,
                    cmd_a=cmd_a,
                    cmd_b=cmd_b,
                )
                pair_values[side] = lateral
            pairs.append(pair_values)

        analysis = analyze_step(bias_step=bias_step, pairs=pairs)
        analyses.append(analysis)
        print("\nLevel result")
        print(f"Median HIGH-LOW effect: {float(analysis['median_paired_lateral_change_m']) * 100:+.2f} cm")
        print(f"Paired noise sigma:     {float(analysis['paired_change_noise_sigma_m']) * 100:.2f} cm")
        print(f"Signal/noise ratio:     {float(analysis['signal_to_noise_ratio']):.2f}")
        print(f"Same-sign pairs:        {analysis['same_sign_pair_count']}/{analysis['pair_count']}")
        print(f"Distinguishable:        {analysis['distinguishable']}")

    recommendation = recommend_resolution(analyses)
    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(),
        "scanner": scanner,
        "production_calibration": {
            "gz_bias": center,
            "cmd_a": cmd_a,
            "cmd_b": cmd_b,
            "calibrated_at_utc": calibrated_at,
        },
        "experiment": {
            "test_distance_m": TEST_DISTANCE_M,
            "bias_steps": list(BIAS_STEPS),
            "pair_orders": [list(order) for order in PAIR_ORDERS],
            "accepted_measurement_target": len(BIAS_STEPS) * len(PAIR_ORDERS) * 2,
            "minimum_useful_lateral_effect_m": MIN_USEFUL_LATERAL_EFFECT_M,
            "minimum_noise_sigma_m": MIN_NOISE_SIGMA_M,
            "required_signal_to_noise_ratio": SIGNAL_TO_NOISE_REQUIRED,
        },
        "levels": analyses,
        "recommendation": recommendation,
        "production_registry_modified": False,
        "session_csv": str(ACTIVE_CSV_PATH),
    }
    _write_result(result)

    print("\n============================================================")
    print("FINAL RESOLUTION RESULT")
    print("============================================================")
    print(f"Status: {recommendation['status']}")
    recommended = recommendation.get("recommended_gz_bias_resolution")
    lateral = recommendation.get("corresponding_lateral_resolution_m")
    noise_step = recommendation.get("first_noise_dominated_bias_step")
    if recommended is not None:
        print(f"Recommended GZ_BIAS resolution: {float(recommended):.5f}")
        print(f"Corresponding lateral change:   {float(lateral) * 100:.2f} cm")
    else:
        print("No tested GZ_BIAS separation was reliably distinguishable.")
    if noise_step is not None:
        print(f"First noise-dominated step:      {float(noise_step):.5f}")
    print(f"Measurement CSV:                 {ACTIVE_CSV_PATH}")
    print(f"Result JSON:                     {RESULT_PATH}")
    print("Production registry modified:    NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
