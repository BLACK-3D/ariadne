"""Generated offline orchestration; no acquired prices, owner state or studies."""
from copy import deepcopy
from datetime import date, datetime, timezone
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
from ariadne import atomic, options_experiments as engine
from ariadne import options_historical_reference as historical
from scripts import options_experiments as cli
from test_options_historical_reference import records, parent_fixture, NOW, WINDOW


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ExperimentCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        body, metadata = parent_fixture()
        cls.datasets = {profile: historical.normalize(records(), stock_raw=body,
            stock_metadata=metadata, window=WINDOW, clock_profile=profile)[0]
            for profile in cli.PROFILES}

    def setUp(self):
        parent = ROOT / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="experiments-cli-", dir=parent)
        self.box = Path(self.temporary.name)
        for name in cli.SOURCE_FILES:
            target = self.box / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.workspace = self.box / "captured"
        self.workspace.mkdir()
        inventory, outputs = {}, {}
        self.catalogs = {}
        for profile, data in self.datasets.items():
            directory = self.workspace / profile
            directory.mkdir()
            raw = cli._canonical(data)
            chunk = profile + "/quotes.json"
            (self.workspace / chunk).write_bytes(raw)
            inventory[chunk] = {"bytes": len(raw), "sha256": digest(raw)}
            manifest = {"schema": 1, "as_of": data["as_of"], "evidence_kind": "historical_quotes",
                "symbol": "KO", "source": deepcopy(data["source"]),
                "selection": {"start": cli.START, "end": cli.END, "max_dte": None,
                    "reason": "Generated125-session orchestration fixture; no market results."},
                "limitations": ["Generated fixture only."],
                "chunks": [{"path": chunk, "sha256": digest(raw), "bytes": len(raw),
                    "snapshots": len(data["snapshots"]),
                    "quotes": sum(len(row["quotes"]) for row in data["snapshots"]),
                    "first_observed_at": data["snapshots"][0]["observed_at"],
                    "last_observed_at": data["snapshots"][-1]["observed_at"]}]}
            encoded = cli._canonical(manifest)
            name = profile + "/catalog.json"
            (self.workspace / name).write_bytes(encoded)
            inventory[name] = {"bytes": len(encoded), "sha256": digest(encoded)}
            outputs[profile] = {"catalog_path": name, "catalog_sha256": digest(encoded)}
            self.catalogs[profile] = digest(encoded)
        benchmark = b'{"generated_benchmark":true}\n'
        (self.workspace / "benchmark.json").write_bytes(benchmark)
        inventory["benchmark.json"] = {"bytes": len(benchmark), "sha256": digest(benchmark)}
        complete = {"schema": 1, "status": "completed",
            "kind": "fixed_KO_historical_nullable_sizes_reference_research",
            "source_records": 125, "source_stable": True,
            "execution_qualified": False, "automatic_adoption": False,
            "paper_operations": 0, "model_calls": 0,
            "artifacts": inventory, "outputs": outputs}
        self.capture = cli._canonical(complete)
        (self.workspace / "completed.json").write_bytes(self.capture)
        intent = b'{"schema":1,"purpose":"generated orchestration test","evidence":{"oracle_sha256":"unconsumed"}}'
        (self.box / "intent.json").write_bytes(intent)
        self.args = cli.parser().parse_args(["--workspace", "captured",
            "--source-completed-sha256", digest(self.capture),
            "--eod-manifest-sha256", self.catalogs[cli.PROFILES[0]],
            "--delayed-manifest-sha256", self.catalogs[cli.PROFILES[1]],
            "--benchmark-sha256", digest(benchmark), "--intent", "intent.json",
            "--intent-sha256", digest(intent), "--output", "staging/study"])
        self.owner_bytes = b"GENERATED SQLITE AND TOKEN SENTINEL; NEVER OPEN"
        (self.box / "data").mkdir()
        (self.box / "data/ariadne.sqlite3").write_bytes(self.owner_bytes)
        self.authorities, self.seen, self.queries = [], [], []
        self.revoked, self.after_run, self.on_publish = False, None, None
        self.configs = deepcopy(engine.configs()[:2])

    def tearDown(self):
        try:
            self.assertEqual((self.box / "data/ariadne.sqlite3").read_bytes(), self.owner_bytes)
            self.assertTrue(all(item.closed for item in self.authorities))
        finally:
            self.temporary.cleanup()

    @property
    def destination(self):
        return self.box / self.args.output

    def authority(self, box, owner_data, detached):
        parent = self
        class Authority:
            closed = False
            def check(self):
                if parent.revoked:
                    raise PermissionError("Generated pause epoch revoked.")
            def scope(self):
                return {"mode": "generated_readonly_authority"}
            def close(self):
                self.closed = True
        value = Authority()
        self.authorities.append(value)
        return value

    def timeline(self, *arguments, **keywords):
        self.assertTrue((self.destination / "registration.json").is_file())
        parent = self
        def at(clock):
            parent.queries.append(str(clock))
            return {"status": "unavailable", "label": None, "generated_only": True}
        return SimpleNamespace(at=at, describe=lambda: {"generated_fixture": True})

    def study(self, rows, config, **arguments):
        self.assertTrue((self.destination / "registration.json").is_file())
        if len(rows) == 125:
            self.assertTrue((self.destination / "design-results.json").is_file())
            self.assertTrue((self.destination / "selection.json").is_file())
        self.assertEqual(len(arguments["decision_contexts"]), len(rows))
        self.assertEqual(len(arguments["fill_contexts"]), len(rows))
        arguments["checkpoint"]()
        self.seen.append((len(rows), config["id"]))
        pnl = 0 if config["id"] == "cash" else len(rows)
        curve = [{"source_date": row["observed_at"][:10], "available_at": row["available_at"],
            "cash": str(25000 + (0 if config["id"] == "cash" else index + 1)),
            "equity": str(25000 + (0 if config["id"] == "cash" else index + 1)),
            "quantity": 0, "held": False} for index, row in enumerate(rows)]
        report = {"config": deepcopy(config), "state": {"cash": str(25000+pnl),
            "contributions": "25000", "position": None, "pending": None, "hold": None,
            "closed_trades": int(config["id"] != "cash")},
            "complete": True, "terminal_pnl": str(pnl), "nav_complete": True,
            "all_source_frames_accounted": True, "curve": curve, "events": [], "trades": []}
        if self.after_run:
            self.after_run(len(self.seen), rows, report)
        return report

    def run_study(self, *, now=NOW):
        original = atomic.publish_bytes
        def publish(path, raw, **kwargs):
            if self.on_publish:
                self.on_publish(path, raw)
            return original(path, raw, **kwargs)
        with patch("sqlite3.connect", side_effect=AssertionError("No owner database access")), \
             patch("socket.socket", side_effect=AssertionError("No network")), \
             patch.object(engine, "configs", return_value=deepcopy(self.configs)), \
             patch.object(engine, "run", side_effect=self.study), \
             patch("ariadne.options_regime.build_timeline", side_effect=self.timeline) as builder, \
             patch.object(atomic, "publish_bytes", side_effect=publish):
            result = cli.run(self.args, root=self.box, now=now, _authority=self.authority)
            self.assertEqual(builder.call_count, 1)
            return result

    @staticmethod
    def tamper(path):
        before = path.stat()
        body = path.read_bytes()
        path.write_bytes(bytes([body[0] ^ 1]) + body[1:])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    def failure(self):
        self.assertFalse((self.destination / "completed.json").exists())
        receipt = json.loads((self.destination / "failed.json").read_bytes())
        self.assertFalse(receipt["replay_allowed"])
        self.assertEqual(receipt["paper_operations"], 0)
        return receipt

    def test_all_designs_and_selection_are_durable_before_full_continuous_calls(self):
        original = cli.SourceLease.verify
        def verify(lease):
            self.assertTrue((self.destination / "registration.json").is_file())
            return original(lease)
        with patch.object(cli.SourceLease, "verify", verify):
            result = self.run_study()
        self.assertEqual(result["phase_counts"], {"DESIGN": 4, "FULL_CONTINUOUS": 4})
        self.assertEqual(len(self.seen), 8)
        self.assertTrue(all(count < 125 for count, _ in self.seen[:4]))
        self.assertTrue(all(count == 125 for count, _ in self.seen[4:]))
        selected = json.loads((self.destination / "selected-development.json").read_bytes())
        self.assertEqual(selected["candidate_id"], self.configs[1]["id"])
        for profile, value in selected["profiles"].items():
            count = next(n for n, identifier in self.seen if identifier == selected["candidate_id"])
            # Profile boundaries can differ by one source frame; use recorded baseline.
            self.assertEqual(value["development_increment"], str(25125-int(value["design_cash"])))
            self.assertEqual(value["new_funding"], "0")
        for name, item in result["artifacts"].items():
            raw = (self.destination/name).read_bytes()
            self.assertEqual((len(raw), digest(raw)), (item["bytes"], item["sha256"]))

    def test_fill_context_uses_regular_open_even_when_source_close_is_early(self):
        self.run_study()
        audit = json.loads((self.destination / "contexts.json").read_bytes())
        for profile in cli.PROFILES:
            fills = audit["profiles"][profile]["fill"]
            row = next(item for item in fills if item["query_at"].startswith("2026-07-02"))
            self.assertEqual(datetime.fromisoformat(row["query_at"]).hour, 13)
            self.assertEqual(datetime.fromisoformat(row["query_at"]).minute, 30)
        self.assertEqual(cli._session_open(date(2026, 11, 2)).hour, 14)

    def test_pause_revocation_stops_without_full_phase_or_replay(self):
        self.after_run = lambda count, rows, report: setattr(self, "revoked", count == 1)
        with self.assertRaises(PermissionError):
            self.run_study()
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.failure()["phase"], "DESIGN")
        with self.assertRaises(FileExistsError):
            self.run_study()

    def test_source_same_length_restored_mtime_tamper_fails_before_full_phase(self):
        target = self.workspace / "benchmark.json"
        self.after_run = lambda count, rows, report: self.tamper(target) if count == 1 else None
        with self.assertRaises(ValueError):
            self.run_study()
        self.failure()
        self.assertTrue(all(count < 125 for count, _ in self.seen))

    def test_final_completion_checkpoint_rejects_late_earlier_output_tamper(self):
        def mutate(path, raw):
            if path.name.startswith(".completed.json") or path.name == "completed.json":
                self.tamper(self.destination / "selection.json")
        # publish_bytes receives the final destination, before its staged checkpoint.
        self.on_publish = mutate
        with self.assertRaises(ValueError):
            self.run_study()
        self.failure()

    def test_existing_or_interrupted_directory_is_never_reused(self):
        self.destination.mkdir(parents=True)
        (self.destination / "attempt.json").write_bytes(b"preserved uncertain attempt")
        with self.assertRaises(FileExistsError):
            self.run_study()
        self.assertEqual(self.seen, [])
        self.assertEqual((self.destination / "attempt.json").read_bytes(), b"preserved uncertain attempt")

    def test_old_registration_and_wrong_metadata_hash_reject_before_output(self):
        with self.assertRaises(ValueError):
            self.run_study(now=datetime(2028, 1, 1, tzinfo=timezone.utc))
        self.assertFalse(self.destination.exists())
        self.args.source_completed_sha256 = "0"*64
        with self.assertRaises(ValueError):
            self.run_study()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.seen, [])

    def test_detached_flag_cannot_bypass_owner_database_or_initialize_another(self):
        self.args.detached_research = True
        with self.assertRaisesRegex(ValueError, "bypass"):
            self.run_study()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.seen, [])

    def test_selected_held_full_path_retains_null_development_result(self):
        def hold(count, rows, report):
            if len(rows) == 125 and report["config"]["id"] != "cash":
                report["complete"] = False
                report["terminal_pnl"] = None
                report["state"]["hold"] = {"reason": "generated unavailable disposal"}
        self.after_run = hold
        self.run_study()
        values = json.loads((self.destination / "selected-development.json").read_bytes())["profiles"]
        self.assertTrue(all(value["development_increment"] is None for value in values.values()))
        self.assertTrue(all(value["full_hold"] for value in values.values()))

    def test_continuous_path_cannot_reseed_at_design_boundary(self):
        def reset(count, rows, report):
            if len(rows) == 125 and report["config"]["id"] != "cash":
                report["state"]["contributions"] = "50000"
        self.after_run = reset
        with self.assertRaisesRegex(ValueError, "funding"):
            self.run_study()
        self.failure()
