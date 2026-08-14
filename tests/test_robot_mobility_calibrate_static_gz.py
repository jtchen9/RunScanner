import builtins
import importlib.util
import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "TestGyro" / "robot_mobility_calibrate.py"


def load_module():
    motion = types.ModuleType("robot_mobility_motion")
    motion.MIN_MOVE_DISTANCE_M = 0.01
    sys.modules["robot_mobility_motion"] = motion
    spec = importlib.util.spec_from_file_location("calibration_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StaticGzCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_trimmed_mean_discards_both_tails(self):
        values = [-100.0] + [2.0] * 8 + [100.0]
        self.assertEqual(self.module._trimmed_mean(values, 0.10), 2.0)

    def test_active_stage_uses_stationary_measurement_without_walking(self):
        original_measure = self.module._measure_stationary_gz_bias
        original_prompt = self.module._prompt_accept_stage
        original_append = self.module._append_row
        original_input = builtins.input
        original_run_move = getattr(self.module.motion, "_run_move", None)
        rows = []
        try:
            self.module._measure_stationary_gz_bias = lambda: (-0.262639673, 0.1, 170)
            self.module._prompt_accept_stage = lambda _name: True
            self.module._append_row = rows.append
            builtins.input = lambda _prompt="": ""

            def must_not_walk(*_args, **_kwargs):
                raise AssertionError("active stationary calibration must not move the robot")

            self.module.motion._run_move = must_not_walk
            result = self.module._run_gz_bias_stage("twin-scout-charlie", None)
        finally:
            self.module._measure_stationary_gz_bias = original_measure
            self.module._prompt_accept_stage = original_prompt
            self.module._append_row = original_append
            builtins.input = original_input
            if original_run_move is None:
                delattr(self.module.motion, "_run_move")
            else:
                self.module.motion._run_move = original_run_move

        self.assertEqual(result["method"], "stationary_trimmed_mean")
        self.assertAlmostEqual(result["gz_bias"], -0.262639673)
        self.assertEqual(rows[0]["phase"], "gz_bias_stationary")
        self.assertTrue(rows[0]["accepted"])

    def test_seven_movement_diagnostic_is_retained_but_disabled(self):
        self.assertTrue(hasattr(self.module, "_run_gz_bias_stage_seven_movement_disabled"))
        self.assertEqual(
            self.module.GZ_BIAS_SEQUENCE,
            (0.0, -9.0, 9.0, -6.0, 6.0, -3.0, 3.0),
        )

    def test_distance_sequence_is_unchanged(self):
        self.assertEqual(
            self.module.RAW_SEQUENCE_M,
            (1.00, 0.00, 2.00, 0.20, 1.50, 0.50, 1.80, 0.10, 1.20, 0.30),
        )


if __name__ == "__main__":
    unittest.main()
