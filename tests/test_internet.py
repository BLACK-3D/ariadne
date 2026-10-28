"""Opt-in Internet isolation, provenance and usable offline failure behavior."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from ariadne.config import Settings
from ariadne.internet import (InternetService, MAX_TEXT, TextPage, extract_text,
    internet_enabled, public_url, worker_request)
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class InternetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="internet-test-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name)/"data", api_token="private-ariadne-pairing-secret-"+"a"*25)
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.service = InternetService(self.settings, self.store, self.registry)

    def result(self, operation="fetch", text="Dated fixture data."):
        return {"ok": True, "operation": operation, "sources": [{"url": "https://example.com/", "title": "Example",
            "text": text, "fetched_at": "2026-10-28T00:00:00+00:00", "sha256": hashlib.sha256(text.encode()).hexdigest(), "cached": False}]}

    def enable(self):
        return self.service.configure({"enabled": True})

    def test_new_installation_disables_network_and_persists_switch(self):
        self.assertFalse(internet_enabled(self.store))
        with mock.patch.object(self.service, "_worker", side_effect=AssertionError("Disabled network cannot launch a child")):
            self.assertFalse(self.service.fetch("https://example.com/")["ok"])
            self.assertFalse(self.service.search("weather")["ok"])
            self.assertFalse(self.service.probe()["connection_verified"])
        self.enable()
        reopened = InternetService(self.settings, self.store, self.registry)
        self.assertEqual(reopened.config(), {"enabled": True, "revision": 1})

    def test_owner_switch_uses_atomic_expected_revision_and_rejects_bad_types(self):
        self.service.configure({"enabled": True}, expected_revision=0)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.configure({"enabled": False}, expected_revision=0)
        for value in ({"enabled": 1}, {"enabled": True, "other": 1}, {}, {"enabled": "true"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.configure(value)
        self.assertEqual(self.service.config(), {"enabled": True, "revision": 1})

    def test_enabled_cached_read_avoids_repeat_network_and_works_after_disable(self):
        self.enable()
        with mock.patch.object(self.service, "_worker", return_value=self.result()) as worker:
            first = self.service.fetch("https://example.com/")
            again = self.service.fetch("https://example.com/")
            self.service.configure({"enabled": False})
            offline = self.service.fetch("https://example.com/", refresh=True)
        self.assertEqual(worker.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(again["cached"])
        self.assertTrue(offline["offline"])
        self.assertEqual(offline["sources"][0]["fetched_at"], first["sources"][0]["fetched_at"])
        self.assertFalse(self.service.status()["connection_verified"])

    def test_failed_refresh_retains_old_provenance_and_reports_failure(self):
        self.enable()
        with mock.patch.object(self.service, "_worker", return_value=self.result()):
            first = self.service.fetch("https://example.com/")
        with mock.patch.object(self.service, "_worker", return_value={"ok": False, "sources": [], "error": "HTTP 503"}):
            refreshed = self.service.fetch("https://example.com/", refresh=True)
        self.assertTrue(refreshed["cached"])
        self.assertTrue(refreshed["refresh_failed"])
        self.assertEqual(refreshed["refresh_error"], "HTTP 503")
        self.assertEqual(refreshed["sources"][0]["sha256"], first["sources"][0]["sha256"])
        self.assertEqual(refreshed["sources"][0]["fetched_at"], "2026-10-28T00:00:00+00:00")

    def test_source_result_is_discarded_when_configuration_changes_during_request(self):
        self.enable()
        def worker(request, revision):
            self.service.configure({"enabled": False})
            return self.result()
        with mock.patch.object(self.service, "_worker", side_effect=worker):
            result = self.service.fetch("https://example.com/")
        self.assertFalse(result["ok"])
        self.assertIn("discarded", result["error"])
        self.assertEqual(self.service.status()["cache_entries"], 0)

    def test_stale_cache_remains_readable_offline_and_refreshes_online(self):
        self.enable()
        with mock.patch.object(self.service, "_worker", return_value=self.result()):
            self.service.search("fixture")
        with self.service.database() as db:
            db.execute("UPDATE internet_cache SET fetched=0")
        self.service.configure({"enabled": False})
        result = self.service.search("fixture")
        self.assertTrue(result["stale"])
        self.enable()
        with mock.patch.object(self.service, "_worker", return_value=self.result(text="New dated data.")) as worker:
            fresh = self.service.search("fixture")
        worker.assert_called_once()
        self.assertFalse(fresh["cached"])

    def test_cache_has_hard_entry_cap_and_eviction_is_oldest_first(self):
        self.enable()
        with mock.patch("ariadne.internet.MAX_CACHE", 3), mock.patch.object(self.service, "_worker", return_value=self.result()):
            for number in range(5):
                self.service.search("fixture "+str(number))
        self.assertEqual(self.service.status()["cache_entries"], 3)
        with self.service.database() as db:
            labels = [row[0] for row in db.execute("SELECT label FROM internet_cache ORDER BY fetched")]
        self.assertEqual(labels, ["fixture 2", "fixture 3", "fixture 4"])

    def test_history_stays_bounded_and_marks_cached_and_failed_requests(self):
        with mock.patch("ariadne.internet.MAX_HISTORY", 3):
            for number in range(5):
                self.service.search("fixture "+str(number))
        rows = self.service.history()["requests"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["label"], "fixture 4")
        self.assertFalse(rows[0]["ok"])
        self.assertFalse(rows[0]["cached"])

    def test_private_pairing_secret_is_not_sent_or_logged_raw_or_encoded(self):
        token = self.settings.api_token
        self.enable()
        encoded = "".join("%"+format(ord(character), "02x") for character in token)
        for value in (token, encoded, quote(encoded), "hello "+token):
            with self.subTest(value=value), mock.patch.object(self.service, "_worker", side_effect=AssertionError("Secret must not leave the app")), self.assertRaisesRegex(ValueError, "private"):
                self.service.search(value)
        with self.assertRaisesRegex(ValueError, "private"):
            self.service.fetch("https://example.com/"+token)
        self.assertEqual(self.service.history()["requests"], [])

    def test_live_verification_expires_and_keeps_actual_observation_time(self):
        self.enable()
        with mock.patch.object(self.service, "_worker", return_value=self.result()):
            self.service.fetch("https://example.com/")
        with self.service.database() as db:
            db.execute("UPDATE internet_history SET created='2020-01-01T00:00:00+00:00'")
        status = self.service.status()
        self.assertFalse(status["connection_verified"])
        self.assertEqual(status["connection_verified_at"], "2020-01-01T00:00:00+00:00")
        self.assertGreater(status["connection_verification_age_seconds"], 300)

    def test_database_sidecars_must_not_be_hardlinked(self):
        self.service.status()
        target = self.settings.data_dir/"fixture.txt"
        target.write_text("owner data", encoding="utf-8")
        sidecar = Path(str(self.service.path)+"-journal")
        try:
            os.link(target, sidecar)
        except PermissionError:
            self.skipTest("Windows sandbox does not allow the hardlink fixture.")
        with self.assertRaisesRegex(ValueError, "one link"):
            self.service.status()
        self.assertEqual(target.read_text(), "owner data")

    def test_search_fallback_is_explicit_about_wikipedia_only_scope(self):
        def response(url):
            payload = (b"<html>challenge</html>" if "duckduckgo" in url else json.dumps({"query": {"search": [{"title": "Local AI", "snippet": "Open <span>research</span>"}]}}).encode())
            return {"url": url, "requested_url": url, "payload": payload, "content_type": "text/html" if "duckduckgo" in url else "application/json", "bytes": len(payload), "fetched_at": "2026-10-28T00:00:00+00:00"}
        with mock.patch("ariadne.internet.public_get", side_effect=response):
            result = worker_request({"operation": "search", "query": "local AI", "limit": 2})
        self.assertEqual(result["provider"], "wikipedia_only_fallback")
        self.assertEqual(result["sources"][0]["text"], "Open research")
        self.assertTrue(any("Wikipedia only" in warning for warning in result["warnings"]))

    def test_general_search_snippets_have_provider_response_hash_and_are_not_fetched_pages(self):
        payload = b'<a class="result__a" href="https://example.com/page">Title</a><a class="result__snippet">Snippet</a>'
        response = {"url": "https://html.duckduckgo.com/html/", "payload": payload, "fetched_at": "2026-10-28T00:00:00+00:00"}
        with mock.patch("ariadne.internet.public_get", return_value=response) as getter:
            result = worker_request({"operation": "search", "query": "fixture", "limit": 2})
        self.assertEqual(getter.call_count, 1)
        self.assertEqual(result["sources"][0]["result_kind"], "search_snippet")
        self.assertEqual(result["sources"][0]["hash_scope"], "search_response")
        self.assertEqual(result["sources"][0]["text"], "Snippet")

    def test_html_is_text_only_with_hidden_active_content_and_bounded_expansion(self):
        title, text, truncated = extract_text(b'<title>Fixture</title><script>run()</script><style>secret</style><p>Hello &amp; world</p><iframe>hidden</iframe>', "text/html; charset=utf-8")
        self.assertEqual(title, "Fixture")
        self.assertIn("Hello & world", text)
        self.assertNotIn("run()", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("hidden", text)
        _, text, truncated = extract_text(("&amp;"*50_000).encode(), "text/html")
        self.assertLessEqual(len(text), MAX_TEXT)
        self.assertTrue(truncated)
        for mime in ("application/pdf", "application/octet-stream", "text/javascript", "image/svg+xml"):
            with self.subTest(mime=mime), self.assertRaises(ValueError):
                extract_text(b"data", mime)

    def test_url_canonicalization_never_retains_fragment_or_authentication(self):
        self.assertEqual(public_url("https://EXAMPLE.com:443/path?q=hello#fragment"), "https://example.com/path?q=hello")
        for value in ("https://user:pw@example.com/", "https://example.com/?api_key=secret", "https://example.com/?ACCESS-TOKEN=value", "https://example.com/?%70assword=value"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_url(value)

    def test_worker_requires_fixed_parent_activation_and_no_arbitrary_arguments(self):
        env = {key: value for key, value in os.environ.items() if key != "ARIADNE_INTERNET_WORKER"}
        result = subprocess.run([sys.executable, "-I", "-B", str(Path.cwd()/"scripts"/"internet_worker.py")], input=b'{"operation":"probe"}\n', capture_output=True, env=env, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        with self.assertRaises(ValueError):
            worker_request({"operation": "shell", "command": "do-not-run"})


if __name__ == "__main__":
    unittest.main()
