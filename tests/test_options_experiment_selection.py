"""Generated structural, numeric and causal-prefix experiment checks only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_experiments as engine
from ariadne import options_historical_reference as historical
from ariadne.qqq_data import session_dates, session_close_utc


def config(identifier="a", signal="ma5_reclaim_call", *, cap=1):
    return {"id": identifier, "signal": signal, "take_profit": "2", "max_hold": 5, "max_contracts": cap}


def report(identifier, profit, *, closed=1, complete=True):
    cash = Decimal("25000")+Decimal(profit)
    return {"config": config(identifier, "cash" if identifier == "cash" else "ma5_reclaim_call"),
        "state": {"cash": format(cash, "f"), "contributions": "25000", "closed_trades": closed,
            "position": None, "pending": None, "hold": None},
        "complete": complete, "all_source_frames_accounted": True,
        "terminal_pnl": profit if complete else None,
        "curve": [{"source_date": "2026-07-31", "available_at": "2026-07-31T20:15:00+00:00"}]}


def paired():
    return {engine.CLOCK_PROFILES[0]: [report("cash", "0", closed=0), report("a", "100"), report("b", "500")],
            engine.CLOCK_PROFILES[1]: [report("cash", "0", closed=0), report("a", "90"), report("b", "80")]}


def quote(*, expiry="2026-07-15", strike="20", bid="0.60", ask="0.65", bid_size=230, ask_size=230):
    numeric = int(Decimal(strike)*1000)
    return {"contract_id": "KO"+expiry[2:].replace("-", "")+"C"+format(numeric, "08d"),
        "expiry": expiry, "type": "call", "strike": strike, "multiplier": 100,
        "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size, "volume": 100000, "open_interest": 100000}


def selection_row(quotes):
    observed = session_close_utc(date(2026, 7, 1))
    return {"observed_at": observed.isoformat(), "available_at": (observed+timedelta(minutes=15)).isoformat(),
        "underlying_symbol": "KO", "underlying_price": "20", "quotes": quotes}


def generated_market(profile):
    result = []
    for day in session_dates(date(2026, 6, 1), date(2026, 8, 7)):
        observed = session_close_utc(day)
        _, available = historical.v1._availability(day, profile)
        spot = "19.5" if day == date(2026, 6, 30) else "20"
        bid, ask = {date(2026, 7, 7): ("1.30", "1.35"), date(2026, 7, 8): ("1.40", "1.45"),
                    date(2026, 7, 9): ("1.20", "1.25")}.get(day, ("0.60", "0.65"))
        result.append({"observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "underlying_symbol": "KO", "underlying_price": spot,
            "quotes": [quote(expiry=historical.expiry_for(day).isoformat(), bid=bid, ask=ask)]})
    return result


def design_reports(markets):
    cutoff = datetime.fromisoformat(engine.DESIGN_END)
    return {profile: [engine.run([deepcopy(row) for row in rows if datetime.fromisoformat(row["available_at"]) <= cutoff], config("real_generated"))]
            for profile, rows in markets.items()}


class ExperimentSelectionTests(unittest.TestCase):
    def test_selection_uses_worse_profile_profit_and_stable_lexical_tie_break(self):
        supplied = paired(); before = deepcopy(supplied)
        chosen = engine.select(supplied)
        self.assertEqual(chosen["candidate_id"], "a")
        self.assertEqual(chosen["ranked_ids"], ["a", "b"])
        self.assertEqual(Decimal(chosen["worst_terminal_pnl"]["a"]), Decimal(90))
        self.assertEqual(supplied, before)
        for reports in supplied.values():
            reports[1] = report("a", "10"); reports[2] = report("b", "10")
            reports.reverse()
        self.assertEqual(engine.select(supplied)["ranked_ids"], ["a", "b"])

    def test_both_exact_profiles_and_bounded_nonempty_lists_required(self):
        original = paired()
        for value in ({}, {"first": original[engine.CLOCK_PROFILES[0]], "second": original[engine.CLOCK_PROFILES[1]]},
                      {engine.CLOCK_PROFILES[0]: original[engine.CLOCK_PROFILES[0]]},
                      {**original, "third": []}, {engine.CLOCK_PROFILES[0]: [], engine.CLOCK_PROFILES[1]: []}):
            with self.assertRaises(ValueError): engine.select(value)
        invalid = paired(); invalid[engine.CLOCK_PROFILES[1]] = tuple(invalid[engine.CLOCK_PROFILES[1]])
        with self.assertRaises(ValueError): engine.select(invalid)

    def test_duplicate_in_either_profile_rejected_even_if_other_profile_count_differs(self):
        for profile in engine.CLOCK_PROFILES:
            supplied = paired(); supplied[profile].append(deepcopy(supplied[profile][1]))
            with self.subTest(profile=profile), self.assertRaises(ValueError): engine.select(supplied)

    def test_matched_ids_require_identical_configurations_across_profiles(self):
        supplied = paired(); supplied[engine.CLOCK_PROFILES[1]][1]["config"]["take_profit"] = "5"
        with self.assertRaises(ValueError): engine.select(supplied)
        supplied = paired(); supplied[engine.CLOCK_PROFILES[1]].pop()
        with self.assertRaises(ValueError): engine.select(supplied)

    def test_positive_completed_closed_trades_required_in_both_profiles(self):
        for mutation in (lambda r: r.update(terminal_pnl="0", state={**r["state"], "cash": "25000"}),
                         lambda r: r.update(terminal_pnl="-1", state={**r["state"], "cash": "24999"}),
                         lambda r: r["state"].update(closed_trades=0),
                         lambda r: r.update(complete=False, terminal_pnl=None),
                         lambda r: r.update(all_source_frames_accounted=False)):
            supplied = paired()
            for profile in engine.CLOCK_PROFILES:
                supplied[profile] = [report("a", "100")]
            mutation(supplied[engine.CLOCK_PROFILES[1]][0])
            self.assertEqual(engine.select(supplied)["candidate_id"], "cash")

    def test_truthy_nonboolean_completeness_and_bool_or_negative_trade_counts_rejected(self):
        mutations = (lambda r: r.update(complete="true"), lambda r: r.update(all_source_frames_accounted=1),
            lambda r: r["state"].update(closed_trades=True), lambda r: r["state"].update(closed_trades=-1),
            lambda r: r["state"].update(contributions="50000"))
        for mutate in mutations:
            supplied = paired(); mutate(supplied[engine.CLOCK_PROFILES[1]][1])
            with self.assertRaises(ValueError): engine.select(supplied)

    def test_claimed_complete_cannot_contradict_position_pending_hold_or_terminal_cash(self):
        mutations = (lambda r: r["state"].update(position={"quantity": 1}),
            lambda r: r["state"].update(pending={"action": "BUY"}), lambda r: r["state"].update(hold={"code": "held"}),
            lambda r: r["state"].update(position={}), lambda r: r["state"].update(pending={}), lambda r: r["state"].update(hold={}),
            lambda r: r.update(terminal_pnl=None), lambda r: r["state"].update(cash="25000"))
        for mutate in mutations:
            supplied = paired(); mutate(supplied[engine.CLOCK_PROFILES[0]][1])
            with self.assertRaises(ValueError): engine.select(supplied)

    def test_incomplete_held_report_is_retained_but_never_ranked(self):
        supplied = paired()
        for profile in engine.CLOCK_PROFILES:
            supplied[profile][1].update(complete=False, terminal_pnl=None)
            supplied[profile][1]["state"].update(position={"quantity": 1}, hold={"code": "residual"})
        result = engine.select(supplied)
        self.assertEqual(result["candidate_id"], "b")
        self.assertNotIn("a", result["ranked_ids"])

    def test_design_cutoff_rejects_later_availability_with_exact_boundary_inclusive(self):
        supplied = paired()
        for reports in supplied.values():
            for value in reports: value["curve"][0]["available_at"] = engine.DESIGN_END
        self.assertEqual(engine.select(supplied)["candidate_id"], "a")
        supplied[engine.CLOCK_PROFILES[1]][1]["curve"].append({"available_at": "2026-08-01T04:00:00+00:00"})
        with self.assertRaises(ValueError): engine.select(supplied)

    def test_later_price_and_liquidity_mutations_cannot_change_design_execution_or_selection(self):
        markets = {profile: generated_market(profile) for profile in engine.CLOCK_PROFILES}
        before = deepcopy(markets)
        first = design_reports(markets); first_choice = engine.select(first)
        self.assertTrue(all(values[0]["complete"] and values[0]["state"]["closed_trades"] > 0 for values in first.values()))
        self.assertEqual(first_choice["candidate_id"], "real_generated")
        cutoff = datetime.fromisoformat(engine.DESIGN_END)
        for rows in markets.values():
            for row in rows:
                if datetime.fromisoformat(row["available_at"]) > cutoff:
                    row["underlying_price"] = "90"
                    row["quotes"][0].update(bid="0", ask="10", bid_size=None, ask_size=None)
        second = design_reports(markets)
        self.assertEqual(first, second)
        self.assertEqual(first_choice, engine.select(second))
        self.assertNotEqual(markets, before)

    def test_direct_sizing_reserves_both_fees_and_rounds_premium_up_per_contract(self):
        sized = engine.size("25000", "0", quote(ask="0.65000001"), None)
        self.assertEqual(sized["quantity"], 15)
        self.assertEqual(Decimal(sized["unit_premium"]), Decimal("65.01"))
        self.assertEqual(Decimal(sized["entry_fee"]), Decimal("9.75"))
        self.assertEqual(Decimal(sized["reserved_exit_fee"]), Decimal("9.75"))
        self.assertEqual(Decimal(sized["all_in_commitment"]), Decimal("994.65"))

    def test_remaining_monthly_gross_cash_equity_and_current_both_side_depth_bound_size(self):
        self.assertEqual(engine.size("25000", "24950", quote(ask="0.10", bid="0.09"), None)["quantity"], 4)
        self.assertEqual(engine.size("25000", "24999", quote(), None)["quantity"], 0)
        low_cash = engine.size("1000", "0", quote(ask="0.10", bid="0.09"), None)
        self.assertEqual(low_cash["quantity"], 3)
        self.assertEqual(Decimal(low_cash["entry_risk_ceiling"]), Decimal(40))
        self.assertEqual(engine.size("25000", "0", quote(bid_size=3, ask_size=50), None)["quantity"], 3)
        self.assertEqual(engine.size("25000", "0", quote(bid_size=50, ask_size=2), None)["quantity"], 2)
        self.assertEqual(engine.size("25000", "0", quote(), 1)["quantity"], 1)
        for bad in (None, True, 0):
            self.assertEqual(engine.size("25000", "0", quote(bid_size=bad), None)["quantity"], 0)

    def test_sizing_and_selection_are_invariant_to_hostile_ambient_decimal_context(self):
        supplied = paired()
        expected_size, expected_choice = engine.size("25000", "0", quote(ask="0.65000001"), None), engine.select(supplied)
        cfg, state = config(), {"cash": "25000", "monthly_committed": "0"}
        history, row = [Decimal(20)]*12+[Decimal("19.5"), Decimal(20)], selection_row([quote(strike="20.5")])
        expected_contract = engine.choose(cfg, history, row, state, None)
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_DOWN; context.traps[Inexact] = True
            self.assertEqual(engine.size("25000", "0", quote(ask="0.65000001"), None), expected_size)
            self.assertEqual(engine.select(supplied), expected_choice)
            self.assertEqual(engine.choose(cfg, history, row, state, None), expected_contract)

    def test_current_contract_selection_prioritizes_dte_then_strike_without_future_data(self):
        cfg, state = config(), {"cash": "25000", "monthly_committed": "0"}
        history = [Decimal(20)]*12+[Decimal("19.5"), Decimal(20)]
        near_spot = quote(expiry="2026-07-17", strike="20")
        near_dte = quote(expiry="2026-07-15", strike="21")
        row = selection_row([near_spot, near_dte])
        self.assertEqual(engine.choose(cfg, history, row, state, None)["contract_id"], near_dte["contract_id"])
        same_dte_atm = quote()
        row["quotes"].append(same_dte_atm)
        self.assertEqual(engine.choose(cfg, history, row, state, None)["contract_id"], same_dte_atm["contract_id"])

    def test_unaffordable_or_unknown_current_quotes_do_not_displace_affordable_contract(self):
        cfg, state = config(), {"cash": "25000", "monthly_committed": "0"}
        history = [Decimal(20)]*12+[Decimal("19.5"), Decimal(20)]
        fallback = quote(expiry="2026-07-17")
        for bad in (quote(ask="11", bid="10.9"), quote(bid_size=None)):
            row = selection_row([bad, fallback])
            self.assertEqual(engine.choose(cfg, history, row, state, None)["contract_id"], fallback["contract_id"])

    def test_invalid_registered_configuration_shapes_and_types_rejected(self):
        mutations = (lambda c: c.update(signal="oracle_winner"), lambda c: c.update(max_hold=True),
            lambda c: c.update(max_hold=6), lambda c: c.update(max_contracts=2),
            lambda c: c.update(max_contracts=True), lambda c: c.update(take_profit=2),
            lambda c: c.update(take_profit="3"), lambda c: c.update(id=""), lambda c: c.update(extra="unexpected"))
        for mutate in mutations:
            value = config(); mutate(value)
            with self.assertRaises(ValueError): engine.validate_config(value)
        for bad in (-1, 0, 2, True, "1"):
            with self.subTest(max_contracts=bad), self.assertRaises(ValueError): engine.size("25000", "0", quote(), bad)


if __name__ == "__main__": unittest.main()
