"""Reuse capture controls for historical NULL liquidity and offline first-body reuse."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ariadne import atomic, options_archive
from ariadne import options_historical_reference as historical
from scripts import options_historical_capture as cli
import test_options_reference_capture as previous
from test_options_historical_reference import records as historical_records
from test_options_recent_capture import evidence


class HistoricalCaptureTests(previous.ReferenceCaptureTests):
    def setUp(self):
        # The inherited ten tests execute against the new CLI without changing
        # the strict collector or importing its TestCase under another name.
        replacement = patch.object(previous, "cli", cli)
        replacement.start()
        self.addCleanup(replacement.stop)
        super().setUp()

    def unknown_sources(self):
        self.supplied = historical_records(unknown=True)
        self.mapping = {row["chain_url"]: evidence(row["chain_raw"], row["chain_url"], row["received_at"])
                        for row in self.supplied}

    def retained(self, *, body=None, mutate=None):
        first = self.supplied[0]
        body = first["chain_raw"] if body is None else body
        failure = {"status": "failed_stopped_no_retry", "GETs_attempted": 1,
                   "reason": "Actual side sizes and volume must be bounded integers.",
                   "paper_operations": 0, "replay_allowed": False,
                   "requests": {"1": {"url": first["chain_url"], "http_status": 200,
                       "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                       "received_at": "2026-11-01T12:30:00Z"}}}
        if mutate is not None:
            mutate(failure)
        receipt = cli._canonical(failure)
        for name, filename, raw in (("retained_body", "retained-first.bin", body),
                                    ("retained_receipt", "retained-failed.json", receipt)):
            relative = "staging/inputs/" + filename
            (self.box / relative).write_bytes(raw)
            self.arguments[name] = relative
            self.arguments[name + "_sha256"] = hashlib.sha256(raw).hexdigest()
        return body, receipt, failure

    def test_retained_complete_first_response_preserves_NULLs_clocks_and_uses_only124_new_GETs(self):
        self.unknown_sources()
        raw, receipt, original = self.retained()
        original_pins = {name: ((self.box / self.arguments[name]).read_bytes(),
                               hashlib.sha256((self.box / self.arguments[name]).read_bytes()).hexdigest())
                         for name in ("retained_body", "retained_receipt")}
        result = self.run_capture()
        self.assertEqual((result["source_records"], result["GETs_attempted"],
                          result["offline_reused_source_records"]), (125, 124, 1))
        self.assertEqual(self.calls, [row["chain_url"] for row in self.supplied[1:]])
        self.assertNotIn(self.supplied[0]["chain_url"], self.calls)
        destination = self.box / result["workspace"]
        self.assertEqual((destination / "retained-first.bin").read_bytes(), raw)
        self.assertEqual((destination / "retained-first-import-failure.json").read_bytes(), receipt)
        first = result["requests"]["1"]
        self.assertEqual(first["acquisition"], "offline_retained_complete_HTTP200_response")
        self.assertNotIn("started_at", first)
        self.assertEqual(first["received_at"], original["requests"]["1"]["received_at"])
        self.assertEqual(first["original_failed_import_sha256"], hashlib.sha256(receipt).hexdigest())
        self.assertEqual((first["unknown_bid_size_quotes"], first["unknown_ask_size_quotes"]), (2, 2))
        self.assertEqual((destination / first["raw_path"]).read_bytes(), raw)
        self.assertEqual(sum(row["acquisition"] == "new_anonymous_GET" for row in result["requests"].values()), 124)
        for name, (body, digest) in original_pins.items():
            self.assertEqual((self.box / self.arguments[name]).read_bytes(), body)
            self.assertEqual(hashlib.sha256(body).hexdigest(), digest)
        workspace = cli._root_guard(destination)
        for profile in historical.CLOCK_PROFILES:
            output = result["outputs"][profile]
            chunks = list(options_archive.iter_archive(workspace, output["catalog_path"],
                expected_manifest_sha256=output["catalog_sha256"], research_symbols=("KO",)))
            self.assertEqual(len(chunks[0]["snapshots"]), 125)
            for row in chunks[0]["snapshots"]:
                for quote in row["quotes"]:
                    self.assertIsNone(quote["bid_size"])
                    self.assertIsNone(quote["ask_size"])
                    self.assertEqual((quote["volume"], quote["open_interest"]), (100000, 500000))
                    self.assertEqual((quote["bid"], quote["ask"]), ("0.6", "0.65"))
            audit = json.loads((destination / profile / "audit.json").read_bytes())
            self.assertEqual((audit["unknown_bid_size_quotes"], audit["unknown_ask_size_quotes"]), (250, 250))
            self.assertTrue(audit["unknown_side_sizes_preserved_as_null"])
            self.assertFalse(audit["side_size_values_coerced_or_inferred"])
            self.assertEqual(audit["source_manifest"]["chain_records"][0]["chain_received_at"], first["received_at"])

    def test_inexact_retained_failure_identity_is_rejected_before_GET_and_reservation(self):
        self.unknown_sources()
        mutations = (lambda f: f.update(GETs_attempted=2), lambda f: f.update(GETs_attempted=True),
                     lambda f: f.update(GETs_attempted=1.0),
                     lambda f: f.update(paper_operations=1), lambda f: f.update(paper_operations=False),
                     lambda f: f.update(paper_operations=0.0),
                     lambda f: f.update(reason="Network timeout"), lambda f: f.update(status="completed"),
                     lambda f: f["requests"]["1"].update(http_status=403),
                     lambda f: f["requests"]["1"].update(http_status=200.0),
                     lambda f: f["requests"]["1"].update(bytes=float(f["requests"]["1"]["bytes"])),
                     lambda f: f["requests"]["1"].update(sha256="0"*64),
                     lambda f: f["requests"]["1"].update(url=f["requests"]["1"]["url"].replace("ticker=KO", "ticker=F")),
                     lambda f: f["requests"].update({"2": deepcopy(f["requests"]["1"])}),
                     lambda f: f["requests"]["1"].update(received_at="not-an-aware-clock"),
                     lambda f: f["requests"]["1"].update(received_at="2026-11-01T12:30:00"),
                     lambda f: f["requests"]["1"].update(received_at="2026-11-01T14:00:01Z"),
                     lambda f: f["requests"]["1"].update(received_at="2026-05-04T19:59:59Z"))
        for index, mutate in enumerate(mutations):
            self.retained(mutate=mutate)
            self.arguments["output"] = "staging/generated-invalid-retained-" + str(index)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.run_capture()
            self.assertEqual(self.calls, [])
            self.assertEqual(self.authorities, [])
            self.assertFalse((self.box / self.arguments["output"]).exists())

    def test_retained_hash_or_incomplete_pair_is_rejected_before_any_owner_or_GET(self):
        self.unknown_sources()
        for field in ("retained_body_sha256", "retained_receipt_sha256"):
            self.retained()
            self.arguments[field] = "0"*64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Retained source bytes changed"):
                self.run_capture()
            self.assertEqual(self.calls, [])
            self.assertFalse((self.box / self.arguments["output"]).exists())
        self.retained()
        del self.arguments["retained_receipt_sha256"]
        with self.assertRaisesRegex(ValueError, "both retained"):
            self.run_capture()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])

    def test_retained_missing_size_FIELD_is_not_unknown_NULL_and_stops_before_new_GET(self):
        self.unknown_sources()
        rows = json.loads(self.supplied[0]["chain_raw"])
        del rows[1]["bid_size"]
        self.retained(body=cli._canonical(rows))
        with self.assertRaises(ValueError):
            self.run_capture()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.authorities, [])
        self.assertFalse((self.box / self.arguments["output"]).exists())

    def test_late_original_retained_receipt_tamper_cannot_complete_new_attempt(self):
        self.unknown_sources()
        _, original_receipt, _ = self.retained()
        original = atomic.publish_bytes
        fired = []
        def publish(destination, body, *, checkpoint=None):
            if Path(destination).name == "completed.json":
                fired.append(True)
                self.tamper(self.box / self.arguments["retained_receipt"])
            return original(destination, body, checkpoint=checkpoint)
        with patch.object(atomic, "publish_bytes", side_effect=publish), self.assertRaises(ValueError):
            self.run_capture()
        self.assertEqual(fired, [True])
        self.failed()
        self.assertEqual((self.box / self.arguments["output"] / "retained-first-import-failure.json").read_bytes(), original_receipt)


if __name__ == "__main__":
    import unittest
    unittest.main()
