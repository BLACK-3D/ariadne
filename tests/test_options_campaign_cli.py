"""Generated isolated-box coverage for immutable offline campaign delivery."""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ariadne.options_data import canonical, clock
from ariadne.qqq_data import exchange_day
from test_options_recovery import fixture

SOURCE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("options_campaign_cli_under_test", SOURCE / "scripts/options_campaign.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class OptionsCampaignCLITests(unittest.TestCase):
    def setUp(self):
        parent = SOURCE / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="options-campaign-cli-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "data/workspace"
        self.workspace.mkdir(parents=True)
        self.dataset = fixture(21)
        for row in self.dataset["snapshots"]:
            for quote in row["quotes"]:
                quote.update(bid="0.7", ask="0.8")
        first, last = self.dataset["snapshots"][0], self.dataset["snapshots"][-1]
        self.manifest = {"schema": 1, "as_of": self.dataset["as_of"],
            "evidence_kind": "synthetic_fixture", "symbol": "QQQ", "source": deepcopy(self.dataset["source"]),
            "selection": {"start": exchange_day(clock(first["observed_at"])).isoformat(),
                "end": exchange_day(clock(last["observed_at"])).isoformat(), "max_dte": None,
                "reason": "Generated regression fixtures only; no actual market returns."},
            "limitations": ["Synthetic unit-test data cannot establish an option strategy edge."], "chunks": []}
        for index in range(0, 21, 6):
            chunk = deepcopy(self.dataset)
            chunk["snapshots"] = chunk["snapshots"][index:index+6]
            raw = canonical(chunk)
            name = f"quotes/part-{index:02d}.json"
            self.write(name, raw)
            self.manifest["chunks"].append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "snapshots": len(chunk["snapshots"]),
                "quotes": sum(len(row["quotes"]) for row in chunk["snapshots"]),
                "first_observed_at": chunk["snapshots"][0]["observed_at"],
                "last_observed_at": chunk["snapshots"][-1]["observed_at"]})
        self.manifest_raw = canonical(self.manifest)
        self.write("quotes/catalog.json", self.manifest_raw)
        self.digest = hashlib.sha256(self.manifest_raw).hexdigest()
        self.output = self.root / "staging/result"

    def write(self, name, raw):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def args(self, *extra):
        return cli.parser().parse_args(["--stage", "conventional", "--manifest", "quotes/catalog.json",
            "--manifest-sha256", self.digest, "--output", "staging/result", "--detached-research", *extra])

    def owner_args(self, *extra):
        args = self.args(*extra)
        args.detached_research = False
        return args

    def owner(self, paused=False):
        self.owner_db = self.root / "data/ariadne.sqlite3"
        with closing(sqlite3.connect(self.owner_db)) as db, db:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO settings VALUES('paused',?)", (json.dumps(paused),))
            db.execute("CREATE TABLE opt_accounts(id TEXT,state TEXT)")
            db.execute("INSERT INTO opt_accounts VALUES('private-account','DO NOT READ OR MODIFY')")

    def change_pause(self, value):
        with closing(sqlite3.connect(self.owner_db)) as db, db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('paused',?)", (json.dumps(value),))

    def load(self, name):
        return json.loads((self.output / name).read_bytes())

    def assert_held(self):
        self.assertEqual(self.load("failed.json")["status"], "failed")
        self.assertFalse((self.output / "completed.json").exists())
        self.assertFalse(self.load("failed.json")["replay_allowed"])

    def test_detached_both_profiles_one_stream_and_registration_precedes_price_iteration(self):
        from ariadne import options_archive, options_campaign
        original = options_archive.iter_archive
        seen = []
        def stream(*args, **kwargs):
            self.assertTrue((self.output / "registration.json").is_file())
            self.assertTrue((self.output / "source_snapshot/ariadne_options_campaign.py").is_file())
            for chunk in original(*args, **kwargs):
                seen.append(len(chunk["snapshots"]))
                yield chunk
        with patch("ariadne.options_archive.iter_archive", side_effect=stream), \
                patch("sqlite3.connect", side_effect=AssertionError("Detached run must not open owner SQLite")), \
                patch("socket.create_connection", side_effect=AssertionError("No network/model")), \
                patch("subprocess.Popen", side_effect=AssertionError("No worker/process")):
            result = cli.run(self.args(), root=self.root)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, [6, 6, 6, 3])
        self.assertEqual(len(self.load("index.json")["reports"]), 2)
        registered = self.load("registration.json")
        self.assertEqual(registered["candidate_ids"], list(options_campaign.DEFAULT_CANDIDATE_IDS))
        self.assertEqual(registered["candidate_preset"], "baseline")
        self.assertEqual(registered["manifests"][0]["sha256"], self.digest)
        self.assertFalse(result["owner_authority"]["owner_database_opened"])
        self.assertFalse(result["owner_authority"]["owner_readiness_established"])
        self.assertEqual(result["owner_authority"]["mode"], "detached_research_owner_unchecked")
        self.assertTrue(result["source_streams_exhausted"])
        self.assertLess(sum(path.stat().st_size for path in self.output.rglob("*") if path.is_file()), cli.MAX_OUTPUT)
        for item in self.load("index.json")["reports"]:
            envelope = self.load(item["path"])
            self.assertFalse(envelope["report"]["comparison"]["holdout_qualified"])
            self.assertEqual(envelope["registration_sha256"], hashlib.sha256((self.output / "registration.json").read_bytes()).hexdigest())
        for source, item in registered["source_snapshots"].items():
            self.assertEqual(hashlib.sha256((self.output / source).read_bytes()).hexdigest(), item["sha256"])

    def test_single_profile_and_explicit_candidate_subset_remain_registered(self):
        result = cli.run(self.args("--profile", "strict_sizes", "--candidate", "cash"), root=self.root)
        index = self.load("index.json")
        self.assertEqual(index["candidate_ids"], ["cash"])
        self.assertEqual(index["liquidity_profiles"], ["strict_sizes"])
        self.assertEqual(len(index["reports"]), 1)
        self.assertEqual(result["paper_operations"], 0)

    def test_followup_preset_infers_real_ko_symbol_within_sixteen_ledgers(self):
        from ariadne.options_data import contract_id
        from ariadne.options_campaign import FOLLOWUP_CANDIDATE_IDS
        self.manifest["symbol"] = "KO"
        for item in self.manifest["chunks"]:
            dataset = json.loads((self.workspace / item["path"]).read_bytes())
            for row in dataset["snapshots"]:
                row["underlying_symbol"], row["underlying_price"] = "KO", "50"
                for quote in row["quotes"]:
                    quote["strike"] = "50"
                    quote["contract_id"] = contract_id("KO", quote["expiry"], quote["type"], "50")
            raw = canonical(dataset)
            self.write(item["path"], raw)
            item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        raw = canonical(self.manifest)
        self.write("quotes/catalog.json", raw)
        self.digest = hashlib.sha256(raw).hexdigest()
        result = cli.run(self.args("--preset", "volatility_followup"), root=self.root)
        self.assertEqual(result["status"], "completed")
        registration = self.load("registration.json")
        self.assertEqual(registration["candidate_preset"], "volatility_followup")
        self.assertEqual(registration["candidate_ids"], list(FOLLOWUP_CANDIDATE_IDS))
        self.assertEqual(len(registration["candidate_ids"]) * len(registration["liquidity_profiles"]), 16)
        for item in self.load("index.json")["reports"]:
            report = self.load(item["path"])["report"]
            self.assertEqual(report["symbol"], "KO")
            self.assertFalse(report["deployable"])
        self.assertEqual(result["paper_operations"], 0)

    def test_preset_cannot_be_combined_with_explicit_candidates(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            self.args("--preset", "volatility_followup", "--candidate", "cash")
        args = self.args()
        args.preset, args.candidate = "volatility_followup", ["cash"]
        with self.assertRaisesRegex(ValueError, "never both"):
            cli.run(args, root=self.root)
        self.assert_held()

    def test_registration_pins_the_actual_exchange_clock_function_and_resource(self):
        from ariadne import briefing
        from ariadne.qqq_data import exchange_day
        opened = set()
        original = Path.open
        def observe(path, *args, **kwargs):
            if path.suffix == ".tzif":
                opened.add(path.relative_to(SOURCE).as_posix())
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", new=observe):
            exchange_day(datetime(2024, 1, 3, 1, tzinfo=timezone.utc))
        self.assertIn("ariadne/resources/briefing/America_Chicago.tzif", opened)
        cli.run(self.args("--profile", "strict_sizes", "--candidate", "cash"), root=self.root)
        pins = self.load("registration.json")["module_sha256"]
        actual_function = Path(briefing._tz.__code__.co_filename).relative_to(SOURCE).as_posix()
        self.assertIn(actual_function, pins)
        for name in opened:
            self.assertEqual(pins[name], hashlib.sha256((SOURCE / name).read_bytes()).hexdigest())

    def test_changed_guarded_clock_source_or_tzif_stops_before_report_publication(self):
        from ariadne import options_campaign
        from scripts import options_market
        original_campaign, original_read = options_campaign.campaign_profiles, options_market.read
        for index, target in enumerate(("ariadne/briefing.py", "ariadne/resources/briefing/America_Chicago.tzif")):
            with self.subTest(dependency=target):
                state = {"finished_iteration": False}
                def campaign(*args, **kwargs):
                    result = original_campaign(*args, **kwargs)
                    state["finished_iteration"] = True
                    return result
                def read(guard, name, limit):
                    raw = original_read(guard, name, limit)
                    if state["finished_iteration"] and guard.root == SOURCE and name == target:
                        return raw + b" "  # Simulate changed guarded bytes; never modify production files.
                    return raw
                args = self.args()
                args.output = f"staging/changed-clock-{index}"
                with patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), \
                        patch("scripts.options_market.read", side_effect=read), self.assertRaisesRegex(ValueError, "source/kernel"):
                    cli.run(args, root=self.root)
                folder = self.root / args.output
                self.assertEqual(json.loads((folder / "failed.json").read_bytes())["status"], "failed")
                self.assertFalse((folder / "completed.json").exists())
                self.assertFalse(any(folder.glob("archive-*.json")))

    def test_owner_pause_reads_preserve_all_database_rows(self):
        self.owner()
        with closing(sqlite3.connect(self.owner_db)) as db, db:
            before = list(db.iterdump())
        statements, connect = [], sqlite3.connect
        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with patch("sqlite3.connect", side_effect=traced):
            result = cli.run(self.owner_args("--profile", "strict_sizes", "--candidate", "cash"), root=self.root)
        with closing(connect(self.owner_db)) as db, db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertTrue(result["owner_authority"]["owner_database_opened"])
        self.assertTrue(any("WHERE key='paused'" in value for value in statements))
        self.assertFalse(any("opt_accounts" in value for value in statements))
        self.assertFalse(any(value.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for value in statements))

    def test_owner_connection_closes_inside_native_pins_on_success_and_failure(self):
        from ariadne import backup
        self.owner()
        original_pins, connect = backup._pin_database_files, sqlite3.connect
        state = {"depth": 0, "closed": 0, "opened": 0}
        test = self
        class PinnedConnection(sqlite3.Connection):
            def close(self):
                test.assertGreater(state["depth"], 0)
                state["closed"] += 1
                return super().close()
        @contextmanager
        def pins(path):
            with original_pins(path):
                state["depth"] += 1
                try:
                    yield
                finally:
                    state["depth"] -= 1
        def opened(*args, **kwargs):
            self.assertGreater(state["depth"], 0)
            state["opened"] += 1
            return connect(*args, factory=PinnedConnection, **kwargs)
        authority = cli.PauseAuthority(cli._root_guard(self.root), "data", False)
        with patch("ariadne.backup._pin_database_files", side_effect=pins), patch("sqlite3.connect", side_effect=opened):
            try:
                authority.check()
                authority.check()
                self.assertEqual(state, {"depth": 1, "closed": 0, "opened": 1})
                self.assertFalse(authority.db.in_transaction)
            finally:
                authority.close()
            authority.close()  # Idempotent cleanup must not reopen or release twice.
        self.assertEqual(state, {"depth": 0, "closed": 1, "opened": 1})
        with self.assertRaisesRegex(ValueError, "closed"):
            authority.check()
        self.change_pause(True)
        with patch("ariadne.backup._pin_database_files", side_effect=pins), patch("sqlite3.connect", side_effect=opened), self.assertRaises(ValueError):
            cli.run(self.owner_args(), root=self.root)
        self.assertEqual(state, {"depth": 0, "closed": 2, "opened": 2})

    def test_owner_connection_open_failure_releases_retained_pins(self):
        from ariadne import backup
        self.owner()
        state = {"depth": 0}
        original = backup._pin_database_files
        @contextmanager
        def pins(path):
            with original(path):
                state["depth"] += 1
                try:
                    yield
                finally:
                    state["depth"] -= 1
        with patch("ariadne.backup._pin_database_files", side_effect=pins), \
                patch("sqlite3.connect", side_effect=sqlite3.OperationalError("generated open failure")), \
                self.assertRaises(sqlite3.OperationalError):
            cli.run(self.owner_args(), root=self.root)
        self.assertEqual(state["depth"], 0)
        self.assert_held()

    def test_retained_owner_name_replacement_is_rejected_before_begin_and_commit(self):
        from scripts import options_market
        from types import SimpleNamespace
        self.owner()
        original_lstat, original_metadata = Path.lstat, options_market.metadata
        for phase in ("before_begin", "before_commit"):
            with self.subTest(phase=phase):
                authority = cli.PauseAuthority(cli._root_guard(self.root), "data", False)
                authority.check()
                statements, state = [], {"replaced": phase == "before_begin"}
                authority.db.set_trace_callback(statements.append)
                def lstat(path, *args, **kwargs):
                    info = original_lstat(path, *args, **kwargs)
                    if path == self.owner_db and state["replaced"]:
                        fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                        fields["st_ino"] += 1
                        return SimpleNamespace(**fields)
                    return info
                def metadata(raw):
                    value = original_metadata(raw)
                    if phase == "before_commit":
                        state["replaced"] = True
                    return value
                try:
                    with patch.object(Path, "lstat", new=lstat), \
                            patch("scripts.options_market.metadata", side_effect=metadata), \
                            self.assertRaisesRegex(ValueError, "identity changed"):
                        authority.check()
                    self.assertFalse(authority.db.in_transaction)
                    self.assertNotIn("COMMIT", statements)
                    if phase == "before_begin":
                        self.assertNotIn("BEGIN", statements)
                    else:
                        self.assertIn("BEGIN", statements)
                        self.assertIn("ROLLBACK", statements)
                finally:
                    # Mocking ends before canonical pins revalidate their actual files.
                    authority.close()

    def test_wal_reader_uses_one_connection_and_fresh_transactions_for_committed_pause(self):
        self.owner()
        connect = sqlite3.connect
        with closing(connect(self.owner_db, isolation_level=None)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            statements, connections = [], []
            def opened(*args, **kwargs):
                db = connect(*args, **kwargs)
                connections.append(db)
                db.set_trace_callback(statements.append)
                return db
            authority = cli.PauseAuthority(cli._root_guard(self.root), "data", False)
            with patch("sqlite3.connect", side_effect=opened):
                try:
                    authority.check()
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute("INSERT OR REPLACE INTO settings VALUES('paused','true')")
                    authority.check()  # An uncommitted writer does not become a pause receipt.
                    self.assertFalse(authority.db.in_transaction)
                    writer.execute("COMMIT")
                    with self.assertRaisesRegex(ValueError, "paused"):
                        authority.check()
                    self.assertFalse(authority.db.in_transaction)
                    self.assertEqual(len(connections), 1)
                    self.assertEqual(sum(value == "BEGIN" for value in statements), 3)
                    self.assertEqual(sum(value == "COMMIT" for value in statements), 2)
                    self.assertIn("ROLLBACK", statements)
                finally:
                    authority.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute("SELECT 1")

    def test_wal_false_true_false_epoch_is_fresh_and_reader_does_not_pin_snapshot(self):
        self.owner()
        with closing(sqlite3.connect(self.owner_db, isolation_level=None)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            authority = cli.PauseAuthority(cli._root_guard(self.root), "data", False)
            try:
                authority.check()
                writer.execute("INSERT OR REPLACE INTO settings VALUES('paused','true')")
                writer.execute("INSERT OR REPLACE INTO settings VALUES('paused','false')")
                checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                self.assertEqual(checkpoint[0], 0)  # No old read transaction prevents checkpoint.
                with self.assertRaisesRegex(ValueError, "epoch"):
                    authority.check()
                self.assertFalse(authority.db.in_transaction)
            finally:
                authority.close()

    def _locked_owner_check(self, release_after):
        self.owner()
        authority = cli.PauseAuthority(cli._root_guard(self.root), "data", False)
        self.addCleanup(authority.close)
        authority.check()
        ready, release, errors = threading.Event(), threading.Event(), []
        def lock():
            try:
                with closing(sqlite3.connect(self.owner_db, isolation_level=None)) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    writer.execute("UPDATE opt_accounts SET state='generated lock test'")
                    ready.set()
                    release.wait(8)
                    writer.execute("ROLLBACK")
            except BaseException as error:
                errors.append(error)
                ready.set()
        thread = threading.Thread(target=lock)
        thread.start()
        timer = None
        try:
            self.assertTrue(ready.wait(3))
            self.assertFalse(errors)
            if release_after is not None:
                timer = threading.Timer(release_after, release.set)
                timer.start()
            started = time.monotonic()
            if release_after is None:
                with self.assertRaises((sqlite3.OperationalError, TimeoutError)):
                    authority.check()
            else:
                authority.check()
            elapsed = time.monotonic() - started
            self.assertFalse(authority.db.in_transaction)
            return elapsed
        finally:
            release.set()
            if timer is not None:
                timer.cancel()
                timer.join(2)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            authority.close()

    def test_transient_exclusive_writer_beyond_old_one_second_timeout_is_tolerated(self):
        elapsed = self._locked_owner_check(1.25)
        self.assertGreaterEqual(elapsed, 1.1)
        self.assertLess(elapsed, 4.5)

    def test_busy_writer_stops_within_existing_five_second_check_budget(self):
        elapsed = self._locked_owner_check(None)
        self.assertGreaterEqual(elapsed, 4.5)
        self.assertLess(elapsed, 6)

    def test_missing_or_paused_owner_fails_without_creating_or_clearing_database(self):
        for paused in (None, True):
            with self.subTest(paused=paused):
                if paused is not None:
                    self.owner(paused)
                output = "staging/missing" if paused is None else "staging/paused"
                args = self.owner_args()
                args.output = output
                with self.assertRaises((ValueError, OSError)):
                    cli.run(args, root=self.root)
                failed = json.loads((self.root / output / "failed.json").read_bytes())
                self.assertFalse((self.root / output / "completed.json").exists())
                self.assertFalse(failed["owner_settings_written"])
                if paused is None:
                    self.assertFalse((self.root / "data/ariadne.sqlite3").exists())
                else:
                    with closing(sqlite3.connect(self.owner_db)) as db, db:
                        self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], "true")

    def test_pause_false_true_false_epoch_change_stops_the_stream(self):
        from ariadne import options_archive
        self.owner()
        original = options_archive.iter_archive
        def stream(*args, **kwargs):
            self.change_pause(True)
            self.change_pause(False)
            yield from original(*args, **kwargs)
        with patch("ariadne.options_archive.iter_archive", side_effect=stream), self.assertRaisesRegex(ValueError, "epoch"):
            cli.run(self.owner_args(), root=self.root)
        self.assert_held()
        self.assertFalse(any(self.output.glob("archive-*.json")))

    def test_pause_during_atomic_report_publication_blocks_that_report(self):
        from ariadne import atomic
        self.owner()
        original = atomic.publish_bytes
        def publish(path, raw, *, checkpoint=None):
            if path.name == "archive-01--strict_sizes.json":
                callback = checkpoint
                def checkpoint():
                    self.change_pause(True)
                    callback()
            return original(path, raw, checkpoint=checkpoint)
        with patch("ariadne.atomic.publish_bytes", side_effect=publish), self.assertRaisesRegex(ValueError, "paused"):
            cli.run(self.owner_args(), root=self.root)
        self.assert_held()
        self.assertFalse((self.output / "archive-01--strict_sizes.json").exists())

    def test_corrupt_later_chunk_never_publishes_partial_metrics(self):
        from ariadne import options_archive
        original = options_archive.iter_archive
        def stream(*args, **kwargs):
            iterator = original(*args, **kwargs)
            yield next(iterator)
            last = self.workspace / self.manifest["chunks"][-1]["path"]
            last.write_bytes(last.read_bytes() + b" ")
            yield from iterator
        with patch("ariadne.options_archive.iter_archive", side_effect=stream), self.assertRaises(ValueError):
            cli.run(self.args(), root=self.root)
        self.assert_held()
        self.assertFalse(any(self.output.glob("archive-*.json")))
        self.assertFalse((self.output / "index.json").exists())

    def test_changed_already_studied_chunk_is_rehashed_before_publication(self):
        from ariadne import options_campaign
        original = options_campaign.campaign_profiles
        def campaign(*args, **kwargs):
            result = original(*args, **kwargs)
            first = self.workspace / self.manifest["chunks"][0]["path"]
            first.write_bytes(first.read_bytes() + b" ")
            return result
        with patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), self.assertRaisesRegex(ValueError, "source"):
            cli.run(self.args(), root=self.root)
        self.assert_held()
        self.assertFalse(any(self.output.glob("archive-*.json")))

    def test_same_length_source_tampering_with_restored_mtime_cannot_complete(self):
        from ariadne import atomic
        original = atomic.publish_bytes
        first = self.workspace / self.manifest["chunks"][0]["path"]
        original_raw = first.read_bytes()
        attempted = []
        def publish(path, raw, *, checkpoint=None):
            if path.name == "archive-01--strict_sizes.json":
                before = first.stat()
                changed = original_raw.replace(b'"bid":"0.7"', b'"bid":"0.6"', 1)
                self.assertEqual(len(changed), len(original_raw))
                self.assertNotEqual(changed, original_raw)
                attempted.append(True)
                first.write_bytes(changed)
                os.utime(first, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual((first.stat().st_ino, first.stat().st_size, first.stat().st_mtime_ns),
                    (before.st_ino, before.st_size, before.st_mtime_ns))
            return original(path, raw, checkpoint=checkpoint)
        with patch("ariadne.atomic.publish_bytes", side_effect=publish), self.assertRaises((ValueError, OSError)):
            cli.run(self.args(), root=self.root)
        self.assertTrue(attempted)
        self.assert_held()

    def test_artifact_tampering_at_completed_atomic_checkpoint_is_held(self):
        from ariadne import atomic
        original = atomic.publish_bytes
        def publish(path, raw, *, checkpoint=None):
            if path.name == "completed.json":
                callback = checkpoint
                def checkpoint():
                    (self.output / "source_snapshot/scripts_options_campaign.py").write_bytes(b"changed at commit")
                    callback()
            return original(path, raw, checkpoint=checkpoint)
        with patch("ariadne.atomic.publish_bytes", side_effect=publish), self.assertRaisesRegex(ValueError, "artifacts"):
            cli.run(self.args(), root=self.root)
        self.assert_held()

    def test_engine_early_return_cannot_claim_source_exhaustion(self):
        from ariadne import options_campaign
        original = options_campaign.campaign_profiles
        def campaign(stream, **kwargs):
            return original([next(iter(stream))], **kwargs)
        with patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), self.assertRaisesRegex(ValueError, "iterator"):
            cli.run(self.args(), root=self.root)
        self.assert_held()
        self.assertFalse(any(self.output.glob("archive-*.json")))

    def test_manifest_change_after_registration_and_wrong_hash_are_held(self):
        from ariadne import options_campaign
        original = options_campaign.campaign_profiles
        def campaign(*args, **kwargs):
            self.write("quotes/catalog.json", self.manifest_raw + b" ")
            return original(*args, **kwargs)
        with patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), self.assertRaises(ValueError):
            cli.run(self.args(), root=self.root)
        self.assert_held()
        args = self.args()
        args.output, args.manifest_sha256 = "staging/wrong-hash", ["0" * 64]
        with self.assertRaises(ValueError):
            cli.run(args, root=self.root)
        self.assertFalse((self.root / args.output / "registration.json").exists())

    def test_aggregate_report_limit_rejects_without_truncation(self):
        from ariadne import options_campaign
        original = options_campaign.campaign_profiles
        def campaign(*args, **kwargs):
            result = original(*args, **kwargs)
            result["profiles"]["strict_sizes"]["oversized_test_field"] = "x" * 300000
            return result
        with patch.object(cli, "MAX_OUTPUT", 200000), patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), self.assertRaisesRegex(ValueError, "8MiB"):
            cli.run(self.args(), root=self.root)
        self.assert_held()
        self.assertFalse(any(self.output.glob("archive-*.json")))
        self.assertLess(sum(path.stat().st_size for path in self.output.rglob("*") if path.is_file()), 200000)

    def test_source_snapshot_tampering_prevents_completed_marker(self):
        from ariadne import options_campaign
        original = options_campaign.campaign_profiles
        def campaign(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.output / "source_snapshot/scripts_options_campaign.py"
            path.write_bytes(b"changed")
            return result
        with patch("ariadne.options_campaign.campaign_profiles", side_effect=campaign), self.assertRaisesRegex(ValueError, "artifacts"):
            cli.run(self.args(), root=self.root)
        self.assert_held()

    def test_existing_empty_failed_or_completed_output_is_never_reused(self):
        self.output.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            cli.run(self.args(), root=self.root)
        self.assertEqual(list(self.output.iterdir()), [])
        (self.output / "failed.json").write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            cli.run(self.args(), root=self.root)
        self.assertEqual((self.output / "failed.json").read_bytes(), b"original")

    def test_path_candidate_and_manifest_bounds_reject_without_escaping(self):
        cases = [("output", "docs/public-results"), ("output", "../outside"), ("output", "E:/forbidden"),
            ("candidate", ["arbitrary_python"]), ("candidate", ["cash", "cash"]),
            ("profile", ["strict_sizes", "strict_sizes"]),
            ("manifest", ["quotes/catalog.json"] * 5)]
        for index, (field, value) in enumerate(cases):
            with self.subTest(field=field, value=value):
                args = self.args()
                args.output = f"staging/reject-{index}"
                setattr(args, field, value)
                with self.assertRaises((ValueError, OSError)):
                    cli.run(args, root=self.root)

    def test_main_installs_guards_and_reports_failure_without_actions(self):
        with patch.object(cli, "install_offline_guards") as guards, \
                patch.object(cli, "run", side_effect=ValueError("held")), patch("sys.stderr"):
            self.assertEqual(cli.main(["--stage", "conventional", "--manifest", "a.json", "--manifest-sha256", "0" * 64,
                "--output", "staging/result", "--detached-research"]), 2)
        guards.assert_called_once()
