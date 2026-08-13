#!/usr/bin/env python3
"""Adaptive coarse GZ_BIAS candidate search for one robot.

This is a test-only experiment.  It measures a stationary gyro center, screens
GZ_BIAS candidates at 0.1 spacing, extends outward until each side is clearly
worse, then brings the best neighboring pair to seven accepted trials each.

The experiment writes diagnostic CSV/JSON files only.  It never modifies the
production mobility calibration registry.
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
from typing import Dict, List, Mapping, MutableMapping, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))


TEST_DISTANCE_M = 1.80
BIAS_STEP = 0.10
INITIAL_SCREENING_TRIALS = 2
FINALIST_TOTAL_TRIALS = 7
MAX_STEPS_FROM_CENTER = 4

OBVIOUSLY_WORSE_RMS_MARGIN_M = 0.020
FINAL_RMS_TIE_MARGIN_M = 0.010
SOFT_MEASUREMENT_LIMIT = 24

STATIONARY_SAMPLE_SEC = 5.0
STATIONARY_DISCARD_SEC = 1.0
STATIONARY_SAMPLE_DT_SEC = 0.02
STATIONARY_TRIM_FRACTION = 0.10

REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"
SCANNER_NAME_PATH = ROBOT_ROOT / "scanner_name.txt"
OUTPUT_DIR = SCRIPT_DIR / "gz_bias_candidate_search"
ACTIVE_CSV_PATH = OUTPUT_DIR / "current_candidate_search.csv"
RESULT_PATH = OUTPUT_DIR / "candidate_search_result.json"
ARCHIVE_DIR = OUTPUT_DIR / "archive"

CSV_FIELDS = (
    "recorded_at_utc",
    "scanner",
    "phase",
    "reason",
    "sequence_number",
    "candidate_index",
    "trial_gz_bias",
    "candidate_trial_number",
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


def _load_identity_and_distance_model() -> Tuple[str, float, float, float, str]:
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
        raise RuntimeError(f"no production calibration for {scanner}")
    if entry.get("production_loader_enabled") is not True:
        raise RuntimeError(f"production calibration is not enabled for {scanner}")
    model = entry.get("distance_model")
    if not isinstance(model, dict):
        raise RuntimeError(f"distance_model is missing for {scanner}")

    production_bias = _finite(entry.get("gz_bias"), "production gz_bias")
    cmd_a = _finite(model.get("cmd_a"), "production cmd_a")
    cmd_b = _finite(model.get("cmd_b"), "production cmd_b")
    if cmd_a <= 0.0:
        raise RuntimeError("production cmd_a must be positive")
    calibrated_at = str(entry.get("calibrated_at_utc") or "")
    return scanner, production_bias, cmd_a, cmd_b, calibrated_at


def _archive_previous(scanner: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in (ACTIVE_CSV_PATH, RESULT_PATH):
        if path.exists():
            destination = ARCHIVE_DIR / f"{scanner}-{_timestamp()}-{path.name}"
            shutil.move(str(path), str(destination))


def _append_csv(row: Mapping[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not ACTIVE_CSV_PATH.exists()
    with ACTIVE_CSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in CSV_FIELDS})


def _trimmed_mean(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise RuntimeError("no gyro samples collected")
    ordered = sorted(values)
    count = int(len(ordered) * fraction)
    if count > 0 and 2 * count < len(ordered):
        ordered = ordered[count:-count]
    return statistics.fmean(ordered)


def _measure_stationary_bias() -> Tuple[float, float, int]:
    import robot_mobility_motion as motion

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
        answer = input("Keep this measurement? [Y/n]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter Y to keep this measurement or N to retry it.")


def _execute_move(gz_bias: float, cmd_a: float, cmd_b: float) -> Tuple[bool, str]:
    import robot_mobility_motion as motion

    motor_distance = max(0.0, cmd_a * TEST_DISTANCE_M + cmd_b)
    return motion._run_move(
        forward=True,
        distance_m=TEST_DISTANCE_M,
        calibration_gz_bias=gz_bias,
        motor_distance_override=motor_distance,
    )


def summarize_candidate(values: Sequence[float]) -> Dict[str, float | int]:
    if not values:
        raise ValueError("candidate has no accepted lateral measurements")
    numbers = [float(value) for value in values]
    mean = statistics.fmean(numbers)
    variance = statistics.variance(numbers) if len(numbers) > 1 else 0.0
    rms = math.sqrt(statistics.fmean(value * value for value in numbers))
    absolute = [abs(value) for value in numbers]
    return {
        "count": len(numbers),
        "signed_mean_m": mean,
        "absolute_mean_m": abs(mean),
        "sample_standard_deviation_m": math.sqrt(variance),
        "rms_lateral_error_m": rms,
        "median_lateral_m": float(statistics.median(numbers)),
        "median_absolute_lateral_m": float(statistics.median(absolute)),
        "maximum_absolute_lateral_m": max(absolute),
        "within_3cm_count": sum(value <= 0.03 for value in absolute),
        "within_5cm_count": sum(value <= 0.05 for value in absolute),
    }


def obviously_worse(
    outer_values: Sequence[float],
    inward_values: Sequence[float],
) -> bool:
    outer = summarize_candidate(outer_values)
    inward = summarize_candidate(inward_values)
    return (
        float(outer["rms_lateral_error_m"])
        > float(inward["rms_lateral_error_m"]) + OBVIOUSLY_WORSE_RMS_MARGIN_M
        and min(abs(value) for value in outer_values)
        >= float(inward["median_absolute_lateral_m"])
    )


def choose_best_neighboring_pair(
    candidates: Mapping[int, Sequence[float]],
) -> Tuple[int, int, List[Dict[str, float]]]:
    indexes = sorted(candidates)
    pairs: List[Dict[str, float]] = []
    for left, right in zip(indexes, indexes[1:]):
        if right != left + 1:
            continue
        left_summary = summarize_candidate(candidates[left])
        right_summary = summarize_candidate(candidates[right])
        score = (
            float(left_summary["rms_lateral_error_m"])
            + float(right_summary["rms_lateral_error_m"])
        ) / 2.0
        pairs.append({"left_index": left, "right_index": right, "pair_score_m": score})
    if not pairs:
        raise ValueError("no neighboring candidate pair is available")
    pairs.sort(key=lambda item: (item["pair_score_m"], abs(item["left_index"] + item["right_index"])))
    winner = pairs[0]
    return int(winner["left_index"]), int(winner["right_index"]), pairs


def choose_final_candidate(
    first_index: int,
    first_values: Sequence[float],
    second_index: int,
    second_values: Sequence[float],
) -> Tuple[int, str]:
    first = summarize_candidate(first_values)
    second = summarize_candidate(second_values)
    first_rms = float(first["rms_lateral_error_m"])
    second_rms = float(second["rms_lateral_error_m"])
    if abs(first_rms - second_rms) > FINAL_RMS_TIE_MARGIN_M:
        return (first_index, "LOWEST_RMS") if first_rms < second_rms else (second_index, "LOWEST_RMS")

    keys = (
        "absolute_mean_m",
        "sample_standard_deviation_m",
        "maximum_absolute_lateral_m",
    )
    first_rank = tuple(float(first[key]) for key in keys) + (abs(first_index),)
    second_rank = tuple(float(second[key]) for key in keys) + (abs(second_index),)
    if first_rank <= second_rank:
        return first_index, "RMS_TIE_BREAK"
    return second_index, "RMS_TIE_BREAK"


class Experiment:
    def __init__(self, scanner: str, center: float, cmd_a: float, cmd_b: float):
        self.scanner = scanner
        self.center = center
        self.cmd_a = cmd_a
        self.cmd_b = cmd_b
        self.values: Dict[int, List[float]] = {}
        self.forward_values: Dict[int, List[float]] = {}
        self.accepted_count = 0
        self.soft_limit_acknowledged = False

    def bias(self, index: int) -> float:
        return self.center + index * BIAS_STEP

    def _soft_limit_prompt(self) -> None:
        if self.soft_limit_acknowledged or self.accepted_count < SOFT_MEASUREMENT_LIMIT:
            return
        print("\nThe soft limit of 24 accepted measurements has been reached.")
        while True:
            answer = input("Continue this diagnostic? [Y/n]: ").strip().lower()
            if answer in {"", "y", "yes"}:
                self.soft_limit_acknowledged = True
                return
            if answer in {"n", "no"}:
                raise RuntimeError("candidate search stopped at operator soft limit")
            print("Enter Y to continue or N to stop.")

    def collect(self, index: int, phase: str, reason: str) -> None:
        self._soft_limit_prompt()
        attempt = 1
        while True:
            candidate_trial = len(self.values.get(index, [])) + 1
            print("\n------------------------------------------------------------")
            print(f"Phase:                    {phase}")
            print(f"Reason:                   {reason}")
            print(f"Candidate index:          {index:+d}")
            print(f"Trial GZ_BIAS:            {self.bias(index):.9f}")
            print(f"Candidate trial number:   {candidate_trial}")
            print(f"Accepted total so far:    {self.accepted_count}")
            input(
                "Place the robot at the common start mark and heading; "
                "press Enter when ready: "
            )
            print("Starting in 3 seconds...")
            time.sleep(3.0)
            ok, detail = _execute_move(self.bias(index), self.cmd_a, self.cmd_b)
            row: Dict[str, object] = {
                "recorded_at_utc": _utc_now(),
                "scanner": self.scanner,
                "phase": phase,
                "reason": reason,
                "sequence_number": self.accepted_count + 1,
                "candidate_index": index,
                "trial_gz_bias": self.bias(index),
                "candidate_trial_number": candidate_trial,
                "accepted": False,
                "execution_ok": ok,
                "execution_detail": detail,
            }
            if not ok:
                print(f"Movement failed: {detail}")
                _append_csv(row)
                answer = input("Retry this movement? [Y/n]: ").strip().lower()
                if answer in {"", "y", "yes"}:
                    attempt += 1
                    continue
                raise RuntimeError("candidate search stopped after movement failure")

            forward = _prompt_cm("Actual forward distance (cm): ")
            lateral = _prompt_cm("Lateral shift (cm, right positive): ")
            keep = _keep_measurement()
            row.update(
                {
                    "accepted": keep,
                    "actual_forward_distance_m": forward,
                    "actual_lateral_shift_m": lateral,
                }
            )
            _append_csv(row)
            if keep:
                self.values.setdefault(index, []).append(lateral)
                self.forward_values.setdefault(index, []).append(forward)
                self.accepted_count += 1
                return
            attempt += 1

    def ensure_count(self, index: int, count: int, phase: str, reason: str) -> None:
        while len(self.values.get(index, [])) < count:
            self.collect(index, phase, reason)


def _screen_direction(experiment: Experiment, direction: int) -> Dict[str, object]:
    inward = 0
    outer = direction
    stop_reason = ""
    boundary_confirmed = False
    while abs(outer) <= MAX_STEPS_FROM_CENTER:
        experiment.ensure_count(
            outer,
            INITIAL_SCREENING_TRIALS,
            "screening",
            "initial_neighbor" if abs(outer) == 1 else "outward_extension",
        )
        if obviously_worse(experiment.values[outer], experiment.values[inward]):
            boundary_confirmed = True
            stop_reason = "OUTER_CANDIDATE_OBVIOUSLY_WORSE"
            break

        outer_rms = float(summarize_candidate(experiment.values[outer])["rms_lateral_error_m"])
        inward_rms = float(summarize_candidate(experiment.values[inward])["rms_lateral_error_m"])
        if outer_rms >= inward_rms:
            experiment.ensure_count(
                outer,
                INITIAL_SCREENING_TRIALS + 1,
                "screening",
                "ambiguous_outer_candidate",
            )
            if obviously_worse(experiment.values[outer], experiment.values[inward]):
                boundary_confirmed = True
                stop_reason = "OUTER_CANDIDATE_OBVIOUSLY_WORSE_AFTER_THIRD_TRIAL"
                break

        inward = outer
        outer += direction
    else:
        stop_reason = "MAXIMUM_SEARCH_DISTANCE_REACHED"

    if not stop_reason:
        stop_reason = "MAXIMUM_SEARCH_DISTANCE_REACHED"
    return {
        "direction": direction,
        "boundary_confirmed": boundary_confirmed,
        "last_tested_index": inward if abs(outer) > MAX_STEPS_FROM_CENTER else outer,
        "stop_reason": stop_reason,
    }


def _write_result(result: Mapping[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(RESULT_PATH)


def main() -> int:
    scanner, production_bias, cmd_a, cmd_b, calibrated_at = _load_identity_and_distance_model()
    _archive_previous(scanner)

    print("============================================================")
    print("Adaptive coarse GZ_BIAS candidate search")
    print("============================================================")
    print(f"Robot:                       {scanner}")
    print(f"Production GZ_BIAS:          {production_bias:.9f}")
    print(f"Fixed movement distance:     {TEST_DISTANCE_M:.2f} m")
    print(f"Coarse bias spacing:         {BIAS_STEP:.2f}")
    print("Production registry writes:  DISABLED")
    print()
    input("Keep the robot stationary and press Enter to measure the center: ")
    center, spread, sample_count = _measure_stationary_bias()
    print(f"Stationary center GZ_BIAS:   {center:.9f}")
    print(f"Stationary sample spread:    {spread:.6f} deg/s ({sample_count} samples)")
    print("Candidates begin at center and center ±0.1.")
    input("Press Enter to begin movement screening: ")

    experiment = Experiment(scanner, center, cmd_a, cmd_b)

    # Counterbalanced initial order: LOW/CENTER/HIGH then HIGH/LOW/CENTER.
    for index in (-1, 0, 1, 1, -1, 0):
        experiment.collect(index, "screening", "initial_counterbalanced_scan")

    lower = _screen_direction(experiment, -1)
    upper = _screen_direction(experiment, 1)

    first, second, pair_scores = choose_best_neighboring_pair(experiment.values)
    print("\n============================================================")
    print("SCREENING COMPLETE")
    print("============================================================")
    for index in sorted(experiment.values):
        summary = summarize_candidate(experiment.values[index])
        print(
            f"index={index:+d} bias={experiment.bias(index):+.9f} "
            f"n={summary['count']} rms={float(summary['rms_lateral_error_m']) * 100:.2f} cm"
        )
    print(
        f"Selected neighboring finalists: {first:+d} and {second:+d} "
        f"({experiment.bias(first):.9f}, {experiment.bias(second):.9f})"
    )
    input("Press Enter to begin the interleaved finalist comparison: ")

    round_number = 0
    while (
        len(experiment.values[first]) < FINALIST_TOTAL_TRIALS
        or len(experiment.values[second]) < FINALIST_TOTAL_TRIALS
    ):
        order = (first, second) if round_number % 2 == 0 else (second, first)
        for index in order:
            if len(experiment.values[index]) < FINALIST_TOTAL_TRIALS:
                experiment.collect(index, "finalist", "interleaved_final_comparison")
        round_number += 1

    selected, selection_reason = choose_final_candidate(
        first,
        experiment.values[first],
        second,
        experiment.values[second],
    )

    candidate_results: Dict[str, object] = {}
    for index in sorted(experiment.values):
        candidate_results[str(index)] = {
            "candidate_index": index,
            "gz_bias": experiment.bias(index),
            "lateral_measurements_m": experiment.values[index],
            "forward_measurements_m": experiment.forward_values[index],
            "statistics": summarize_candidate(experiment.values[index]),
            "is_finalist": index in {first, second},
            "is_selected": index == selected,
        }

    result: Dict[str, object] = {
        "generated_at_utc": _utc_now(),
        "scanner": scanner,
        "production_calibration": {
            "gz_bias": production_bias,
            "cmd_a": cmd_a,
            "cmd_b": cmd_b,
            "calibrated_at_utc": calibrated_at,
        },
        "stationary_measurement": {
            "gz_bias_center": center,
            "sample_spread": spread,
            "sample_count": sample_count,
        },
        "experiment": {
            "test_distance_m": TEST_DISTANCE_M,
            "bias_step": BIAS_STEP,
            "initial_screening_trials": INITIAL_SCREENING_TRIALS,
            "finalist_total_trials": FINALIST_TOTAL_TRIALS,
            "maximum_steps_from_center": MAX_STEPS_FROM_CENTER,
            "obviously_worse_rms_margin_m": OBVIOUSLY_WORSE_RMS_MARGIN_M,
            "final_rms_tie_margin_m": FINAL_RMS_TIE_MARGIN_M,
            "accepted_measurement_count": experiment.accepted_count,
        },
        "direction_search": {"lower": lower, "upper": upper},
        "pair_scores": pair_scores,
        "finalists": [first, second],
        "selected_candidate_index": selected,
        "selected_gz_bias": experiment.bias(selected),
        "selection_reason": selection_reason,
        "candidates": candidate_results,
        "production_registry_modified": False,
        "session_csv": str(ACTIVE_CSV_PATH),
    }
    _write_result(result)

    print("\n============================================================")
    print("FINAL CANDIDATE RESULT")
    print("============================================================")
    for index in (first, second):
        summary = summarize_candidate(experiment.values[index])
        print(f"Candidate {index:+d}: GZ_BIAS={experiment.bias(index):.9f}")
        print(f"  n:                       {summary['count']}")
        print(f"  signed mean:             {float(summary['signed_mean_m']) * 100:+.2f} cm")
        print(f"  standard deviation:      {float(summary['sample_standard_deviation_m']) * 100:.2f} cm")
        print(f"  RMS lateral error:       {float(summary['rms_lateral_error_m']) * 100:.2f} cm")
        print(f"  maximum absolute error:  {float(summary['maximum_absolute_lateral_m']) * 100:.2f} cm")
        print(f"  within 3 cm:             {summary['within_3cm_count']}/{summary['count']}")
        print(f"  within 5 cm:             {summary['within_5cm_count']}/{summary['count']}")
    print(f"Selected candidate:          {selected:+d}")
    print(f"Selected GZ_BIAS:            {experiment.bias(selected):.9f}")
    print(f"Selection reason:            {selection_reason}")
    print(f"Accepted measurements:       {experiment.accepted_count}")
    print(f"Measurement CSV:             {ACTIVE_CSV_PATH}")
    print(f"Result JSON:                 {RESULT_PATH}")
    print("Production registry modified: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
