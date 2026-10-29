"""Hand-reconciled offline research, causal fills and guarded artifacts."""
import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from ariadne.config import Settings
from ariadne.market_research import (MAX_BYTES, MarketResearchTools, audit_csv,
    market_timezone, run_study, simulate, validate_metadata)
from ariadne.tools import ToolRegistry


def metadata():
    return {"symbol": "DEMO", "currency": "USD", "source": "Synthetic test bars, not market data", "adjustment": "raw",
        "timeframe_minutes": 1, "timezone": "America/New_York", "session_open": "09:30", "session_close": "09:38", "weekdays": [0, 1, 2, 3, 4],
        "expected_sessions": ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"],
        "study": {"initial_cash": "1000", "shares": 2, "commission_per_order": "0.10", "fee_bps": "1", "spread_bps": "2", "slippage_bps": "1",
            "train_sessions": 1, "test_sessions": 1, "holdout_sessions": 1, "parameter_grid": [{"fast": 1, "slow": 2}, {"fast": 2, "slow": 3}]}}


def csv_data(last_multiplier=1):
    rows = ["timestamp,open,high,low,close,volume,symbol"]
    for day in range(4):
        for minute, close in enumerate((10, 11, 12, 11, 10, 9, 10, 11)):
            close = Decimal(close + day) * (last_multiplier if day == 3 else 1)
            rows.append(f"2026-10-{26+day}T09:{30+minute}:00-04:00,{close},{close+1},{close-1},{close},1000,DEMO")
    return ("\n".join(rows)+"\n").encode()


def bar(at, op, close, session="2026-10-29"):
    stamp = datetime(2026, 10, 29, 13, 30, tzinfo=timezone.utc)+timedelta(minutes=at)
    return {"timestamp": stamp.isoformat(), "end_timestamp": (stamp+timedelta(minutes=1)).isoformat(), "session": session,
        "open": Decimal(op), "close": Decimal(close), "high": max(Decimal(op), Decimal(close)),
        "low": min(Decimal(op), Decimal(close)), "volume": Decimal(1000)}


class MarketPureTests(unittest.TestCase):
    def test_offline_timezone_transition_rules_do_not_require_system_zoneinfo(self):
        factory = mock.Mock(side_effect=AssertionError("No system tzdata lookup"))
        factory.from_file = ZoneInfo.from_file
        with mock.patch("ariadne.market_research.ZoneInfo", factory), mock.patch("socket.socket", side_effect=AssertionError("No network")):
            zone = market_timezone("America/New_York")
        factory.assert_not_called()
        before_spring = datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).astimezone(zone)
        after_spring = datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc).astimezone(zone)
        self.assertEqual((before_spring.hour, before_spring.minute, after_spring.hour, after_spring.minute), (1, 59, 3, 0))
        first_fall = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc).astimezone(zone)
        second_fall = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).astimezone(zone)
        self.assertEqual((first_fall.hour, first_fall.fold, second_fall.hour, second_fall.fold), (1, 0, 1, 1))
        self.assertEqual(datetime(2026, 10, 29, 13, 30, tzinfo=timezone.utc).astimezone(market_timezone("America/Chicago")).hour, 8)
        with self.assertRaises(ValueError):
            market_timezone("Europe/London")

    def test_exact_costs_and_adverse_sides_match_hand_calculation(self):
        costs = {**metadata()["study"], "initial_cash": "100", "shares": 2, "commission_per_order": "1", "fee_bps": "0", "spread_bps": "20", "slippage_bps": "10"}
        result = simulate([bar(0, "10", "11"), bar(1, "11", "12")], costs, benchmark=True)
        self.assertEqual(result["fills"][0]["fill_price"], "10.020")
        self.assertEqual(result["fills"][1]["fill_price"], "11.976")
        self.assertEqual(Decimal(result["summary"]["ending_cash"]), Decimal("101.912"))
        self.assertEqual(result["summary"]["fees_paid"], "2.00")
        self.assertEqual(Decimal(result["summary"]["spread_slippage_cost"]), Decimal("0.088"))
        self.assertEqual(result["summary"]["ending_position"], 0)
        self.assertEqual(result["fills"][-1]["fill_clock"], "bar_close")

    def test_signal_cannot_fill_at_its_own_close_or_use_current_bar_close(self):
        costs = {**metadata()["study"], "commission_per_order": "0", "fee_bps": "0", "spread_bps": "0", "slippage_bps": "0", "shares": 1}
        result = simulate([bar(0, "3", "3"), bar(1, "100", "1"), bar(2, "7", "7")], costs,
            {"fast": 1, "slow": 2}, history=[bar(-2, "1", "1"), bar(-1, "2", "2")])
        self.assertEqual(result["fills"][0]["reference_price"], "100")
        self.assertEqual(result["fills"][0]["signal_bar_open_at"], bar(0, "3", "3")["timestamp"])
        self.assertEqual(result["fills"][0]["filled_at"], bar(1, "100", "1")["timestamp"])
        self.assertEqual(result["fills"][1]["reference_price"], "7")

    def test_exit_commission_is_reserved_without_borrowing(self):
        costs = {**metadata()["study"], "initial_cash": "20", "shares": 1, "commission_per_order": "6", "fee_bps": "0", "spread_bps": "0", "slippage_bps": "0"}
        result = simulate([bar(0, "10", "10"), bar(1, "10", "10")], costs, benchmark=True)
        self.assertEqual(result["summary"]["fills"], 0)
        self.assertEqual(result["summary"]["skipped_unaffordable_entries"], 2)
        self.assertEqual(result["summary"]["ending_cash"], "20")

    def test_fractional_cent_fixed_commission_cannot_underreserve_exit_fees(self):
        value = metadata(); value["study"]["commission_per_order"] = "1.00499"
        with self.assertRaises(ValueError):
            validate_metadata(value)
        value["study"]["commission_per_order"] = "1.00000000"
        self.assertEqual(validate_metadata(value)["study"]["commission_per_order"], "1.00000000")

    def test_no_pending_order_crosses_session_boundary(self):
        costs = {**metadata()["study"], "shares": 1}
        bars = [bar(0, "1", "1"), bar(1, "2", "3"), bar(2, "4", "4", "2026-10-30"), bar(3, "5", "5", "2026-10-30")]
        result = simulate(bars, costs, {"fast": 1, "slow": 2})
        self.assertTrue(all(fill["bar_open_at"] != bars[2]["timestamp"] for fill in result["fills"]))
        self.assertEqual(result["summary"]["ending_position"], 0)

    def test_complete_observed_sessions_pass_and_missing_whole_dates_are_explicit(self):
        bars, audit = audit_csv(csv_data(), validate_metadata(metadata()))
        self.assertTrue(audit["valid"])
        self.assertEqual(len(bars), 32)
        value = metadata(); value["expected_sessions"].append("2026-10-30")
        _, audit = audit_csv(csv_data(), validate_metadata(value))
        self.assertEqual(audit["issues"]["missing_session"], 1)
        value.pop("expected_sessions")
        _, audit = audit_csv(csv_data(), validate_metadata(value))
        self.assertIn("missing entire sessions", audit["calendar_basis"])

    def test_gaps_duplicates_order_and_bad_ohlc_are_reported_without_sorting(self):
        rows = csv_data().decode().splitlines()
        for kind, modified in (
            ("missing_session_bars", rows[:2]+rows[3:]),
            ("duplicate_timestamp", rows[:2]+[rows[1]]+rows[2:]),
            ("non_increasing_timestamp", [rows[0], rows[2], rows[1]]+rows[3:]),
            ("invalid_ohlc", [rows[0], rows[1].replace(",10,11,9,10,", ",10,8,9,10,")]+rows[2:])):
            with self.subTest(kind=kind):
                _, audit = audit_csv(("\n".join(modified)+"\n").encode(), validate_metadata(metadata()))
                self.assertFalse(audit["valid"])
                self.assertIn(kind, audit["issues"])

    def test_naive_timestamps_mixed_symbols_nan_and_oversized_cells_are_rejected(self):
        for bad in (csv_data().replace(b"-04:00", b""), csv_data().replace(b",DEMO", b",OTHER"), csv_data().replace(b",10,11,9,10,", b",NaN,11,9,10,")):
            _, audit = audit_csv(bad, validate_metadata(metadata()))
            self.assertFalse(audit["valid"])
        with self.assertRaises(ValueError):
            audit_csv(b"date,open,high,low,close,volume\n"+b"a"*150000, validate_metadata(metadata()))
        with self.assertRaises(ValueError):
            audit_csv(b"x"*(MAX_BYTES+1), validate_metadata(metadata()))

    def test_adjustment_timezone_and_unsupported_executable_fields_are_rejected(self):
        for field, bad in (("adjustment", {}), ("timezone", "No/SuchZone"), ("timeframe_minutes", True), ("session_close", "25:00"), ("weekdays", [True])):
            value = metadata(); value[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_metadata(value)
        value = metadata(); value["study"]["python"] = "print('arbitrary')"
        with self.assertRaises(ValueError):
            validate_metadata(value)

    def test_reserved_holdout_never_changes_parameter_selection(self):
        value = validate_metadata(metadata())
        bars1, audit1 = audit_csv(csv_data(), value)
        bars2, audit2 = audit_csv(csv_data(10), value)
        with localcontext() as ctx:
            ctx.prec = 40
            first = run_study(bars1, audit1, value)
            second = run_study(bars2, audit2, value)
        self.assertEqual(first["walk_forward"], second["walk_forward"])
        a, b = first["reserved_holdout"], second["reserved_holdout"]
        self.assertEqual(a["parameters_selected_before_holdout"], b["parameters_selected_before_holdout"])
        self.assertEqual(a["training_candidates"], b["training_candidates"])
        self.assertNotEqual(a["sma"]["summary"]["ending_cash"], b["sma"]["summary"]["ending_cash"])
        self.assertLess(a["selection_training_last_timestamp"], a["first_timestamp"])
        for fold in first["walk_forward"]["folds"]:
            self.assertLess(fold["training_last"], fold["test_first"])
        self.assertEqual(a["sma"]["summary"]["ending_position"], 0)

    def test_bad_data_daily_totalreturn_illiquidity_and_insufficient_splits_do_not_study(self):
        good = validate_metadata(metadata())
        bars, audit = audit_csv(csv_data(), good)
        changes = [{**good, "adjustment": "total_return_adjusted"}, {**good, "timeframe_minutes": 1440},
            {**good, "study": {**good["study"], "train_sessions": 3}}]
        for value in changes:
            with self.assertRaises(ValueError):
                run_study(bars, audit, value)
        with self.assertRaises(ValueError):
            run_study(bars, {**audit, "valid": False}, good)
        bars[0]["volume"] = Decimal(0)
        with self.assertRaises(ValueError):
            run_study(bars, audit, good)

    def test_daily_openbb_date_column_is_audited_without_invented_intraday_sessions(self):
        value = metadata(); value.update(timeframe_minutes=1440); value.pop("expected_sessions")
        bars, audit = audit_csv(b"date,open,high,low,close,volume\n2026-10-26,10,12,9,11,1000\n", validate_metadata(value))
        self.assertTrue(audit["valid"])
        self.assertEqual(len(bars), 1)

    def test_dst_fold_duplicate_session_slots_are_not_treated_as_ordinary_bars(self):
        value = metadata(); value.update(session_open="01:00", session_close="01:02", weekdays=[6]); value.pop("expected_sessions")
        rows = b"timestamp,open,high,low,close,volume\n2026-11-01T01:00:00-04:00,1,1,1,1,100\n2026-11-01T01:00:00-05:00,1,1,1,1,100\n"
        _, audit = audit_csv(rows, validate_metadata(value))
        self.assertIn("duplicate_session_slot", audit["issues"])


class MarketArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".market-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.registry = ToolRegistry(Settings(data_dir=Path(temporary.name), api_token="market-test-token"))
        self.tools = MarketResearchTools(self.registry)
        self.csv = self.registry.root / "bars.csv"; self.csv.write_bytes(csv_data())
        self.meta = self.registry.root / "assumptions.json"; self.meta.write_text(json.dumps(metadata()), encoding="utf-8")

    def args(self, **extra):
        return {"action": "study", "path": "bars.csv", "metadata_path": "assumptions.json", "request_id": "study-one", **extra}

    def test_offline_artifact_hash_and_exact_replay_are_verified(self):
        with mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No model or worker")):
            result = self.tools.execute("market_research", self.args())
            again = self.tools.execute("market_research", self.args())
        self.assertTrue(result["ok"])
        self.assertFalse(result["replayed"])
        self.assertTrue(again["replayed"])
        self.assertEqual(result["artifacts"], again["artifacts"])
        artifact = result["artifacts"][0]
        raw = self.registry._path(artifact["path"]).read_bytes()
        self.assertEqual(artifact["sha256"], hashlib.sha256(raw).hexdigest())
        report = json.loads(raw)
        self.assertEqual(report["inputs"]["csv"]["sha256"], hashlib.sha256(self.csv.read_bytes()).hexdigest())
        self.assertEqual(report["model_calls"], 0)

    def test_changed_inputs_and_tampered_report_never_overwrite_existing_id(self):
        result = self.tools.execute("market_research", self.args())
        path = self.registry._path(result["artifacts"][0]["path"])
        original = path.read_bytes()
        self.csv.write_bytes(csv_data(2))
        with self.assertRaises(ValueError):
            self.tools.execute("market_research", self.args())
        self.assertEqual(path.read_bytes(), original)
        self.csv.write_bytes(csv_data())
        path.write_bytes(original.replace(b'"model_calls":0', b'"model_calls":1'))
        changed = path.read_bytes()
        with self.assertRaises(ValueError):
            self.tools.execute("market_research", self.args())
        self.assertEqual(path.read_bytes(), changed)

    def test_paths_e_drive_traversal_and_request_injection_are_rejected_before_read(self):
        for args in (self.args(path="../bars.csv"), self.args(path="E:\\anything.csv"), self.args(metadata_path="/absolute.json"), self.args(request_id="../../outside"), self.args(action={})):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.tools.execute("market_research", args)

    def test_duplicate_metadata_and_nonfinite_values_are_not_accepted(self):
        for raw in ('{"symbol":"A","symbol":"B"}', '{"x":NaN}'):
            self.meta.write_text(raw)
            with self.assertRaises(ValueError):
                self.tools.execute("market_research", self.args())

    def test_cancellation_does_not_publish_and_fingerprint_tracks_source_changes(self):
        first = self.tools.fingerprint_material("market_research", self.args())
        self.csv.write_bytes(csv_data(2))
        second = self.tools.fingerprint_material("market_research", self.args())
        self.assertNotEqual(first["csv_sha256"], second["csv_sha256"])
        with self.assertRaises(ValueError):
            self.tools.execute("market_research", self.args(), cancelled=lambda: True)
        self.assertFalse((self.registry.root / "reports").exists())

    def test_bad_data_can_be_audited_but_study_refuses(self):
        self.csv.write_bytes(csv_data().replace(b",10,11,9,10,", b",10,8,9,10,"))
        result = self.tools.execute("market_research", self.args(action="audit", request_id="audit-bad"))
        self.assertFalse(result["summary"]["valid"])
        with self.assertRaises(ValueError):
            self.tools.execute("market_research", self.args())

    def test_large_derived_artifact_uses_bounded_atomic_bytes_and_rechecks_hash(self):
        value = metadata(); value["session_close"] = "12:50"
        rows = ["timestamp,open,high,low,close,volume,symbol"]
        for day in range(4):
            start = datetime(2026, 10, 26+day, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
            for minute in range(200):
                price = 10 + minute % 8
                rows.append(f"{(start+timedelta(minutes=minute)).isoformat()},{price},{price+1},{price-1},{price},1000,DEMO")
        self.csv.write_text("\n".join(rows)+"\n")
        self.meta.write_text(json.dumps(value))
        result = self.tools.execute("market_research", self.args())
        artifact = result["artifacts"][0]
        self.assertGreater(artifact["bytes"], 100000)
        self.assertEqual(hashlib.sha256(self.registry._path(artifact["path"]).read_bytes()).hexdigest(), artifact["sha256"])

    def test_modified_timezone_record_is_rejected_before_dataset_calculation(self):
        source = Path(__file__).resolve().parents[1] / "ariadne/resources/market/America_New_York.tzif"
        fake = self.registry.root / "tampered.tzif"
        fake.write_bytes(source.read_bytes()+b"changed")
        with mock.patch("ariadne.market_research._allowed_path", return_value=fake), self.assertRaises(ValueError):
            market_timezone("America/New_York")

    def test_symlink_or_hardlinked_inputs_do_not_escape_guard(self):
        linked = self.registry.root / "linked.csv"
        try:
            linked.hardlink_to(self.csv)
        except OSError:
            self.skipTest("Hardlink creation unavailable")
        with self.assertRaises(ValueError):
            self.tools.execute("market_research", self.args(path="linked.csv"))


if __name__ == "__main__":
    unittest.main()
