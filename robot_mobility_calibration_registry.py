#!/usr/bin/env python3
"""Load one robot's accepted mobility calibration safely.

The registry and identity are read for each requested snapshot.  A motion
primitive keeps the returned immutable values for its complete execution.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
ROBOT_ROOT = Path(__file__).resolve().parent
SCANNER_NAME_PATH = ROBOT_ROOT / "scanner_name.txt"
REGISTRY_PATH = ROBOT_ROOT / "robot_mobility_calibration.json"


@dataclass(frozen=True)
class MobilityCalibrationSnapshot:
    scanner: str
    gz_bias: float
    cmd_a: float
    cmd_b: float
    source: str
    kick_distance_m: float = 0.0
    skip_threshold_m: float = 0.0
    calibrated_at_utc: str = ""
    warning: str = ""
    bump_positive_y_distance_m: Optional[float] = None
    bump_negative_y_distance_m: Optional[float] = None
    forward_kick_right_speed: int = 40
    forward_kick_left_speed: int = 40

    def motor_distance(self, desired_distance_m: float) -> float:
        value = self.cmd_a * float(desired_distance_m) + self.cmd_b
        return max(0.0, value)

    def detail(self) -> str:
        detail = (
            f"calibration_source={self.source} "
            f"calibration_scanner={self.scanner or 'unknown'} "
            f"calibration_gz_bias={self.gz_bias:.9f} "
            f"calibration_cmd_a={self.cmd_a:.12f} "
            f"calibration_cmd_b={self.cmd_b:.12f} "
            f"calibration_kick_distance_m={self.kick_distance_m:.3f} "
            f"calibration_skip_threshold_m={self.skip_threshold_m:.3f} "
            f"calibration_forward_kick_right_speed={self.forward_kick_right_speed} "
            f"calibration_forward_kick_left_speed={self.forward_kick_left_speed}"
        )
        if self.warning:
            compact_warning = "_".join(self.warning.split())
            detail += f" calibration_warning={compact_warning}"
        return detail

    def bump_crossing_distance(self, direction: str) -> float:
        values = {
            "positive_y": self.bump_positive_y_distance_m,
            "negative_y": self.bump_negative_y_distance_m,
        }
        if direction not in values:
            raise MobilityCalibrationError(
                f"unsupported bump-crossing direction: {direction}"
            )
        value = values[direction]
        if value is None:
            raise MobilityCalibrationError(
                f"bump-crossing calibration is missing for {direction}"
            )
        return value


class MobilityCalibrationError(RuntimeError):
    """Production movement cannot start without valid robot calibration."""


def _finite_number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite")
    return result


def load_mobility_calibration(
    *,
    scanner_name_path: Path = SCANNER_NAME_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> MobilityCalibrationSnapshot:
    """Return the named robot's valid entry or fail closed before movement."""
    try:
        scanner = scanner_name_path.read_text(encoding="utf-8").strip()
        if not scanner:
            raise ValueError("robot identity is empty")

        document = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported calibration registry schema")
        robots = document.get("robots")
        if not isinstance(robots, dict):
            raise ValueError("calibration registry has no robots object")
        entry = robots.get(scanner)
        if not isinstance(entry, dict):
            raise ValueError(f"no calibration for {scanner}")
        if entry.get("production_loader_enabled") is not True:
            raise ValueError(f"production calibration for {scanner} is not enabled")

        model = entry.get("distance_model")
        if not isinstance(model, dict):
            raise ValueError("distance_model is missing")
        gz_bias = _finite_number(entry.get("gz_bias"), "gz_bias")
        cmd_a = _finite_number(model.get("cmd_a"), "cmd_a")
        cmd_b = _finite_number(model.get("cmd_b"), "cmd_b")
        if cmd_a <= 0.0:
            raise ValueError("cmd_a must be positive")

        short_move = entry.get("short_move")
        if short_move is None:
            kick_distance_m = 0.0
            skip_threshold_m = 0.0
        elif isinstance(short_move, dict):
            kick_distance_m = _finite_number(
                short_move.get("kick_distance_m"),
                "kick_distance_m",
            )
            skip_threshold_m = _finite_number(
                short_move.get("skip_threshold_m"),
                "skip_threshold_m",
            )
            if kick_distance_m < 0.0:
                raise ValueError("kick_distance_m must not be negative")
            if skip_threshold_m < 0.0:
                raise ValueError("skip_threshold_m must not be negative")
            if skip_threshold_m > kick_distance_m:
                raise ValueError("skip_threshold_m must not exceed kick_distance_m")
        else:
            raise ValueError("short_move must be an object")

        bump_crossing = entry.get("bump_crossing")
        bump_positive_y_distance_m: Optional[float] = None
        bump_negative_y_distance_m: Optional[float] = None
        if bump_crossing is not None:
            if not isinstance(bump_crossing, dict):
                raise ValueError("bump_crossing must be an object")
            positive_y = bump_crossing.get("positive_y")
            negative_y = bump_crossing.get("negative_y")
            if not isinstance(positive_y, dict) or not isinstance(negative_y, dict):
                raise ValueError("bump_crossing must contain positive_y and negative_y")
            bump_positive_y_distance_m = _finite_number(
                positive_y.get("command_distance_m"),
                "bump_crossing.positive_y.command_distance_m",
            )
            bump_negative_y_distance_m = _finite_number(
                negative_y.get("command_distance_m"),
                "bump_crossing.negative_y.command_distance_m",
            )
            if bump_positive_y_distance_m <= 0.0 or bump_negative_y_distance_m <= 0.0:
                raise ValueError("bump-crossing command distances must be positive")

        move_startup = entry.get("move_startup")
        forward_kick_right_speed = 40
        forward_kick_left_speed = 40
        if move_startup is not None:
            if not isinstance(move_startup, dict):
                raise ValueError("move_startup must be an object")
            forward_startup = move_startup.get("forward")
            if not isinstance(forward_startup, dict):
                raise ValueError("move_startup.forward must be an object")
            right_value = _finite_number(
                forward_startup.get("right_kick_speed"),
                "move_startup.forward.right_kick_speed",
            )
            left_value = _finite_number(
                forward_startup.get("left_kick_speed"),
                "move_startup.forward.left_kick_speed",
            )
            if not right_value.is_integer() or not left_value.is_integer():
                raise ValueError("forward kick speeds must be integers")
            forward_kick_right_speed = int(right_value)
            forward_kick_left_speed = int(left_value)
            if not (0 <= forward_kick_right_speed <= 100):
                raise ValueError("right_kick_speed must be between 0 and 100")
            if not (0 <= forward_kick_left_speed <= 100):
                raise ValueError("left_kick_speed must be between 0 and 100")

        return MobilityCalibrationSnapshot(
            scanner=scanner,
            gz_bias=gz_bias,
            cmd_a=cmd_a,
            cmd_b=cmd_b,
            source="registry",
            kick_distance_m=kick_distance_m,
            skip_threshold_m=skip_threshold_m,
            calibrated_at_utc=str(entry.get("calibrated_at_utc") or ""),
            bump_positive_y_distance_m=bump_positive_y_distance_m,
            bump_negative_y_distance_m=bump_negative_y_distance_m,
            forward_kick_right_speed=forward_kick_right_speed,
            forward_kick_left_speed=forward_kick_left_speed,
        )
    except MobilityCalibrationError:
        raise
    except Exception as exc:
        raise MobilityCalibrationError(f"{type(exc).__name__}: {exc}") from exc
