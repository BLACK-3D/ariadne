"""Generated finite capture fixtures; no network, databases or owner actions."""
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ariadne import atomic, options_archive
from ariadne import options_eod_data as transport
from ariadne import options_recent_history as recent
from scripts import options_recent_capture as cli
from test_options_recent_history import records, NOW, raw


def evidence(body, url, received_at):
    return {"url": url, "received_at": received_at,
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "http_status": 200, "content_base64": base64.b64encode(body).decode("ascii")}


class RecentCaptureTests(unittest.TestCase):
    def setUp(self):
        # All filesystem mutations are confined to a generated box inside Yikes.
        temporary = ROOT / "runtime" / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="recent-capture-test-", dir=temporary)
        self.box = Path(self.temporary.name)
        for name in cli.SOURCE_FILES:
            destination = self.box / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.owner_path = self.box / "data" / "ariadne.sqlite3"
        self.owner_path.parent.mkdir(parents=True)
        self.owner_sentinel = b"GENERATED OWNER SENTINEL: MUST NEVER BE OPENED AS SQLITE"
        self.owner_path.write_bytes(self.owner_sentinel)
        self.calls, self.authorities = [], []
        self.revoked, self.before_fetch = False, None
        self.mapping = {}
        self.supplied = records()
        for record in self.supplied:
            self.mapping[record["stock_url"]] = evidence(record["stock_raw"], record["stock_url"], record["stock_received_at"])
            self.mapping[record["chain_url"]] = evidence(record["chain_raw"], record["chain_url"], record["received_at"])
            url = recent.source.expiration_url(record["source_date"])
            self.mapping[url] = evidence(raw({"call": [recent.EXPIRY], "put": [recent.EXPIRY]}), url, record["received_at"])

    def tearDown(self):
        try:
            self.assertEqual(self.owner_path.read_bytes(), self.owner_sentinel)
            self.assertTrue(all(authority.closed for authority in self.authorities))
        finally:
            self.temporary.cleanup()

    def authority(self, box):
        parent = self
        class FakeAuthority:
            closed = False
            checks = 0
            def check(self):
                self.checks += 1
                if parent.revoked:
                    raise PermissionError("Generated pause epoch revoked; no retry.")
            def scope(self):
                return {"generated_fixture": True, "internet_permission_pinned": True}
            def close(self):
                self.closed = True
        value = FakeAuthority()
        self.authorities.append(value)
        return value

    def fetch(self, url, *, deadline, check, budget, approved_url):
        check()
        self.assertEqual(approved_url(url).hostname, transport.HOST)
        self.assertGreater(deadline, 0)
        self.calls.append(url)
        if self.before_fetch is not None:
            self.before_fetch(len(self.calls), url)
        result = deepcopy(self.mapping[url])
        budget["requests"] += 1
        budget["remaining"] -= result["bytes"]
        if result["http_status"] != 200:
            error = transport.OptionsEODUnavailable("Generated HTTP403; no retry.")
            error.source_evidence = result
            raise error
        return result

    def args(self, *, output="staging/generated-recent", benchmark=None, benchmark_sha256=None):
        return SimpleNamespace(output=output, benchmark=benchmark, benchmark_sha256=benchmark_sha256)

    def run_capture(self, args=None, *, now=NOW):
        with patch.dict(os.environ, {"ARIADNE_OFFLINE": "0"}), \
                patch("sqlite3.connect", side_effect=AssertionError("No test owner database access")), \
                patch.object(transport, "_fetch_common", side_effect=AssertionError("No real transport")):
            return cli.run(self.args() if args is None else args, root=self.box, now=now,
                           _fetch=self.fetch, _authority=self.authority)

    def failed(self, output="staging/generated-recent"):
        directory = self.box / output
        self.assertFalse((directory / "completed.json").exists())
        result = json.loads((directory / "failed.json").read_bytes())
        self.assertEqual(result["status"], "failed_stopped_no_retry")
        self.assertFalse(result["replay_allowed"])
        self.assertEqual(result["paper_operations"], 0)
        return result

    @staticmethod
    def change_same_size(path):
        before = path.stat()
        body = path.read_bytes()
        index = next(i for i, byte in enumerate(body) if byte not in (10, 13))
        changed = body[:index] + bytes((body[index] ^ 1,)) + body[index+1:]
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        if path.stat().st_size != before.st_size or path.stat().st_mtime_ns != before.st_mtime_ns:
            raise AssertionError("Generated tamper must preserve recorded length and mtime")

    def test_complete54GETs_both_profiles_full_artifacts_and_guarded_archive(self):
        benchmark = self.box / "staging" / "benchmark.json"
        benchmark.parent.mkdir(parents=True)
        benchmark_bytes = b'{"generated_benchmark":"copy exact bytes, no parsing or acquisition"}\n'
        benchmark.write_bytes(benchmark_bytes)
        digest = hashlib.sha256(benchmark_bytes).hexdigest()
        result = self.run_capture(self.args(benchmark="staging/benchmark.json", benchmark_sha256=digest))
        self.assertEqual((result["status"], result["GETs_attempted"]), ("completed", 54))
        self.assertEqual(len(self.calls), 54)
        self.assertEqual(len(set(self.calls)), 54)
        self.assertEqual(result["paper_operations"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["automatic_adoption"])
        self.assertFalse(result["execution_qualified"])
        self.assertFalse(result["replay_allowed"])
        self.assertEqual((self.box / result["workspace"] / "benchmark.json").read_bytes(), benchmark_bytes)
        for name, item in result["artifacts"].items():
            body = (self.box / result["workspace"] / name).read_bytes()
            self.assertEqual((len(body), hashlib.sha256(body).hexdigest()), (item["bytes"], item["sha256"]))
        ordered = json.loads((self.box / result["workspace"] / "intent.json").read_bytes())["requested_urls_in_order"]
        self.assertEqual(self.calls, ordered)
        self.assertEqual([row["ordinal"] for row in result["requests"]], list(range(1, 55)))
        self.assertTrue(all(row["http_status"] == 200 for row in result["requests"]))
        self.assertTrue(all(row["partial_chain"] for row in result["requests"][3::2]))
        workspace = cli._root_guard(self.box / result["workspace"])
        datasets = []
        for profile in recent.CLOCK_PROFILES:
            output = result["outputs"][profile]
            parts = list(options_archive.iter_archive(workspace, output["catalog_path"],
                expected_manifest_sha256=output["catalog_sha256"], research_symbols=("KO",)))
            self.assertEqual(len(parts), 1)
            dataset = parts[0]
            self.assertEqual(len(dataset["snapshots"]), 26)
            self.assertEqual(sum(len(row["quotes"]) for row in dataset["snapshots"]), 52)
            self.assertTrue(all(row["underlying_symbol"] == "KO" for row in dataset["snapshots"]))
            datasets.append(dataset)
        self.assertEqual(datasets[0]["source"]["sha256"], datasets[1]["source"]["sha256"])
        self.assertNotEqual(datasets[0]["snapshots"][0]["available_at"], datasets[1]["snapshots"][0]["available_at"])

    def test_out_of_window_stops_before_GET_or_output_reservation(self):
        with self.assertRaisesRegex(ValueError, "2026-11-01"):
            self.run_capture(now=datetime(2027, 11, 1, 14, tzinfo=timezone.utc))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertFalse((self.box / "staging/generated-recent").exists())

    def test_explicit_offline_environment_blocks_all_GETs_and_records_stop(self):
        with patch.dict(os.environ, {"ARIADNE_OFFLINE": "1"}):
            with self.assertRaises(PermissionError):
                cli.run(self.args(), root=self.box, now=NOW, _fetch=self.fetch, _authority=self.authority)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.failed()["GETs_attempted"], 0)

    def test_midcollection_revocation_stops_without_another_GET_or_retry(self):
        def revoke(ordinal, url):
            if ordinal == 3:
                self.revoked = True
        self.before_fetch = revoke
        with self.assertRaises(PermissionError):
            self.run_capture()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.failed()["GETs_attempted"], 3)
        self.assertEqual(len(set(self.calls)), len(self.calls))

    def test_http403_preserves_original_error_body_and_stops_no_fallback(self):
        first_chain = self.supplied[0]["chain_url"]
        body = b'{"generated_denial":"HTTP403: no alternate route"}'
        self.mapping[first_chain] = evidence(body, first_chain, self.supplied[0]["received_at"])
        self.mapping[first_chain]["http_status"] = 403
        with self.assertRaises(transport.OptionsEODUnavailable):
            self.run_capture()
        result = self.failed()
        self.assertEqual(len(self.calls), 4)
        denied = result["requests"][-1]
        self.assertEqual(denied["http_status"], 403)
        self.assertEqual((self.box / "staging/generated-recent" / denied["raw_path"]).read_bytes(), body)
        self.assertEqual(denied["sha256"], hashlib.sha256(body).hexdigest())

    def test_missing_fixed_expiry_stops_before_chain_without_replacement(self):
        url = recent.source.expiration_url(recent.DAYS[0])
        self.mapping[url] = evidence(raw({"call": ["2026-11-20"], "put": ["2026-11-20"]}), url, self.supplied[0]["received_at"])
        with self.assertRaises(ValueError):
            self.run_capture()
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn(self.supplied[0]["chain_url"], self.calls)
        self.failed()

    def test_invalid_whole_stock_grid_stops_before_second_stock_or_chains(self):
        url = self.supplied[0]["stock_url"]
        self.mapping[url] = evidence(b"[]", url, self.supplied[0]["stock_received_at"])
        with self.assertRaises(ValueError):
            self.run_capture()
        self.assertEqual(self.calls, [url])
        self.failed()

    def test_source_same_length_restored_mtime_tampering_is_rejected_after_lease(self):
        target = self.box / "ariadne/internet.py"
        def tamper(ordinal, url):
            if ordinal == 4:
                self.change_same_size(target)
        self.before_fetch = tamper
        with self.assertRaises(ValueError):
            self.run_capture()
        self.failed()

    def test_existing_interrupted_output_is_never_replayed_or_overwritten(self):
        destination = self.box / "staging/generated-recent"
        destination.mkdir(parents=True)
        marker = b'{"generated":"interrupted existing attempt"}'
        (destination / "intent.json").write_bytes(marker)
        with self.assertRaises(FileExistsError):
            self.run_capture()
        self.assertEqual((destination / "intent.json").read_bytes(), marker)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertEqual(sorted(path.name for path in destination.iterdir()), ["intent.json"])

    def test_benchmark_hash_mismatch_blocks_GET_and_output_reservation(self):
        path = self.box / "staging/benchmark.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"generated benchmark")
        with self.assertRaisesRegex(ValueError, "benchmark bytes changed"):
            self.run_capture(self.args(benchmark="staging/benchmark.json", benchmark_sha256="0"*64))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.box / "staging/generated-recent").exists())

    def test_late_artifact_tamper_at_final_publication_cannot_claim_completion(self):
        original = atomic.publish_bytes
        fired = []
        def publish(destination, body, *, checkpoint=None):
            if Path(destination).name == "completed.json":
                fired.append(True)
                self.change_same_size(self.box / "staging/generated-recent/intent.json")
            return original(destination, body, checkpoint=checkpoint)
        with patch.object(atomic, "publish_bytes", side_effect=publish):
            with self.assertRaises(ValueError):
                self.run_capture()
        self.assertEqual(fired, [True])
        self.failed()

    def test_late_source_tamper_at_final_publication_cannot_claim_source_stable(self):
        original = atomic.publish_bytes
        fired = []
        def publish(destination, body, *, checkpoint=None):
            if Path(destination).name == "completed.json":
                fired.append(True)
                self.change_same_size(self.box / "ariadne/internet.py")
            return original(destination, body, checkpoint=checkpoint)
        with patch.object(atomic, "publish_bytes", side_effect=publish):
            with self.assertRaises(ValueError):
                self.run_capture()
        self.assertEqual(fired, [True])
        self.failed()


if __name__ == "__main__":
    unittest.main()
