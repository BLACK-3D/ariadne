"""Generated quote capture and local-reference controls, never live acquisition."""
from copy import deepcopy
from datetime import datetime, timezone
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
from ariadne import options_reference_history as reference
from scripts import options_reference_capture as cli
from test_options_recent_capture import evidence
from test_options_reference_history import records, stock_fixture, NOW


class ReferenceCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT / "runtime/tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="reference-capture-test-", dir=temporary)
        self.box = Path(self.temporary.name)
        for name in cli.SOURCE_FILES:
            target = self.box / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.owner_path = self.box / "data/ariadne.sqlite3"
        self.owner_path.parent.mkdir(parents=True)
        self.owner_bytes = b"GENERATED SQLITE/TOKEN SENTINEL: NEVER AN OWNER DATABASE"
        self.owner_path.write_bytes(self.owner_bytes)
        self.calls, self.authorities = [], []
        self.revoked, self.on_fetch = False, None
        self.supplied = records()
        self.mapping = {row["chain_url"]: evidence(row["chain_raw"], row["chain_url"], row["received_at"])
                        for row in self.supplied}
        self.write_inputs()

    def tearDown(self):
        try:
            self.assertEqual(self.owner_path.read_bytes(), self.owner_bytes)
            self.assertTrue(all(value.closed for value in self.authorities))
        finally:
            self.temporary.cleanup()

    def write_inputs(self, *, body=None, proof_mutation=None, metadata_mutation=None):
        supplied, meta = stock_fixture()
        body = supplied if body is None else body
        meta["sha256"] = hashlib.sha256(body).hexdigest()
        proof = {"status": "completed_private_reference_qualification", "sha256": meta["sha256"],
                 "url": meta["url"], "received_at": meta["received_at"], "overlap_all_equal": False}
        if proof_mutation is not None:
            proof_mutation(proof)
        proof_bytes = cli._canonical(proof)
        meta["reconciliation_sha256"] = hashlib.sha256(proof_bytes).hexdigest()
        if metadata_mutation is not None:
            metadata_mutation(meta)
        values = {"reference": ("reference.csv", body),
                  "reference_metadata": ("reference-metadata.json", cli._canonical(meta)),
                  "reference_proof": ("reference-proof.json", proof_bytes),
                  "benchmark": ("benchmark.json", b'{"generated_benchmark":"retained exact copy only"}\n')}
        self.inputs = values
        self.arguments = {"output": "staging/generated-reference"}
        for name, (filename, raw) in values.items():
            relative = "staging/inputs/" + filename
            path = self.box / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.arguments[name] = relative
            self.arguments[name + "_sha256"] = hashlib.sha256(raw).hexdigest()

    def authority(self, box):
        parent = self
        class FakeAuthority:
            closed = False
            def check(self):
                if parent.revoked:
                    raise PermissionError("Generated global pause revoked; no retry.")
            def scope(self):
                return {"generated_fixture": True, "internet_permission_pinned": True}
            def close(self):
                self.closed = True
        value = FakeAuthority()
        self.authorities.append(value)
        return value

    def fetch(self, url, *, deadline, check, budget, approved_url):
        check()
        parsed = approved_url(url)
        self.assertEqual((parsed.scheme, parsed.hostname), ("https", transport.HOST))
        self.assertTrue(parsed.path.startswith("/options/"))
        self.calls.append(url)
        if self.on_fetch is not None:
            self.on_fetch(len(self.calls), url)
        result = deepcopy(self.mapping[url])
        budget["requests"] += 1
        budget["remaining"] -= result["bytes"]
        if result["http_status"] != 200:
            error = transport.OptionsEODUnavailable("Generated HTTP403; stop rather than retry.")
            error.source_evidence = result
            raise error
        return result

    def run_capture(self, *, now=NOW, environ="0"):
        with patch.dict(os.environ, {"ARIADNE_OFFLINE": environ}), \
                patch("sqlite3.connect", side_effect=AssertionError("No database access in capture fixtures")), \
                patch("socket.socket", side_effect=AssertionError("No real network sockets in capture fixtures")), \
                patch.object(transport, "_fetch_common", side_effect=AssertionError("No live source transport")):
            return cli.run(SimpleNamespace(**self.arguments), root=self.box, now=now,
                           _fetch=self.fetch, _authority=self.authority)

    def failed(self):
        directory = self.box / self.arguments["output"]
        self.assertFalse((directory / "completed.json").exists())
        value = json.loads((directory / "failed.json").read_bytes())
        self.assertEqual(value["status"], "failed_stopped_no_retry")
        self.assertFalse(value["replay_allowed"])
        self.assertEqual(value["paper_operations"], 0)
        return value

    @staticmethod
    def tamper(path):
        info = path.stat()
        original = path.read_bytes()
        path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        if (path.stat().st_size, path.stat().st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise AssertionError("Generated tamper must preserve size and mtime")

    def test_125_quote_GETs_copy_all_local_reference_evidence_and_exhaust_both_archives(self):
        result = self.run_capture()
        self.assertEqual((result["status"], result["GETs_attempted"]), ("completed", 125))
        self.assertEqual(len(self.calls), len(set(self.calls)))
        self.assertEqual(self.calls, [row["chain_url"] for row in self.supplied])
        self.assertTrue(all("stock-data" not in url and "halfonadouble" not in url for url in self.calls))
        self.assertEqual((result["paper_operations"], result["model_calls"]), (0, 0))
        self.assertFalse(result["automatic_adoption"])
        self.assertFalse(result["execution_qualified"])
        destination = self.box / result["workspace"]
        for filename, original in self.inputs.values():
            self.assertEqual((destination / filename).read_bytes(), original)
        for filename, item in result["artifacts"].items():
            body = (destination / filename).read_bytes()
            self.assertEqual((len(body), hashlib.sha256(body).hexdigest()), (item["bytes"], item["sha256"]))
        intent = json.loads((destination / "intent.json").read_bytes())
        self.assertEqual(intent["reference_network_requests"], 0)
        self.assertEqual(intent["retries"], 0)
        self.assertEqual(intent["urls"], self.calls)
        self.assertEqual(len(result["requests"]), 125)
        workspace = cli._root_guard(destination)
        normalized = []
        for profile in reference.CLOCK_PROFILES:
            output = result["outputs"][profile]
            chunks = list(options_archive.iter_archive(workspace, output["catalog_path"],
                expected_manifest_sha256=output["catalog_sha256"], research_symbols=("KO",)))
            self.assertEqual(len(chunks), 1)
            self.assertEqual(len(chunks[0]["snapshots"]), 125)
            self.assertEqual(sum(len(row["quotes"]) for row in chunks[0]["snapshots"]), 250)
            audit = json.loads((destination / profile / "audit.json").read_bytes())
            self.assertFalse(audit["cross_provider_reconciliation_passed"])
            self.assertFalse(audit["owner_source_replaced"])
            self.assertFalse(audit["execution_qualified"])
            normalized.append(chunks[0])
        self.assertEqual(normalized[0]["source"]["sha256"], normalized[1]["source"]["sha256"])
        self.assertNotEqual(normalized[0]["snapshots"][0]["available_at"], normalized[1]["snapshots"][0]["available_at"])

    def test_old_window_rejects_before_GET_authority_or_output(self):
        with self.assertRaisesRegex(ValueError, "2026-11-01"):
            self.run_capture(now=datetime(2027, 11, 1, 14, tzinfo=timezone.utc))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertFalse((self.box / self.arguments["output"]).exists())

    def test_false_or_unbound_reference_proof_is_not_admitted_preGET(self):
        mutations = (lambda p: p.update(overlap_all_equal=True), lambda p: p.update(sha256="0"*64),
                     lambda p: p.update(status="partial"), lambda p: p.update(url="https://example.com/F.csv"),
                     lambda p: p.update(received_at="2026-11-01T11:00:00Z"))
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.write_inputs(proof_mutation=mutate)
                with self.assertRaisesRegex(ValueError, "source-equivalence evidence"):
                    self.run_capture()
                self.assertEqual(self.calls, [])
                self.assertEqual(self.authorities, [])
                self.assertFalse((self.box / self.arguments["output"]).exists())
        self.write_inputs(metadata_mutation=lambda meta: meta.update(reconciliation_sha256="0"*64))
        with self.assertRaisesRegex(ValueError, "source-equivalence evidence"):
            self.run_capture()
        self.assertEqual(self.calls, [])

    def test_unusable_whole_CSV_rejects_even_when_all_local_proof_hashes_match(self):
        body, _ = stock_fixture()
        self.write_inputs(body=body.replace(b"20.12345678500000000", b"NaN"))
        with self.assertRaises(ValueError):
            self.run_capture()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertFalse((self.box / self.arguments["output"]).exists())

    def test_offline_and_global_pause_each_stop_before_any_quote_request(self):
        with self.assertRaises(PermissionError):
            self.run_capture(environ="1")
        self.assertEqual(self.failed()["GETs_attempted"], 0)
        self.assertEqual(self.calls, [])
        self.arguments["output"] = "staging/generated-global-pause"
        self.revoked = True
        with self.assertRaises(PermissionError):
            self.run_capture()
        self.assertEqual(self.failed()["GETs_attempted"], 0)
        self.assertEqual(self.calls, [])

    def test_revoked_authority_retains_inflight_response_but_never_requests_next_day(self):
        def revoke(ordinal, url):
            if ordinal == 3:
                self.revoked = True
        self.on_fetch = revoke
        with self.assertRaises(PermissionError):
            self.run_capture()
        value = self.failed()
        self.assertEqual((value["GETs_attempted"], len(self.calls)), (3, 3))
        record = value["requests"]["3"]
        self.assertEqual((self.box / self.arguments["output"] / record["raw_path"]).read_bytes(),
                         self.supplied[2]["chain_raw"])

    def test_http403_is_evidenced_without_retry_alternate_expiry_or_completion(self):
        url = self.supplied[1]["chain_url"]
        body = b'{"generated_error":"HTTP403 denied"}'
        self.mapping[url] = evidence(body, url, self.supplied[1]["received_at"])
        self.mapping[url]["http_status"] = 403
        with self.assertRaises(transport.OptionsEODUnavailable):
            self.run_capture()
        value = self.failed()
        self.assertEqual(self.calls, [row["chain_url"] for row in self.supplied[:2]])
        row = value["requests"]["2"]
        self.assertEqual(row["http_status"], 403)
        self.assertEqual((self.box / self.arguments["output"] / row["raw_path"]).read_bytes(), body)

    def test_crossed_or_duplicate_quote_body_stops_before_another_request(self):
        first = self.supplied[0]
        original = json.loads(first["chain_raw"])
        for label in ("crossed", "duplicate"):
            rows = deepcopy(original)
            if label == "crossed":
                rows[1]["bid"] = 1
            else:
                rows.append(deepcopy(rows[1]))
            body = cli._canonical(rows)
            self.mapping[first["chain_url"]] = evidence(body, first["chain_url"], first["received_at"])
            self.arguments["output"] = "staging/generated-invalid-" + label
            self.calls.clear()
            with self.subTest(body=label), self.assertRaises(ValueError):
                self.run_capture()
            self.assertEqual(self.calls, [first["chain_url"]])
            stopped = self.failed()
            self.assertEqual(stopped["GETs_attempted"], 1)
            row = stopped["requests"]["1"]
            self.assertEqual((self.box / self.arguments["output"] / row["raw_path"]).read_bytes(), body)

    def test_existing_or_interrupted_output_never_replays_or_overwrites(self):
        destination = self.box / self.arguments["output"]
        destination.mkdir(parents=True)
        marker = b'{"generated":"previous interrupted attempt"}'
        (destination / "intent.json").write_bytes(marker)
        with self.assertRaises(FileExistsError):
            self.run_capture()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertEqual((destination / "intent.json").read_bytes(), marker)
        self.assertEqual(sorted(path.name for path in destination.iterdir()), ["intent.json"])

    def test_final_atomic_checkpoint_rejects_same_size_restored_mtime_artifact_and_source_edits(self):
        original = atomic.publish_bytes
        for target in ("intent.json", "../source"):
            self.arguments["output"] = "staging/generated-tamper-" + ("artifact" if target == "intent.json" else "source")
            fired = []
            def publish(destination, body, *, checkpoint=None):
                if Path(destination).name == "completed.json":
                    fired.append(True)
                    path = (self.box / self.arguments["output"] / "intent.json"
                            if target == "intent.json" else self.box / "ariadne/internet.py")
                    self.tamper(path)
                return original(destination, body, checkpoint=checkpoint)
            with self.subTest(target=target), patch.object(atomic, "publish_bytes", side_effect=publish):
                with self.assertRaises(ValueError):
                    self.run_capture()
                self.assertEqual(fired, [True])
                self.failed()


if __name__ == "__main__":
    unittest.main()
