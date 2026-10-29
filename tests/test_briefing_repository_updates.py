"""Daily repository reporting preserves policy, saved clocks and immutable briefs."""
import copy
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from ariadne.briefing import BriefingCollectionStopped, BriefingConflict, BriefingService
from ariadne.briefing_summary import MAX_SUMMARY_CHARS, compact_summary, repository_summary
from ariadne.brief_poster import HEIGHT, MAX_SVG_BYTES, WIDTH, poster_view
from ariadne.config import Settings
from ariadne.internet import SETTINGS_KEY
from ariadne.store import Store
from ariadne.tools import ToolRegistry

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 11, tzinfo=UTC)


def snapshot(now=NOW):
    return {"schema": 1, "checked_at": now.isoformat(), "status": "cached", "stale": False, "counts": {"changed": 99},
            "sources": [
                {"id": "one", "name": "Upstream one", "repo": "publisher/one", "status": "baseline", "version": "v1.0",
                 "checked_at": now.isoformat(), "published_at": (now-timedelta(days=5)).isoformat(), "url": "https://github.com/publisher/one", "changes": []},
                {"id": "two", "name": "Upstream two", "repo": "publisher/two", "status": "changed", "version": "v2.0", "previous_version": "v1.0",
                 "checked_at": now.isoformat(), "published_at": now.isoformat(), "url": "https://github.com/publisher/two", "changes": ["version"]}],
            "warnings": [], "model_calls": 0}


def report(radar=None):
    return {"generated_at_utc": NOW.isoformat(), "local_date": "2026-10-29", "config": {"repository_updates": True},
            "activity": {"runs_count": 0, "events_count": 0, "unresolved_approvals": []},
            "public_sources": {"sources": []}, "repository_updates": {"enabled": True, **(snapshot() if radar is None else radar)}, "warnings": []}


class RepositoryBriefingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".repository-brief-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.service = BriefingService(self.settings, self.store, self.registry)

    def enable(self, **values):
        return self.service.configure({"enabled": True, "repository_updates": True, **values}, NOW-timedelta(days=1))

    def saved_report(self, receipt):
        row = next(item for item in receipt["artifacts"] if item["path"].endswith(".json"))
        return json.loads(self.registry._path(row["path"]).read_bytes())

    def test_legacy_config_default_and_exact_boolean_validation(self):
        self.assertFalse(self.service.config()["repository_updates"])
        old = self.service.config(); old.pop("repository_updates")
        with self.service.database() as db:
            db.execute("INSERT OR REPLACE INTO briefing_settings VALUES(1,?)", (json.dumps(old),))
        self.assertFalse(self.service.config()["repository_updates"])
        for value in (1, "true", [], None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "boolean"):
                self.service.configure({"repository_updates": value})
        self.assertFalse(self.service.config()["repository_updates"])

    def test_config_compare_and_swap_cannot_overwrite_newer_pause_or_flag(self):
        first = self.service.config()
        self.service.configure({"paused": True}, expected_revision=first["revision"])
        current = self.service.config()
        with self.assertRaises(BriefingConflict):
            self.service.configure({"repository_updates": True, "paused": False}, expected_revision=first["revision"])
        self.assertEqual(self.service.config(), current)
        for value in (True, -1, "1"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "non-negative integer"):
                self.service.configure({}, expected_revision=value)

    def test_repository_only_collection_does_not_require_weather_region(self):
        self.enable(online_sources=True)
        cfg = self.service.config()
        self.assertIsNone(cfg["region"])
        self.assertTrue(self.service.status(NOW)["collection_enabled"])
        child = mock.Mock(returncode=1)
        child.poll.return_value = 1
        with mock.patch("subprocess.Popen", return_value=child) as runner:
            result = self.service.collect()
        self.assertFalse(result["collected"])
        self.assertEqual(runner.call_args.kwargs["env"]["ARIADNE_OFFLINE"], "0")

    def test_repository_policy_rechecks_disable_pause_and_master_revision(self):
        self.enable(online_sources=True)
        self.store.set_setting(SETTINGS_KEY, {"enabled": True, "revision": 1})
        cfg = self.service.config()
        self.service.check_repository_collection(cfg, 1)
        self.service.configure({"repository_updates": False})
        with self.assertRaises(BriefingCollectionStopped):
            self.service.check_repository_collection(cfg, 1)
        self.service.configure({"repository_updates": True})
        cfg = self.service.config()
        self.store.set_setting(SETTINGS_KEY, {"enabled": False, "revision": 2})
        with self.assertRaises(BriefingCollectionStopped):
            self.service.check_repository_collection(cfg, 1)
        self.assertTrue(self.service.config()["repository_updates"])

    def assert_writer_waits_for_publication(self, write):
        self.enable(online_sources=True)
        self.store.set_setting(SETTINGS_KEY, {"enabled": True, "revision": 1})
        cfg = self.service.config()
        attempted, done = threading.Event(), threading.Event()
        errors = []
        def writer():
            attempted.set()
            try:
                write(cfg)
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        with self.service.repository_publication_lease(cfg, 1):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(attempted.wait(3))
            self.assertFalse(done.wait(.15), "Revocation must serialize with file publication")
        thread.join(timeout=5)
        self.assertTrue(done.is_set())
        self.assertEqual(errors, [])

    def test_publication_lease_pins_briefing_config_until_commit(self):
        self.assert_writer_waits_for_publication(lambda cfg: self.service.configure({"repository_updates": False}, expected_revision=cfg["revision"]))
        self.assertFalse(self.service.config()["repository_updates"])

    def test_publication_lease_pins_canonical_master_until_commit(self):
        self.assert_writer_waits_for_publication(lambda cfg: self.store.set_setting(SETTINGS_KEY, {"enabled": False, "revision": 2}))
        self.assertFalse(self.store.setting(SETTINGS_KEY)["enabled"])

    def test_disabled_flag_does_not_read_private_radar_cache(self):
        with mock.patch("ariadne.repository_radar.load_snapshot", side_effect=AssertionError("Disabled cache read")):
            self.assertEqual(self.service.repository_snapshot()["status"], "disabled")

    def test_missing_or_corrupt_repository_cache_does_not_break_regional_brief(self):
        self.enable()
        with mock.patch("ariadne.repository_radar.load_snapshot", side_effect=ValueError("fixture corrupt cache")):
            receipt = self.service.run("radar-missing", NOW)
        radar = self.saved_report(receipt)["repository_updates"]
        self.assertEqual(radar["status"], "unavailable")
        self.assertFalse(radar["collection_enabled"])
        self.assertEqual(radar["sources"], [])

    def test_local_day_key_and_collection_callback_are_bound_to_saved_config(self):
        self.enable(online_sources=True)
        cfg = self.service.config()
        with mock.patch("ariadne.repository_radar.collect_radar", return_value=snapshot()) as collect:
            self.service.collect_repository_updates(cfg, None, NOW+timedelta(hours=15))
        self.assertEqual(collect.call_args.kwargs["day_key"], "2026-10-29")
        self.assertTrue(callable(collect.call_args.kwargs["check"]))
        self.assertTrue(callable(collect.call_args.kwargs["publication_lease"]))

    def test_existing_collector_cli_can_refresh_repositories_without_region(self):
        from scripts import briefing as cli
        self.enable(online_sources=True)
        cfg = self.service.config()
        environment = {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0",
                       "ARIADNE_BRIEFING_CONFIG_REVISION": str(cfg["revision"]), "ARIADNE_BRIEFING_INTERNET_REVISION": "null"}
        with mock.patch.dict(os.environ, environment), mock.patch("sys.argv", ["briefing.py", "collect-worker", "--data-dir", str(self.settings.data_dir)]), \
                mock.patch.object(cli, "install_network_policy"), mock.patch.object(cli, "Store", return_value=self.store), \
                mock.patch.object(cli, "BriefingService", return_value=self.service), \
                mock.patch.object(self.service, "collect_repository_updates", return_value=snapshot()) as radar, \
                mock.patch.object(cli, "collect_external", side_effect=AssertionError("No regional requests without a region")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main()
        radar.assert_called_once_with(cfg, None)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "sources": 0, "repository_updates": True})

    def test_collector_cli_rechecks_revocation_after_repository_stage(self):
        from scripts import briefing as cli
        self.enable(online_sources=True)
        cfg = self.service.config()
        def revoke(*args):
            self.store.set_setting("paused", True)
            return snapshot()
        environment = {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0",
                       "ARIADNE_BRIEFING_CONFIG_REVISION": str(cfg["revision"]), "ARIADNE_BRIEFING_INTERNET_REVISION": "null"}
        with mock.patch.dict(os.environ, environment), mock.patch("sys.argv", ["briefing.py", "collect-worker", "--data-dir", str(self.settings.data_dir)]), \
                mock.patch.object(cli, "install_network_policy"), mock.patch.object(cli, "Store", return_value=self.store), \
                mock.patch.object(cli, "BriefingService", return_value=self.service), \
                mock.patch.object(self.service, "collect_repository_updates", side_effect=revoke), \
                mock.patch.object(cli, "collect_external", side_effect=AssertionError("Revoked stage must not reach weather")):
            with self.assertRaisesRegex(SystemExit, "revoked"):
                cli.main()

    def test_collector_validation_failure_is_not_mislabeled_as_policy_revocation(self):
        from scripts import briefing as cli
        self.enable(online_sources=True)
        cfg = self.service.config()
        environment = {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0",
                       "ARIADNE_BRIEFING_CONFIG_REVISION": str(cfg["revision"]), "ARIADNE_BRIEFING_INTERNET_REVISION": "null"}
        with mock.patch.dict(os.environ, environment), mock.patch("sys.argv", ["briefing.py", "collect-worker", "--data-dir", str(self.settings.data_dir)]), \
                mock.patch.object(cli, "install_network_policy"), mock.patch.object(cli, "Store", return_value=self.store), \
                mock.patch.object(cli, "BriefingService", return_value=self.service), \
                mock.patch.object(self.service, "collect_repository_updates", side_effect=ValueError("PRIVATE_UNTRUSTED_FIXTURE")):
            with self.assertRaises(SystemExit) as result:
                cli.main()
        message = str(result.exception)
        self.assertIn("failed validation", message)
        self.assertNotIn("revoked", message)
        self.assertNotIn("PRIVATE_UNTRUSTED_FIXTURE", message)

    def test_collector_bad_revision_environment_has_bounded_configuration_error(self):
        from scripts import briefing as cli
        self.enable(online_sources=True)
        environment = {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0",
                       "ARIADNE_BRIEFING_CONFIG_REVISION": "PRIVATE_INVALID_VALUE", "ARIADNE_BRIEFING_INTERNET_REVISION": "null"}
        with mock.patch.dict(os.environ, environment), mock.patch("sys.argv", ["briefing.py", "collect-worker", "--data-dir", str(self.settings.data_dir)]), \
                mock.patch.object(cli, "install_network_policy"), mock.patch.object(cli, "Store", return_value=self.store), \
                mock.patch.object(cli, "BriefingService", return_value=self.service), \
                mock.patch.object(self.service, "collect_repository_updates", side_effect=AssertionError("No request after invalid environment")):
            with self.assertRaises(SystemExit) as result:
                cli.main()
        message = str(result.exception)
        self.assertIn("configuration is missing or invalid", message)
        self.assertNotIn("PRIVATE_INVALID_VALUE", message)

    def test_daily_brief_claim_is_once_per_day_and_preserves_offline_snapshot_times(self):
        self.enable()
        saved = snapshot(NOW-timedelta(days=2))
        with mock.patch("ariadne.repository_radar.load_snapshot", return_value=saved), mock.patch.object(self.service, "collect", return_value={"collected": False, "reason": "Offline fixture"}) as collect:
            receipt = self.service.run_due(NOW)
            self.assertIsNone(self.service.run_due(NOW+timedelta(hours=1)))
        self.assertEqual(collect.call_count, 1)
        value = self.saved_report(receipt)
        self.assertEqual(value["repository_updates"]["checked_at"], saved["checked_at"])
        md = self.registry._path(next(item["path"] for item in receipt["artifacts"] if item["path"].endswith(".md"))).read_text()
        self.assertIn("Repository advances", md)
        self.assertIn("first observations are not new releases", md)
        self.assertIn("STALE", md)
        self.assertEqual(value["model_calls"], 0)

    def test_enabling_repository_updates_does_not_rewrite_completed_old_report(self):
        receipt = self.service.run("old-report", NOW)
        artifacts = [(item["path"], self.registry._path(item["path"]).read_bytes()) for item in receipt["artifacts"]]
        self.enable()
        with mock.patch("ariadne.repository_radar.load_snapshot", side_effect=AssertionError("No collection during replay")):
            replay = self.service.run("old-report", NOW+timedelta(hours=1))
        self.assertTrue(replay["replayed"])
        self.assertEqual(artifacts, [(path, self.registry._path(path).read_bytes()) for path, _ in artifacts])

    def test_interrupted_daily_receipt_holds_repository_collection(self):
        self.enable()
        with mock.patch("ariadne.repository_radar.load_snapshot", return_value=snapshot()):
            receipt = self.service.run_due(NOW)
        with self.service.database() as db:
            db.execute("UPDATE briefing_runs SET status='running' WHERE request_id=?", (receipt["request_id"],))
        self.service.recover()
        with mock.patch.object(self.service, "collect", side_effect=AssertionError("Do not replay interrupted collection")):
            self.assertIsNone(self.service.run_due(NOW+timedelta(days=1)))
        self.assertTrue(self.service.config()["paused"])


class RepositoryPresentationTests(unittest.TestCase):
    def test_baselines_and_saved_changed_counts_do_not_trust_raw_aggregates(self):
        view = repository_summary(report(), NOW)
        self.assertEqual(view["counts"], {"changed": 1, "baseline": 1, "unchanged": 0, "failed": 0})
        self.assertIn("first observations are not new releases", view["description"])
        self.assertFalse(view["stale"])

    def test_old_unknown_future_or_failed_checks_remain_explicit(self):
        for checked in (None, "bad time", (NOW-timedelta(days=2)).isoformat(), (NOW+timedelta(hours=1)).isoformat()):
            value = report()
            for row in value["repository_updates"]["sources"]:
                row["checked_at"] = checked
            with self.subTest(checked=checked):
                self.assertTrue(repository_summary(value, NOW)["stale"])
        value = report(); value["repository_updates"]["sources"][0]["status"] = "cached_refresh_failed"
        self.assertEqual(repository_summary(value, NOW)["counts"]["failed"], 1)

    def test_partial_poll_preserves_successes_and_prior_failed_source_clock(self):
        value = report()
        value["repository_updates"]["status"] = "partial"
        failed = value["repository_updates"]["sources"][0]
        failed.update(status="cached_refresh_failed", checked_at=(NOW-timedelta(days=3)).isoformat(), last_attempt_at=NOW.isoformat())
        view = repository_summary(value, NOW)
        self.assertEqual(view["status"], "partial")
        self.assertEqual(view["counts"]["changed"], 1)
        self.assertEqual(view["counts"]["failed"], 1)
        self.assertEqual(view["items"][0]["checked_at"], (NOW-timedelta(days=3)).isoformat(timespec="seconds"))
        self.assertTrue(view["stale"])

    def test_unsafe_source_urls_and_markup_remain_inert(self):
        value = report()
        for row in value["repository_updates"]["sources"]:
            row["url"] = "javascript:alert(1)"
            row["name"] = '<script>attack</script>\x00'
        summary = repository_summary(value, NOW)
        self.assertTrue(all("url" not in item for item in summary["items"]))
        content = poster_view(value, NOW)["svg"]
        root = ET.fromstring(content)
        self.assertFalse(any("href" in key or key.startswith("on") for item in root.iter() for key in item.attrib))
        self.assertNotIn("<script>", content)

    def test_poster_watch_fits_one_page_and_unknown_cache_has_no_invented_zero_changes(self):
        value = report()
        result = poster_view(value, NOW)
        text = " ".join(item.text or "" for item in ET.fromstring(result["svg"]).iter())
        self.assertIn("REPOSITORY WATCH", text)
        self.assertIn("1 CHANGED / 1 FIRST OBS.", text)
        self.assertEqual((result["width"], result["height"]), (WIDTH, HEIGHT))
        self.assertLess(len(result["svg"].encode()), MAX_SVG_BYTES)
        for item in ET.fromstring(result["svg"]).iter():
            if item.tag.endswith("text"):
                self.assertTrue(0 <= float(item.attrib["x"]) < WIDTH)
                self.assertTrue(0 <= float(item.attrib["y"]) < HEIGHT)
        missing = report({"sources": [], "status": "unavailable", "checked_at": None})
        text = poster_view(missing, NOW)["svg"]
        self.assertIn("No verified repository snapshot", text)
        self.assertNotIn("0 CHANGED", text)

    def test_summary_keeps_existing_budget_and_presentation_is_pure(self):
        value = report(); original = copy.deepcopy(value)
        with mock.patch("builtins.open", side_effect=AssertionError("No file access")), mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
            summary = compact_summary(value, NOW)
            poster_view(value, NOW)
        self.assertLessEqual(len(json.dumps(summary, ensure_ascii=False)), MAX_SUMMARY_CHARS)
        self.assertEqual(summary["repository_updates"]["counts"]["baseline"], 1)
        self.assertEqual(value, original)


if __name__ == "__main__":
    unittest.main()
