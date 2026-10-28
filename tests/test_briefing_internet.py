"""The Internet master switch also gates separately scheduled public brief data."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ariadne.briefing import (BriefingService, BriefingCollectionStopped,
                              collect_external, _region_digest)
from ariadne.config import Settings
from ariadne.internet import SETTINGS_KEY
from ariadne.store import Store
from ariadne.tools import ToolRegistry


REGION = {"name": "St. Louis, MO", "latitude": 38.627, "longitude": -90.1994, "country": "US"}


class Collector:
    def __init__(self, transition=None, returncode=None):
        self.returncode = returncode
        self.transition = transition
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if self.transition and not self.waited:
            self.waited = True
            self.transition()
        if self.returncode is not None:
            return self.returncode
        raise subprocess.TimeoutExpired("fixture collector", timeout)

    def kill(self):
        self.killed = True
        self.returncode = -9


class BriefingInternetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".briefing-internet-test-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.service = BriefingService(self.settings, self.store, self.registry)
        self.now = datetime(2026, 10, 28, 11, tzinfo=timezone.utc)
        self.service.configure({"enabled": True, "region": REGION, "online_sources": True}, self.now)

    def master(self, enabled, revision=1):
        self.store.set_setting(SETTINGS_KEY, {"enabled": enabled, "revision": revision})

    def saved_cache(self):
        cfg = self.service.config()
        value = {"schema": 1, "collected_at": self.now.isoformat(), "sources": [{
            "kind": "alerts", "title": "Saved fixture alert", "url": "https://api.weather.gov/alerts/active?point=38,-90",
            "fetched_at": self.now.isoformat(), "issued_at": self.now.isoformat(), "status": "available", "items": []}], "warnings": []}
        self.service.save_cache(value, _region_digest(cfg))
        return self.service.cache(now=self.now)

    def test_explicit_master_off_blocks_parent_without_changing_briefing_preferences(self):
        self.master(False)
        config = self.service.config()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No Internet collector while disabled")):
            result = self.service.collect()
        self.assertFalse(result["collected"])
        self.assertIn("Internet access is disabled", result["reason"])
        self.assertEqual(self.service.config(), config)
        self.assertTrue(config["online_sources"])
        self.assertFalse(self.service.status(self.now)["collection_enabled"])

    def test_malformed_or_truthy_master_flag_fails_closed(self):
        for value in ({"enabled": 1, "revision": 1}, {"enabled": "true", "revision": 1}, True, []):
            with self.subTest(value=value):
                self.store.set_setting(SETTINGS_KEY, value)
                with mock.patch("subprocess.Popen", side_effect=AssertionError("No Internet collector for malformed policy")):
                    self.assertFalse(self.service.collect()["collected"])

    def test_missing_legacy_master_uses_existing_opt_in_and_revision_binding(self):
        child = Collector(returncode=1)
        environment = dict(os.environ)
        with mock.patch("subprocess.Popen", return_value=child) as runner:
            result = self.service.collect()
        self.assertFalse(result["collected"])
        args, kwargs = runner.call_args
        self.assertIn("collect-worker", args[0])
        self.assertEqual(json.loads(kwargs["env"]["ARIADNE_BRIEFING_INTERNET_REVISION"]), None)
        self.assertEqual(int(kwargs["env"]["ARIADNE_BRIEFING_CONFIG_REVISION"]), self.service.config()["revision"])
        self.assertEqual(kwargs["env"]["ARIADNE_OFFLINE"], "0")
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(dict(os.environ), environment)

    def test_master_disable_kills_inflight_parent_collector_and_keeps_cache(self):
        before = self.saved_cache()
        self.master(True)
        child = Collector(lambda: self.master(False, 2))
        with mock.patch("subprocess.Popen", return_value=child):
            result = self.service.collect()
        self.assertFalse(result["collected"])
        self.assertTrue(child.killed)
        self.assertEqual(self.service.collectors, set())
        self.assertEqual(self.service.cache(now=self.now), before)

    def test_disable_then_enable_revision_cannot_resume_inflight_collector(self):
        self.master(True)
        def transition():
            self.master(False, 2)
            self.master(True, 3)
        child = Collector(transition)
        with mock.patch("subprocess.Popen", return_value=child):
            result = self.service.collect()
        self.assertFalse(result["collected"])
        self.assertIn("settings changed", result["reason"])
        self.assertTrue(child.killed)

    def test_autonomy_pause_or_brief_collection_change_kills_inflight_child(self):
        for transition in (lambda: self.store.set_setting("paused", True),
                           lambda: self.service.configure({"online_sources": False}, self.now)):
            with self.subTest(transition=transition):
                self.store.set_setting("paused", False)
                self.service.configure({"online_sources": True}, self.now)
                self.master(True)
                child = Collector(transition)
                with mock.patch("subprocess.Popen", return_value=child):
                    self.assertFalse(self.service.collect()["collected"])
                self.assertTrue(child.killed)

    def test_service_stop_kills_owned_collector_and_blocks_next_launch(self):
        self.master(True)
        child = Collector(self.service.stop)
        with mock.patch("subprocess.Popen", return_value=child):
            result = self.service.collect()
        self.assertFalse(result["collected"])
        self.assertTrue(child.killed)
        self.assertIn("stopped", result["reason"])
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Stopped service cannot collect")):
            self.assertFalse(self.service.collect()["collected"])

    def test_worker_gate_blocks_before_first_request(self):
        self.master(False)
        cfg = self.service.config()
        fetch = mock.Mock(side_effect=AssertionError("No request before policy approval"))
        with self.assertRaises(BriefingCollectionStopped):
            collect_external(cfg, fetch, self.now, check=lambda: self.service.check_collection(cfg, 1))
        fetch.assert_not_called()

    def test_worker_checks_between_chained_points_and_forecast_fetches(self):
        self.master(True)
        cfg = self.service.config()
        def points(url):
            self.master(False, 2)
            return b'{"properties":{"forecast":"https://api.weather.gov/gridpoints/LSX/97,69/forecast"}}'
        fetch = mock.Mock(side_effect=points)
        with self.assertRaises(BriefingCollectionStopped):
            collect_external(cfg, fetch, self.now, check=lambda: self.service.check_collection(cfg, 1))
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("/points/", fetch.call_args.args[0])

    def test_fetch_failure_does_not_swallow_later_policy_cancellation(self):
        self.master(True)
        cfg = self.service.config()
        def fail(url):
            self.master(False, 2)
            raise OSError("Fixture network failure")
        fetch = mock.Mock(side_effect=fail)
        with self.assertRaises(BriefingCollectionStopped):
            collect_external(cfg, fetch, self.now, check=lambda: self.service.check_collection(cfg, 1))
        self.assertEqual(fetch.call_count, 1)

    def test_collector_cache_publication_rechecks_master_when_acquiring_lease(self):
        before = self.saved_cache()
        self.master(True)
        cfg = self.service.config()
        original = self.service.check_collection
        def revoke_before_lease(config, revision):
            original(config, revision)
            self.master(False, 2)
        value = {"schema": 1, "collected_at": self.now.isoformat(), "sources": [], "warnings": []}
        with mock.patch.object(self.service, "check_collection", side_effect=revoke_before_lease):
            with self.assertRaises(BriefingCollectionStopped):
                self.service.save_cache(value, _region_digest(cfg), collection_policy=(cfg, 1))
        self.assertEqual(self.service.cache(now=self.now), before)

    def test_changed_briefing_revision_rejects_worker_cache(self):
        before = self.saved_cache()
        self.master(True)
        cfg = self.service.config()
        self.service.configure({"hour": 7}, self.now)
        with self.assertRaises(BriefingCollectionStopped):
            self.service.save_cache({"collected_at": self.now.isoformat(), "sources": []}, _region_digest(cfg), collection_policy=(cfg, 1))
        self.assertEqual(self.service.cache(now=self.now), before)

    def test_offline_report_generation_remains_available_with_source_ages_and_warning(self):
        self.saved_cache()
        self.master(False)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Offline report must not launch a collector")):
            receipt = self.service.run("offline-master", self.now, collect=True)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(receipt["source_count"], 1)
        self.assertTrue(any("Internet access is disabled" in row for row in receipt["warnings"]))
        path = next(a["path"] for a in receipt["artifacts"] if a["path"].endswith(".json"))
        report = json.loads(self.registry._path(path).read_bytes())
        self.assertEqual(report["public_sources"]["sources"][0]["fetched_at"], self.now.isoformat())
        self.assertTrue(self.service.config()["online_sources"])

    def test_actual_collect_worker_process_checks_shared_master_before_network(self):
        before = self.saved_cache()
        self.master(False)
        script = Path.cwd()/"scripts"/"briefing.py"
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA") if key in os.environ}
        env.update(ARIADNE_BRIEFING_COLLECTOR="1", ARIADNE_OFFLINE="0",
                   ARIADNE_BRIEFING_INTERNET_REVISION="1", ARIADNE_BRIEFING_CONFIG_REVISION=str(self.service.config()["revision"]))
        child = subprocess.run([sys.executable, "-I", "-B", str(script), "collect-worker", "--data-dir", str(self.settings.data_dir), "--workspace", str(self.settings.workspace)],
                               env=env, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"Collection is disabled or paused", child.stderr)
        self.assertEqual(self.service.cache(now=self.now), before)


if __name__ == "__main__":
    unittest.main()
