"""Independent end-to-end checks of inert metadata and cache publication leases."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from ariadne import repository_radar as radar
from ariadne.briefing import BriefingCollectionStopped, BriefingService
from ariadne.brief_poster import poster_view
from ariadne.config import Settings
from ariadne.internet import SETTINGS_KEY
from ariadne.store import Store
from ariadne.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 11, tzinfo=timezone.utc)


def metadata(entry, *, tag="v1.0", title="Review fixture", message="Review fixture"):
    if entry["kind"] == "github_release":
        value = {"tag_name": tag, "name": title, "published_at": "2026-10-28T00:00:00Z",
                 "draft": False, "prerelease": False, "body": "DO_NOT_EXECUTE_REMOTE_BODY",
                 "html_url": "https://127.0.0.1/untrusted", "assets": [{"browser_download_url": "https://127.0.0.1/run.exe"}]}
    elif entry["kind"] == "github_commit":
        value = {"sha": "a" * 40, "commit": {"message": message, "committer": {"date": "2026-10-28T00:00:00Z"}},
                 "html_url": "https://127.0.0.1/untrusted"}
    else:
        value = {"id": entry["repo"], "sha": "b" * 40, "lastModified": "2026-10-28T00:00:00Z",
                 "cardData": {"license": "apache-2.0", "remote_code": "DO_NOT_EXECUTE_REMOTE_BODY"},
                 "siblings": [{"rfilename": "DO_NOT_EXECUTE_REMOTE_BODY.py"}]}
    return json.dumps(value).encode("utf-8")


class RepositoryRadarReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".recovery-test-radar-review-", dir=ROOT)
        self.root = Path(temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(ROOT))
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=self.root / "data")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.service = BriefingService(self.settings, self.store, self.registry)
        self.service.configure({"enabled": True, "online_sources": True, "repository_updates": True}, NOW - timedelta(days=2))
        self.store.set_setting(SETTINGS_KEY, {"enabled": True, "revision": 1})
        self.config = self.service.config()
        # Distinct service locks/connections model the independently scheduled
        # Windows reporter; a shared Python RLock must not make this pass.
        self.other_store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.other_service = BriefingService(self.settings, self.other_store, ToolRegistry(self.settings))
        self.entries = radar.watchlist()
        self.by_url = {radar._endpoint(entry): entry for entry in self.entries}

    def fetch(self, endpoint):
        return metadata(self.by_url[endpoint])

    def collect(self, **values):
        return radar.collect_radar(self.settings.data_dir, fetch=self.fetch, now=NOW,
            check=lambda: self.service.check_repository_collection(self.config, 1),
            publication_lease=lambda: self.service.repository_publication_lease(self.config, 1), **values)

    def assert_publication_blocks(self, mutate):
        attempted, finished = threading.Event(), threading.Event()
        errors, threads = [], []
        original = radar._save

        def intercepted(*args, **kwargs):
            def writer():
                attempted.set()
                try:
                    mutate()
                except BaseException as error:
                    errors.append(error)
                finally:
                    finished.set()
            thread = threading.Thread(target=writer)
            threads.append(thread)
            thread.start()
            self.assertTrue(attempted.wait(3))
            self.assertFalse(finished.wait(.15), "Actual cache commit must hold the authority lease")
            return original(*args, **kwargs)

        try:
            with mock.patch.object(radar, "_save", side_effect=intercepted):
                result = self.collect()
        finally:
            for thread in threads:
                thread.join(timeout=5)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(result["counts"]["baseline"], len(self.entries))
        self.assertTrue((self.settings.data_dir / radar.CACHE_NAME).is_file())

    def test_real_collector_holds_briefing_lease_through_file_commit(self):
        self.assert_publication_blocks(lambda: self.other_service.configure({"repository_updates": False}, expected_revision=self.config["revision"]))
        self.assertFalse(self.service.config()["repository_updates"])

    def test_real_collector_holds_master_lease_through_file_commit(self):
        self.assert_publication_blocks(lambda: self.other_store.set_setting(SETTINGS_KEY, {"enabled": False, "revision": 2}))
        self.assertFalse(self.store.setting(SETTINGS_KEY)["enabled"])

    def test_disable_in_publication_gap_preserves_prior_snapshot_bytes(self):
        radar.collect_radar(self.settings.data_dir, fetch=self.fetch, now=NOW - timedelta(days=1))
        path = self.settings.data_dir / radar.CACHE_NAME
        before = path.read_bytes()

        @contextmanager
        def revoke_then_lease():
            self.service.configure({"repository_updates": False}, expected_revision=self.config["revision"])
            with self.service.repository_publication_lease(self.config, 1):
                yield

        with self.assertRaises(BriefingCollectionStopped):
            radar.collect_radar(self.settings.data_dir, fetch=self.fetch, now=NOW,
                check=lambda: self.service.check_repository_collection(self.config, 1), publication_lease=revoke_then_lease)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.settings.data_dir.glob(".repository-radar-cache-*.tmp")), [])

    def test_remote_urls_code_and_markup_never_become_actions_or_svg_elements(self):
        tag = 'v2/<script>alert("remote")</script>'
        def fetch(endpoint):
            return metadata(self.by_url[endpoint], tag=tag, title='"><script>DO_NOT_EXECUTE_REMOTE_BODY</script>')
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Metadata started a process")), \
             mock.patch("os.system", side_effect=AssertionError("Metadata ran a command")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("Unexpected network access")):
            saved = radar.collect_radar(self.settings.data_dir, fetch=fetch, now=NOW)
            report = {"generated_at_utc": NOW.isoformat(), "local_date": "2026-10-29",
                      "config": {"repository_updates": True}, "activity": {"runs_count": 0, "events_count": 0, "unresolved_approvals": []},
                      "public_sources": {"sources": []}, "repository_updates": {"enabled": True, **saved}, "warnings": []}
            graphic = poster_view(report, NOW)
        self.assertNotIn("127.0.0.1", json.dumps(saved))
        root = ET.fromstring(graphic["svg"])
        for element in root.iter():
            self.assertNotIn(element.tag.split("}")[-1], {"script", "image", "foreignObject", "iframe"})
            self.assertFalse(any(name.endswith("href") for name in element.attrib))
        self.assertEqual(saved["model_calls"], 0)

    def test_text_normalization_is_idempotent_at_whitespace_cutoffs(self):
        for limit in (1, 2, 60, 100, 120, 200, 240):
            for whitespace in (" ", "\t", "\n", "\u0085", "\u00a0", "\u2003", "\u2028", "\u3000"):
                raw = "x" * max(0, limit - 1) + whitespace + "after cutoff"
                with self.subTest(limit=limit, whitespace=repr(whitespace)):
                    value = radar._text(raw, limit)
                    self.assertLessEqual(len(value), limit)
                    self.assertEqual(radar._text(value, limit), value)
                    self.assertEqual(value, value.strip())

    def test_production_service_composition_publishes_all_sources_with_cutoff_text(self):
        seen = []
        def public_get(endpoint, *, deadline, max_redirects, max_bytes):
            self.assertEqual(max_redirects, 0)
            self.assertEqual(max_bytes, radar.MAX_RESPONSE_BYTES)
            entry = self.by_url[endpoint]
            value = json.loads(metadata(entry, title="x" * 239 + " cut title", message="x" * 239 + " cut commit"))
            if entry["kind"] == "hf_model":
                value["cardData"]["license"] = "x" * 119 + " cut declaration"
            seen.append(endpoint)
            return {"requested_url": endpoint, "url": endpoint, "payload": json.dumps(value).encode("utf-8")}
        with mock.patch.dict(os.environ, {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0"}), \
             mock.patch("ariadne.internet.public_get", side_effect=public_get), \
             mock.patch("socket.create_connection", side_effect=AssertionError("Qualification must not contact a network")):
            result = self.service.collect_repository_updates(self.config, 1, now=NOW)
        self.assertEqual(seen, list(self.by_url))
        self.assertEqual(result["counts"], {"changed": 0, "baseline": len(self.entries), "unchanged": 0, "failed": 0})
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["model_calls"], 0)
        loaded = radar.load_snapshot(self.settings.data_dir, NOW)
        self.assertFalse(loaded["blocked"])
        self.assertEqual(len(loaded["sources"]), len(self.entries))
        self.assertEqual([row["id"] for row in loaded["sources"]], [entry["id"] for entry in self.entries])
        self.assertTrue(all(row["checked_at"] for row in loaded["sources"]))


if __name__ == "__main__":
    unittest.main()
