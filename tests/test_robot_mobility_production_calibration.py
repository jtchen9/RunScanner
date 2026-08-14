import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_MODULE_PATH = PROJECT_ROOT / "robot_mobility_calibration_registry.py"
MOTION_MODULE_PATH = PROJECT_ROOT / "robot_mobility_motion.py"
REGISTRY_JSON_PATH = PROJECT_ROOT / "robot_mobility_calibration.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_motion_with_hardware_stubs(registry_module):
    sys.modules["robot_mobility_calibration_registry"] = registry_module

    testgyro = types.ModuleType("TestGyro")
    testgyro.__path__ = []
    motor_module = types.ModuleType("TestGyro.DFRobot_RaspberryPi_DC_Motor")
    motor_module.DFRobot_DC_Motor_IIC = object
    sys.modules["TestGyro"] = testgyro
    sys.modules["TestGyro.DFRobot_RaspberryPi_DC_Motor"] = motor_module

    imu_module = types.ModuleType("icm20948")
    imu_module.ICM20948 = object
    sys.modules["icm20948"] = imu_module

    tof_module = types.ModuleType("robot_mobility_vl53l1x")
    tof_module.check_blocked = lambda *_args, **_kwargs: (False, "test")
    sys.modules["robot_mobility_vl53l1x"] = tof_module
    return _load("motion_under_test", MOTION_MODULE_PATH)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = _load("registry_under_test", REGISTRY_MODULE_PATH)

    def _identity_file(self, directory, scanner):
        path = Path(directory) / "scanner_name.txt"
        path.write_text(scanner + "\n", encoding="utf-8")
        return path

    def test_development_registry_contains_accepted_charlie_result(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = self.registry.load_mobility_calibration(
                scanner_name_path=self._identity_file(directory, "twin-scout-charlie"),
                registry_path=REGISTRY_JSON_PATH,
            )
        self.assertEqual(snapshot.source, "registry")
        self.assertAlmostEqual(snapshot.gz_bias, 0.3784392055364879)
        self.assertAlmostEqual(snapshot.cmd_a, 1.2908976510067114)
        self.assertAlmostEqual(snapshot.cmd_b, -0.06750996224832195)
        self.assertAlmostEqual(snapshot.kick_distance_m, 0.045)
        self.assertAlmostEqual(snapshot.skip_threshold_m, 0.0225)

    def test_delta_entry_is_preserved(self):
        document = json.loads(REGISTRY_JSON_PATH.read_text(encoding="utf-8"))
        delta = document["robots"]["twin-scout-delta"]
        self.assertAlmostEqual(delta["gz_bias"], 0.3067374709591769)
        self.assertTrue(delta["production_loader_enabled"])

    def test_unknown_robot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.registry.MobilityCalibrationError) as caught:
                self.registry.load_mobility_calibration(
                    scanner_name_path=self._identity_file(directory, "twin-scout-unknown"),
                    registry_path=REGISTRY_JSON_PATH,
                )
        self.assertIn("no calibration for twin-scout-unknown", str(caught.exception))

    def test_missing_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.registry.MobilityCalibrationError):
                self.registry.load_mobility_calibration(
                    scanner_name_path=self._identity_file(directory, "twin-scout-charlie"),
                    registry_path=Path(directory) / "missing.json",
                )


class MotionFailClosedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = _load("robot_mobility_calibration_registry", REGISTRY_MODULE_PATH)
        cls.motion = _load_motion_with_hardware_stubs(cls.registry)

    def setUp(self):
        self.original_snapshot = self.motion._production_calibration_snapshot
        self.original_motor_begin = self.motion._motor_begin
        self.motor_called = False

        def unavailable():
            raise self.registry.MobilityCalibrationError("test registry failure")

        def motor_begin():
            self.motor_called = True
            raise AssertionError("motor initialization must not be reached")

        self.motion._production_calibration_snapshot = unavailable
        self.motion._motor_begin = motor_begin

    def tearDown(self):
        self.motion._production_calibration_snapshot = self.original_snapshot
        self.motion._motor_begin = self.original_motor_begin

    def test_forward_move_stops_before_motor_initialization(self):
        ok, detail = self.motion._run_move(True, 1.0)
        self.assertFalse(ok)
        self.assertIn("MOBILITY_CALIBRATION_UNAVAILABLE", detail)
        self.assertFalse(self.motor_called)

    def test_turn_stops_before_motor_initialization(self):
        ok, detail = self.motion._run_turn(False, 90.0)
        self.assertFalse(ok)
        self.assertIn("MOBILITY_CALIBRATION_UNAVAILABLE", detail)
        self.assertFalse(self.motor_called)

    def test_composite_turn_stops_before_motor_initialization(self):
        ok, detail = self.motion._run_composite_signed_turn(8.1)
        self.assertFalse(ok)
        self.assertIn("MOBILITY_CALIBRATION_UNAVAILABLE", detail)
        self.assertFalse(self.motor_called)


if __name__ == "__main__":
    unittest.main()
