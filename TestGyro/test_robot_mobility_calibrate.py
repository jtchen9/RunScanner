import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("robot_mobility_calibrate.py")


def load_module():
    motion = types.ModuleType("robot_mobility_motion")
    motion.MIN_MOVE_DISTANCE_M = 0.01
    sys.modules["robot_mobility_motion"] = motion
    spec = importlib.util.spec_from_file_location("calibration_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CalibrationAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_fixed_gz_sequence_matches_settled_experiment(self):
        self.assertEqual(
            self.module.GZ_BIAS_SEQUENCE,
            (0.0, -9.0, 9.0, -6.0, 6.0, -3.0, 3.0),
        )
        self.assertEqual(self.module.GZ_TEST_DISTANCE_M, 0.50)

    def test_distance_sequence_has_ten_points_and_kick_only_point(self):
        self.assertEqual(len(self.module.RAW_SEQUENCE_M), 10)
        self.assertEqual(self.module.RAW_SEQUENCE_M.count(0.0), 1)
        self.assertEqual(min(self.module.RAW_SEQUENCE_M), 0.0)
        self.assertEqual(max(self.module.RAW_SEQUENCE_M), 2.0)

    def test_detail_parser_extracts_named_numeric_field(self):
        detail = "forward_done final_yaw_deg=-6.350 max_abs_yaw_deg=6.350 source=test"
        self.assertEqual(self.module._detail_number(detail, "final_yaw_deg"), -6.350)
        self.assertEqual(self.module._detail_number(detail, "max_abs_yaw_deg"), 6.350)

    def test_detail_parser_rejects_missing_field(self):
        with self.assertRaises(RuntimeError):
            self.module._detail_number("forward_done", "final_yaw_deg")

    def test_charlie_seven_point_regression_reproduces_zero_crossing(self):
        points = [
            (0.0, 0.044), (-9.0, 17.955), (9.0, -13.261),
            (-6.0, 9.077), (6.0, -8.493), (-3.0, 5.440), (3.0, -6.350),
        ]
        fit = self.module._ordinary_line_fit(points)
        self.assertAlmostEqual(fit["zero_crossing_gz_bias"], 0.3766165403, places=8)
        self.assertGreater(fit["r_squared"], 0.98)
        quality = self.module._gz_quality(points, fit)
        self.assertEqual(quality["status"], "pass")
        self.assertTrue(quality["preferred_central_region"])

    def test_gz_quality_rejects_wrong_slope_direction(self):
        points = [(-9.0, -9.0), (-6.0, -6.0), (-3.0, -3.0),
                  (0.0, 0.1), (3.0, 3.0), (6.0, 6.0), (9.0, 9.0)]
        fit = self.module._ordinary_line_fit(points)
        quality = self.module._gz_quality(points, fit)
        self.assertEqual(quality["status"], "review")
        self.assertFalse(quality["checks"]["negative_slope"])

    def test_distance_fit_still_returns_inverse_command_model(self):
        points = [(0.0, 0.06), (0.5, 0.54), (1.0, 1.02), (2.0, 1.98)]
        fit = self.module._linear_fit(points)
        self.assertGreater(fit["actual_a"], 0.0)
        self.assertTrue(math.isfinite(fit["cmd_a"]))
        self.assertTrue(math.isfinite(fit["cmd_b"]))


if __name__ == "__main__":
    unittest.main()
