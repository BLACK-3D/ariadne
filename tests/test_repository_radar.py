"""Bounded repository metadata remains inert, dated and policy-revocable."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ariadne import repository_radar as radar

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc)


class RepositoryRadarTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".repo-radar-test-", dir=ROOT)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(ROOT.resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.entries = radar.watchlist()
        self.requests = []

    def payload(self, entry, *, version="v1.0", notes="Release notes", revision="a" * 40, license="apache-2.0"):
        if entry["kind"] == "github_release":
            value = {"tag_name": version, "name": version, "body": notes, "draft": False, "prerelease": False,
                     "published_at": "2026-10-28T12:00:00Z", "html_url": "file:///UNTRUSTED"}
        elif entry["kind"] == "github_commit":
            value = {"sha": revision, "commit": {"message": "Synthetic source commit", "committer": {"date": "2026-10-28T12:00:00Z"}}}
        else:
            value = {"id": entry["repo"], "sha": revision, "lastModified": "2026-10-28T12:00:00Z", "cardData": {"license": license}}
        return json.dumps(value).encode("utf-8")

    def fetch(self, url):
        self.requests.append(url)
        return self.payload(next(entry for entry in self.entries if radar._endpoint(entry) == url))

    def collect(self, *, now=NOW, fetch=None, **kwargs):
        return radar.collect_radar(self.root, fetch=fetch or self.fetch, now=now, **kwargs)

    def cache_bytes(self):
        return (self.root / radar.CACHE_NAME).read_bytes()

    def test_first_observation_is_baseline_never_new_release(self):
        result = self.collect()
        self.assertEqual(result["counts"], {"changed": 0, "baseline": 15, "unchanged": 0, "failed": 0})
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(len(self.requests), 15)
        self.assertTrue(all(not row["changes"] for row in result["sources"]))
        self.assertTrue(all(row["url"].startswith(("https://github.com/", "https://huggingface.co/")) for row in result["sources"]))
        self.assertNotIn("UNTRUSTED", self.cache_bytes().decode())

    def test_same_local_day_reuses_immutable_snapshot_without_requests(self):
        first = self.collect(day_key="2026-10-29")
        raw = self.cache_bytes()
        result = self.collect(now=NOW + timedelta(hours=10), day_key="2026-10-29", fetch=lambda _: self.fail("No repeat request"))
        self.assertFalse(result["refreshed"])
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["checked_at"], first["checked_at"])
        self.assertEqual(self.cache_bytes(), raw)

    def test_new_local_day_refreshes_even_when_utc_date_is_unchanged(self):
        self.collect(now=NOW, day_key="2026-10-28")
        result = self.collect(now=NOW + timedelta(hours=1), day_key="2026-10-29")
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["generation"], 2)
        self.assertEqual(result["counts"]["unchanged"], 15)

    def test_changed_version_and_release_notes_have_separate_fields(self):
        self.collect()
        def changed(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            return self.payload(entry, version="v1.1" if entry["id"] == "qlib" else "v1.0")
        result = self.collect(now=NOW + timedelta(days=1), fetch=changed)
        row = next(row for row in result["sources"] if row["id"] == "qlib")
        self.assertEqual(result["counts"]["changed"], 1)
        self.assertEqual(row["previous_version"], "v1.0")
        self.assertEqual(row["version"], "v1.1")
        self.assertEqual(set(row["changes"]), {"version", "title"})

    def test_release_note_edit_is_detected_without_retaining_remote_body(self):
        self.collect()
        def changed(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            return self.payload(entry, notes="PRIVATE_UNTRUSTED_REMOTE_BODY" if entry["id"] == "qlib" else "Release notes")
        result = self.collect(now=NOW + timedelta(days=1), fetch=changed)
        row = next(row for row in result["sources"] if row["id"] == "qlib")
        self.assertEqual(row["changes"], ["notes_sha256"])
        self.assertNotIn("PRIVATE_UNTRUSTED_REMOTE_BODY", self.cache_bytes().decode())

    def test_model_revision_and_declared_license_changes_are_observations(self):
        self.collect()
        def changed(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            return self.payload(entry, revision="b" * 40 if entry["id"] == "qwen35" else "a" * 40,
                                license="new-terms" if entry["id"] == "qwen35" else "apache-2.0")
        result = self.collect(now=NOW + timedelta(days=1), fetch=changed)
        row = next(row for row in result["sources"] if row["id"] == "qwen35")
        self.assertEqual(set(row["changes"]), {"revision", "version", "license"})
        self.assertEqual(row["previous_revision"], "a" * 40)

    def test_failed_refresh_keeps_last_observed_time_and_version(self):
        first = self.collect()
        old = next(row for row in first["sources"] if row["id"] == "qlib")
        def failed(url):
            if "/microsoft/qlib/" in url:
                raise OSError("FIXTURE_SECRET_MUST_NOT_REACH_CACHE")
            return self.fetch(url)
        result = self.collect(now=NOW + timedelta(days=1), fetch=failed)
        row = next(row for row in result["sources"] if row["id"] == "qlib")
        self.assertEqual(row["status"], "cached_refresh_failed")
        self.assertEqual(row["checked_at"], old["checked_at"])
        self.assertEqual(row["version"], old["version"])
        self.assertNotEqual(row["last_attempt_at"], old["last_attempt_at"])
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertNotIn("FIXTURE_SECRET", self.cache_bytes().decode())

    def test_all_sources_unavailable_does_not_claim_freshness_or_no_changes(self):
        def fail(_):
            raise OSError("fixture")
        result = self.collect(fetch=fail)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["counts"]["failed"], 15)
        self.assertTrue(result["stale"])
        self.assertTrue(all(row["checked_at"] is None for row in result["sources"]))

    def test_http_protocol_failure_preserves_one_old_source_and_continues(self):
        first = self.collect()
        old = next(row for row in first["sources"] if row["id"] == "qlib")
        def failed(url):
            if "/microsoft/qlib/" in url:
                raise http.client.BadStatusLine("UNTRUSTED_SECRET_PROTOCOL_BODY")
            return self.fetch(url)
        result = self.collect(now=NOW + timedelta(days=1), fetch=failed)
        row = next(row for row in result["sources"] if row["id"] == "qlib")
        self.assertEqual(row["status"], "cached_refresh_failed")
        self.assertEqual(row["checked_at"], old["checked_at"])
        self.assertEqual(result["counts"]["unchanged"], 14)
        self.assertNotIn("UNTRUSTED_SECRET", self.cache_bytes().decode())

    def test_offline_read_has_no_network_and_recomputes_staleness(self):
        first = self.collect()
        with patch("ariadne.internet.public_get", side_effect=AssertionError("No network")):
            result = radar.load_snapshot(self.root, NOW + timedelta(days=2))
        self.assertEqual(result["status"], "cached")
        self.assertTrue(result["stale"])
        self.assertEqual(result["checked_at"], first["checked_at"])
        self.assertTrue(all(row["stale"] for row in result["sources"]))

    def test_missing_cache_read_does_not_initialize_directory_or_token(self):
        absent = self.root / "not-created"
        result = radar.load_snapshot(absent, NOW)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(absent.exists())

    def test_corrupt_cache_holds_collection_and_is_not_overwritten(self):
        path = self.root / radar.CACHE_NAME
        path.write_bytes(b"NOT_VALID_OWNER_CACHE")
        result = self.collect(fetch=lambda _: self.fail("Corrupt cache must hold collection"))
        self.assertTrue(result["blocked"])
        self.assertEqual(path.read_bytes(), b"NOT_VALID_OWNER_CACHE")
        self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])

    def test_duplicate_cache_keys_and_future_check_time_are_held(self):
        self.collect()
        path = self.root / radar.CACHE_NAME
        good = path.read_bytes()
        path.write_bytes(good.replace(b'"schema":1', b'"schema":1,"schema":1'))
        self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])
        value = json.loads(good)
        value["checked_at"] = (NOW + timedelta(days=2)).isoformat()
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])

    def test_cache_identity_and_derived_links_cannot_be_redirected(self):
        self.collect()
        path = self.root / radar.CACHE_NAME
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutation in (lambda value: value["sources"][0].update(repo="attacker/other"),
                         lambda value: value["sources"][0].update(url="https://attacker.example/"),
                         lambda value: value.update(watchlist_sha256="0" * 64)):
            value = json.loads(json.dumps(original))
            mutation(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])

    def test_overflowing_cached_timestamp_is_held_without_mutation(self):
        self.collect()
        path = self.root / radar.CACHE_NAME
        value = json.loads(path.read_text(encoding="utf-8"))
        value["checked_at"] = "0001-01-01T00:00:00+14:00"
        path.write_text(json.dumps(value), encoding="utf-8")
        canary = path.read_bytes()
        self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])
        self.assertTrue(self.collect(fetch=lambda _: self.fail("No corrupt-cache requests"))["blocked"])
        self.assertEqual(path.read_bytes(), canary)

    def test_policy_revoked_after_response_is_not_downgraded_to_source_error(self):
        allowed = [True]
        def fetch(url):
            payload = self.fetch(url)
            allowed[0] = False
            return payload
        def check():
            if not allowed[0]:
                raise RuntimeError("Policy revoked")
        with self.assertRaisesRegex(RuntimeError, "revoked"):
            self.collect(fetch=fetch, check=check)
        self.assertFalse((self.root / radar.CACHE_NAME).exists())
        self.assertEqual(len(self.requests), 1)

    def test_final_publication_lease_spans_atomic_replace(self):
        held = [False]
        @contextmanager
        def lease():
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
        replace = radar.os.replace
        def commit(source, destination):
            self.assertTrue(held[0])
            return replace(source, destination)
        with patch.object(radar.os, "replace", side_effect=commit):
            result = self.collect(publication_lease=lease)
        self.assertEqual(result["status"], "fresh")
        self.assertFalse(held[0])

    def test_revocation_at_publication_leaves_old_cache_byte_identical(self):
        self.collect()
        old = self.cache_bytes()
        allowed = [True]
        def check():
            if not allowed[0]:
                raise RuntimeError("Policy revoked")
        @contextmanager
        def lease():
            allowed[0] = False
            yield
        with self.assertRaisesRegex(RuntimeError, "revoked"):
            self.collect(now=NOW + timedelta(days=1), check=check, publication_lease=lease)
        self.assertEqual(self.cache_bytes(), old)
        self.assertEqual(list(self.root.glob(".repository-radar-cache-*.tmp")), [])

    def test_concurrent_collector_is_refused_before_any_request(self):
        from ariadne.locking import InstanceLock
        owner = InstanceLock(self.root / "repository-radar.lock")
        owner.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "Another Ariadne"):
                self.collect(fetch=lambda _: self.fail("Concurrent request"))
        finally:
            owner.release()

    def test_size_limit_and_duplicate_remote_keys_fail_as_unavailable(self):
        for payload in (b"x" * (radar.MAX_RESPONSE_BYTES + 1), b'{"tag_name":"v1","tag_name":"v2"}'):
            result = self.collect(fetch=lambda _: payload, day_key="2026-10-" + ("28" if payload.startswith(b"x") else "29"))
            self.assertEqual(result["counts"]["failed"], 15)
            self.assertEqual(result["status"], "unavailable")

    def test_transport_redirect_away_from_fixed_endpoint_is_not_adopted(self):
        def fetch(url):
            return {"requested_url": url, "url": "https://attacker.example/", "payload": self.fetch(url)}
        result = self.collect(fetch=fetch)
        self.assertEqual(result["counts"]["failed"], 15)

    def test_shared_deadline_stops_following_requests_and_rejects_late_response(self):
        clock = [0.0]
        def fetch(url):
            clock[0] += radar.MAX_SECONDS + 1
            return self.fetch(url)
        with patch.object(radar.time, "monotonic", side_effect=lambda: clock[0]):
            result = self.collect(fetch=fetch)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result["counts"]["failed"], 15)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(all(row["checked_at"] is None for row in result["sources"]))

    def test_atomic_publication_failure_retains_old_cache_and_removes_owned_stage(self):
        self.collect()
        original = self.cache_bytes()
        with patch.object(radar.os, "replace", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaises(OSError):
                self.collect(now=NOW + timedelta(days=1))
        self.assertEqual(self.cache_bytes(), original)
        self.assertEqual(list(self.root.glob(".repository-radar-cache-*.tmp")), [])

    def test_remote_draft_prerelease_and_bad_hf_identity_are_not_adopted(self):
        def fetch(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            value = json.loads(self.payload(entry))
            if entry["kind"] == "github_release":
                value["prerelease"] = True
            elif entry["kind"] == "hf_model":
                value["id"] = "other/model"
            else:
                value["sha"] = "bad"
            return json.dumps(value).encode()
        self.assertEqual(self.collect(fetch=fetch)["counts"]["failed"], 15)

    def test_release_text_remains_data_and_output_has_no_control_characters(self):
        def fetch(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            value = json.loads(self.payload(entry))
            if entry["kind"] == "github_release":
                value["name"] = "<script>do_not_execute()</script>\u0000\ud800"
            return json.dumps(value).encode()
        with patch("subprocess.Popen", side_effect=AssertionError("No candidate process")), patch("socket.create_connection", side_effect=AssertionError("No socket")):
            result = self.collect(fetch=fetch)
        self.assertNotIn("\x00", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("\ud800", json.dumps(result, ensure_ascii=False))
        self.assertIn("do_not_execute", result["sources"][0]["title"])

    def test_long_realistic_commit_message_cut_at_space_is_cache_valid(self):
        def fetch(url):
            entry = next(row for row in self.entries if radar._endpoint(row) == url)
            value = json.loads(self.payload(entry))
            if entry["kind"] == "github_commit":
                value["commit"]["message"] = "A" * 239 + " B\n\nMore commit details."
            return json.dumps(value).encode()
        result = self.collect(fetch=fetch)
        commit = next(row for row in result["sources"] if row["kind"] == "github_commit")
        self.assertEqual(commit["title"], "A" * 239)
        self.assertEqual(result["counts"]["baseline"], 15)
        self.assertFalse(radar.load_snapshot(self.root, NOW)["blocked"])

    def test_real_network_requires_existing_collector_environment_and_leases(self):
        with patch.dict(os.environ, {"ARIADNE_BRIEFING_COLLECTOR": "0", "ARIADNE_OFFLINE": "1"}), patch("ariadne.internet.public_get", side_effect=AssertionError("No request")):
            with self.assertRaisesRegex(ValueError, "enabled briefing"):
                radar.collect_radar(self.root, now=NOW)

    def test_live_transport_is_fixed_and_receives_shared_deadline(self):
        def public_get(url, *, deadline, max_redirects, max_bytes):
            self.assertLessEqual(deadline, radar.time.monotonic() + 10.1)
            self.assertEqual(max_redirects, 0)
            self.assertEqual(max_bytes, radar.MAX_RESPONSE_BYTES)
            return {"requested_url": url, "url": url, "payload": self.fetch(url)}
        with patch.dict(os.environ, {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0"}), patch("ariadne.internet.public_get", side_effect=public_get):
            result = radar.collect_radar(self.root, now=NOW, check=lambda: None, publication_lease=nullcontext)
        self.assertEqual(result["status"], "fresh")

    def test_wrong_watchlist_pin_holds_reads_and_collection(self):
        with patch.object(radar, "WATCHLIST_SHA256", "0" * 64):
            self.assertTrue(radar.load_snapshot(self.root, NOW)["blocked"])
            with self.assertRaisesRegex(ValueError, "reviewed source pin"):
                self.collect()

    def test_hardlinked_cache_is_held_and_canary_is_unchanged(self):
        canary = self.root / "private-canary"
        canary.write_bytes(b"UNCHANGED_PRIVATE_CANARY")
        try:
            os.link(canary, self.root / radar.CACHE_NAME)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unavailable under this Windows filesystem policy")
        self.assertTrue(self.collect(fetch=lambda _: self.fail("No linked input"))["blocked"])
        self.assertEqual(canary.read_bytes(), b"UNCHANGED_PRIVATE_CANARY")


class RepositoryTransportQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".repo-radar-test-", dir=ROOT)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(ROOT.resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.calls = []

    def transport(self, responses):
        from tests.test_internet_review import _Response, _Socket
        from ariadne import internet
        import socket
        context = unittest.mock.Mock()
        context.wrap_socket.side_effect = lambda raw, server_hostname: raw
        connection = unittest.mock.Mock()
        connection.getresponse.side_effect = responses
        patches = [patch.object(internet.socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.215.14", 443))]),
                   patch.object(internet.socket, "socket", side_effect=lambda *args: _Socket(self.calls)),
                   patch.object(internet, "_tls_context", return_value=context),
                   patch.object(internet.http.client, "HTTPSConnection", return_value=connection)]
        started = [item.start() for item in patches]
        for item in reversed(patches):
            self.addCleanup(item.stop)
        return started

    def test_default_public_reader_retains_three_redirect_allowance(self):
        from ariadne import internet
        from tests.test_internet_review import _Response
        resolver, sockets, _, _ = self.transport([_Response(302, {"Location": "/moved"}), _Response(payload=b"valid")])
        result = internet.public_get("https://public.example.com/")
        self.assertEqual(result["payload"], b"valid")
        self.assertEqual(result["url"], "https://public.example.com/moved")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(sockets.call_count, 2)

    def test_radar_zero_redirect_limit_never_resolves_or_contacts_location(self):
        from tests.test_internet_review import _Response
        resolver, sockets, _, _ = self.transport([_Response(302, {"Location": "https://other.example.com/not-allowed"})])
        entries = radar.watchlist()[:1]
        with patch.object(radar, "watchlist", return_value=entries), patch.dict(os.environ, {"ARIADNE_BRIEFING_COLLECTOR": "1", "ARIADNE_OFFLINE": "0"}):
            result = radar.collect_radar(self.root, now=NOW, check=lambda: None, publication_lease=nullcontext)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(sockets.call_count, 1)
        self.assertEqual(resolver.call_args.args[0], "api.github.com")
        self.assertNotIn("other.example.com", json.dumps(result))

    def test_response_limit_applies_to_header_and_stream_before_metadata_parser(self):
        from ariadne import internet
        from tests.test_internet_review import _Response
        self.transport([_Response(headers={"Content-Length": "65"}, payload=b"unused"), _Response(payload=b"x" * 65)])
        with self.assertRaisesRegex(ValueError, "byte limit"):
            internet.public_get("https://public.example.com/", max_bytes=64)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            internet.public_get("https://public.example.com/", max_bytes=64)

    def test_invalid_quota_types_and_values_fail_before_dns(self):
        from ariadne import internet
        with patch.object(internet.socket, "getaddrinfo", side_effect=AssertionError("No DNS")):
            for value in (True, -1, 4, "0", 0.0):
                with self.subTest(max_redirects=value), self.assertRaises(ValueError):
                    internet.public_get("https://public.example.com/", max_redirects=value)
            for value in (True, 0, -1, internet.MAX_BYTES + 1, "256", 1.0):
                with self.subTest(max_bytes=value), self.assertRaises(ValueError):
                    internet.public_get("https://public.example.com/", max_bytes=value)


if __name__ == "__main__":
    unittest.main()
