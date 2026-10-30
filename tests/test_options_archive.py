"""Immutable catalog identity, global bounds and cross-file chronology checks."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.options_archive import (MAX_ARCHIVE, MAX_MANIFEST, inspect_archive,
                                     iter_archive, load_manifest, parse_manifest)
from ariadne.options_data import canonical
from ariadne.qqq_data import exchange_day
from ariadne.options_data import clock
from ariadne.tools import ToolRegistry
from test_options_recovery import fixture


class OptionsArchiveTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="options-archive-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name) / "data")
        self.registry = ToolRegistry(self.settings)
        self.dataset = fixture(8)
        first, last = self.dataset["snapshots"][0], self.dataset["snapshots"][-1]
        self.manifest = {"schema":1, "as_of":self.dataset["as_of"],
                         "evidence_kind":self.dataset["evidence_kind"], "symbol":"QQQ",
                         "source":deepcopy(self.dataset["source"]),
                         "selection":{"start":exchange_day(clock(first["observed_at"])).isoformat(),
                                      "end":exchange_day(clock(last["observed_at"])).isoformat(),
                                      "max_dte":None, "reason":"Fixed synthetic test interval; no market evidence."},
                         "limitations":["Synthetic fixtures cannot establish market returns."], "chunks":[]}
        for index in range(0, 8, 2):
            value = deepcopy(self.dataset)
            value["snapshots"] = value["snapshots"][index:index + 2]
            self.add_chunk(value, "imports/part-" + str(index) + ".json")
        self.path = "imports/catalog.json"
        self.save_manifest()

    def write(self, path, raw):
        target = self.registry._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def save_manifest(self):
        self.raw = canonical(self.manifest)
        self.write(self.path, self.raw)
        self.digest = hashlib.sha256(self.raw).hexdigest()

    def add_chunk(self, dataset, path):
        raw = canonical(dataset)
        self.write(path, raw)
        snapshots = dataset["snapshots"]
        row = {"path":path, "sha256":hashlib.sha256(raw).hexdigest(), "bytes":len(raw),
               "snapshots":len(snapshots), "quotes":sum(len(s["quotes"]) for s in snapshots),
               "first_observed_at":snapshots[0]["observed_at"], "last_observed_at":snapshots[-1]["observed_at"]}
        self.manifest["chunks"].append(row)
        return row

    def replace_chunk(self, index, mutate):
        chunk = self.manifest["chunks"][index]
        value = json.loads(self.registry._path(chunk["path"]).read_bytes())
        mutate(value)
        raw = canonical(value)
        self.write(chunk["path"], raw)
        chunk.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        self.save_manifest()

    def reject(self, mutate):
        value = deepcopy(self.manifest)
        mutate(value)
        with self.assertRaises(ValueError):
            parse_manifest(canonical(value))

    def test_streamed_chunks_retain_exact_input_and_compact_hash_identity_offline(self):
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            chunks = list(iter_archive(self.registry, self.path, expected_manifest_sha256=self.digest))
            result = inspect_archive(self.registry, self.path)
        self.assertEqual([s for chunk in chunks for s in chunk["snapshots"]], self.dataset["snapshots"])
        self.assertEqual(result["manifest_sha256"], self.digest)
        self.assertEqual(result["manifest_bytes"], len(self.raw))
        self.assertEqual(result["quotes"], 8)
        self.assertEqual(result["session_gaps"], 0)
        self.assertEqual(result["chunks"], self.manifest["chunks"])
        self.assertFalse(result["execution_qualified"])
        self.assertFalse(result["forward_eligible"])
        self.assertEqual(load_manifest(self.registry, self.path), (self.raw, self.manifest))

    def test_changed_manifest_rejected_before_reading_any_chunk(self):
        self.write(self.path, self.raw + b" ")
        with patch.object(self.registry, "_open_read", wraps=self.registry._open_read) as opened:
            with self.assertRaises(ValueError):
                list(iter_archive(self.registry, self.path, expected_manifest_sha256=self.digest))
        self.assertEqual(opened.call_count, 1)

    def test_chunk_whitespace_tampering_rejected_even_when_json_is_equivalent(self):
        chunk = self.manifest["chunks"][0]
        self.write(chunk["path"], self.registry._path(chunk["path"]).read_bytes() + b" ")
        with self.assertRaises(ValueError):
            inspect_archive(self.registry, self.path)

    def test_same_length_chunk_tampering_rejected(self):
        chunk = self.manifest["chunks"][0]
        raw = self.registry._path(chunk["path"]).read_bytes()
        changed = raw.replace(b'"bid":"1"', b'"bid":"2"')
        self.assertNotEqual(raw, changed)
        self.write(chunk["path"], changed)
        with self.assertRaises(ValueError):
            inspect_archive(self.registry, self.path)

    def test_duplicate_and_case_aliased_paths_rejected(self):
        self.reject(lambda v:v["chunks"][1].update(path=v["chunks"][0]["path"]))
        self.reject(lambda v:v["chunks"][1].update(path=v["chunks"][0]["path"].upper()))

    def test_unsafe_paths_rejected_before_filesystem_access(self):
        for path in ("../outside.json", "C:/outside.json", "E:/forbidden.json", "//server/share.json", "imports/NUL.json", "imports/part.json:stream", "imports/part.json.", "imports\\part.json"):
            with self.subTest(path=path):
                self.reject(lambda v,p=path:v["chunks"][0].update(path=p))

    def test_global_bytes_and_chunk_count_are_bounded(self):
        value = deepcopy(self.manifest)
        value["chunks"] = []
        from datetime import datetime, timedelta, timezone
        start = datetime(2020,1,1,tzinfo=timezone.utc)
        for index in range(17):
            stamp = (start + timedelta(days=index)).isoformat()
            value["chunks"].append({"path":f"imports/{index}.json", "sha256":"a" * 64,
                                    "bytes":32 * 1024 * 1024, "snapshots":1,"quotes":1,
                                    "first_observed_at":stamp,"last_observed_at":stamp})
        with self.assertRaises(ValueError):parse_manifest(canonical(value))
        value["chunks"] = value["chunks"][:1] * 97
        with self.assertRaises(ValueError):parse_manifest(canonical(value))

    def test_noninteger_boolean_counts_and_oversize_chunk_rejected(self):
        for field, values in (("bytes", (True, 0, 32 * 1024 * 1024 + 1)), ("quotes", (True, 0, 200001)), ("snapshots", (True, 0, 4001))):
            for value in values:
                with self.subTest(field=field,value=value):
                    self.reject(lambda v,f=field,x=value:v["chunks"][0].update({f:x}))

    def test_duplicate_keys_nonfinite_invalid_utf8_and_size_rejected(self):
        for raw in (self.raw.replace(b'"schema":1', b'"schema":1,"schema":1'), b'{"x":NaN}', b"\xff", b" " * (MAX_MANIFEST + 1), "{}"):
            with self.subTest(raw_type=type(raw)), self.assertRaises(ValueError):parse_manifest(raw)

    def test_unknown_keys_and_false_verification_claims_rejected(self):
        for location in (lambda v:v, lambda v:v["selection"], lambda v:v["chunks"][0], lambda v:v["source"]):
            self.reject(lambda v,loc=location:loc(v).update(unreviewed=True))
        self.reject(lambda v:v["source"].update(independently_verified=True))

    def test_declared_coverage_lie_rejected(self):
        self.manifest["chunks"][0]["quotes"] = 3
        self.save_manifest()
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_cross_chunk_source_basis_switch_rejected(self):
        self.replace_chunk(1, lambda value:value["source"].update(quote_time_basis="assumed_eod"))
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_cross_chunk_provider_switch_rejected(self):
        self.replace_chunk(1, lambda value:value["source"].update(provider="Another feed"))
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_cross_chunk_evidence_kind_switch_rejected(self):
        self.replace_chunk(1, lambda value:value.update(evidence_kind="historical_quotes"))
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_selected_session_missing_inside_chunk_is_rejected(self):
        chunk = self.manifest["chunks"][1]
        self.replace_chunk(1, lambda value:value["snapshots"].pop())
        chunk.update(snapshots=1,quotes=1,last_observed_at=chunk["first_observed_at"])
        self.save_manifest()
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_selected_terminal_session_missing_is_rejected_after_exhaustion(self):
        self.manifest["chunks"].pop()
        self.save_manifest()
        with self.assertRaises(ValueError):list(iter_archive(self.registry,self.path))

    def test_overlapping_or_reversed_chunk_intervals_rejected(self):
        self.reject(lambda v:v["chunks"][1].update(first_observed_at=v["chunks"][0]["last_observed_at"]))
        self.reject(lambda v:v["chunks"].reverse())

    def test_predeclared_dte_scope_is_enforced(self):
        self.manifest["selection"]["max_dte"] = 1
        self.save_manifest()
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)

    def test_zero_quotes_and_missing_size_remain_in_quality_counts(self):
        self.replace_chunk(0, lambda value:value["snapshots"][0]["quotes"][0].update(bid="0",ask="0",bid_size=None,ask_size=0))
        result = inspect_archive(self.registry,self.path)
        counts = result["quote_quality_counts"]
        self.assertEqual((counts["zero_bid_quotes"],counts["zero_ask_quotes"],counts["missing_bid_size_quotes"],counts["zero_ask_size_quotes"]),(1,1,1,1))
        self.assertEqual(result["quotes"],8)

    def test_checkpoint_revocation_stops_before_next_chunk(self):
        calls=[]
        def checkpoint():
            calls.append(True)
            if len(calls)==4:raise RuntimeError("Owner revoked")
        with self.assertRaisesRegex(RuntimeError,"Owner revoked"):
            list(iter_archive(self.registry,self.path,checkpoint=checkpoint))
        self.assertEqual(len(calls),4)

    def test_linked_chunk_is_rejected_by_guarded_reader(self):
        chunk=self.manifest["chunks"][0]
        target=self.registry._path(chunk["path"])
        linked=target.with_name("hardlink.json")
        os.link(target,linked)
        with self.assertRaises(ValueError):inspect_archive(self.registry,self.path)
