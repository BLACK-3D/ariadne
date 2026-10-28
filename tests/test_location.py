"""Location reads remain explicit, bounded, private, and honestly approximate."""
import json
import os
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from ariadne import location


NOW = datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)


def provider(**changes):
    return {"ok": True, "latitude": 38.6, "longitude": -90.2, "accuracy_meters": 204,
            "measured_at_utc": (NOW-timedelta(seconds=3)).isoformat(), **changes}


class LocationTests(unittest.TestCase):
    def windows(self, value=None, *, raw=None, returncode=0, timeout=15):
        payload = raw if raw is not None else json.dumps(value if value is not None else provider()).encode()
        completed = SimpleNamespace(returncode=returncode, stdout=payload)
        with patch("ariadne.location.os.name", "nt"), \
             patch("ariadne.location.subprocess.run", return_value=completed) as execute:
            result = location.detect_windows_location(timeout, now=NOW)
        return result, execute

    def test_windows_position_keeps_provider_accuracy_and_measurement_age(self):
        result, execute = self.windows()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "windows_location_service")
        self.assertEqual(result["accuracy_meters"], 204)
        self.assertEqual(result["age_seconds"], 3)
        self.assertTrue(result["device_location"])
        self.assertFalse(result["exact"])
        self.assertFalse(result["stale"])
        self.assertIn("estimate", result["note"])
        args, kwargs = execute.call_args
        self.assertEqual(kwargs["timeout"], 15)
        self.assertEqual(json.loads(kwargs["input"])["timeout_seconds"], 13)
        self.assertIn("-NoProfile", args[0])
        self.assertIn("-NonInteractive", args[0])
        self.assertEqual(args[0][-1], location._WINDOWS_SCRIPT)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def test_windows_denial_is_unavailable_without_silent_city_substitution(self):
        result, _ = self.windows({"ok": False, "status": "permission_denied", "permission": "Denied"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "permission_denied")
        self.assertNotIn("latitude", result)
        self.assertNotIn("longitude", result)

    def test_windows_missing_accuracy_is_explicit_and_old_measurements_are_flagged(self):
        result, _ = self.windows(provider(accuracy_meters=None, measured_at_utc=(NOW-timedelta(minutes=6)).isoformat()))
        self.assertTrue(result["ok"])
        self.assertTrue(result["stale"])
        self.assertIsNone(result["accuracy_meters"])
        self.assertIn("did not report", result["note"])

    def test_invalid_native_results_fail_closed(self):
        invalid = [provider(latitude=91), provider(longitude=-181), provider(latitude=True),
                   provider(latitude=float("nan")), provider(accuracy_meters=-1),
                   provider(measured_at_utc="not a time"), provider(measured_at_utc="2026-10-28T23:00:00"),
                   provider(measured_at_utc=(NOW+timedelta(hours=1)).isoformat()),
                   {"ok": "true"}, [], {"ok": False, "permission": []}]
        for value in invalid:
            with self.subTest(value=value):
                result, _ = self.windows(value)
                self.assertFalse(result["ok"])
                self.assertNotIn("latitude", result)
        for raw in (b"not json", b"x" * 8193, b"\xff"):
            with self.subTest(raw_length=len(raw)):
                self.assertFalse(self.windows(raw=raw)[0]["ok"])
        self.assertFalse(self.windows(returncode=1)[0]["ok"])

    def test_timeout_does_not_retry_or_change_location_settings(self):
        with patch("ariadne.location.os.name", "nt"), \
             patch("ariadne.location.subprocess.run", side_effect=subprocess.TimeoutExpired("fixed-provider", 15)) as execute:
            result = location.detect_windows_location(now=NOW)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["ok"])
        self.assertEqual(execute.call_count, 1)
        self.assertNotIn("Set-ItemProperty", location._WINDOWS_SCRIPT)
        self.assertNotIn("reg add", location._WINDOWS_SCRIPT.casefold())
        self.assertIn("$watcher.Stop()", location._WINDOWS_SCRIPT)
        self.assertIn("$watcher.Dispose()", location._WINDOWS_SCRIPT)

    def test_busy_or_unsupported_platform_never_starts_a_second_provider(self):
        with patch("ariadne.location.os.name", "posix"), patch("ariadne.location.subprocess.run") as execute:
            self.assertEqual(location.detect_windows_location(now=NOW)["status"], "unsupported")
        execute.assert_not_called()
        self.assertTrue(location._DETECT_LOCK.acquire(blocking=False))
        try:
            with patch("ariadne.location.os.name", "nt"), patch("ariadne.location.subprocess.run") as execute:
                self.assertEqual(location.detect_windows_location(now=NOW)["status"], "busy")
            execute.assert_not_called()
        finally:
            location._DETECT_LOCK.release()

    def test_timeout_values_cannot_expand_deadline_or_enter_command_text(self):
        with patch("ariadne.location.subprocess.run") as execute:
            for timeout in (0, 16, -1, True, 1.5, "15; arbitrary command", None):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    location.detect_windows_location(timeout, now=NOW)
        execute.assert_not_called()

    def test_browser_result_is_validated_without_running_processes_or_networks(self):
        value = {"latitude": 38.7, "longitude": -90.3, "accuracy_meters": 30,
                 "timestamp_ms": (NOW-timedelta(seconds=5)).timestamp()*1000}
        with patch("ariadne.location.subprocess.run") as execute:
            result = location.validate_browser_location(value, now=NOW)
        execute.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "browser_device_geolocation")
        self.assertEqual(result["age_seconds"], 5)
        self.assertFalse(result["exact"])
        self.assertEqual(result["accuracy_meters"], 30)

    def test_browser_rejects_invalid_coordinates_accuracy_and_claimed_source_labels(self):
        base = {"latitude": 38.7, "longitude": -90.3, "accuracy_meters": 30, "timestamp_ms": NOW.timestamp()*1000}
        invalid = [{**base, "latitude": 91}, {**base, "longitude": float("inf")}, {**base, "accuracy_meters": None},
                   {**base, "accuracy_meters": -1}, {**base, "accuracy_meters": True},
                   {**base, "timestamp_ms": (NOW+timedelta(hours=1)).timestamp()*1000},
                   {**base, "source": "exact_gps"}, {**base, "exact": True}, {}, []]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                location.validate_browser_location(value, now=NOW)

    def test_city_reference_is_an_official_reference_point_not_a_device_position(self):
        with patch("ariadne.location.subprocess.run") as execute:
            result = location.st_louis_reference()
        execute.assert_not_called()
        self.assertFalse(result["device_location"])
        self.assertFalse(result["exact"])
        self.assertEqual(result["status"], "city_reference")
        self.assertIsNone(result["accuracy_meters"])
        self.assertEqual(result["geoid"], "2965000")
        self.assertEqual(result["source_as_of"], "2025-01-01")
        self.assertTrue(result["source_url"].startswith("https://tigerweb.geo.census.gov/"))
        self.assertNotIn("measured_at_utc", result)


if __name__ == "__main__":
    unittest.main()
