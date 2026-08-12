#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from robot_mobility_calibration_registry import (
    MobilityCalibrationSnapshot,
    load_mobility_calibration,
)


ROOT = Path(__file__).resolve().parent.parent


def _load_motion_module():
    motor_module_name = "TestGyro.DFRobot_RaspberryPi_DC_Motor"
    motor_module = types.ModuleType(motor_module_name)
    motor_module.DFRobot_DC_Motor_IIC = object
    sys.modules[motor_module_name] = motor_module

    imu_module = types.ModuleType("icm20948")
    imu_module.ICM20948 = object
    sys.modules["icm20948"] = imu_module

    tof_module = types.ModuleType("robot_mobility_vl53l1x")
    tof_module.check_blocked = lambda: False
    sys.modules["robot_mobility_vl53l1x"] = tof_module

    spec = importlib.util.spec_from_file_location(
        "robot_mobility_motion_short_move_test",
        ROOT / "robot_mobility_motion.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_wizard_module(motion_module):
    sys.modules["robot_mobility_motion"] = motion_module
    spec = importlib.util.spec_from_file_location(
        "robot_mobility_calibrate_short_move_test",
        ROOT / "TestGyro" / "robot_mobility_calibrate.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShortMoveRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.name_path = root / "scanner_name.txt"
        self.registry_path = root / "robot_mobility_calibration.json"
        self.name_path.write_text("twin-scout-charlie\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, entry):
        self.registry_path.write_text(
            json.dumps({
                "schema_version": 1,
                "robots": {"twin-scout-charlie": entry},
            }),
            encoding="utf-8",
        )

    def _entry(self):
        return {
            "production_loader_enabled": True,
            "gz_bias": -0.375,
            "distance_model": {"cmd_a": 1.205, "cmd_b": -0.0866},
            "short_move": {
                "kick_distance_m": 0.06,
                "skip_threshold_m": 0.03,
            },
        }

    def _load(self):
        return load_mobility_calibration(
            fallback_gz_bias=0.3,
            fallback_distance_model={"cmd_a": 1.1, "cmd_b": -0.07},
            scanner_name_path=self.name_path,
            registry_path=self.registry_path,
        )

    def test_loads_robot_specific_short_move_values(self):
        self._write(self._entry())
        result = self._load()
        self.assertEqual(result.source, "registry")
        self.assertAlmostEqual(result.kick_distance_m, 0.06)
        self.assertAlmostEqual(result.skip_threshold_m, 0.03)

    def test_legacy_entry_keeps_old_behavior(self):
        entry = self._entry()
        entry.pop("short_move")
        self._write(entry)
        result = self._load()
        self.assertEqual(result.source, "registry")
        self.assertEqual(result.kick_distance_m, 0.0)
        self.assertEqual(result.skip_threshold_m, 0.0)

    def test_invalid_short_move_uses_complete_fallback(self):
        entry = self._entry()
        entry["short_move"]["skip_threshold_m"] = 0.07
        self._write(entry)
        result = self._load()
        self.assertEqual(result.source, "fallback")
        self.assertIn("must not exceed", result.warning)


class ShortMoveExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.motion = _load_motion_module()

    def _calibration(self):
        return MobilityCalibrationSnapshot(
            scanner="twin-scout-charlie",
            gz_bias=-0.375,
            cmd_a=1.205,
            cmd_b=-0.0866,
            source="registry",
            kick_distance_m=0.06,
            skip_threshold_m=0.03,
        )

    def _install_fake_hardware(self):
        class Motor:
            M1 = 1
            M2 = 2
            CW = 3
            CCW = 4
            ALL = 5

            def motor_stop(self, *_args):
                pass

            def motor_movement(self, *_args):
                pass

        class Imu:
            def read_accelerometer_gyro_data(self):
                return 0.0, 0.0, 0.0, 0.0, 0.0, -0.375

        self.motion._motor_begin = lambda: (True, Motor(), "ok")
        self.motion._imu_begin = lambda: (True, Imu(), "ok")
        self.motion.time.sleep = lambda _seconds: None

    def test_below_threshold_skips_without_starting_motor(self):
        self.motion._motor_begin = lambda: self.fail("motor must not start")
        ok, detail = self.motion._run_move(
            True,
            0.02,
            calibration=self._calibration(),
        )
        self.assertTrue(ok)
        self.assertIn("short_move_skipped", detail)

    def test_equal_threshold_is_not_skipped(self):
        self.motion._motor_begin = lambda: (False, None, "motor_probe_reached")
        ok, detail = self.motion._run_move(
            True,
            0.03,
            calibration=self._calibration(),
        )
        self.assertFalse(ok)
        self.assertEqual(detail, "motor_probe_reached")

    def test_above_threshold_with_zero_cruise_reports_kick_only(self):
        self._install_fake_hardware()
        ok, detail = self.motion._run_move(
            True,
            0.04,
            calibration=self._calibration(),
        )
        self.assertTrue(ok)
        self.assertIn("move_execution=kick_only", detail)

    def test_normal_move_reports_kick_plus_cruise(self):
        self._install_fake_hardware()
        ok, detail = self.motion._run_move(
            True,
            0.50,
            calibration=self._calibration(),
        )
        self.assertTrue(ok)
        self.assertIn("move_execution=kick_plus_cruise", detail)

    def test_calibration_override_is_never_skipped(self):
        self.motion._motor_begin = lambda: (False, None, "motor_probe_reached")
        ok, detail = self.motion._run_move(
            True,
            0.01,
            calibration=self._calibration(),
            calibration_gz_bias=-0.375,
            motor_distance_override=0.0,
        )
        self.assertFalse(ok)
        self.assertEqual(detail, "motor_probe_reached")

    def test_backward_behavior_is_not_changed(self):
        self.motion._motor_begin = lambda: (False, None, "motor_probe_reached")
        ok, detail = self.motion._run_move(
            False,
            0.02,
            calibration=self._calibration(),
        )
        self.assertFalse(ok)
        self.assertEqual(detail, "motor_probe_reached")


class CalibrationWizardRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.motion = _load_motion_module()
        cls.wizard = _load_wizard_module(cls.motion)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.registry_path = root / "robot_mobility_calibration.json"
        self.csv_path = root / "current_calibration.csv"
        self.wizard.REGISTRY_PATH = self.registry_path
        self.wizard.ACTIVE_CSV_PATH = self.csv_path

    def tearDown(self):
        self.temp.cleanup()

    def test_update_preserves_existing_buck_voltage_and_writes_short_move(self):
        self.registry_path.write_text(
            json.dumps({
                "schema_version": 1,
                "robots": {
                    "twin-scout-charlie": {"buck_voltage_v": 8.2},
                },
            }),
            encoding="utf-8",
        )
        fit = {"actual_a": 0.83, "actual_b": 0.07, "cmd_a": 1.20, "cmd_b": -0.08}
        self.wizard._write_registry(
            "twin-scout-charlie",
            -0.375,
            fit,
            {"status": "pass"},
            0.06,
        )
        entry = json.loads(self.registry_path.read_text())["robots"]["twin-scout-charlie"]
        self.assertEqual(entry["buck_voltage_v"], 8.2)
        self.assertEqual(entry["short_move"]["kick_distance_m"], 0.06)
        self.assertEqual(entry["short_move"]["skip_threshold_m"], 0.03)

if __name__ == "__main__":
    unittest.main()
