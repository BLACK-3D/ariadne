"""Isolated generated fixtures, never provider market data or investment evidence."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.internet import SETTINGS_KEY
from ariadne.options_workflow import OptionsWorkflow, _json, _digest
from ariadne import options_eod_data as data
from ariadne.qqq_data import session_dates, session_close_utc
from ariadne.store import Store
from ariadne.tools import ToolRegistry

UTC = timezone.utc
DAY = date(2024, 1, 2)
EXPIRY = date(2024, 1, 19)


def raw(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def stock_rows(day, price=12):
    start = datetime(day.year, day.month, day.day, 9, 30)
    minutes = int((session_close_utc(day)-session_close_utc(day).replace(hour=14, minute=30)).total_seconds()/60)+1
    # Fixture sessions used below are January normal closes. Explicit reviewed
    # close-derived length also supports the short-session test helper.
    from ariadne.briefing import _tz
    close = session_close_utc(day).astimezone(_tz("America/Chicago"))
    minutes = (close.hour+1)*60+close.minute-570+1
    return [{"timestamp": (start+timedelta(minutes=n)).strftime("%Y-%m-%d %I:%M %p"),
             "open": price, "high": price+1, "low": price-1, "close": price, "volume": 5}
            for n in range(minutes)]


def evidence(body, url, received):
    return {"url": url, "received_at": received.isoformat(), "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "content_base64": base64.b64encode(body).decode(), "http_status": 200}


def bundle(day, received, *, price=12, bid=0.8, ask=0.9, expiry=EXPIRY):
    suffix = expiry.strftime("%y%m%d")
    rows = [{"warning": "only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]
    for kind, side in (("call", "C"), ("put", "P")):
        rows.append({"osi-key": "F-----"+suffix+side+"00012500", "ticker": "F", "date": day.isoformat(),
                     "expiration": expiry.isoformat(), "strike": 12.5, "type": kind,
                     "bid": bid, "ask": ask, "bid_size": 5, "ask_size": 5})
    metadata = {"call": [expiry.isoformat()], "put": [expiry.isoformat()]}
    return {"session": day.isoformat(),
            "expirations": evidence(raw(metadata), data.expiration_url(day), received),
            "chains": [evidence(raw(rows), data.chain_url(day, expiry), received)],
            "stock": evidence(raw(stock_rows(day, price)), data.stock_url(day), received)}


class EODWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/"runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="eod-workflow-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.store.set_setting("paused", False)
        self.store.set_setting(SETTINGS_KEY, {"enabled": True, "revision": 1})
        self.registry = ToolRegistry(self.settings)
        self.controls = {"enabled": True, "paused": False, "revision": 1}
        self.owner = OptionsWorkflow(self.registry, self.store, controls=lambda: dict(self.controls))
        self.clock = datetime(2024, 1, 2, 22, tzinfo=UTC)
        self.counter = 0

    def body(self, **extra):
        self.counter += 1
        with self.owner.database() as db:
            revision = self.owner._meta(db)[0]
        return {"request_id": "eod-workflow-"+str(self.counter), "expected_revision": revision, **extra}

    def ready(self, *, observed=False, upgraded=False):
        self.owner.configure({"enabled": True, "paused": False, "expected_revision": 0}, self.clock-timedelta(minutes=2))
        if upgraded:
            self.owner.start_paper(self.body(acknowledge_simulated=True), self.clock-timedelta(minutes=1))
            self.owner.upgrade_portfolio(self.body(acknowledge_simulated=True), self.clock)
        else:
            self.owner.start_portfolio(self.body(acknowledge_simulated=True), self.clock)
        if observed:
            from test_options_live import bundle as aapl_bundle
            self.clock = datetime(2024, 1, 3, 14, 35, tzinfo=UTC)
            self.owner.feed.configure(self.body(enabled=True, acknowledge_feed=True), self.clock)
            self.owner.feed.collect(self.body(), self.clock,
                                    collector=lambda **kwargs: aapl_bundle(DAY, self.clock))
        self.before = self.paper()
        self.clock += timedelta(minutes=1)

    def paper(self):
        with self.owner.database() as db:
            return self.owner._account(db, self.clock)

    def migrate(self, baseline_path=None):
        body = self.body(acknowledge_simulated=True, acknowledge_partial_eod=True, baseline_path=baseline_path)
        self.migration_body = body
        return self.owner.eod.migrate(body, self.clock)

    def collect(self, day=DAY, *, value=None, collector=None):
        next_day = session_dates(day+timedelta(days=1), day+timedelta(days=7))[0]
        self.clock = datetime(next_day.year, next_day.month, next_day.day, 15, 5, tzinfo=UTC)
        payload = bundle(day, self.clock) if value is None else value
        body = self.body()
        self.collect_body = body
        return self.owner.eod.collect(body, self.clock, collector=collector or (lambda **kwargs: payload))

    def put(self, relative, payload):
        path = self.registry._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return relative

    def test_passive_status_is_uninitialized_and_creates_no_eod_tables(self):
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            value = self.owner.status(self.clock)["eod"]
        self.assertFalse(value["config"]["initialized"])
        self.assertFalse(value["migration_eligible"])
        self.assertIsNone(value["quality"])
        with self.owner.database() as db:
            self.assertFalse(self.owner.eod._exists(db))

    def test_fresh_shared_account_migrates_in_place_without_another_500(self):
        self.ready()
        before = deepcopy(self.paper())
        receipt = self.migrate()
        after = self.paper()
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["started_at"], before["started_at"])
        for key in ("cash", "contributions", "month", "monthly_start_equity", "monthly_committed", "fees", "trade_count", "closed_trade_count"):
            self.assertEqual(after["state"][key], before["state"][key])
        self.assertEqual(after["state"]["schema"], 3)
        self.assertEqual(after["state"]["symbol"], "F")
        self.assertEqual(after["state"]["migration"]["new_funding"], "0")
        self.assertEqual(receipt["simulated_funding_added"], "0")
        self.assertFalse(receipt["monthly_budget_reset"])
        self.assertEqual(len(receipt["artifacts"]), 7)
        self.assertFalse(receipt["execution_qualified"])
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0], 1)

    def test_observed_aapl_history_and_both_legacy_witnesses_remain_exact(self):
        self.ready(observed=True, upgraded=True)
        with self.owner.database() as db:
            old_forward = bytes(db.execute("SELECT value FROM opt_data WHERE id=2").fetchone()[0])
            witnesses = self.owner.eod._legacy_witnesses(db, self.before["id"])
            legacy_source = self.owner.feed.config(db)
        before = deepcopy(self.paper())
        self.migrate()
        after = self.paper()
        self.assertEqual(before["state"]["processed_count"], 1)
        self.assertEqual(after["state"]["processed_count"], 0)
        self.assertEqual(after["state"]["cash"], "500")
        with self.owner.database() as db:
            self.assertEqual(bytes(db.execute("SELECT value FROM opt_data WHERE id=2").fetchone()[0]), old_forward)
            self.assertEqual(self.owner.eod._legacy_witnesses(db, before["id"]), witnesses)
            saved = db.execute("SELECT old_state FROM opt_eod_migrations WHERE account_id=?", (before["id"],)).fetchone()[0]
            self.assertEqual(json.loads(saved), before["state"])
            source = self.owner.feed.config(db)
        self.assertFalse(source["enabled"])
        self.assertEqual(source["revision"], legacy_source["revision"]+1)
        self.assertEqual(source["activation_at"], legacy_source["activation_at"])

    def test_migration_completed_replay_verifies_without_reset_or_second_transition(self):
        self.ready(); first = self.migrate(); before = deepcopy(self.paper())
        again = self.owner.eod.migrate(self.migration_body, self.clock)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["artifacts"], first["artifacts"])
        self.assertEqual(self.paper(), before)
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_eod_migrations").fetchone()[0], 1)

    def test_baseline_manifest_retains_original_stock_bytes_without_past_fills(self):
        self.ready()
        payload = raw(stock_rows(DAY))
        path = self.put("imports/f-stock.json", payload)
        manifest = {"schema": 1, "kind": "onclick_f_stock_baseline", "raw_path": path,
                    "raw_sha256": _digest(payload), "received_at": self.clock.isoformat(), "through_date": DAY.isoformat()}
        # Baseline dates must precede activation's exchange day.
        self.clock = datetime(2024, 1, 3, 15, tzinfo=UTC)
        manifest["received_at"] = self.clock.isoformat()
        self.migrate(self.put("imports/baseline.json", raw(manifest)))
        account = self.paper()
        self.assertEqual(len(account["state"]["history"]), 1)
        self.assertEqual(account["state"]["trade_count"], 0)
        self.assertIsNone(account["state"]["last_daily_result"])
        self.assertEqual(account["state"]["processed_count"], 0)
        self.assertEqual(self.owner.status(self.clock)["eod"]["baseline_count"], 1)
        receipt = self.owner.status(self.clock)["last_run"]
        source = next(item for item in receipt["artifacts"] if item["path"].endswith("baseline-source.json"))
        self.assertEqual(self.registry._path(source["path"]).read_bytes(), payload)

    def test_external_paths_or_extra_authority_fail_before_eod_schema_or_reads(self):
        self.ready()
        for value in ("../outside.json", "E:/private.json", "https://outside.invalid/x"):
            with self.subTest(value=value), patch.object(self.owner, "_read", side_effect=AssertionError("No file read")), self.assertRaises(ValueError):
                self.owner.eod.migrate(self.body(acknowledge_simulated=True, acknowledge_partial_eod=True, baseline_path=value), self.clock)
        with self.assertRaises(ValueError):
            self.owner.eod.migrate(self.body(acknowledge_simulated=True, acknowledge_partial_eod=True, baseline_path=None, funding="500"), self.clock)
        with self.owner.database() as db:
            self.assertFalse(self.owner.eod._exists(db))

    def test_nested_baseline_path_and_hash_failure_never_open_external_raw_data(self):
        self.ready(); self.clock = datetime(2024, 1, 3, 15, tzinfo=UTC)
        manifest = {"schema": 1, "kind": "onclick_f_stock_baseline", "raw_path": "../outside.json", "raw_sha256": "a"*64,
                    "received_at": self.clock.isoformat(), "through_date": DAY.isoformat()}
        manifest_path = self.put("imports/bad-baseline.json", raw(manifest))
        original = self.owner._read; opened = []
        def read(path, *args):
            opened.append(path); return original(path, *args)
        with patch.object(self.owner, "_read", side_effect=read), self.assertRaises(ValueError):
            self.migrate(manifest_path)
        self.assertEqual(opened, [manifest_path])
        self.assertEqual(self.paper()["state"]["schema"], 2)
        self.assertEqual(self.owner.status(self.clock)["hold"]["status"], "failed")

    def test_source_capture_keeps_date_only_clocks_and_same_account(self):
        self.ready(); self.migrate(); before = deepcopy(self.paper())
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            receipt = self.collect()
        after = self.paper(); status = self.owner.status(self.clock)
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["state"]["cash"], "500")
        self.assertEqual(after["state"]["trade_count"], 0)
        self.assertEqual(after["state"]["processed_count"], 1)
        self.assertIsNone(receipt["quote_observed_at"])
        self.assertEqual(receipt["source_date"], DAY.isoformat())
        self.assertEqual(len(receipt["artifacts"]), 4)
        self.assertEqual(status["eod"]["quality"]["quote_time_basis"], "vendor_eod_date_only")
        self.assertIsNone(status["eod"]["quality"]["last_observed_at"])
        self.assertFalse(status["eod"]["execution_qualified"])
        self.assertEqual(self.owner.brief_snapshot(self.clock)["eod"], status["eod"])

    def test_capture_replay_is_read_only_and_same_source_day_cannot_run_twice(self):
        self.ready(); self.migrate(); first = self.collect(); before = deepcopy(self.paper())
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No replay")):
            repeated = self.owner.eod.collect(self.collect_body, self.clock)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(repeated["artifacts"], first["artifacts"])
        self.assertEqual(self.paper(), before)
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No duplicate day")), self.assertRaises(HandoffConflict):
            self.owner.eod.collect(self.body(), self.clock)

    def test_normalization_failure_retains_raw_evidence_and_blocks_automatic_retry(self):
        self.ready(); self.migrate(); before = deepcopy(self.paper())
        next_day = date(2024, 1, 3)
        self.clock = datetime(next_day.year, next_day.month, next_day.day, 15, 5, tzinfo=UTC)
        broken = bundle(DAY, self.clock); broken["stock"]["sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "raw evidence is retained"):
            self.collect(value=broken)
        status = self.owner.status(self.clock)
        self.assertEqual(status["paper"], before)
        self.assertEqual(status["hold"]["status"], "failed")
        self.assertTrue(status["eod"]["last_run"]["source_failed"])
        self.assertEqual(len(status["eod"]["last_run"]["artifacts"]), 2)
        self.owner.eod.verify(status["eod"]["last_run"])
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No retry")):
            self.assertIsNone(self.owner.eod.run_due(self.clock))
            self.assertIsNone(self.owner.eod.run_due(self.clock+timedelta(minutes=1)))

    def test_pause_resume_epoch_during_capture_revokes_results(self):
        self.ready(); self.migrate(); before = deepcopy(self.paper())
        def revoked(**kwargs):
            self.store.set_setting("paused", True); self.store.set_setting("paused", False)
            return bundle(DAY, self.clock)
        with self.assertRaises(HandoffConflict):
            self.collect(collector=revoked)
        self.assertEqual(self.paper()["state"], before["state"])
        self.assertEqual(self.owner.status(self.clock)["hold"]["status"], "failed")

    def test_actual_decision_clock_is_taken_after_receipt_for_production_call(self):
        self.ready(); self.migrate()
        start = datetime(2024, 1, 3, 15, 5, tzinfo=UTC)
        end = start+timedelta(seconds=4)
        value = bundle(DAY, end-timedelta(seconds=1))
        clocks = iter((start, end))
        with patch("ariadne.options_eod_workflow._now", side_effect=lambda value=None: value if value is not None else next(clocks)):
            receipt = self.owner.eod.collect(self.body(), collector=lambda **kwargs: value)
        self.clock = end
        self.assertEqual(receipt["recorded_at"], end.isoformat())
        self.assertEqual(self.paper()["state"]["last_decided_at"], end.isoformat())

    def test_future_actual_receipt_is_rejected_without_backdating_decision(self):
        self.ready(); self.migrate(); before = deepcopy(self.paper())
        self.clock = datetime(2024, 1, 3, 15, 5, tzinfo=UTC)
        value = bundle(DAY, self.clock+timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "later than its decision clock"):
            self.collect(value=value)
        self.assertEqual(self.paper()["state"], before["state"])

    def test_source_pause_retains_money_and_never_falls_back_to_legacy(self):
        self.ready(observed=True); self.migrate(); before = deepcopy(self.paper())
        self.owner.eod.configure(self.body(enabled=False, acknowledge_source=True), self.clock)
        self.assertEqual(self.paper(), before)
        with patch.object(self.owner.feed, "run_due", side_effect=AssertionError("No legacy fallback")), patch.object(self.owner.eod, "_collect", side_effect=AssertionError("Paused source")):
            self.assertIsNone(self.owner.collect_due(self.clock))
        self.assertTrue(self.owner.status(self.clock)["eod"]["config"]["initialized"])
        self.assertFalse(self.owner.status(self.clock)["eod"]["config"]["enabled"])

    def test_old_artifact_or_migration_witness_change_is_not_adopted(self):
        self.ready(observed=True); receipt = self.migrate()
        path = next(item["path"] for item in receipt["artifacts"] if item["path"].endswith("previous-state.json"))
        self.registry._path(path).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.paper()

    def test_source_cache_cannot_disagree_with_its_completed_artifact(self):
        self.ready(); self.migrate(); self.collect()
        with self.owner.database() as db:
            row = db.execute("SELECT value FROM opt_eod_data").fetchone()
            value = json.loads(bytes(row[0])); value["underlying_close"] = "13"
            encoded = _json(value).encode()
            db.execute("UPDATE opt_eod_data SET value=?,digest=?,bytes=?", (encoded, _digest(encoded), len(encoded)))
        with self.assertRaisesRegex(ValueError, "accepted EOD prefix row differs from its completed receipt"):
            self.owner.status(self.clock)

    def two_captures(self):
        self.ready(); self.migrate()
        first = self.collect(DAY)
        self.collect(date(2024, 1, 3))
        return first

    def test_older_raw_artifact_tamper_stops_collection_before_worker_with_failed_receipt(self):
        first = self.two_captures(); before = deepcopy(self.paper()["state"])
        artifact = next(item for item in first["artifacts"] if item["path"].endswith("/bundle.json"))
        path = self.registry._path(artifact["path"])
        original = path.read_bytes()
        path.write_bytes(b" "+original[1:])  # Same byte length; only a full hash detects it.
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker after old evidence change")), self.assertRaisesRegex(ValueError, "missing or changed"):
            self.collect(date(2024, 1, 4))
        with self.owner.database() as db:
            run = db.execute("SELECT status,receipt FROM opt_runs WHERE request_id=?", (self.collect_body["request_id"],)).fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertEqual(json.loads(db.execute("SELECT state FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0]), before)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_eod_data").fetchone()[0], 2)
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No automatic retry")):
            self.assertIsNone(self.owner.eod.run_due(self.clock+timedelta(minutes=1)))

    def test_missing_older_artifact_cannot_allow_another_collection(self):
        first = self.two_captures()
        artifact = next(item for item in first["artifacts"] if item["path"].endswith("/provenance.json"))
        self.registry._path(artifact["path"]).unlink()
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker after missing evidence")), self.assertRaises((ValueError, FileNotFoundError)):
            self.collect(date(2024, 1, 4))
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT status FROM opt_runs WHERE request_id=?", (self.collect_body["request_id"],)).fetchone()[0], "failed")

    def test_older_cache_value_and_local_digest_cannot_replace_receipt_bound_history(self):
        self.two_captures(); before = deepcopy(self.paper()["state"])
        with self.owner.database() as db:
            row = db.execute("SELECT value FROM opt_eod_data WHERE source_date=?", (DAY.isoformat(),)).fetchone()
            changed = json.loads(bytes(row[0])); changed["underlying_close"] = "13"
            encoded = _json(changed).encode()
            db.execute("UPDATE opt_eod_data SET value=?,digest=?,bytes=? WHERE source_date=?", (encoded, _digest(encoded), len(encoded), DAY.isoformat()))
        with self.assertRaisesRegex(ValueError, "accepted EOD prefix row differs"):
            self.owner.status(self.clock)
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker after changed cache")), self.assertRaisesRegex(ValueError, "accepted EOD prefix row differs"):
            self.collect(date(2024, 1, 4))
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT status FROM opt_runs WHERE request_id=?", (self.collect_body["request_id"],)).fetchone()[0], "failed")
            self.assertEqual(json.loads(db.execute("SELECT state FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0]), before)

    def test_older_cache_bytes_changed_without_digest_fail_full_pre_worker_audit(self):
        self.two_captures()
        with self.owner.database() as db:
            row = db.execute("SELECT value FROM opt_eod_data WHERE source_date=?", (DAY.isoformat(),)).fetchone()
            old = bytes(row[0]); changed = old.replace(b'"underlying_close":"12"', b'"underlying_close":"13"')
            self.assertNotEqual(old, changed)
            self.assertEqual(len(old), len(changed))
            db.execute("UPDATE opt_eod_data SET value=? WHERE source_date=?", (changed, DAY.isoformat()))
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker after changed old bytes")), self.assertRaisesRegex(ValueError, "older accepted EOD prefix cache row"):
            self.collect(date(2024, 1, 4))
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT status FROM opt_runs WHERE request_id=?", (self.collect_body["request_id"],)).fetchone()[0], "failed")

    def test_old_artifact_changed_while_worker_returns_blocks_new_publication(self):
        first = self.two_captures(); before = deepcopy(self.paper()["state"])
        path = self.registry._path(next(item["path"] for item in first["artifacts"] if item["path"].endswith("/bundle.json")))
        def changed(**kwargs):
            path.write_bytes(b"changed")
            return bundle(date(2024, 1, 4), self.clock)
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.collect(date(2024, 1, 4), collector=changed)
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT status FROM opt_runs WHERE request_id=?", (self.collect_body["request_id"],)).fetchone()[0], "failed")
            self.assertEqual(json.loads(db.execute("SELECT state FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0]), before)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_eod_data").fetchone()[0], 2)
            self.assertFalse(db.execute("SELECT 1 FROM opt_reports WHERE request_id=?", (self.collect_body["request_id"],)).fetchone())

    def test_metadata_audit_preserves_bounded_full_prefix_and_does_not_rehash_old_artifacts_on_status(self):
        first = self.two_captures()
        original = self.owner.eod.verify; checked = []
        def verify(receipt):
            checked.append(receipt["request_id"])
            return original(receipt)
        with patch.object(self.owner.eod, "verify", side_effect=verify):
            self.owner.status(self.clock)
        self.assertNotIn(first["request_id"], checked)
        with self.owner.database() as db:
            audit = self.owner.eod._audit_prefix(db, self.paper()["id"], self.paper()["state"], full=True)
        self.assertEqual(audit["snapshots"], 2)
        self.assertEqual(audit["prefix_sha256"], self.paper()["state"]["prefix_sha256"])

    def test_automatic_integrity_failure_is_recorded_once_before_network(self):
        self.two_captures()
        with self.owner.database() as db:
            db.execute("UPDATE opt_eod_data SET digest=? WHERE source_date=?", ("0"*64, DAY.isoformat()))
        self.clock = datetime(2024, 1, 5, 15, 5, tzinfo=UTC)
        with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker")):
            with self.assertRaisesRegex(ValueError, "accepted EOD prefix row differs"):
                self.owner.eod.run_due(self.clock)
            self.assertIsNone(self.owner.eod.run_due(self.clock+timedelta(minutes=1)))
        with self.owner.database() as db:
            rows = db.execute("SELECT status FROM opt_runs WHERE day='eod-feed-2024-01-04'").fetchall()
            self.assertEqual([row[0] for row in rows], ["failed"])

    def test_global_local_brief_internet_controls_block_worker(self):
        for control in ("global", "local", "brief", "internet"):
            with self.subTest(control=control):
                self.setUp()
                self.ready(); self.migrate(); self.clock = datetime(2024, 1, 3, 15, 5, tzinfo=UTC)
                if control == "global": self.store.set_setting("paused", True)
                elif control == "brief": self.controls["paused"] = True
                elif control == "internet": self.store.set_setting(SETTINGS_KEY, {"enabled": False, "revision": 2})
                else:
                    self.owner.configure({"enabled": True, "paused": True, "expected_revision": self.owner.status(self.clock)["revision"]}, self.clock)
                with patch.object(self.owner.eod, "_collect", side_effect=AssertionError("No worker")):
                    self.assertIsNone(self.owner.eod.run_due(self.clock))

    def test_migration_cannot_expand_shared_report_quota(self):
        self.ready(); before = deepcopy(self.paper())
        with self.owner.database() as db:
            for number in range(100):
                db.execute("INSERT INTO opt_reports VALUES(?,?,?)", ("prior-"+str(number), 1, "{}"))
        with self.assertRaisesRegex(ValueError, "quota is full"):
            self.migrate()
        self.assertEqual(self.paper()["state"], before["state"])
        self.assertFalse(self.owner.status(self.clock)["eod"]["config"]["initialized"])

    def test_legacy_archive_cannot_reset_or_remove_the_new_shared_account(self):
        self.ready(); self.migrate(); before = deepcopy(self.paper())
        with self.assertRaises(HandoffConflict):
            self.owner.archive(self.body(reason="new cap please", archive_data=True), self.clock)
        self.assertEqual(self.paper(), before)


if __name__ == "__main__":
    unittest.main()
