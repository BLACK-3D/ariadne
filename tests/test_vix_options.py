"""Synthetic software-only VIX contracts and chronology; no market evidence."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import unittest

from ariadne.briefing import _tz
from ariadne.qqq_data import session_dates, session_close_utc
from ariadne.vix_options import (VixOptionsError, PREREGISTRATION_SHA256, RULE_SHA256,
                                 STRATEGY_IDS, VALIDATION_POLICY, load_dataset, quality, compare, registration)

UTC = timezone.utc


def cid(root="VIX", expiry="2026-02-18", kind="call", strike="22"):
    return root+date.fromisoformat(expiry).strftime("%y%m%d")+("C" if kind == "call" else "P")+f"{int(Decimal(strike)*1000):08d}"


def fixture(*, target=True):
    """Fabricated constant-VX reference and option quotes, never market returns."""
    zone = _tz("America/Chicago")
    days = session_dates(date(2026, 1, 2), date(2026, 2, 27))
    sessions = []
    for day in days:
        opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=zone).astimezone(UTC)
        closed = datetime(day.year, day.month, day.day, 15, 15, tzinfo=zone).astimezone(UTC)
        sessions.append({"date": day.isoformat(), "open_at": opened.isoformat(), "close_at": closed.isoformat()})
    contract = {"contract_id": cid(), "root": "VIX", "type": "call", "strike": "22", "expiry": "2026-02-18",
                "last_trading_date": "2026-02-17", "last_trading_at": "2026-02-17T22:00:00Z", "exercise_style": "European",
                "settlement_type": "cash", "settlement_symbol": "VRO", "settlement_method": "AM_SOQ", "multiplier": 100, "deliverable_shares": 0}
    rows = []
    as_of = "2026-03-02T23:00:00Z"
    for index, item in enumerate(sessions):
        if item["date"] > "2026-02-20":
            break
        closed = datetime.fromisoformat(item["close_at"])
        available = (closed+timedelta(minutes=1)).isoformat()
        clocks = {"observed_at": item["close_at"], "available_at": available, "retrieved_at": as_of}
        bid, ask = ("6", "6.1") if target and 22 <= index <= 23 else ("1", "1.1")
        quote = {"contract_id": cid(), "bid": bid, "ask": ask, "bid_size": 5, "ask_size": 5, **clocks}
        references = [] if item["date"] > "2026-02-18" else [{"contract_id": "VX-2026-02-18", "symbol": "VX", "expiry": "2026-02-18", "price": "20", **clocks}]
        rows.append({"session": item["date"], **clocks, "vix_close": "20" if index < 20 else "30", "vix9d_close": None,
                     "references": references, "quotes": [quote] if item["date"] <= "2026-02-17" else []})
    return {"schema": 1, "kind": "vix_index_options_history", "name": "SYNTHETIC TEST ONLY", "synthetic": True, "as_of": as_of,
            "provenance": {"provider": "fabricated unittest fixture", "source_url": "local-file:/synthetic-vix-fixture.json", "raw_sha256": "a"*64,
                           "usage_rights_status": "synthetic_fixture", "availability_basis": "provider_recorded", "size_basis": "observed"},
            "calendar": {"timezone": "America/Chicago", "source_url": "https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf",
                         "checked_at": "2026-01-01T00:00:00Z", "version": "synthetic-test-calendar-2026", "coverage_start": "2026-01-02", "coverage_end": "2026-02-27",
                         "sessions": sessions, "contracts": [contract]}, "snapshots": rows}


def row(data, day):
    return next(item for item in data["snapshots"] if item["session"] == day)


class VixOptionsTests(unittest.TestCase):
    def test_frozen_registration_exact_bytes(self):
        self.assertNotEqual(RULE_SHA256, PREREGISTRATION_SHA256)
        path = Path(__file__).resolve().parent.parent/"ariadne"/"resources"/"vix"/"preregistration.json"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), PREREGISTRATION_SHA256)
        self.assertFalse(registration()["outcomes_seen_for_design"])
        self.assertEqual(tuple(registration()["signals"]["registered_order"]), STRATEGY_IDS)
        self.assertEqual(VALIDATION_POLICY["version"], "vix-options-validation-v2")

    def test_roundtrip_detached_no_input_mutation(self):
        data = fixture()
        original = deepcopy(data)
        self.assertEqual(load_dataset(json.dumps(data)), data)
        parsed = load_dataset(data)
        parsed["name"] = "changed local detached copy"
        report = compare(data)
        self.assertEqual(data, original)
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["execution_qualified"])
        self.assertFalse(report["forward_paper_authority"])
        self.assertEqual(report["research_gate"], "synthetic_fixture_not_market_evidence")
        self.assertIn("SYNTHETIC", report["summary"]["label"])
        self.assertLess(len(json.dumps(report["summary"]).encode()), 4000)
        self.assertLess(len(json.dumps(report["coverage"]).encode()), 4000)

    def test_target_requires_later_quote_and_fee_accounting(self):
        report = compare(fixture())
        ledger = report["shared_research"]
        self.assertEqual(ledger["closed_trades"], 1)
        trade = ledger["trades"][0]
        self.assertEqual(trade["entry_session"], "2026-02-03")
        self.assertEqual(trade["exit_session"], "2026-02-05")
        self.assertGreater(datetime.fromisoformat(trade["entry_at"]), datetime.fromisoformat(trade["decision_at"])+timedelta(seconds=60))
        self.assertEqual(trade["net_pnl"], "488.7")
        self.assertEqual(ledger["fees"], "1.3")
        february = next(item for item in ledger["monthly"] if item["month"] == "2026-02")
        self.assertEqual(february["committed"], "111.3")
        self.assertEqual(february["remaining"], "388.7")
        self.assertEqual(ledger["contributions"], "1000")
        self.assertEqual(ledger["realized_pnl"], "488.7")
        self.assertEqual(report["doubled_fee_stress"]["realized_pnl"], "487.4")
        self.assertEqual(ledger["payout_milestones"], {"2x": 1, "5x": 1, "10x": 0})

    def test_only_one_combined_research_budget_counterfactuals_never_summed(self):
        report = compare(fixture())
        self.assertEqual(set(report["counterfactuals"]), set(STRATEGY_IDS))
        ledger = report["shared_research"]
        self.assertTrue(ledger["separate_from_owner_account"])
        for item in ledger["daily"]:
            self.assertLessEqual(Decimal(item["monthly_committed"]), 500)
        self.assertIn("never sum", report["counterfactual_warning"])
        self.assertFalse(ledger["winning_strategy_claim"])

    def test_missing_mandatory_disposal_holds_without_inventing_settlement(self):
        data = fixture(target=False)
        row(data, "2026-02-13")["quotes"] = []
        report = compare(data)
        ledger = report["shared_research"]
        self.assertEqual(ledger["status"], "held")
        self.assertEqual(ledger["hold"], "missing_mandatory_disposal_quote")
        self.assertFalse(ledger["cap_qualified_model"])
        self.assertEqual(ledger["closed_trades"], 0)
        self.assertIsNotNone(ledger["position"])
        self.assertEqual(ledger["realized_pnl"], "0")
        self.assertTrue(ledger["daily"][-1]["mark_stale"])

    def test_forced_exit_precedes_last_trading_date(self):
        ledger = compare(fixture(target=False))["shared_research"]
        self.assertEqual(ledger["closed_trades"], 1)
        self.assertEqual(ledger["trades"][0]["exit_session"], "2026-02-13")
        self.assertEqual(ledger["trades"][0]["exit_reason"], "precommitted_disposal")

    def test_missing_exact_expiry_reference_blocks_entry(self):
        data = fixture()
        for item in data["snapshots"]:
            item["references"] = []
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 0)
        self.assertGreater(report["coverage"]["counts"]["missing_or_stale_exact_expiry_reference"], 0)
        self.assertIn("missing_or_stale_exact_expiry_reference", report["shared_research"]["rejections"])

    def test_wrong_expiry_reference_cannot_substitute(self):
        data = fixture()
        for item in data["snapshots"]:
            for reference in item["references"]:
                reference.update(contract_id="VX-2026-02-25", expiry="2026-02-25")
        self.assertEqual(compare(data)["shared_research"]["closed_trades"], 0)

    def test_spot_moneyness_is_not_forward_moneyness(self):
        data = fixture()
        # The synthetic spot signal goes to30; the future reference remains20.
        self.assertEqual(compare(data)["shared_research"]["closed_trades"], 1)
        for item in data["snapshots"]:
            for reference in item["references"]:
                reference["price"] = "30"
        self.assertEqual(compare(data)["shared_research"]["closed_trades"], 0)

    def test_missing_next_session_cancels_pending_entry(self):
        data = fixture()
        data["snapshots"] = [item for item in data["snapshots"] if item["session"] != "2026-02-03"]
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 0)
        self.assertEqual(report["shared_research"]["rejections"]["missing_next_session_cancels_entry"], 1)
        self.assertEqual(report["coverage"]["missing_session_count"], 1)

    def test_delayed_observation_cannot_fill_before_signal_available(self):
        data = fixture()
        row(data, "2026-02-02")["available_at"] = "2026-02-03T21:15:00Z"
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 0)
        self.assertEqual(report["shared_research"]["rejections"]["quote_not_later_than_durable_decision"], 1)

    def test_zero_size_wide_crossed_stale_or_over_cap_quotes_cannot_fill(self):
        cases = [({"ask_size": 0}, "no_observed_executable_size"),
                 ({"bid": "0.1", "ask": "1.1"}, "spread_above_25_percent"),
                 ({"bid": "2", "ask": "1.1"}, "nonpositive_or_crossed_quote"),
                 ({"bid": "1.2", "ask": "1.24"}, "premium_outside_per_entry_cap")]
        for mutation, reason in cases:
            with self.subTest(reason=reason):
                data = fixture()
                row(data, "2026-02-03")["quotes"][0].update(mutation)
                ledger = compare(data)["shared_research"]
                self.assertEqual(ledger["closed_trades"], 0)
                self.assertIn(reason, ledger["rejections"])
        data = fixture()
        quote = row(data, "2026-02-03")["quotes"][0]
        quote["observed_at"] = "2026-02-03T20:30:00Z"
        self.assertEqual(compare(data)["shared_research"]["rejections"]["stale_quote"], 1)

    def test_stale_reference_blocks_entry(self):
        data = fixture()
        row(data, "2026-02-03")["references"][0]["observed_at"] = "2026-02-03T20:30:00Z"
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["closed_trades"], 0)
        self.assertIn("missing_or_stale_exact_expiry_reference", ledger["rejections"])

    def test_registered_spike_fade_uses_long_put_only(self):
        data = fixture()
        contract = data["calendar"]["contracts"][0]
        identifier = cid(kind="put", strike="18")
        contract.update(contract_id=identifier, type="put", strike="18")
        for index, item in enumerate(data["snapshots"]):
            item["vix_close"] = "30" if index < 20 else "24"
            for quote in item["quotes"]: quote["contract_id"] = identifier
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 1)
        self.assertEqual(report["shared_research"]["trades"][0]["strategy_id"], STRATEGY_IDS[1])
        self.assertEqual(report["counterfactuals"][STRATEGY_IDS[0]]["closed_trades"], 0)

    def test_registered_short_term_stress_requires_six_observed_ratios(self):
        data = fixture()
        for index, item in enumerate(data["snapshots"]):
            item["vix_close"] = "20"
            item["vix9d_close"] = "18" if index < 20 else "22"
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["closed_trades"], 1)
        self.assertEqual(ledger["trades"][0]["strategy_id"], STRATEGY_IDS[2])
        row(data, "2026-01-30")["vix9d_close"] = None
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["closed_trades"], 0)

    def test_vixw_weekly_cash_option_supported(self):
        data = fixture()
        identifier = cid(root="VIXW", expiry="2026-02-11")
        data["calendar"]["contracts"][0].update(contract_id=identifier, root="VIXW", expiry="2026-02-11", last_trading_date="2026-02-10", last_trading_at="2026-02-10T22:00:00Z")
        for item in data["snapshots"]:
            if item["session"] > "2026-02-10": item["quotes"] = []
            for quote in item["quotes"]: quote["contract_id"] = identifier
            if item["session"] > "2026-02-11": item["references"] = []
            for reference in item["references"]: reference.update(contract_id="VX-2026-02-11", expiry="2026-02-11")
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["closed_trades"], 1)
        self.assertEqual(ledger["trades"][0]["contract_id"], identifier)

    def test_fee_stress_cannot_raise_entry_or_shared_allowance(self):
        data = fixture()
        for item in data["snapshots"]:
            for quote in item["quotes"]:
                if Decimal(quote["ask"]) < 2:
                    quote.update(bid="1.2", ask="1.237")
                else:
                    quote.update(bid="6.2", ask="6.3")
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 1)
        february = next(item for item in report["shared_research"]["monthly"] if item["month"] == "2026-02")
        self.assertEqual(february["committed"], "125")
        self.assertEqual(february["remaining"], "375")
        self.assertEqual(report["doubled_fee_stress"]["closed_trades"], 0)
        self.assertGreater(report["doubled_fee_stress"]["rejections"]["premium_outside_per_entry_cap"], 0)

    def test_profitable_synthetic_trades_do_not_recycle_shared_500_allowance(self):
        # This deliberately fabricated path verifies the risk ledger, not an edge.
        data = fixture()
        data["as_of"] = "2026-05-04T23:00:00Z"
        data["calendar"].update(coverage_start="2026-03-02", coverage_end="2026-04-30", sessions=[], contracts=[])
        schedule = [("VIX", "2026-04-15", "22", "2026-04-06"),
                    ("VIXW", "2026-04-22", "22", "2026-04-09"),
                    ("VIXW", "2026-04-29", "22", "2026-04-14"),
                    ("VIXW", "2026-04-29", "21", "2026-04-17"),
                    ("VIXW", "2026-04-29", "23", "2026-04-23")]
        zone = _tz("America/Chicago")
        days = session_dates(date(2026, 3, 2), date(2026, 4, 30))
        for root, expiry, strike, target in schedule:
            day = date.fromisoformat(expiry)
            ltd = day-timedelta(days=1)
            cutoff = datetime(ltd.year, ltd.month, ltd.day, 16, tzinfo=zone).astimezone(UTC).isoformat()
            data["calendar"]["contracts"].append({"contract_id": cid(root=root, expiry=expiry, strike=strike), "root": root, "type": "call", "strike": strike,
                "expiry": expiry, "last_trading_date": ltd.isoformat(), "last_trading_at": cutoff, "exercise_style": "European", "settlement_type": "cash",
                "settlement_symbol": "VRO", "settlement_method": "AM_SOQ", "multiplier": 100, "deliverable_shares": 0})
        data["snapshots"] = []
        for index, day in enumerate(days):
            closed = datetime(day.year, day.month, day.day, 15, 15, tzinfo=zone).astimezone(UTC)
            opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=zone).astimezone(UTC)
            data["calendar"]["sessions"].append({"date": day.isoformat(), "open_at": opened.isoformat(), "close_at": closed.isoformat()})
            clocks = {"observed_at": closed.isoformat(), "available_at": (closed+timedelta(minutes=1)).isoformat(), "retrieved_at": data["as_of"]}
            references = [{"contract_id": "VX-"+expiry, "symbol": "VX", "expiry": expiry, "price": "20", **clocks}
                          for expiry in sorted({item[1] for item in schedule}) if day.isoformat() <= expiry]
            quotes = []
            for contract, (_, _, _, target) in zip(data["calendar"]["contracts"], schedule):
                if day.isoformat() > contract["last_trading_date"]: continue
                bid, ask = ("6.2", "6.3") if day.isoformat() >= target else ("1.2", "1.237")
                quotes.append({"contract_id": contract["contract_id"], "bid": bid, "ask": ask, "bid_size": 5, "ask_size": 5, **clocks})
            spot = str((Decimal("20")*Decimal("1.11")**index).quantize(Decimal("0.0001")))
            data["snapshots"].append({"session": day.isoformat(), **clocks, "vix_close": spot, "vix9d_close": None, "references": references, "quotes": quotes})
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["closed_trades"], 4)
        april = next(item for item in ledger["monthly"] if item["month"] == "2026-04")
        self.assertEqual(april["committed"], "500")
        self.assertEqual(april["remaining"], "0")
        self.assertGreater(Decimal(ledger["cash"]), Decimal(ledger["contributions"]))
        self.assertGreater(ledger["rejections"]["shared_monthly_cap_or_cash_exhausted"], 0)
        self.assertEqual(sum(value["entries"] for value in ledger["strategy_attribution"].values()), 4)

    def test_held_exposure_never_rolls_month_or_adds_funding(self):
        data = fixture(target=False)
        row(data, "2026-02-13")["quotes"] = []
        zone = _tz("America/Chicago")
        for day in session_dates(date(2026, 3, 2), date(2026, 3, 31)):
            opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=zone).astimezone(UTC)
            closed = datetime(day.year, day.month, day.day, 15, 15, tzinfo=zone).astimezone(UTC)
            data["calendar"]["sessions"].append({"date": day.isoformat(), "open_at": opened.isoformat(), "close_at": closed.isoformat()})
            if day.day < 4:
                data["snapshots"].append({"session": day.isoformat(), "observed_at": closed.isoformat(), "available_at": (closed+timedelta(minutes=1)).isoformat(),
                    "retrieved_at": "2026-04-02T23:00:00Z", "vix_close": "30", "vix9d_close": None, "references": [], "quotes": []})
        data["calendar"]["coverage_end"] = "2026-03-31"
        data["as_of"] = "2026-04-02T23:00:00Z"
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["contributions"], "1000")
        self.assertEqual(ledger["daily"][-1]["month"], "2026-02")
        self.assertEqual([item["month"] for item in ledger["monthly"]], ["2026-01", "2026-02"])
        self.assertIsNotNone(ledger["position"])

    def test_incomplete_history_never_fabricates_exit(self):
        data = fixture(target=False)
        data["snapshots"] = [item for item in data["snapshots"] if item["session"] <= "2026-02-04"]
        ledger = compare(data)["shared_research"]
        self.assertEqual(ledger["status"], "incomplete_open_position")
        self.assertEqual(ledger["closed_trades"], 0)
        self.assertEqual(ledger["realized_pnl"], "0")
        self.assertIsNotNone(ledger["position"])

    def test_no_lookahead_data_and_reference_provenance(self):
        mutations = [lambda d: row(d, "2026-02-02").update(available_at="2026-02-02T21:14:59Z"),
                     lambda d: row(d, "2026-02-02")["quotes"][0].update(available_at="2026-02-02T22:00:00Z"),
                     lambda d: row(d, "2026-02-02")["references"][0].update(observed_at="2026-02-03T20:00:00Z"),
                     lambda d: d.update(as_of="2026-02-02T22:00:00Z")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = fixture(); mutate(data)
                with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_reject_wrong_contract_models_aliases_and_delivery(self):
        mutations = [{"root": "VXX"}, {"root": "VX"}, {"exercise_style": "American"}, {"settlement_type": "physical"},
                     {"settlement_method": "PM_CLOSE"}, {"settlement_symbol": "VIX"}, {"multiplier": True},
                     {"deliverable_shares": 100}, {"expiry": "2026-02-20"}, {"strike": "22.0001"}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = fixture(); data["calendar"]["contracts"][0].update(mutation)
                with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_reject_spot_and_continuous_reference_aliases(self):
        for identifier, symbol in (("VIX", "VIX"), ("VX1", "VX"), ("VXX", "VX"), ("VX-2026-02-25", "VX")):
            with self.subTest(identifier=identifier):
                data = fixture(); data["snapshots"][0]["references"][0].update(contract_id=identifier, symbol=symbol)
                with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_reject_missing_calendar_and_expiration_day_extension(self):
        data = fixture(); data["calendar"]["sessions"].pop(4)
        with self.assertRaises(VixOptionsError): load_dataset(data)
        data = fixture(); data["calendar"]["contracts"][0].update(last_trading_date="2026-02-18", last_trading_at="2026-02-18T14:00:00Z")
        with self.assertRaises(VixOptionsError): load_dataset(data)
        data = fixture(); data["calendar"]["contracts"][0]["last_trading_at"] = "2026-02-17T21:15:00Z"
        with self.assertRaises(VixOptionsError): load_dataset(data)
        data = fixture(); data["calendar"]["source_url"] = "https://cboe.com.attacker.invalid/calendar"
        with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_actual_pre_curb_history_rejected_and_synthetic_era_explicit(self):
        data = fixture()
        zone = _tz("America/Chicago")
        data["snapshots"] = []
        data["calendar"].update(coverage_start="2022-04-01", coverage_end="2022-05-31", sessions=[])
        for day in session_dates(date(2022, 4, 1), date(2022, 5, 31)):
            opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=zone).astimezone(UTC)
            data["calendar"]["sessions"].append({"date": day.isoformat(), "open_at": opened.isoformat(), "close_at": (session_close_utc(day)+timedelta(minutes=15)).isoformat()})
        data["calendar"]["contracts"][0].update(contract_id=cid(expiry="2022-05-18"), expiry="2022-05-18", last_trading_date="2022-05-17", last_trading_at="2022-05-17T21:00:00Z")
        self.assertTrue(quality(data)["synthetic"])
        data["synthetic"] = False
        data["provenance"]["usage_rights_status"] = "user_supplied_permitted"
        with self.assertRaisesRegex(VixOptionsError, "2022-04-25"):
            load_dataset(data)
        data["calendar"]["coverage_start"] = "2022-04-25"
        data["calendar"]["sessions"] = [item for item in data["calendar"]["sessions"] if item["date"] >= "2022-04-25"]
        report = compare(data)
        self.assertFalse(report["synthetic"])
        self.assertFalse(report["execution_qualified"])
        self.assertEqual(report["rule_sha256"], RULE_SHA256)
        self.assertEqual(report["registration_sha256"], PREREGISTRATION_SHA256)
        self.assertEqual(report["coverage"]["minimum_actual_calendar_date"], "2022-04-25")

    def test_early_close_last_trading_has_no_curb(self):
        data = fixture()
        zone = _tz("America/Chicago")
        data["snapshots"] = []
        data["calendar"].update(coverage_start="2023-07-01", coverage_end="2023-07-31", sessions=[])
        for day in session_dates(date(2023, 7, 1), date(2023, 7, 31)):
            opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=zone).astimezone(UTC)
            data["calendar"]["sessions"].append({"date": day.isoformat(), "open_at": opened.isoformat(), "close_at": (session_close_utc(day)+timedelta(minutes=15)).isoformat()})
        data["calendar"]["contracts"][0].update(contract_id=cid(root="VIXW", expiry="2023-07-05"), root="VIXW", expiry="2023-07-05", last_trading_date="2023-07-03", last_trading_at="2023-07-03T17:15:00Z")
        parsed = load_dataset(data)
        self.assertEqual(parsed["calendar"]["contracts"][0]["last_trading_at"], "2023-07-03T17:15:00Z")
        self.assertEqual(parsed, data)
        data["calendar"]["contracts"][0]["last_trading_at"] = "2023-07-03T18:00:00Z"
        with self.assertRaisesRegex(VixOptionsError, "no curb"):
            load_dataset(data)

    def test_fabricated_early_or_normal_session_close_is_rejected(self):
        data = fixture()
        next(item for item in data["calendar"]["sessions"] if item["date"] == "2026-02-03")["close_at"] = "2026-02-03T18:15:00Z"
        with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_duplicate_unknown_and_oversized_json(self):
        with self.assertRaises(VixOptionsError): load_dataset('{"schema":1,"schema":1}')
        with self.assertRaises(VixOptionsError): load_dataset(b"x"*(10*1024*1024+1))
        data = fixture(); data["activate_paper"] = True
        with self.assertRaises(VixOptionsError): load_dataset(data)
        data = fixture(); data["snapshots"][0]["quotes"][0]["open_interest"] = 100
        with self.assertRaises(VixOptionsError): load_dataset(data)

    def test_numbers_sizes_and_nan_are_strict(self):
        for key, value in (("bid", 1.0), ("bid", "NaN"), ("bid", "1e2"), ("bid", "-1"), ("bid_size", True), ("bid_size", None), ("bid_size", 1.5)):
            with self.subTest(key=key, value=value):
                data = fixture(); data["snapshots"][0]["quotes"][0][key] = value
                with self.assertRaises(VixOptionsError): load_dataset(data)
        with self.assertRaises(VixOptionsError): load_dataset('{"value":NaN}')

    def test_empty_quotes_and_empty_history_report_honestly(self):
        data = fixture()
        for item in data["snapshots"]: item["quotes"] = []
        report = compare(data)
        self.assertEqual(report["shared_research"]["closed_trades"], 0)
        self.assertEqual(report["quality"]["counts"], {})
        data["snapshots"] = []
        report = compare(data)
        self.assertEqual(report["summary"]["cash"], "500")
        self.assertEqual(report["coverage"]["observations"], 0)
        self.assertEqual(report["shared_research"]["trades"], [])

    def test_historical_assumption_and_actual_label_remain_unqualified(self):
        data = fixture()
        data["synthetic"] = False
        data["provenance"].update(usage_rights_status="user_supplied_permitted", availability_basis="historical_assumption")
        report = compare(data)
        self.assertFalse(report["execution_qualified"])
        self.assertEqual(report["research_gate"], "retrospective_vix_quotes_unverified")
        self.assertTrue(any("availability is assumed" in value for value in report["quality"]["assumptions"]))
        self.assertFalse(report["coverage"]["provenance_authenticated"])

    def test_decimal_context_does_not_change_results(self):
        data = fixture()
        expected = compare(data)
        with localcontext() as context:
            context.prec = 8
            self.assertEqual(compare(data), expected)


if __name__ == "__main__":
    unittest.main()
