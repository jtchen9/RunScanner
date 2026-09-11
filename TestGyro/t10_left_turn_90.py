#!/usr/bin/env python3
"""Make ten physical +90-degree (left/CCW) turns with zero stop margin."""

import sys
import time
from dataclasses import replace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = SCRIPT_DIR.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

import robot_mobility_motion as motion
from robot_mobility_calibration_registry import load_mobility_calibration


TURN_COUNT = 10
TURN_ANGLE_DEG = 90.0
SETTLE_SEC = 0.75


def main() -> int:
    production = load_mobility_calibration()
    zero_margin = replace(
        production,
        source="ten_turn_test_zero_margin",
        warning="test_only_production_calibration_unchanged",
        turn_ccw_stop_margin_deg=0.0,
        turn_cw_stop_margin_deg=0.0,
    )

    print(f"Robot: {production.scanner}")
    print("Ten physical +90-degree left/CCW turns; stop margin = 0 degrees.")
    print("Production calibration will not be changed and no result file will be saved.")
    input("Clear the rotation area and press Enter to start: ")
    time.sleep(3.0)

    for turn_index in range(1, TURN_COUNT + 1):
        result = motion._run_turn_measured(
            left=False,
            angle_deg=TURN_ANGLE_DEG,
            calibration=zero_margin,
        )
        print(
            f"Turn {turn_index}/{TURN_COUNT}: "
            f"{'OK' if result.ok else 'FAILED'} "
            f"measured_yaw_deg={result.measured_yaw_deg:+.3f}"
        )
        if not result.ok:
            print(result.detail)
            return 1
        if turn_index < TURN_COUNT:
            time.sleep(SETTLE_SEC)

    print("Completed ten +90-degree turns.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
