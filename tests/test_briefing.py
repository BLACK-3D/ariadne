"""Daily clock, provenance, true recorded activity and crash-safe briefing receipts."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ariadne.briefing import (BriefingService, BriefingConflict, collect_external,
                              _DEFAULT, _feed_items, _public_https, _region_digest, _tz)
from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry

UTC = timezone.utc
REGION = {"name": "Chicago, IL", "latitude": 41.8781, "longitude": -87.6298, "country": "US"}


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="briefing-test-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.service = BriefingService(self.settings, self.store, self.registry)
        self.now = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)

    def read_report(self, receipt):
        row = next(row for row in receipt["artifacts"] if row["path"].endswith(".json"))
        return json.loads(self.registry._path(row["path"]).read_bytes())

    def cache_value(self, timestamp=None):
        return {"schema": 1, "collected_at": (timestamp or self.now).isoformat(), "sources": [{
            "kind": "alerts", "title": "NWS fixture", "url": "https://api.weather.gov/alerts/active?point=41,-87",
            "fetched_at": (timestamp or self.now).isoformat(), "issued_at": "2026-10-27T00:00:00+00:00",
            "status": "available", "items": [{"title": "Saved advisory", "expires": "2026-10-28T10:00:00+00:00"}]}], "warnings": []}

    def enable(self, now=None, **values):
        return self.service.configure({"enabled": True, "region": REGION, **values}, now or self.now-timedelta(days=1))

    def test_bundled_chicago_clock_handles_dst_23_and_25_hour_days(self):
        zone = _tz("America/Chicago")
        self.assertEqual(datetime(2026, 7, 1, 6, tzinfo=zone).astimezone(UTC).hour, 11)
        self.assertEqual(datetime(2026, 12, 1, 6, tzinfo=zone).astimezone(UTC).hour, 12)
        cfg = self.service.config()
        self.assertEqual((self.service._slot(cfg, datetime(2026, 3, 8).date())-self.service._slot(cfg, datetime(2026, 3, 7).date())).total_seconds(), 23*3600)
        self.assertEqual((self.service._slot(cfg, datetime(2026, 11, 1).date())-self.service._slot(cfg, datetime(2026, 10, 31).date())).total_seconds(), 25*3600)

    def test_defaults_are_unconfigured_and_owner_config_is_preserved_on_reopen(self):
        status = self.service.status(self.now)
        self.assertFalse(status["config"]["enabled"])
        self.assertTrue(status["region_required"])
        self.enable(hour=7, online_sources=True)
        reopened = BriefingService(self.settings, self.store, self.registry)
        self.assertEqual(reopened.config()["hour"], 7)
        self.assertEqual(reopened.config()["region"], REGION)
        self.assertTrue(reopened.config()["online_sources"])

    def test_invalid_config_fails_without_replacing_saved_owner_config(self):
        self.enable()
        original = self.service.config()
        for values in ({"enabled": 1}, {"hour": True}, {"timezone": "invalid"},
                       {"region": {**REGION, "latitude": float("nan")}},
                       {"news_feeds": [{"title": "Bad", "url": "https://localhost/feed"}]},
                       {"news_feeds": [{"title": "Bad", "url": "https://example.gov:8443/feed"}]},
                       {"news_feeds": [{"title": "Bad", "url": "https://user:pass@example.gov/feed"}]},
                       {"revision": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.service.configure(values, self.now)
            self.assertEqual(self.service.config(), original)

    def test_schedule_claims_one_local_day_and_does_not_backfill_before_activation(self):
        self.enable(now=self.now+timedelta(minutes=1))
        self.assertIsNone(self.service.run_due(self.now+timedelta(minutes=2)))
        tomorrow = self.now+timedelta(days=1)
        self.assertIsNone(self.service.run_due(tomorrow-timedelta(seconds=1)))
        with mock.patch.object(self.service, "collect", return_value={"collected": False, "reason": "Offline fixture"}):
            report = self.service.run_due(tomorrow)
        self.assertTrue(report["ok"])
        self.assertEqual(report["request_id"], "daily-2026-10-29")
        self.assertIsNone(self.service.run_due(tomorrow+timedelta(hours=3)))
        self.assertEqual(len(self.service.history()["runs"]), 1)

    def test_scheduled_report_uses_local_six_to_six_window_and_zero_model_calls(self):
        self.enable()
        with mock.patch("subprocess.run", side_effect=AssertionError("Offline collection must launch no worker")), mock.patch("urllib.request.urlopen", side_effect=AssertionError("No network in reports")):
            receipt = self.service.run_due(self.now)
        report = self.read_report(receipt)
        self.assertEqual(report["activity"]["window_start_utc"], "2026-10-27T11:00:00+00:00")
        self.assertEqual(report["activity"]["window_end_utc"], "2026-10-28T11:00:00+00:00")
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual({row["path"].rsplit("/", 1)[-1] for row in receipt["artifacts"]}, {"brief.json", "brief.md", "brief.svg"})
        for row in receipt["artifacts"]:
            data = self.registry._path(row["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])

    def test_activity_counts_cover_entire_window_and_detail_truncation_is_explicit(self):
        inside = self.now.timestamp()-3600
        run = self.store.new_run("Recorded fixture mission")
        with self.store.connect() as db:
            db.execute("UPDATE runs SET created_at=?,updated_at=? WHERE id=?", (inside, inside, run["id"]))
            db.execute("DELETE FROM events")
            db.executemany("INSERT INTO events(run_id,kind,data,created_at) VALUES(?,?,?,?)", [(run["id"], "tool_result", '{"tool":"file_read"}', inside)]*1003)
        receipt = self.service.run("activity", self.now)
        activity = self.read_report(receipt)["activity"]
        self.assertEqual(activity["runs_count"], 1)
        self.assertEqual(activity["events_count"], 1003)
        self.assertEqual(len(activity["events"]), 1000)
        self.assertTrue(activity["details_truncated"])
        self.assertEqual(activity["event_kinds"], [{"kind": "tool_result", "count": 1003}])

    def test_stale_sources_and_expired_alerts_are_labeled_and_region_changes_invalidate_cache(self):
        self.enable()
        cfg = self.service.config()
        self.service.save_cache(self.cache_value(self.now-timedelta(days=2)), _region_digest(cfg))
        receipt = self.service.run("stale", self.now)
        report = self.read_report(receipt)
        source = report["public_sources"]["sources"][0]
        self.assertTrue(source["stale"])
        self.assertTrue(source["items"][0]["expired"])
        self.assertEqual(source["issued_at"], "2026-10-27T00:00:00+00:00")
        text = self.registry._path(receipt["artifacts"][1]["path"]).read_text()
        self.assertIn("STALE", text)
        self.assertIn("EXPIRED", text)
        self.service.configure({"region": {**REGION, "name": "Owner new location", "latitude": 42.1}}, self.now)
        self.assertEqual(self.service.cache(now=self.now)["sources"], [])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.save_cache(self.cache_value(), _region_digest(cfg))

    def test_completed_receipt_replays_verified_bytes_and_never_overwrites_owner_edits(self):
        first = self.service.run("replay", self.now)
        self.store.memory_add("Later owner change")
        replay = BriefingService(self.settings, self.store, self.registry).run("replay", self.now+timedelta(hours=1))
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["artifacts"], replay["artifacts"])
        target = self.registry._path(first["artifacts"][0]["path"])
        target.write_bytes(b"owner edited report")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.run("replay", self.now)
        self.assertEqual(target.read_bytes(), b"owner edited report")

    def test_partial_publish_failure_is_fail_stop_and_pauses_only_scheduled_brief(self):
        self.enable()
        from ariadne.procedures import Procedures
        original = Procedures._publish
        count = 0
        def fail_second(instance, destination, payload, directory_fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Injected disk full")
            return original(instance, destination, payload, directory_fd)
        with mock.patch.object(Procedures, "_publish", fail_second), self.assertRaisesRegex(ValueError, "disk full"):
            self.service.run_due(self.now)
        self.assertTrue(self.service.config()["paused"])
        history = self.service.history()["runs"]
        self.assertEqual(history[0]["status"], "failed")
        self.assertEqual(len(history[0]["artifacts"]), 1)
        self.assertIsNone(self.service.run_due(self.now+timedelta(days=1)))
        with self.assertRaises(BriefingConflict):
            self.service.run("daily-2026-10-28", self.now)

    def test_recover_interrupted_daily_report_pauses_without_replaying_it(self):
        self.enable()
        receipt = self.service.run_due(self.now)
        with self.service.database() as db:
            db.execute("UPDATE briefing_runs SET status='running' WHERE request_id=?", (receipt["request_id"],))
        reopened = BriefingService(self.settings, self.store, self.registry)
        reopened.recover()
        self.assertTrue(reopened.config()["paused"])
        self.assertEqual(reopened.history()["runs"][0]["status"], "interrupted")
        self.assertIsNone(reopened.run_due(self.now+timedelta(days=1)))

    def test_two_instances_same_request_publish_one_immutable_report(self):
        gate = threading.Barrier(2)
        def run(index):
            service = BriefingService(self.settings, self.store, self.registry)
            gate.wait(timeout=5)
            return service.run("concurrent", self.now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(run, [0, 1]))
        self.assertEqual(sum(row["replayed"] for row in receipts), 1)
        self.assertEqual(receipts[0]["artifacts"], receipts[1]["artifacts"])

    def test_collector_is_opt_in_child_only_and_does_not_mutate_main_offline_environment(self):
        self.enable()
        with mock.patch("subprocess.run", side_effect=AssertionError("Disabled collector must not run")):
            self.assertFalse(self.service.collect()["collected"])
        self.service.configure({"online_sources": True}, self.now)
        child = mock.Mock(returncode=1)
        child.poll.return_value = 1
        with mock.patch("subprocess.Popen", return_value=child) as runner:
            self.assertFalse(self.service.collect()["collected"])
        self.assertIn("collect-worker", runner.call_args.args[0])
        self.assertEqual(runner.call_args.kwargs["env"]["ARIADNE_OFFLINE"], "0")
        self.assertNotIn("api_token", str(runner.call_args))
        self.assertNotIn("OPENAI_API_KEY", runner.call_args.kwargs["env"])
        self.service.configure({"paused": True}, self.now)
        with mock.patch("subprocess.run", side_effect=AssertionError("Paused collector must not run")):
            self.assertFalse(self.service.collect()["collected"])

    def test_collector_official_fixtures_preserve_dates_filter_distance_and_feed_terms(self):
        cfg = {**_DEFAULT, "region": REGION, "news_feeds": [{"title": "State feed", "url": "https://example.gov/feed", "region_terms": ["Chicago"]}]}
        now_ms = self.now.timestamp()*1000
        def fetch(url):
            if "/points/" in url:
                return json.dumps({"properties": {"forecast": "https://api.weather.gov/gridpoints/LOT/74,73/forecast", "observationStations": "https://api.weather.gov/gridpoints/LOT/74,73/stations"}}).encode()
            if url.endswith("/stations"):
                return b'{"features":[{"properties":{"stationIdentifier":"KORD"}}]}'
            if url.endswith("/observations/latest"):
                return b'{"properties":{"timestamp":"2026-10-28T10:53:00Z","textDescription":"Fixture observation","temperature":{"value":7,"unitCode":"wmoUnit:degC"}}}'
            if "/gridpoints/" in url:
                return json.dumps({"properties": {"updated": "2026-10-28T09:00:00Z", "periods": [{"name": "Today", "detailedForecast": "Fixture only", "startTime": "2026-10-28T06:00:00-05:00", "endTime": "2026-10-28T18:00:00-05:00"}]}}).encode()
            if "/alerts/" in url:
                return b'{"updated":"2026-10-28T09:00:00Z","features":[]}'
            if "earthquake.usgs.gov" in url:
                return json.dumps({"metadata": {"generated": now_ms}, "features": [{"geometry": {"coordinates": [-87.6, 41.9]}, "properties": {"mag": 3, "place": "Fixture near Chicago", "time": now_ms, "url": "https://earthquake.usgs.gov/earthquakes/eventpage/test"}}, {"geometry": {"coordinates": [0, 0]}, "properties": {"mag": 3, "place": "Distant fixture", "time": now_ms, "url": "https://earthquake.usgs.gov/earthquakes/eventpage/far"}}]}).encode()
            return b'<rss><channel><item><title>Chicago fixture impact</title><description>Fixture news</description><link>https://example.gov/article</link><pubDate>Wed, 28 Oct 2026 09:00:00 GMT</pubDate></item><item><title>Unrelated fixture</title></item></channel></rss>'
        cache = collect_external(cfg, fetch, self.now)
        self.assertEqual(len(cache["sources"]), 5)
        self.assertTrue(all(row["status"] == "available" for row in cache["sources"]))
        self.assertEqual(cache["sources"][0]["issued_at"], "2026-10-28T09:00:00Z")
        self.assertIn("44.6 °F (7 °C)", cache["sources"][1]["items"][0]["summary"])
        self.assertEqual(cache["sources"][2]["items"], [])
        self.assertEqual(len(cache["sources"][3]["items"]), 1)
        self.assertLess(cache["sources"][3]["items"][0]["distance_km"], 10)
        self.assertEqual(len(cache["sources"][4]["items"]), 1)

    def test_source_failures_do_not_imply_clear_weather_or_no_impacts(self):
        cache = collect_external({**_DEFAULT, "region": REGION}, mock.Mock(side_effect=OSError("offline")), self.now)
        self.assertEqual(len(cache["sources"]), 4)
        self.assertTrue(all(row["status"] == "unavailable" and row["error"] for row in cache["sources"]))
        self.assertTrue(all(row["items"] == [] for row in cache["sources"]))

    def test_news_feed_rejects_entities_and_public_fetch_rejects_private_addresses(self):
        with self.assertRaisesRegex(ValueError, "entity"):
            _feed_items(b'<!DOCTYPE rss [<!ENTITY test "bad">]><rss/>', [])
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"):
            with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]), mock.patch("socket.socket", side_effect=AssertionError("No private socket")):
                with self.assertRaisesRegex(ValueError, "public"):
                    _public_https("https://example.gov/feed")

    def test_existing_output_directory_and_hardlinked_database_are_rejected(self):
        directory = self.registry.root/"briefings"/"occupied"
        directory.mkdir(parents=True)
        existing = directory/"brief.md"
        existing.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.service.run("occupied", self.now)
        self.assertEqual(existing.read_bytes(), b"preserve")
        self.service.config()
        original_stat = Path.stat
        def linked_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            return mock.Mock(st_mode=info.st_mode, st_nlink=2, st_file_attributes=getattr(info, "st_file_attributes", 0)) if path == self.service.path else info
        with mock.patch.object(Path, "stat", linked_stat), self.assertRaisesRegex(ValueError, "one link"):
            self.service.config()

    def test_failed_refresh_preserves_previous_offline_source_bytes_and_dates(self):
        self.enable()
        cfg = self.service.config()
        original = self.cache_value(self.now-timedelta(hours=1))
        self.service.save_cache(original, _region_digest(cfg))
        failed = self.cache_value(self.now)
        failed["sources"][0].update(status="unavailable", items=[], error="Network unavailable")
        self.service.save_cache(failed, _region_digest(cfg))
        source = self.service.cache(now=self.now)["sources"][0]
        self.assertEqual(source["status"], "cached_refresh_failed")
        self.assertEqual(source["fetched_at"], original["sources"][0]["fetched_at"])
        self.assertEqual(source["items"][0]["title"], "Saved advisory")
        self.assertEqual(source["last_refresh_attempt_at"], self.now.isoformat())

    def test_global_autonomy_pause_prevents_due_run_and_online_worker(self):
        self.enable(online_sources=True)
        self.store.set_setting("paused", True)
        with mock.patch("subprocess.run", side_effect=AssertionError("Global pause forbids collector")):
            self.assertIsNone(self.service.run_due(self.now))
            self.assertFalse(self.service.collect()["collected"])
        self.assertEqual(self.service.history()["runs"], [])

    def test_startup_recovery_does_not_interrupt_another_live_reporter_process(self):
        self.enable()
        receipt = self.service.run_due(self.now)
        with self.service.database() as db:
            db.execute("UPDATE briefing_runs SET status='running' WHERE request_id=?", (receipt["request_id"],))
        marker = self.root/"child-lock-ready"
        code = ("import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from ariadne.locking import InstanceLock;lock=InstanceLock(Path(sys.argv[2]));"
                "lock.acquire();Path(sys.argv[3]).write_text('ready');time.sleep(30)")
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(Path.cwd()), str(self.settings.data_dir/"briefing.lock"), str(marker)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic()+5
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists(), "Child must own the OS lock before testing recovery")
            self.assertFalse(self.service.recover())
            self.assertFalse(self.service.config()["paused"])
            self.assertEqual(self.service.history()["runs"][0]["status"], "running")
            with self.assertRaises(BriefingConflict):
                self.service.run("blocked-by-live-reporter", self.now)
        finally:
            child.terminate()
            child.communicate(timeout=5)
        self.assertTrue(self.service.recover())
        self.assertTrue(self.service.config()["paused"])
        self.assertEqual(self.service.history()["runs"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
