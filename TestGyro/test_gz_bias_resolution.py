import importlib.util
import math
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("t8_gz_bias_resolution.py")
SPEC = importlib.util.spec_from_file_location("t8_gz_bias_resolution", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
resolution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolution)


class GzBiasResolutionAnalysisTests(unittest.TestCase):
    def test_clear_effect_is_distinguishable(self):
        result = resolution.analyze_step(
            bias_step=0.05,
            pairs=(
                {"low": -0.030, "high": 0.010},
                {"low": -0.025, "high": 0.015},
                {"low": -0.035, "high": 0.005},
            ),
        )
        self.assertTrue(result["distinguishable"])
        self.assertAlmostEqual(result["median_paired_lateral_change_m"], 0.04)

    def test_small_effect_is_noise_dominated(self):
        result = resolution.analyze_step(
            bias_step=0.00625,
            pairs=(
                {"low": 0.001, "high": 0.008},
                {"low": -0.004, "high": 0.004},
                {"low": 0.003, "high": 0.009},
            ),
        )
        self.assertFalse(result["distinguishable"])

    def test_inconsistent_pair_direction_is_not_distinguishable(self):
        result = resolution.analyze_step(
            bias_step=0.025,
            pairs=(
                {"low": -0.020, "high": 0.020},
                {"low": 0.030, "high": -0.010},
                {"low": -0.025, "high": 0.015},
            ),
        )
        self.assertFalse(result["distinguishable"])

    def test_finest_contiguous_distinguishable_step_is_recommended(self):
        levels = [
            {"bias_step": 0.10, "distinguishable": True, "median_paired_lateral_change_m": 0.08},
            {"bias_step": 0.05, "distinguishable": True, "median_paired_lateral_change_m": 0.04},
            {"bias_step": 0.025, "distinguishable": True, "median_paired_lateral_change_m": 0.025},
            {"bias_step": 0.0125, "distinguishable": False, "median_paired_lateral_change_m": 0.012},
            {"bias_step": 0.00625, "distinguishable": False, "median_paired_lateral_change_m": 0.006},
        ]
        result = resolution.recommend_resolution(levels)
        self.assertEqual(result["status"], "RESOLUTION_FOUND")
        self.assertEqual(result["recommended_gz_bias_resolution"], 0.025)
        self.assertEqual(result["first_noise_dominated_bias_step"], 0.0125)

    def test_finer_recovery_after_noise_dominated_level_is_not_trusted(self):
        levels = [
            {"bias_step": 0.10, "distinguishable": True, "median_paired_lateral_change_m": 0.08},
            {"bias_step": 0.05, "distinguishable": False, "median_paired_lateral_change_m": 0.01},
            {"bias_step": 0.025, "distinguishable": True, "median_paired_lateral_change_m": 0.03},
        ]
        result = resolution.recommend_resolution(levels)
        self.assertEqual(result["recommended_gz_bias_resolution"], 0.10)
        self.assertEqual(result["first_noise_dominated_bias_step"], 0.05)

    def test_no_distinguishable_step_has_no_recommendation(self):
        levels = [
            {"bias_step": 0.10, "distinguishable": False, "median_paired_lateral_change_m": 0.01},
            {"bias_step": 0.05, "distinguishable": False, "median_paired_lateral_change_m": 0.005},
        ]
        result = resolution.recommend_resolution(levels)
        self.assertEqual(result["status"], "NO_TESTED_STEP_DISTINGUISHABLE")
        self.assertIsNone(result["recommended_gz_bias_resolution"])


if __name__ == "__main__":
    unittest.main()
