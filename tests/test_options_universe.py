"""Generated routing/clock/ledger evidence, never historical return evidence."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from fractions import Fraction
import unittest
from unittest.mock import patch

from ariadne import options_campaign as core, options_universe as universe
from ariadne.options_data import OptionsDataError, contract_id
from test_options_campaign import fixture, chunked
from test_options_regime import timeline_fixture


def stocks(*, symbols=("AAA", "BBB"), start=date(2023, 11, 1), end=date(2023, 12, 29),
           prices=None, sizes=10, quote_values=None):
    result = {}
    for symbol in symbols:
        data = fixture(start=start, end=end, sizes=sizes, prices=prices, quote_values=quote_values)
        for row in data["snapshots"]:
            row["underlying_symbol"] = symbol
            if prices is None: row["underlying_price"] = "50"
            for quote in row["quotes"]:
                quote["strike"] = "51" if quote["type"] == "call" else "49"
                quote["contract_id"] = contract_id(symbol, quote["expiry"], quote["type"], quote["strike"], research_symbols=symbols)
            row["quotes"].sort(key=lambda value: value["contract_id"])
        result[symbol] = data
    return result


def timeline(labels=None):
    value = timeline_fixture(350)
    observations = tuple(replace(row, label=(labels or {}).get(row.session, "recovery_growth")) for row in value.observations)
    return replace(value, observations=observations)


def run(data=None, *, benchmark=None, preset="conventional_regime", mechanical=True, **kwargs):
    data = data if data is not None else stocks()
    context = patch.object(core, "signal", return_value=True) if mechanical else patch.object(core, "signal", wraps=core.signal)
    with context:
        return universe.universe_campaign(data, symbols=tuple(data), benchmark=benchmark,
            preset=preset, liquidity_profiles=("strict_sizes",), **kwargs)


def candidate(report, identifier="raw_breakout_call", profile="strict_sizes"):
    return report["profiles"][profile]["candidate_results"][identifier]


def fills(report, action):
    return [event for event in report["events"] if event["type"] == "fill" and event["action"] == action]


class UniverseTests(unittest.TestCase):
    def test_exact_finite_profiles_and_rule_copy(self):
        self.assertEqual(len(universe.PRESETS), 2)
        for preset in universe.PRESETS:
            self.assertEqual(len(universe.PRESETS[preset]), 8)
            self.assertNotEqual(universe.profile_rule_sha256(preset, "strict_sizes"), universe.profile_rule_sha256(preset, "unknown_size_price_proxy"))
        changed = universe.profile_rule("conventional_regime", "strict_sizes")
        changed["presets"]["conventional_regime"].clear()
        self.assertEqual(len(universe.PRESETS["conventional_regime"]), 8)
        self.assertEqual(universe.KERNEL_RULE_SHA256, core.RULE_SHA256)
        self.assertEqual(universe.RULE["shared_monthly_gross_cap"], "500.00")

    def test_shared_contributions_fees_and_one_position_across_stocks(self):
        data = stocks(quote_values=lambda index, day: ("4.9", "5") if index % 4 in (3, 0) else ("0.95", "0.98"))
        value = candidate(run(data))
        self.assertEqual(value["metrics"]["contributions"], "1000")
        self.assertEqual([row["amount"] for row in value["events"] if row["type"] == "contribution"], ["500"])
        active, committed, total_fees = None, {}, Fraction(0)
        for event in value["events"]:
            if event["type"] != "fill": continue
            total_fees += Fraction(event["fee"])
            symbol = event["underlying_symbol"]
            if event["action"] == "BUY":
                self.assertIsNone(active); active = symbol
                month = event["source_date"][:7]
                committed[month] = committed.get(month, Fraction(0))+Fraction(event["budget_committed"])
                self.assertLessEqual(Fraction(event["budget_committed"]), 100)
            else:
                self.assertEqual(active, symbol); active = None
        self.assertGreater(len(fills(value, "BUY")), 1)
        self.assertEqual(total_fees, Fraction(value["metrics"]["fees"]))
        self.assertTrue(all(value <= 500 for value in committed.values()))
        self.assertEqual(sum(item["entries"] for item in value["symbol_attribution"].values()), value["metrics"]["entries"])
        self.assertEqual(sum(Fraction(item["fees"]) for item in value["symbol_attribution"].values()), total_fees)
        self.assertEqual(sum(Fraction(item["realized_pnl"]) for item in value["symbol_attribution"].values()), Fraction(value["metrics"]["realized_pnl"]))
        self.assertEqual(Fraction(value["metrics"]["ending_equity"])-Fraction(value["metrics"]["contributions"]), Fraction(value["metrics"]["trading_pnl"]))

    def test_round_robin_resumes_only_next_flat_frame_after_sale(self):
        data = stocks(quote_values=lambda index, day: ("4.9", "5") if index in (15, 16) else ("0.95", "0.98"))
        value = candidate(run(data))
        first_buy, first_sell = fills(value, "BUY")[0], fills(value, "SELL")[0]
        self.assertEqual(first_buy["underlying_symbol"], "AAA")
        later = [event for event in value["events"] if event["type"] == "decision" and event["action"] == "BUY" and event["decided_at"] > first_sell["available_at"]]
        self.assertTrue(later)
        self.assertEqual(later[0]["underlying_symbol"], "BBB")
        self.assertFalse(any(event["type"] == "decision" and event["action"] == "BUY" and event["decided_at"] == first_sell["available_at"] for event in value["events"]))

    def test_under100_gate_uses_saved_decision_and_fill_not_window_max(self):
        data = stocks(end=date(2023, 11, 30))
        for item in data.values():
            for row in item["snapshots"][:10]: row["underlying_price"] = "101"
        value = candidate(run(data))
        self.assertTrue(fills(value, "BUY"))
        self.assertTrue(all(Fraction(event["underlying_spot"]) < 100 for event in fills(value, "BUY")))
        denied = deepcopy(data)
        for item in denied.values():
            for row in item["snapshots"]: row["underlying_price"] = "100"
        self.assertEqual(candidate(run(denied))["metrics"]["entries"], 0)

    def test_pending_buy_above100_cancels_without_spend_or_same_frame_redecision(self):
        data = stocks(end=date(2023, 11, 30))
        for item in data.values(): item["snapshots"][14]["underlying_price"] = "100"
        value = candidate(run(data))
        cancellation = next(event for event in value["events"] if event["type"] == "cancelled" and event["reason"] == "entry_gate_blocked")
        self.assertEqual(cancellation["decision"]["underlying_symbol"], "AAA")
        self.assertFalse(any(event["type"] == "decision" and event["action"] == "BUY" and event["decided_at"] == data["AAA"]["snapshots"][14]["available_at"] for event in value["events"]))
        self.assertEqual(value["curve"][14]["monthly_committed"], "0")
        self.assertEqual(value["curve"][14]["fees"], "0")

    def test_held_position_can_sell_above100(self):
        data = stocks(end=date(2023, 11, 30), quote_values=lambda index, day: ("4.9", "5") if index in (15, 16) else ("0.95", "0.98"))
        for item in data.values():
            for row in item["snapshots"][15:]: row["underlying_price"] = "101"
        value = candidate(run(data))
        sale = fills(value, "SELL")[0]
        self.assertEqual(sale["observed_at"], data["AAA"]["snapshots"][16]["observed_at"])
        self.assertEqual(sale["trade_pnl"], "390.7")

    def test_fill_cannot_peek_later_current_benchmark_publication(self):
        data = stocks(end=date(2023, 11, 30))
        fill_day = data["AAA"]["snapshots"][14]["observed_at"][:10]
        report = run(data, benchmark=timeline({fill_day: "stress_unwind"}))
        value = candidate(report, "growth_breakout_call")
        buy = fills(value, "BUY")[0]
        self.assertEqual(buy["observed_at"], data["AAA"]["snapshots"][14]["observed_at"])
        self.assertEqual(buy["regime_label"], "recovery_growth")
        self.assertEqual(buy["regime_checked_at"], buy["observed_at"])
        contexts = report["context_observations"][14]
        self.assertEqual(contexts["decision"]["label"], "stress_unwind")
        self.assertEqual(contexts["fill"]["label"], "recovery_growth")

    def test_already_published_stress_cancels_pending_call_without_redecision(self):
        data = stocks(end=date(2023, 11, 30))
        # Stock snapshots are at15:30, published15:45; benchmark closes publish
        #16:15, AFTER the decision and BEFORE the next-session modeled fill.
        for item in data.values():
            for row in item["snapshots"]:
                row["observed_at"] = (datetime.fromisoformat(row["observed_at"])-timedelta(minutes=30)).isoformat()
                row["available_at"] = (datetime.fromisoformat(row["available_at"])-timedelta(minutes=30)).isoformat()
        decision_day = data["AAA"]["snapshots"][13]["observed_at"][:10]
        report = run(data, benchmark=timeline({decision_day: "stress_unwind"}))
        value = candidate(report, "growth_breakout_call")
        cancellation = next(event for event in value["events"] if event["type"] == "cancelled" and event["reason"] == "entry_gate_blocked")
        self.assertEqual(cancellation["regime_label"], "stress_unwind")
        self.assertEqual(cancellation["regime_checked_at"], data["AAA"]["snapshots"][14]["observed_at"])
        self.assertEqual(value["curve"][14]["fees"], "0")

    def test_missing_context_blocks_only_gated_exposure_and_discloses_gap(self):
        report = run(stocks(end=date(2023, 11, 30)))
        self.assertTrue(candidate(report)["metrics"]["entries"])
        value = candidate(report, "growth_breakout_call")
        self.assertEqual(value["metrics"]["entries"], 0)
        self.assertFalse(value["context_complete"])
        self.assertEqual(report["profiles"]["strict_sizes"]["comparison"]["ranked_ids"], [])

    def test_missing_exact_held_quote_holds_shared_portfolio_but_exhausts_all_stocks(self):
        data = stocks(end=date(2023, 11, 30))
        bought = fills(candidate(run(data)), "BUY")[0]
        row = data["AAA"]["snapshots"][15]
        row["quotes"] = [quote for quote in row["quotes"] if quote["contract_id"] != bought["contract_id"]]
        report = run(data)
        value = candidate(report)
        self.assertEqual(value["hold"]["code"], "missing_or_unexecutable_position_quote")
        self.assertEqual(value["metrics"]["entries"], 1)
        self.assertEqual(value["state"]["position"]["underlying_symbol"], "AAA")
        self.assertTrue(report["source_streams_exhausted"])
        self.assertEqual(report["coverage"]["source_rows"], len(data["AAA"]["snapshots"]))

    def test_unselected_later_invalid_stock_is_not_hidden_by_holds(self):
        data = stocks(end=date(2023, 11, 30))
        first, last = deepcopy(data["BBB"]), deepcopy(data["BBB"])
        first["snapshots"], last["snapshots"] = first["snapshots"][:-1], last["snapshots"][-1:]
        last["source"]["provider"] = "Changed unselected source"
        data["BBB"] = iter((first, last))
        with self.assertRaises(universe.UniverseError): run(data)

    def test_chunk_boundaries_and_map_order_do_not_change_results(self):
        data = stocks(end=date(2023, 11, 30))
        baseline = run(data)
        partitioned = {symbol: chunked(item, 3, 13, 15) for symbol, item in reversed(tuple(data.items()))}
        changed = run(partitioned)
        self.assertEqual(baseline["coverage"]["source_prefix_sha256"], changed["coverage"]["source_prefix_sha256"])
        for key in ("metrics", "state", "events", "curve", "months", "symbol_attribution"):
            self.assertEqual(candidate(baseline)[key], candidate(changed)[key])
        self.assertEqual(changed["symbols"], ["AAA", "BBB"])

    def test_no_inner_join_forward_fill_duplicate_or_backwards_frame(self):
        for mutation in ("short", "shift", "duplicate", "backwards"):
            data = stocks(end=date(2023, 11, 30))
            rows = data["BBB"]["snapshots"]
            if mutation == "short": rows.pop()
            elif mutation == "shift": rows[0]["available_at"] = (datetime.fromisoformat(rows[0]["available_at"])+timedelta(minutes=1)).isoformat()
            elif mutation == "duplicate": rows.insert(2, deepcopy(rows[1]))
            else: rows[1], rows[2] = rows[2], rows[1]
            with self.subTest(mutation=mutation), self.assertRaises((core.OptionsRecoveryError, OptionsDataError)): run(data)

    def test_common_missing_session_is_dated_hold_and_sources_still_exhaust(self):
        data = stocks(end=date(2023, 11, 30))
        for item in data.values(): item["snapshots"].pop(4)
        report = run(data)
        for value in report["profiles"]["strict_sizes"]["candidate_results"].values():
            self.assertEqual(value["hold"]["code"], "historical_session_gap")
            self.assertFalse(value["coverage"]["complete"])
        self.assertTrue(report["source_streams_exhausted"])

    def test_explicit_unknown_size_proxy_never_supplies_fake_sizes(self):
        data = stocks(end=date(2023, 11, 30), sizes=None)
        with patch.object(core, "signal", return_value=True):
            report = universe.universe_campaign(data, symbols=tuple(data))
        self.assertEqual(len(report["profiles"]), 2)
        self.assertEqual(candidate(report)["metrics"]["entries"], 0)
        self.assertGreater(candidate(report, profile="unknown_size_price_proxy")["metrics"]["entries"], 0)
        self.assertIsNone(data["AAA"]["snapshots"][0]["quotes"][0]["ask_size"])
        self.assertFalse(report["profiles"]["unknown_size_price_proxy"]["execution_qualified"])

    def test_bounds_fail_without_truncating_or_partial_output(self):
        for name, value in (("MAX_SOURCE_BYTES", 1), ("MAX_DAILY_QUOTES", 1), ("MAX_GROUPS", 1), ("MAX_OUTPUT_BYTES", 1), ("MAX_LEDGERS", 1)):
            with self.subTest(limit=name), patch.object(universe, name, value), self.assertRaises(universe.UniverseError): run(stocks(end=date(2023, 11, 30)))
        data = stocks(end=date(2023, 11, 30))
        data = {symbol: chunked(item, 2) for symbol, item in data.items()}
        with patch.object(universe, "MAX_CHUNKS", 1), self.assertRaises(universe.UniverseError): run(data)

    def test_arguments_are_fixed_bounded_and_research_only(self):
        data = stocks(end=date(2023, 11, 30))
        for kwargs in ({"preset": "eval"}, {"liquidity_profiles": ("strict_sizes", "strict_sizes")},
                       {"liquidity_profiles": ("live",)}, {"checkpoint": "code"}, {"benchmark": {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(core.OptionsRecoveryError): universe.universe_campaign(data, symbols=tuple(data), **kwargs)
        with self.assertRaises(core.OptionsRecoveryError): universe.universe_campaign(data, symbols=["AAA", "BBB"])
        with self.assertRaises((core.OptionsRecoveryError, OptionsDataError)): universe.universe_campaign(data, symbols=("AAA",))
        report = run(data)
        self.assertEqual((report["model_calls"], report["network_calls"], report["owner_operations"]), (0, 0, 0))
        self.assertFalse(report["deployable"]); self.assertFalse(report["automatic_adoption"])

    def test_volatility_preset_keeps_real_warmup_and_zero_trade_cases(self):
        report = run(stocks(end=date(2023, 11, 30)), preset="volatility_regime", mechanical=False)
        self.assertEqual(len(report["profiles"]["strict_sizes"]["candidate_results"]), 8)
        for value in report["profiles"]["strict_sizes"]["candidate_results"].values(): self.assertEqual(value["metrics"]["entries"], 0)
        self.assertGreater(candidate(report, "raw_pullback_call")["diagnostics"]["warmup_rows"], 0)

    def test_caller_decimal_context_and_inputs_remain_unchanged(self):
        data = stocks(end=date(2023, 11, 30))
        old = deepcopy(data); baseline = run(data)
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.traps[Inexact] = True
            self.assertEqual(run(data), baseline)
        self.assertEqual(data, old)

    def test_checkpoint_revocation_prevents_final_report(self):
        count = 0
        def checkpoint():
            nonlocal count
            count += 1
            if count == 12: raise RuntimeError("Generated authority revoked")
        with self.assertRaisesRegex(RuntimeError, "revoked"): run(stocks(end=date(2023, 11, 30)), checkpoint=checkpoint)

    def test_selector_skips_unaffordable_symbol_and_month_projection_never_funds_stocks(self):
        data = stocks(end=date(2023, 12, 29))
        for row in data["AAA"]["snapshots"]:
            for quote in row["quotes"]: quote["bid"], quote["ask"] = "1.9", "2"
        value = candidate(run(data))
        self.assertTrue(fills(value, "BUY"))
        self.assertTrue(all(event["underlying_symbol"] == "BBB" for event in fills(value, "BUY")))
        self.assertEqual(value["metrics"]["contributions"], "1000")
        self.assertEqual(len([event for event in value["events"] if event["type"] == "contribution"]), 1)
        row = next(row for row in data["BBB"]["snapshots"] if row["observed_at"].startswith("2023-12-01"))
        state = core._initial("breakout_call", data["BBB"]["snapshots"][0], "strict_sizes")
        state.update(month="2023-11", cash="0", monthly_committed="500")
        before = deepcopy(state)
        cached = universe._cache(row, ("strict_sizes",), {})
        self.assertTrue(universe._affordable(state, row, cached, dict.fromkeys(core.CONVENTIONAL_SIGNALS, True)))
        self.assertEqual(state, before)

    def test_profitable_exits_do_not_recycle_gross_monthly_allowance(self):
        # Early-month warmup is already present, but new month's cap remains500
        # even when each generated target exit adds cash to the same portfolio.
        data = stocks(start=date(2023, 10, 2), end=date(2023, 11, 30), quote_values=lambda index, day: ("4.9", "5") if index % 4 in (0, 3) else ("0.95", "0.98"))
        value = candidate(run(data))
        months = {}
        for event in fills(value, "BUY"):
            month = event["source_date"][:7]
            months[month] = months.get(month, Fraction(0))+Fraction(event["budget_committed"])
        self.assertTrue(months)
        self.assertTrue(all(amount <= 500 for amount in months.values()))
        self.assertGreater(Fraction(value["metrics"]["realized_pnl"]), 0)

    def test_exact99point99_is_accepted_and100_is_not(self):
        for price, permitted in (("99.99", True), ("100", False)):
            data = stocks(symbols=("AAA",), end=date(2023, 11, 30))
            for row in data["AAA"]["snapshots"]:
                row["underlying_price"] = price
                for quote in row["quotes"]:
                    quote["strike"] = "100" if quote["type"] == "call" else "99"
                    quote["contract_id"] = contract_id("AAA", quote["expiry"], quote["type"], quote["strike"], research_symbols=("AAA",))
                row["quotes"].sort(key=lambda quote: quote["contract_id"])
            self.assertEqual(bool(candidate(run(data))["metrics"]["entries"]), permitted)

    def test_held_shared_portfolio_cannot_gain_new_monthly_funding(self):
        data = stocks(end=date(2023, 12, 29))
        bought = fills(candidate(run(data)), "BUY")[0]
        data["AAA"]["snapshots"][15]["quotes"] = [quote for quote in data["AAA"]["snapshots"][15]["quotes"] if quote["contract_id"] != bought["contract_id"]]
        report = run(data)
        value = candidate(report)
        self.assertEqual(value["metrics"]["contributions"], "500")
        self.assertEqual(value["metrics"]["entries"], 1)
        self.assertEqual(value["hold"]["code"], "missing_or_unexecutable_position_quote")
        self.assertEqual(len(report["data_quality"]["BBB"]), 1)
        self.assertEqual(report["coverage"]["last_source_date"], "2023-12-29")

    def test_late_checkpoint_after_source_exhaustion_is_still_authoritative(self):
        data = stocks(end=date(2023, 11, 30))
        exhausted = set()
        def source(symbol):
            yield data[symbol]
            exhausted.add(symbol)
        def checkpoint():
            if exhausted == set(data): raise RuntimeError("Revoked at source EOF")
        with self.assertRaisesRegex(RuntimeError, "source EOF"):
            run({symbol: source(symbol) for symbol in data}, checkpoint=checkpoint)


if __name__ == "__main__": unittest.main()
