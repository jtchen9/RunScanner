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
    config = types.ModuleType("config")
    config.MOTOR_MOVE_DISTANCE_MODEL = {"cmd_a": 1.1, "cmd_b": -0.07}
    sys.modules["config"] = config
    registry = types.ModuleType("robot_mobility_calibration_registry")

    def mobility_calibration_snapshot(**kwargs):
        return types.SimpleNamespace(**kwargs)

    registry.MobilityCalibrationSnapshot = mobility_calibration_snapshot
    sys.modules["robot_mobility_calibration_registry"] = registry
    spec = importlib.util.spec_from_file_location("calibration_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GzWalkingCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def run_stage(self, answers, move_results=None, previous=None):
        module = self.module
        original_measure = module._measure_stationary_gz_bias
        original_append = module._append_row
        original_sleep = module.time.sleep
        original_input = builtins.input
        original_run_move = getattr(module.motion, "_run_move", None)
        rows = []
        calls = []
        answers = iter(answers)
        move_results = iter(move_results or [(True, "forward_done")])
        try:
            module._measure_stationary_gz_bias = lambda: (-0.262639673, 0.1, 170)
            module._append_row = rows.append
            module.time.sleep = lambda _seconds: None
            builtins.input = lambda _prompt="": next(answers)

            def run_move(**kwargs):
                calls.append(kwargs)
                return next(move_results)

            module.motion._run_move = run_move
            result = module._run_gz_bias_stage(
                "twin-scout-charlie",
                previous,
            )
            return result, rows, calls
        finally:
            module._measure_stationary_gz_bias = original_measure
            module._append_row = original_append
            module.time.sleep = original_sleep
            builtins.input = original_input
            if original_run_move is None:
                delattr(module.motion, "_run_move")
            else:
                module.motion._run_move = original_run_move

    def test_static_value_is_default_for_first_three_metre_trial(self):
        result, rows, calls = self.run_stage(["", "", "", ""])
        self.assertEqual(result["method"], "stationary_seed_manual_3m_walk")
        self.assertAlmostEqual(result["gz_bias"], -0.262639673)
        self.assertEqual(result["accepted_trial"], 1)
        self.assertEqual(calls[0]["distance_m"], 3.0)
        self.assertAlmostEqual(calls[0]["calibration_gz_bias"], -0.262639673)
        self.assertEqual(calls[0]["calibration"].scanner, "twin-scout-charlie")
        self.assertAlmostEqual(calls[0]["calibration"].gz_bias, -0.262639673)
        self.assertEqual(calls[0]["calibration"].cmd_a, 1.1)
        self.assertEqual(calls[0]["calibration"].cmd_b, -0.07)
        self.assertEqual(rows[0]["phase"], "gz_bias_stationary_seed")
        self.assertEqual(rows[1]["phase"], "gz_bias_physical_walk")
        self.assertTrue(rows[1]["accepted"])

    def test_unsatisfactory_trial_accepts_operator_supplied_next_bias(self):
        result, rows, calls = self.run_stage(
            ["", "", "", "n", "-0.3", "", "y"],
            move_results=[(True, "first"), (True, "second")],
            previous={"gz_bias": -0.4},
        )
        self.assertEqual([call["calibration_gz_bias"] for call in calls], [
            -0.262639673,
            -0.3,
        ])
        self.assertAlmostEqual(result["gz_bias"], -0.3)
        self.assertEqual(result["accepted_trial"], 2)
        self.assertFalse(rows[1]["accepted"])
        self.assertTrue(rows[2]["accepted"])

    def test_cancel_before_first_walk_never_moves_robot(self):
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.run_stage(["", "q"])

    def test_candidate_must_be_finite(self):
        original_input = builtins.input
        answers = iter(["nan", "inf", "-0.31"])
        try:
            builtins.input = lambda _prompt="": next(answers)
            self.assertEqual(self.module._prompt_gz_candidate(-0.26), -0.31)
        finally:
            builtins.input = original_input

    def test_raw_and_verification_paths_always_supply_bootstrap_snapshot(self):
        calls = []
        original_run_move = getattr(self.module.motion, "_run_move", None)
        try:
            self.module.motion._run_move = lambda **kwargs: (
                calls.append(kwargs) or (True, "ok")
            )
            self.module._execute_raw(
                "twin-scout-bravo",
                1.0,
                -0.61,
            )
            self.module._execute_candidate_calibrated(
                "twin-scout-bravo",
                1.0,
                1.25,
                -0.08,
                -0.61,
            )
        finally:
            if original_run_move is None:
                delattr(self.module.motion, "_run_move")
            else:
                self.module.motion._run_move = original_run_move

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["calibration"].scanner, "twin-scout-bravo")
        self.assertEqual(calls[0]["calibration"].cmd_a, 1.1)
        self.assertEqual(calls[0]["calibration"].cmd_b, -0.07)
        self.assertEqual(calls[1]["calibration"].scanner, "twin-scout-bravo")
        self.assertEqual(calls[1]["calibration"].cmd_a, 1.25)
        self.assertEqual(calls[1]["calibration"].cmd_b, -0.08)

    def test_distance_sequence_and_disabled_diagnostic_are_retained(self):
        self.assertEqual(
            self.module.RAW_SEQUENCE_M,
            (1.00, 0.00, 2.00, 0.20, 1.50, 0.50, 1.80, 0.10, 1.20, 0.30),
        )
        self.assertTrue(
            hasattr(self.module, "_run_gz_bias_stage_seven_movement_disabled")
        )


if __name__ == "__main__":
    unittest.main()
