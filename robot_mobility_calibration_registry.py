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
from typing import Mapping


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
    calibrated_at_utc: str = ""
    warning: str = ""

    def motor_distance(self, desired_distance_m: float) -> float:
        value = self.cmd_a * float(desired_distance_m) + self.cmd_b
        return max(0.0, value)

    def detail(self) -> str:
        detail = (
            f"calibration_source={self.source} "
            f"calibration_scanner={self.scanner or 'unknown'} "
            f"calibration_gz_bias={self.gz_bias:.9f} "
            f"calibration_cmd_a={self.cmd_a:.12f} "
            f"calibration_cmd_b={self.cmd_b:.12f}"
        )
        if self.warning:
            compact_warning = "_".join(self.warning.split())
            detail += f" calibration_warning={compact_warning}"
        return detail


def _finite_number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite")
    return result


def fallback_snapshot(
    *,
    gz_bias: float,
    cmd_a: float,
    cmd_b: float,
    warning: str = "",
    scanner: str = "",
) -> MobilityCalibrationSnapshot:
    return MobilityCalibrationSnapshot(
        scanner=scanner,
        gz_bias=float(gz_bias),
        cmd_a=float(cmd_a),
        cmd_b=float(cmd_b),
        source="fallback",
        warning=warning,
    )


def load_mobility_calibration(
    *,
    fallback_gz_bias: float,
    fallback_distance_model: Mapping[str, object],
    scanner_name_path: Path = SCANNER_NAME_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> MobilityCalibrationSnapshot:
    """Return the named robot's registry entry or a complete safe fallback."""
    fallback_cmd_a = _finite_number(fallback_distance_model.get("cmd_a"), "fallback cmd_a")
    fallback_cmd_b = _finite_number(fallback_distance_model.get("cmd_b"), "fallback cmd_b")

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

        return MobilityCalibrationSnapshot(
            scanner=scanner,
            gz_bias=gz_bias,
            cmd_a=cmd_a,
            cmd_b=cmd_b,
            source="registry",
            calibrated_at_utc=str(entry.get("calibrated_at_utc") or ""),
        )
    except Exception as exc:
        scanner_value = locals().get("scanner", "")
        return fallback_snapshot(
            gz_bias=fallback_gz_bias,
            cmd_a=fallback_cmd_a,
            cmd_b=fallback_cmd_b,
            scanner=scanner_value if isinstance(scanner_value, str) else "",
            warning=f"{type(exc).__name__}: {exc}",
        )
