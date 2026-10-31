"""Offline research publication must bind every input/output and preserve stops."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ariadne.atomic import publish_bytes as atomic_publish
from ariadne.options_data import canonical
from scripts import options_universe as cli
from test_options_universe_data import stock_fixture


def digest(raw): return hashlib.sha256(raw).hexdigest()


class UniverseCLITests(unittest.TestCase):
    def setUp(self):
        cli._TIMELINES.clear()
        parent = cli.ROOT / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="universe-cli-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in cli.SOURCE_FILES:
            destination = self.root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((cli.ROOT/name).read_bytes())
        self.workspace = self.root/"workspace"; self.workspace.mkdir()
        data = stock_fixture()
        raw = canonical(data); (self.workspace/"part.json").write_bytes(raw)
        rows = data["snapshots"]
        manifest = {"schema": 1, "as_of": data["as_of"], "evidence_kind": data["evidence_kind"],
            "symbol": "F", "source": data["source"], "selection": {"start": "2024-01-02", "end": "2024-01-05",
                "max_dte": None, "reason": "Generated controller integrity fixture, no market results."},
            "limitations": ["Synthetic fixture."], "chunks": [{"path": "part.json", "sha256": digest(raw),
                "bytes": len(raw), "snapshots": 4, "quotes": 4, "first_observed_at": rows[0]["observed_at"],
                "last_observed_at": rows[-1]["observed_at"]}]}
        manifest_raw = canonical(manifest); (self.workspace/"catalog.json").write_bytes(manifest_raw)
        (self.workspace/"benchmark.json").write_bytes(b'{"test_fixture":true}')
        (self.root/"intent.json").write_bytes(b'{"schema":1,"purpose":"synthetic fixture"}')
        self.args = cli.parser().parse_args(["--manifest", "catalog.json", "--manifest-sha256", digest(manifest_raw),
            "--benchmark", "benchmark.json", "--benchmark-sha256", digest((self.workspace/"benchmark.json").read_bytes()),
            "--intent", "intent.json", "--intent-sha256", digest((self.root/"intent.json").read_bytes()),
            "--workspace", "workspace", "--detached-research", "--output", "staging/run"])
        self.consume = None
        self.seen = []

    def _engine(self):
        def study(streams, **kwargs):
            self.seen.append(kwargs)
            for stream in streams.values():
                list(stream)
            if self.consume: self.consume()
            return {"source_streams_exhausted": True, "research_only": True, "profiles": {}}
        return SimpleNamespace(__file__=str(cli.ROOT/"ariadne/options_universe.py"), RULE_SHA256="a"*64,
            profile_rule_sha256=lambda preset, profile: digest((preset+profile).encode()), universe_campaign=study)

    def _run(self):
        context = SimpleNamespace(describe=lambda: {"test_fixture": True},
            at=lambda stamp: {"status": "unavailable", "test_fixture": True},
            analogs=lambda stamp: {"matches": [], "test_fixture": True})
        with patch("ariadne.options_regime.build_timeline", return_value=context), \
             patch("importlib.import_module", return_value=self._engine()):
            return cli.run(self.args, root=self.root, now=datetime(2026,10,31,15,tzinfo=timezone.utc))

    def _failed(self):
        with self.assertRaises((ValueError, OSError)):
            self._run()
        self.assertFalse((self.root/"staging/run/completed.json").exists())
        return json.loads((self.root/"staging/run/failed.json").read_bytes())

    def test_completed_controller_binds_full_inputs_outputs_and_zero_owner_operations(self):
        result = self._run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["symbols"], ["F"])
        self.assertEqual(result["paper_operations"], 0)
        for name, identity in result["artifacts"].items():
            raw = (self.root/"staging/run"/name).read_bytes()
            self.assertEqual((len(raw), digest(raw)), (identity["bytes"], identity["sha256"]))
        registration = json.loads((self.root/"staging/run/registration.json").read_bytes())
        self.assertEqual(registration["owner_authority"]["mode"], "detached_research_owner_unchecked")
        self.assertEqual(registration["symbols"], ["F"])
        self.assertFalse((self.root/"data/ariadne.sqlite3").exists())

    def test_completed_or_partial_folder_is_never_reused(self):
        self._run()
        completed = (self.root/"staging/run/completed.json").read_bytes()
        with self.assertRaises(FileExistsError): self._run()
        self.assertEqual((self.root/"staging/run/completed.json").read_bytes(), completed)

    def test_detached_flag_cannot_bypass_an_existing_owner_database(self):
        (self.root/"data").mkdir()
        owner = self.root/"data/ariadne.sqlite3"; owner.write_bytes(b"Never open or initialize this file.")
        with self.assertRaisesRegex(ValueError, "bypass"):
            self._run()
        self.assertEqual(owner.read_bytes(), b"Never open or initialize this file.")
        self.assertFalse((self.root/"staging/run").exists())

    def test_alternative_owner_path_and_nonboolean_detached_are_refused(self):
        self.args.owner_data = "elsewhere"
        with self.assertRaisesRegex(ValueError, "bypass"): self._run()
        self.args.owner_data = "data"; self.args.detached_research = 1
        with self.assertRaisesRegex(ValueError, "boolean"): self._run()

    def test_wrong_manifest_or_benchmark_or_intent_digest_never_runs_study(self):
        self.args.benchmark_sha256 = "0"*64
        failure = self._failed()
        self.assertIn("Benchmark", failure["reason"])
        self.assertEqual(self.seen, [])

    def test_duplicate_and_nonfinite_metadata_are_rejected(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"schema":1,"value":NaN}', b'[]', b'\xff'):
            with self.subTest(raw=raw):
                if raw == b'[]':
                    self.assertEqual(cli._json(raw), [])
                else:
                    with self.assertRaises(ValueError): cli._json(raw)

    def test_missing_final_stock_session_invalidates_entire_attempt(self):
        data = stock_fixture(); data["snapshots"].pop()
        raw = canonical(data); (self.workspace/"part.json").write_bytes(raw)
        value = json.loads((self.workspace/"catalog.json").read_bytes())
        value["chunks"][0].update(bytes=len(raw), sha256=digest(raw), snapshots=3, quotes=3,
            last_observed_at=data["snapshots"][-1]["observed_at"])
        raw = canonical(value); (self.workspace/"catalog.json").write_bytes(raw)
        self.args.manifest_sha256 = [digest(raw)]
        failure = self._failed()
        self.assertIn("missing selected", failure["reason"])

    def test_source_same_length_restored_timestamp_tamper_prevents_completion(self):
        path = self.workspace/"part.json"
        def change():
            info = path.stat(); raw = path.read_bytes()
            changed = raw.replace(b'"bid":"0.5"', b'"bid":"0.4"', 1)
            self.assertNotEqual(raw, changed)
            path.write_bytes(changed); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.consume = change
        failure = self._failed()
        self.assertIn("source bytes changed", failure["reason"])

    def test_earlier_artifact_tamper_in_final_publication_checkpoint_is_detected(self):
        def publish(path, content, **kwargs):
            if path.name == "completed.json":
                result = path.parent/"results.json"
                info = result.stat(); value = result.read_bytes()
                changed = value.replace(b'true', b'null', 1)
                self.assertEqual(len(value), len(changed)); self.assertNotEqual(value, changed)
                result.write_bytes(changed); os.utime(result, ns=(info.st_atime_ns, info.st_mtime_ns))
            return atomic_publish(path, content, **kwargs)
        with patch("ariadne.atomic.publish_bytes", side_effect=publish):
            failure = self._failed()
        self.assertIn("Earlier output changed", failure["reason"])

    def test_unexhausted_stock_stream_cannot_publish_results(self):
        engine = self._engine()
        engine.universe_campaign = lambda *a, **k: {"source_streams_exhausted": True}
        with patch.object(self, "_engine", return_value=engine):
            failure = self._failed()
        self.assertIn("Every stock source", failure["reason"])

    def test_output_quota_is_not_satisfied_by_truncating_results(self):
        with patch.object(cli, "MAX_OUTPUT", 8192), patch.object(cli, "FAILURE_RESERVE", 1024):
            failure = self._failed()
        self.assertIn("output exceeds", failure["reason"])

    def test_offline_guards_are_installed_before_parser_or_run(self):
        with patch.object(cli, "install_offline_guards", side_effect=PermissionError("stopped")), \
             patch.object(cli, "parser") as parser, patch.object(cli, "run") as run:
            with self.assertRaises(PermissionError): cli.main([])
        parser.assert_not_called(); run.assert_not_called()

    def test_individual_mode_keeps_stock_inputs_and_capital_comparisons_separate(self):
        self.args.mode = "individual"
        result = self._run()
        self.assertEqual(result["mode"], "individual")
        body = json.loads((self.root/"staging/run/results.json").read_bytes())
        self.assertEqual(list(body["individual_results"]), ["F"])
        self.assertEqual(self.seen[0]["symbols"], ("F",))
        self.assertIn("Never add capital", body["capital_scope"])

    def test_individual_mode_cannot_raise_peak_ledger_or_output_budgets(self):
        self.args.mode = "individual"
        self.args.manifest = [f"catalog-{index}.json" for index in range(9)]
        self.args.manifest_sha256 = ["0"*64]*9
        with self.assertRaisesRegex(ValueError, "at most8"):
            self._run()
        self.assertFalse((self.root/"staging/run").exists())

    def test_context_cache_binds_raw_bytes_code_and_clock_with_two_entry_bound(self):
        calls, checkpoints = [], []
        def build(value, **kwargs):
            calls.append((value, kwargs["clock_profile"]))
            kwargs["checkpoint"]()
            return object()
        check = lambda: checkpoints.append(True)
        first, hit = cli._timeline(b'{"source":1}', "retained_capture", {"module": "a"}, check, build)
        self.assertFalse(hit)
        second, hit = cli._timeline(b'{"source":1}', "retained_capture", {"module": "a"}, check, build)
        self.assertTrue(hit); self.assertIs(first, second); self.assertEqual(len(calls), 1)
        cli._timeline(b'{"source":1}', "assumed_eod_plus_15m", {"module": "a"}, check, build)
        cli._timeline(b'{"source":1}', "retained_capture", {"module": "b"}, check, build)
        self.assertEqual(len(calls), 3); self.assertEqual(len(cli._TIMELINES), 2)
        cli._timeline(b'{"source":2}', "retained_capture", {"module": "b"}, check, build)
        self.assertEqual(len(calls), 4); self.assertEqual(len(cli._TIMELINES), 2)
        with self.assertRaisesRegex(ValueError, "pause"):
            cli._timeline(b'{"source":2}', "retained_capture", {"module": "b"},
                lambda: (_ for _ in ()).throw(ValueError("pause")), build)
        self.assertEqual(len(calls), 4)

    def test_authority_closes_even_if_source_lease_cleanup_raises(self):
        closed = []
        original_close = cli.PauseAuthority.close
        class FailingClose(ExitStack):
            def close(self):
                super().close()
                raise OSError("generated cleanup failure")
        def close(authority):
            original_close(authority)
            closed.append(True)
        with patch.object(cli, "ExitStack", FailingClose), patch.object(cli.PauseAuthority, "close", close):
            with self.assertRaisesRegex(OSError, "cleanup failure"):
                self._run()
        self.assertEqual(closed, [True])


if __name__ == "__main__": unittest.main()
