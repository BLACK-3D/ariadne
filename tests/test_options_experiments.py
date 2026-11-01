"""Synthetic execution fixtures only; no acquired prices, accounts or networking."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_experiments as engine
from ariadne.qqq_data import session_dates, session_close_utc

D = Decimal
UTC = timezone.utc


def configuration(**changes):
    return {"id": "generated-execution-fixture", "signal": "raw_breakdown_put",
            "take_profit": "2", "max_hold": 5, "max_contracts": 1, **changes}


def quote(day, *, bid="0.95", ask="1", bid_size=100, ask_size=100, strike="20"):
    # No expiry price is invented: fixtures continue using a later contract
    # after the earlier instrument's expiration; identity is explicit.
    expiry = "2026-06-18" if day <= date(2026, 6, 18) else "2026-07-17" if day <= date(2026, 7, 17) else "2026-08-21"
    code = str(int(D(strike) * 1000)).zfill(8)
    return {"contract_id": "KO" + expiry[2:].replace("-", "") + "P" + code,
            "expiry": expiry, "type": "put", "strike": strike, "multiplier": 100,
            "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size,
            "volume": 100000, "open_interest": 500000}


def frames(*, end="2026-06-17", delayed=False, depth=100):
    result = []
    for day in session_dates(date(2026, 5, 11), date.fromisoformat(end)):
        observed = session_close_utc(day)
        if delayed:
            following = session_dates(day + timedelta(days=1), day + timedelta(days=10))[0]
            # Every generated date is May..August, when NY09:35 = UTC13:35.
            available = datetime(following.year, following.month, following.day, 13, 35, tzinfo=UTC)
        else:
            available = observed + timedelta(minutes=15)
        result.append({"observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "underlying_symbol": "KO", "underlying_price": "20",
            "quotes": [quote(day, bid_size=depth, ask_size=depth)]})
    return result


def row_at(rows, label):
    return next(row for row in rows if row["observed_at"][:10] == label)


def alter(rows, label, **values):
    row_at(rows, label)["quotes"][0].update(values)


def fills(report, action=None):
    return [event for event in report["events"] if event["type"] == "fill"
            and (action is None or event["action"] == action)]


def curve_at(report, label):
    return next(row for row in report["curve"] if row["source_date"] == label)


def ready_contexts(rows):
    decisions, contexts = [], []
    for row in rows:
        day = date.fromisoformat(row["observed_at"][:10])
        prior = session_dates(day - timedelta(days=10), day - timedelta(days=1))[-1]
        decisions.append({"status": "ready", "label": "stress_unwind",
                          "source_session": day.isoformat(), "available_at": row["available_at"]})
        contexts.append({"status": "ready", "label": "stress_unwind", "source_session": prior.isoformat(),
                         "available_at": (session_close_utc(prior) + timedelta(minutes=15)).isoformat()})
    return decisions, contexts


def study(rows=None, *, config=None, signal_days=("2026-06-01",), **arguments):
    rows = frames() if rows is None else rows
    selected = set(signal_days)
    calls = []
    def choose(config, history, row, state, context):
        label = row["observed_at"][:10]
        calls.append(label)
        return deepcopy(row["quotes"][0]) if label in selected else None
    with patch.object(engine, "choose", side_effect=choose):
        value = engine.run(rows, configuration() if config is None else config, **arguments)
    return value, calls


class ExperimentExecutionTests(unittest.TestCase):
    def test_later_fill_requires_later_new_york_day_not_merely_elapsed_or_utc_date(self):
        row = row_at(frames(delayed=True), "2026-06-02")
        self.assertFalse(engine.eligible_fill(row, "2026-06-02T13:35:00+00:00"))
        self.assertTrue(engine.eligible_fill(row, "2026-06-01T20:15:00+00:00"))
        crossed_utc_midnight = {"observed_at": "2026-06-02T00:01:01+00:00", "available_at": "2026-06-02T00:16:01+00:00"}
        self.assertFalse(engine.eligible_fill(crossed_utc_midnight, "2026-06-01T23:59:00+00:00"))

    def test_sizing_reserves_both_contract_fees_and_rounds_premium_up(self):
        exact = quote(date(2026, 6, 1), ask="9.987", bid="9.5")
        fit = engine.size("25000", "0", exact, None)
        self.assertEqual(fit["quantity"], 1)
        self.assertEqual(D(fit["all_in_commitment"]), D("1000"))
        just_over = {**exact, "ask": "9.9871"}
        self.assertEqual(engine.size("25000", "0", just_over, None)["quantity"], 0)
        precise = engine.size("25000", "0", {**exact, "ask": "0.12345678"}, 1)
        self.assertEqual(D(precise["unit_premium"]), D("12.35"))
        self.assertEqual(D(precise["entry_fee"]), D("0.65"))
        self.assertEqual(D(precise["reserved_exit_fee"]), D("0.65"))

    def test_sizing_obeys_four_percent_shared_month_remaining_and_both_actual_sides(self):
        source = quote(date(2026, 6, 1), ask="0.1", bid="0.095")
        result = engine.size("1000", "0", source, None)
        self.assertEqual(result["quantity"], 3)
        self.assertLessEqual(D(result["all_in_commitment"]), D("40"))
        self.assertEqual(engine.size("25000", "24999", source, None)["quantity"], 0)
        self.assertEqual(engine.size("25000", "0", {**source, "ask_size": 80, "bid_size": 2}, None)["quantity"], 2)
        for side in ("ask_size", "bid_size"):
            for value in (None, 0, True, "100"):
                with self.subTest(side=side, value=value):
                    self.assertEqual(engine.size("25000", "0", {**source, side: value}, None)["quantity"], 0)

    def test_public_sizing_rejects_invalid_quantity_caps_instead_of_negative_money(self):
        source = quote(date(2026, 6, 1))
        for cap in (-1, 0, 2, True, False, "1"):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                engine.size("25000", "0", source, cap)

    def test_ambient_decimal_precision_rounding_and_traps_cannot_change_sizing(self):
        source = quote(date(2026, 6, 1), ask="0.12345678", bid="0.12")
        expected = engine.size("24241.90", "100", source, None)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            self.assertEqual(engine.size("24241.90", "100", source, None), expected)

    def test_disposal_deadline_counts_regular_sessions_and_preexpiry_month_boundary(self):
        self.assertEqual(engine.deadline(date(2026, 6, 2), date(2026, 6, 18), 5), date(2026, 6, 9))
        self.assertEqual(engine.deadline(date(2026, 6, 10), date(2026, 6, 18), 10), date(2026, 6, 17))
        self.assertEqual(engine.deadline(date(2026, 6, 25), date(2026, 7, 17), 10), date(2026, 6, 29))

    def test_positive_actual_bid_exit_executes_even_with_wide_entry_spread(self):
        rows = frames()
        for label in ("2026-06-03", "2026-06-04"):
            alter(rows, label, bid="2", ask="3")
        value, _ = study(rows)
        self.assertEqual([event["source_date"] for event in fills(value, "BUY")], ["2026-06-02"])
        self.assertEqual([event["source_date"] for event in fills(value, "SELL")], ["2026-06-04"])
        self.assertEqual(D(fills(value, "SELL")[0]["price"]), D("2"))
        self.assertTrue(value["complete"])
        self.assertEqual(D(value["terminal_pnl"]), D("98.70"))
        self.assertEqual(D(value["state"]["fees"]), D("1.30"))
        self.assertEqual(value["state"]["closed_trades"], 1)

    def test_next0935_profile_waits_same_ny_day_for_both_entry_and_conditional_exit(self):
        rows = frames(delayed=True)
        for label in ("2026-06-04", "2026-06-05", "2026-06-08"):
            alter(rows, label, bid="2", ask="3")
        value, _ = study(rows)
        self.assertEqual([e["source_date"] for e in fills(value, "BUY")], ["2026-06-03"])
        self.assertEqual([e["source_date"] for e in fills(value, "SELL")], ["2026-06-08"])
        for event in fills(value):
            self.assertTrue(engine.eligible_fill(row_at(rows, event["source_date"]), event["decided_at"]))

    def test_first_eligible_unknown_entry_cancels_without_searching_future_liquidity(self):
        for delayed, rejected in ((False, "2026-06-02"), (True, "2026-06-03")):
            rows = frames(delayed=delayed)
            alter(rows, rejected, bid_size=None, ask_size=None)
            value, _ = study(rows)
            with self.subTest(delayed=delayed):
                self.assertEqual(fills(value), [])
                self.assertEqual(value["state"]["entries"], 0)
                self.assertEqual(D(value["state"]["cash"]), D("25000"))
                self.assertEqual(D(value["state"]["fees"]), 0)
                self.assertTrue(any(e["type"] == "cancelled" for e in value["events"]))
                self.assertTrue(value["complete"])

    def test_fill_quantity_cannot_grow_above_original_decision_target(self):
        rows = frames()
        alter(rows, "2026-06-01", bid_size=3, ask_size=3)
        alter(rows, "2026-06-02", bid="0.095", ask="0.1", bid_size=100, ask_size=100)
        value, _ = study(rows, config=configuration(max_contracts=None))
        entry = fills(value, "BUY")[0]
        self.assertEqual(entry["quantity"], 3)
        self.assertEqual(entry["decision_target_quantity"], 3)
        self.assertEqual(D(entry["budget_committed"]), D("33.90"))

    def test_fill_repricing_cannot_spend_1000_plus_fees_and_cancels_unfunded_order(self):
        rows = frames()
        alter(rows, "2026-06-02", bid="9.5", ask="9.9871")
        value, _ = study(rows)
        self.assertEqual(fills(value), [])
        self.assertEqual(D(value["state"]["fees"]), 0)
        self.assertEqual(D(value["state"]["cash"]), D("25000"))

    def test_partial_exits_use_each_days_actual_depth_once_and_close_position_once(self):
        rows = frames(depth=3)
        for label in ("2026-06-03", "2026-06-04", "2026-06-05", "2026-06-08"):
            alter(rows, label, bid="0.4", ask="1", bid_size=1)
        value, _ = study(rows, config=configuration(max_contracts=None))
        self.assertEqual(fills(value, "BUY")[0]["quantity"], 3)
        exits = fills(value, "SELL")
        self.assertEqual([(e["source_date"], e["quantity"]) for e in exits],
                         [("2026-06-04", 1), ("2026-06-05", 1), ("2026-06-08", 1)])
        self.assertEqual(len({e["source_date"] for e in exits}), 3)
        self.assertEqual(value["state"]["closed_trades"], 1)
        self.assertEqual(len(value["trades"]), 1)
        self.assertEqual(D(value["trades"][0]["pnl"]), D("-183.90"))
        self.assertEqual(D(value["state"]["fees"]), D("3.90"))
        self.assertEqual(D(value["terminal_pnl"]), D("-183.90"))
        self.assertTrue(value["complete"])
        self.assertFalse(value["nav_complete"])
        self.assertIsNone(value["max_drawdown"])
        # June5 sold the only displayed contract: the residual one cannot
        # reuse that same depth to acquire a full liquidation mark.
        self.assertIsNone(curve_at(value, "2026-06-05")["equity"])

    def test_observed_exit_threshold_latches_even_when_its_source_depth_is_unknown(self):
        rows = frames()
        alter(rows, "2026-06-03", bid="0.4", ask="1", bid_size=None)
        alter(rows, "2026-06-04", bid="0.3", ask="1", bid_size=100)
        value, _ = study(rows)
        decisions = [e for e in value["events"] if e["type"] == "decision" and e["action"] == "SELL"]
        self.assertEqual(decisions[0]["source_date"], "2026-06-03")
        self.assertEqual(fills(value, "SELL")[0]["source_date"], "2026-06-04")
        self.assertIsNone(curve_at(value, "2026-06-03")["equity"])

    def test_missing_mark_recovers_without_invented_nav_or_permanent_early_hold(self):
        rows = frames()
        row_at(rows, "2026-06-03")["quotes"] = [quote(date(2026, 6, 3), strike="21")]
        value, _ = study(rows)
        self.assertIsNone(curve_at(value, "2026-06-03")["equity"])
        self.assertIsNotNone(curve_at(value, "2026-06-04")["equity"])
        self.assertFalse(curve_at(value, "2026-06-03")["held"])
        self.assertTrue(value["complete"])
        self.assertIsNone(value["state"]["hold"])
        self.assertFalse(value["nav_complete"])
        self.assertIsNone(value["max_drawdown"])

    def test_residual_at_deadline_is_permanently_held_without_late_rescue(self):
        rows = frames(depth=3)
        alter(rows, "2026-06-09", bid_size=1)
        for row in rows:
            if row["observed_at"][:10] > "2026-06-09":
                row["quotes"][0].update(bid="10", ask="11", bid_size=100)
        value, _ = study(rows, config=configuration(max_contracts=None))
        self.assertEqual([(e["source_date"], e["quantity"]) for e in fills(value, "SELL")], [("2026-06-09", 1)])
        self.assertEqual(value["state"]["position"]["quantity"], 2)
        self.assertIsNotNone(value["state"]["hold"])
        self.assertFalse(value["complete"])
        self.assertIsNone(value["terminal_pnl"])
        self.assertEqual(value["state"]["closed_trades"], 0)
        self.assertEqual(D(value["state"]["fees"]), D("2.60"))
        self.assertTrue(value["all_source_frames_accounted"])

    def test_zero_bid_at_deadline_is_not_fabricated_zero_settlement(self):
        rows = frames()
        alter(rows, "2026-06-09", bid="0", ask="1", bid_size=100)
        value, _ = study(rows)
        self.assertEqual(fills(value, "SELL"), [])
        self.assertFalse(value["complete"])
        self.assertIsNotNone(value["state"]["hold"])
        self.assertIsNone(value["terminal_pnl"])
        self.assertEqual(D(value["state"]["fees"]), D("0.65"))

    def test_no_new_entry_or_choose_call_on_the_same_frame_as_full_liquidation(self):
        rows = frames()
        for label in ("2026-06-03", "2026-06-04"):
            alter(rows, label, bid="2", ask="3")
        value, calls = study(rows, signal_days=("2026-06-01", "2026-06-04", "2026-06-05"))
        self.assertNotIn("2026-06-04", calls)
        self.assertEqual([e["source_date"] for e in fills(value, "BUY")], ["2026-06-02", "2026-06-08"])

    def test_one_seed_no_sale_refund_and_no_design_check_or_month_cash_reset(self):
        rows = frames(end="2026-08-17")
        for label in ("2026-06-03", "2026-06-04"):
            alter(rows, label, bid="2", ask="3")
        value, _ = study(rows, signal_days=("2026-06-01", "2026-08-03"))
        first_commit = fills(value, "BUY")[0]["budget_committed"]
        self.assertEqual(D(curve_at(value, "2026-06-05")["monthly_committed"]), D(first_commit))
        self.assertEqual(D(curve_at(value, "2026-07-01")["monthly_committed"]), 0)
        self.assertEqual(D(curve_at(value, "2026-07-01")["cash"]), D("25098.70"))
        self.assertEqual(D(curve_at(value, "2026-07-31")["cash"]), D("25098.70"))
        self.assertEqual(D(curve_at(value, "2026-08-03")["cash"]), D("25098.70"))
        self.assertEqual(value["state"]["contributions"], "25000")
        self.assertTrue(all(e["funding_added"] == "0" for e in value["events"] if e["type"] == "month_budget_reset"))
        self.assertEqual(D(value["terminal_pnl"]), D("92.40"))

    def test_first_eligible_bad_fill_context_cancels_and_never_searches_later_regime(self):
        rows = frames()
        decisions, contexts = ready_contexts(rows)
        contexts[next(i for i, r in enumerate(rows) if r["observed_at"][:10] == "2026-06-02")]["label"] = "recovery_growth"
        value, _ = study(rows, config=configuration(signal="stress_breakdown_put"),
                         decision_contexts=decisions, fill_contexts=contexts)
        self.assertEqual(fills(value), [])
        self.assertEqual(value["state"]["entries"], 0)
        self.assertTrue(any(e["type"] == "cancelled" for e in value["events"]))

    def test_stale_or_future_ready_fill_context_is_rejected_at_the_session_open(self):
        rows = frames()
        index = next(i for i, row in enumerate(rows) if row["observed_at"][:10] == "2026-06-02")
        for label in ("2026-05-29", "2026-06-02"):
            decisions, contexts = ready_contexts(rows)
            source_day = date.fromisoformat(label)
            contexts[index].update(source_session=label,
                available_at=(session_close_utc(source_day) + timedelta(minutes=15)).isoformat())
            with self.subTest(source_day=label), self.assertRaises(ValueError):
                study(rows, config=configuration(signal="stress_breakdown_put"),
                      decision_contexts=decisions, fill_contexts=contexts)

    def test_stale_or_future_ready_decision_context_cannot_create_a_new_order(self):
        rows = frames()
        index = next(i for i, row in enumerate(rows) if row["observed_at"][:10] == "2026-06-01")
        for kind in ("stale", "future"):
            decisions, contexts = ready_contexts(rows)
            if kind == "stale":
                decisions[index].update(source_session="2026-05-29", available_at="2026-05-29T20:15:00+00:00")
            else:
                decisions[index]["available_at"] = "2026-06-01T20:16:00+00:00"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                study(rows, config=configuration(signal="stress_breakdown_put"),
                      decision_contexts=decisions, fill_contexts=contexts)

    def test_missing_registered_source_session_stops_new_exposure_and_remains_unranked(self):
        rows = [r for r in frames() if r["observed_at"][:10] != "2026-06-02"]
        value, _ = study(rows)
        self.assertIsNotNone(value["state"]["hold"])
        self.assertFalse(value["complete"])
        self.assertEqual(fills(value), [])
        self.assertTrue(value["all_source_frames_accounted"])

    def test_inputs_are_immutable_and_checkpoint_pause_propagates_without_further_frames(self):
        rows, config = frames(), configuration()
        before = deepcopy((rows, config))
        study(rows, config=config)
        self.assertEqual((rows, config), before)
        calls = []
        def pause():
            calls.append(True)
            if len(calls) == 4:
                raise RuntimeError("generated owner pause")
        with self.assertRaisesRegex(RuntimeError, "generated owner pause"):
            study(rows, config=config, checkpoint=pause)
        self.assertEqual(len(calls), 4)
        self.assertEqual((rows, config), before)

    def test_invalid_config_never_becomes_an_execution_extension(self):
        for changes in ({"signal": "unknown"}, {"max_hold": True}, {"take_profit": 2},
                        {"max_contracts": True}, {"max_contracts": -1}, {"id": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine.run(frames(), configuration(**changes))

    def test_fill_quantity_is_reduced_to_current_depth_even_when_original_target_is_larger(self):
        rows = frames()
        alter(rows, "2026-06-01", bid_size=3, ask_size=3)
        alter(rows, "2026-06-02", bid_size=2, ask_size=100)
        value, _ = study(rows, config=configuration(max_contracts=None))
        entry = fills(value, "BUY")[0]
        self.assertEqual(entry["decision_target_quantity"], 3)
        self.assertEqual(entry["quantity"], 2)
        self.assertEqual(D(entry["budget_committed"]), D("202.60"))

    def test_partial_liquidation_intent_stays_latched_after_bid_recovers(self):
        rows = frames(depth=3)
        alter(rows, "2026-06-03", bid="0.4", ask="1", bid_size=1)
        for label in ("2026-06-04", "2026-06-05", "2026-06-08"):
            alter(rows, label, bid="0.95", ask="1", bid_size=1)
        value, _ = study(rows, config=configuration(max_contracts=None))
        exits = fills(value, "SELL")
        self.assertEqual([(event["source_date"], event["quantity"]) for event in exits],
                         [("2026-06-04", 1), ("2026-06-05", 1), ("2026-06-08", 1)])
        self.assertTrue(all(event["decided_at"] == "2026-06-03T20:15:00+00:00" for event in exits))
        self.assertEqual(len(value["trades"]), 1)
        self.assertTrue(value["complete"])
        self.assertEqual(D(value["terminal_pnl"]), D("-18.90"))


if __name__ == "__main__":
    unittest.main()
