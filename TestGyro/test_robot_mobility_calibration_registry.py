#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from robot_mobility_calibration_registry import load_mobility_calibration


FALLBACK_MODEL = {"cmd_a": 1.1, "cmd_b": -0.07}


def _entry(*, enabled=True, status="pass", gz_bias=0.31, cmd_a=1.05, cmd_b=-0.06):
    return {
        "production_loader_enabled": enabled,
        "quality": {"status": status},
        "gz_bias": gz_bias,
        "distance_model": {"cmd_a": cmd_a, "cmd_b": cmd_b},
        "calibrated_at_utc": "2026-08-12T01:26:27+00:00",
    }


class CalibrationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.name_path = root / "scanner_name.txt"
        self.registry_path = root / "robot_mobility_calibration.json"
        self.name_path.write_text("twin-scout-delta\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, robots):
        self.registry_path.write_text(
            json.dumps({"schema_version": 1, "robots": robots}),
            encoding="utf-8",
        )

    def _load(self):
        return load_mobility_calibration(
            fallback_gz_bias=0.3,
            fallback_distance_model=FALLBACK_MODEL,
            scanner_name_path=self.name_path,
            registry_path=self.registry_path,
        )

    def test_loads_enabled_pass_entry_for_exact_robot(self):
        self._write({"twin-scout-delta": _entry()})
        result = self._load()
        self.assertEqual(result.source, "registry")
        self.assertAlmostEqual(result.gz_bias, 0.31)
        self.assertAlmostEqual(result.motor_distance(1.0), 0.99)

    def test_does_not_borrow_another_robots_entry(self):
        self._write({"twin-scout-charlie": _entry(gz_bias=0.99)})
        result = self._load()
        self.assertEqual(result.source, "fallback")
        self.assertAlmostEqual(result.gz_bias, 0.3)
        self.assertIn("no calibration for twin-scout-delta", result.warning)

    def test_disabled_entry_falls_back(self):
        self._write({"twin-scout-delta": _entry(enabled=False)})
        result = self._load()
        self.assertEqual(result.source, "fallback")
        self.assertIn("not enabled", result.warning)

    def test_explicitly_enabled_review_entry_is_honored(self):
        self._write({"twin-scout-delta": _entry(status="review")})
        result = self._load()
        self.assertEqual(result.source, "registry")
        self.assertAlmostEqual(result.gz_bias, 0.31)

    def test_malformed_registry_falls_back(self):
        self.registry_path.write_text("not json", encoding="utf-8")
        result = self._load()
        self.assertEqual(result.source, "fallback")
        self.assertAlmostEqual(result.motor_distance(1.0), 1.03)
        self.assertTrue(result.warning)

    def test_next_snapshot_reads_updated_registry(self):
        self._write({"twin-scout-delta": _entry(gz_bias=0.31)})
        first = self._load()
        self._write({"twin-scout-delta": _entry(gz_bias=0.42)})
        second = self._load()
        self.assertAlmostEqual(first.gz_bias, 0.31)
        self.assertAlmostEqual(second.gz_bias, 0.42)


if __name__ == "__main__":
    unittest.main()
