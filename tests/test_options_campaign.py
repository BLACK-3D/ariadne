"""Generated contract/clock/risk evidence; never market-performance evidence."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, Inexact, ROUND_DOWN, ROUND_UP, localcontext
from fractions import Fraction
import hashlib
import unittest
from unittest.mock import patch

from ariadne.options_campaign import (campaign, campaign_profiles, signal, profile_rule,
    profile_rule_sha256, RULE, RULE_SHA256, CANDIDATE_IDS, OptionsRecoveryError)
from ariadne.options_data import contract_id
from ariadne.qqq_data import session_dates, session_close_utc


def fixture(*, start=date(2023, 11, 1), end=date(2023, 11, 30), sizes=10,
            prices=None, quote_values=None, historical=False):
    rows = []
    days = session_dates(start, end)
    for index, day in enumerate(days):
        month_end = (day.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        month_sessions = session_dates(day.replace(day=1), month_end)
        expiries = sorted({item for item in month_sessions if item >= day and item.weekday() == 4} | {month_sessions[-1]})
        price = prices[index] if prices is not None else Decimal(100)+Decimal(index)/100
        bid, ask = quote_values(index, day) if quote_values else ("0.95", "0.98")
        chain = [{"contract_id": contract_id("QQQ", expiry.isoformat(), kind, strike),
            "type": kind, "expiry": expiry.isoformat(), "strike": strike, "multiplier": 100,
            "bid": bid, "ask": ask, "bid_size": sizes, "ask_size": sizes,
            "volume": 100, "open_interest": 1000}
            for expiry in expiries for kind, strike in (("call", "103"), ("put", "98"))]
        observed = session_close_utc(day)
        rows.append({"observed_at": observed.isoformat(), "available_at": (observed+timedelta(minutes=15)).isoformat(),
            "underlying_symbol": "QQQ", "underlying_price": str(price),
            "quotes": sorted(chain, key=lambda item: item["contract_id"])})
    as_of = (datetime.fromisoformat(rows[-1]["available_at"])+timedelta(minutes=1)).isoformat()
    return {"schema": 1, "evidence_kind": "historical_quotes" if historical else "synthetic_fixture",
        "as_of": as_of, "currency": "USD", "source": {
            "provider": "Generated campaign regression fixture, not market history",
            "url": "https://example.com/campaign-regression", "sha256": hashlib.sha256(b"campaign-regression").hexdigest(),
            "retrieved_at": as_of, "usage": "Entirely synthetic fixtures, including any historical_quotes schema-label test.",
            "quote_time_basis": "assumed_eod", "independently_verified": False}, "snapshots": rows}


def result(dataset, candidate="breakout_call", **kwargs):
    return campaign(dataset, candidates=[candidate], **kwargs)["candidate_results"][candidate]


def fills(report, action):
    return [event for event in report["events"] if event.get("type") == "fill" and event["action"] == action]


def chunked(data, *breaks):
    previous = 0
    for end in (*breaks, len(data["snapshots"])):
        value = deepcopy(data)
        value["snapshots"] = value["snapshots"][previous:end]
        yield value
        previous = end


class CampaignTests(unittest.TestCase):
    def test_fixed_scope_and_distinct_profile_identity(self):
        self.assertEqual(CANDIDATE_IDS, ("cash", "recovery_call", "breakout_call", "breakdown_put", "rotating_ensemble",
            "rsi_rebound_call", "rsi_exhaustion_put", "diversified_ensemble"))
        self.assertEqual(RULE["entry_maximum_debit"], "100.00")
        self.assertEqual(RULE["monthly_maximum_debit"], "500.00")
        self.assertNotEqual(profile_rule_sha256("strict_sizes"), profile_rule_sha256("unknown_size_price_proxy"))
        self.assertEqual(RULE["candidates"]["rotating_ensemble"]["signals"], ["recovery_call", "breakout_call", "breakdown_put"])
        self.assertEqual(RULE["candidates"]["diversified_ensemble"]["signals"],
            ["recovery_call", "breakout_call", "breakdown_put", "rsi_rebound_call", "rsi_exhaustion_put"])
        self.assertEqual(profile_rule("strict_sizes")["liquidity_profile"], "strict_sizes")
        changed = profile_rule("strict_sizes"); changed["entry_maximum_debit"] = "999"
        self.assertEqual(RULE["entry_maximum_debit"], "100.00")

    def test_no_arbitrary_ids_duplicate_profiles_or_f_account(self):
        data = fixture()
        for value in ([], ["cash", "cash"], ["eval"], "cash", [True]):
            with self.subTest(value=value), self.assertRaises(OptionsRecoveryError):
                campaign(data, candidates=value)
        for profile in ("live", None, [], "STRICT_SIZES"):
            with self.subTest(profile=profile), self.assertRaises(OptionsRecoveryError):
                campaign(data, liquidity_profile=profile)
        with self.assertRaises(OptionsRecoveryError): campaign(data, symbol="F")
        with self.assertRaises(OptionsRecoveryError): campaign(data, checkpoint="arbitrary code")

    def test_warmup_and_deposits_do_not_become_trading_returns(self):
        data = fixture(end=date(2023, 12, 29))
        value = result(data, "cash")
        self.assertEqual(value["metrics"]["trading_pnl"], "0")
        self.assertEqual(value["metrics"]["ending_equity"], value["metrics"]["contributions"])
        self.assertEqual(value["metrics"]["closed_trades"], 0)
        self.assertIsNone(value["metrics"]["observed_win_rate_pct"])
        self.assertGreater(Decimal(value["metrics"]["contributions"]), 500)
        small = deepcopy(data); small["snapshots"] = small["snapshots"][:13]
        self.assertEqual(result(small)["metrics"]["entries"], 0)

    def test_exact_delayed_ask_entry_and_reserved_fees(self):
        data = fixture()
        value = result(data)
        buy = fills(value, "BUY")[0]
        self.assertEqual(buy["observed_at"], data["snapshots"][14]["observed_at"])
        self.assertGreater(datetime.fromisoformat(buy["observed_at"]), datetime.fromisoformat(buy["decided_at"])+timedelta(seconds=60))
        self.assertEqual(Fraction(buy["budget_committed"]), Fraction("98")+Fraction("1.30"))
        self.assertEqual(buy["price"], "0.98")

    def test_exact100_limit_does_not_inherit_legacy125(self):
        accepted = result(fixture(quote_values=lambda index, day: ("0.96", "0.987")))
        self.assertEqual(fills(accepted, "BUY")[0]["budget_committed"], "100")
        rejected = result(fixture(quote_values=lambda index, day: ("0.98", "0.99")))
        self.assertEqual(rejected["metrics"]["entries"], 0)
        self.assertGreater(rejected["diagnostics"]["rejections"]["entry_over_100_including_fees"], 0)

    def test_target_decision_is_not_same_quote_fill_or_net5x(self):
        data = fixture(quote_values=lambda index, day: ("4.9", "5") if index in (15, 16) else ("0.95", "0.98"))
        value = result(data)
        sale = fills(value, "SELL")[0]
        self.assertEqual(sale["observed_at"], data["snapshots"][16]["observed_at"])
        self.assertEqual(sale["reasons"], ["bid_reached_5x_entry_ask"])
        decision = next(event for event in value["events"] if event["type"] == "decision" and event["action"] == "SELL")
        self.assertEqual(decision["decided_at"], data["snapshots"][15]["available_at"])
        self.assertEqual(sale["trade_pnl"], "390.7")
        self.assertEqual(value["metrics"]["payout_milestones"], {"2x": 1, "5x": 0, "10x": 0})

    def test_stop_observation_sells_at_next_worse_bid(self):
        def values(index, day):
            return ("0.49", "0.50") if index == 15 else ("0.4", "0.41") if index == 16 else ("0.95", "0.98")
        value = result(fixture(quote_values=values))
        sale = fills(value, "SELL")[0]
        self.assertEqual(sale["price"], "0.4")
        self.assertEqual(sale["trade_pnl"], "-59.3")
        self.assertEqual(sale["reasons"], ["bid_at_or_below_half_entry_ask"])

    def test_missing_sizes_stay_unknown_in_explicit_proxy_only(self):
        data = fixture(sizes=None)
        reports = campaign_profiles(data)["profiles"]
        strict = reports["strict_sizes"]["candidate_results"]["breakout_call"]
        proxy = reports["unknown_size_price_proxy"]["candidate_results"]["breakout_call"]
        self.assertEqual(strict["metrics"]["entries"], 0)
        self.assertGreater(strict["diagnostics"]["rejections"]["ask_size_unavailable_or_zero"], 0)
        self.assertGreater(proxy["metrics"]["entries"], 0)
        self.assertTrue(all(quote["ask_size"] is None for row in data["snapshots"] for quote in row["quotes"]))
        self.assertFalse(reports["unknown_size_price_proxy"]["liquidity_verified"])
        self.assertFalse(reports["unknown_size_price_proxy"]["deployable"])

    def test_actual_zero_size_never_becomes_unknown_proxy_permission(self):
        for profile in ("strict_sizes", "unknown_size_price_proxy"):
            with self.subTest(profile=profile):
                self.assertEqual(result(fixture(sizes=0), liquidity_profile=profile)["metrics"]["entries"], 0)

    def test_later_held_quote_does_not_invent_exit_or_mark(self):
        data = fixture()
        held_id = fills(result(data), "BUY")[0]["contract_id"]
        data["snapshots"][15]["quotes"] = [quote for quote in data["snapshots"][15]["quotes"] if quote["contract_id"] != held_id]
        value = result(data)
        self.assertEqual(value["hold"]["code"], "missing_or_unexecutable_position_quote")
        self.assertFalse(value["coverage"]["complete"])
        self.assertEqual(value["coverage"]["last_processed_date"], "2023-11-21")
        self.assertEqual(value["metrics"]["closed_trades"], 0)
        self.assertEqual(value["state"]["position"]["marked_bid"], "0.95")

    def test_gap_holds_all_ledgers_but_exhausts_later_chunks(self):
        data = fixture()
        del data["snapshots"][15]
        consumed = []
        def stream():
            for index, part in enumerate(chunked(data, 16)):
                consumed.append(index); yield part
        reports = campaign_profiles(stream())
        self.assertEqual(consumed, [0, 1])
        self.assertTrue(reports["coverage"]["source_stream_exhausted"])
        for profile in reports["profiles"].values():
            self.assertEqual(profile["comparison"]["ranked_ids"], [])
            self.assertTrue(all(value["hold"]["code"] == "historical_session_gap" for value in profile["candidate_results"].values()))

    def test_corrupt_later_chunk_invalidates_even_after_all_hold(self):
        data = fixture(); del data["snapshots"][1]
        parts = list(chunked(data, 16))
        parts[1]["snapshots"][0]["quotes"][0]["ask"] = "nan"
        with self.assertRaises(ValueError): campaign_profiles(iter(parts))

    def test_overlapping_chunks_and_changed_evidence_do_not_reset(self):
        data = fixture(); first, second = list(chunked(data, 10))
        with self.assertRaises(ValueError): campaign(iter([first, first]))
        second["source"]["provider"] = "Different provider"
        with self.assertRaises(ValueError): campaign(iter([first, second]))

    def test_chunked_full_reports_equal_and_validate_once_per_chunk(self):
        import ariadne.options_campaign as module
        data = fixture()
        expected = campaign_profiles(data)
        original = module._rows
        with patch.object(module, "_rows", wraps=original) as checked:
            actual = campaign_profiles(chunked(data, 7, 15))
        self.assertEqual(checked.call_count, 3)
        self.assertEqual(actual["coverage"]["source_rows"], expected["coverage"]["source_rows"])
        self.assertEqual(actual["coverage"]["source_prefix_sha256"], expected["coverage"]["source_prefix_sha256"])
        for profile, report in actual["profiles"].items():
            for key, candidate in report["candidate_results"].items():
                reference = expected["profiles"][profile]["candidate_results"][key]
                self.assertEqual(candidate["metrics"], reference["metrics"])
                self.assertEqual(candidate["events"], reference["events"])
                self.assertEqual(candidate["curve"], reference["curve"])

    def test_checkpoint_runs_each_row_and_final_and_revokes_no_return(self):
        data = fixture(); calls = []
        campaign_profiles(data, checkpoint=lambda: calls.append(1))
        self.assertEqual(len(calls), len(data["snapshots"])+1)
        before = deepcopy(data)
        calls = []
        def revoke():
            calls.append(1)
            if len(calls) == 15: raise RuntimeError("Owner pause epoch changed")
        with self.assertRaisesRegex(RuntimeError, "Owner pause"):
            campaign_profiles(data, checkpoint=revoke)
        self.assertEqual(data, before)

    def test_small_or_synthetic_evidence_cannot_claim_winner(self):
        report = campaign_profiles(fixture())
        for value in report["profiles"].values():
            self.assertEqual(value["comparison"]["ranked_ids"], [])
            self.assertFalse(value["comparison"]["holdout_qualified"])
            self.assertFalse(value["comparison"]["automatic_adoption"])
            self.assertTrue(all(not result["metrics"]["sample_sufficient_for_edge"] for result in value["candidate_results"].values()))

    def test_ranking_only_complete_common_historical_window_with_trades(self):
        data = fixture(historical=True)
        report = campaign(data)
        self.assertTrue(report["comparison"]["all_ledgers_complete"])
        self.assertIn("breakout_call", report["comparison"]["ranked_ids"])
        self.assertNotIn("cash", report["comparison"]["ranked_ids"])
        self.assertNotIn("breakdown_put", report["comparison"]["ranked_ids"])
        self.assertFalse(report["deployable"])
        self.assertEqual(report["rule"]["universe"], ["QQQ"])

    def test_per_month_single_ensemble_and_no_funding_return(self):
        data = fixture(start=date(2023, 9, 1), end=date(2023, 12, 29))
        report = campaign(data)
        ensemble = report["candidate_results"]["rotating_ensemble"]
        for row in ensemble["curve"]:
            self.assertLessEqual(Decimal(row["monthly_committed"]), 500)
            self.assertGreaterEqual(Decimal(row["monthly_pnl"]), -500)
        total = sum((Fraction(event["budget_committed"]) for event in fills(ensemble, "BUY")), Fraction())
        self.assertGreater(total, 0)
        self.assertEqual(Fraction(ensemble["metrics"]["trading_pnl"]), Fraction(ensemble["metrics"]["ending_equity"])-Fraction(ensemble["metrics"]["contributions"]))
        self.assertEqual(ensemble["state"]["entries"]-ensemble["state"]["closed_trades"], int(ensemble["state"]["position"] is not None))

    def test_put_signal_and_contract_are_not_call_aliases(self):
        base = fixture()
        data = fixture(prices=[Decimal(100)-Decimal(index)/100 for index in range(len(base["snapshots"]))])
        value = result(data, "breakdown_put")
        buy = fills(value, "BUY")[0]
        self.assertIn("P00098000", buy["contract_id"])
        self.assertEqual(value["state"]["position"], None)

    def test_original_rsi_arithmetic_edge_cases_and_inclusive_thresholds(self):
        import ariadne.options_campaign as module
        def history(values): return [{"underlying_price": str(value)} for value in values]
        for prices, expected in (([100]*15, 50), (list(range(100, 115)), 100),
                                 (list(range(114, 99, -1)), 0),
                                 ([113]+[106]*12+[107, 109], 30),
                                 ([87]+[94]*12+[93, 91], 70)):
            with self.subTest(prices=prices):
                self.assertEqual(module._rsi14(list(map(Decimal, prices))), expected)
        rebound = history([113]+[106]*12+[107, 109])
        exhaustion = history([87]+[94]*12+[93, 91])
        self.assertTrue(signal("rsi_rebound_call", rebound))
        self.assertTrue(signal("rsi_exhaustion_put", exhaustion))
        self.assertFalse(signal("rsi_rebound_call", history(["112.9"]+[106]*12+[107, 109])))
        self.assertFalse(signal("rsi_exhaustion_put", history(["87.1"]+[94]*12+[93, 91])))
        for identifier, values in (("rsi_rebound_call", rebound), ("rsi_exhaustion_put", exhaustion)):
            self.assertFalse(signal(identifier, values[-14:]))
            self.assertEqual(signal(identifier, history([1000]*9)+values), signal(identifier, values))
            self.assertFalse(signal(identifier, history([100]*15)))

    def test_original_rsi_requires_strict_reversal_of_both_previous_closes(self):
        def history(values): return [{"underlying_price": str(value)} for value in values]
        for identifier, values in (("rsi_rebound_call", [113]+[106]*12+[107, 107]),
                ("rsi_rebound_call", [113]+[107]*12+[105, 106]),
                ("rsi_exhaustion_put", [87]+[94]*12+[93, 93]),
                ("rsi_exhaustion_put", [87]+[93]*12+[95, 94])):
            with self.subTest(identifier=identifier, values=values):
                self.assertFalse(signal(identifier, history(values)))

    def test_original_signals_route_to_correct_delayed_long_contract(self):
        for identifier, initial, tail, kind in (
                ("rsi_rebound_call", [104]+[97]*12+[98, 100], Decimal("0.1"), "call"),
                ("rsi_exhaustion_put", [96]+[103]*12+[102, 100], Decimal("-0.1"), "put")):
            prices = list(map(Decimal, initial))
            prices += [Decimal(100)+tail*index for index in range(1, 7)]
            value = result(fixture(prices=prices), identifier)
            buy = fills(value, "BUY")[0]
            with self.subTest(identifier=identifier):
                self.assertEqual(buy["strategy_id"], identifier)
                self.assertEqual(buy["option_type"], kind)
                self.assertEqual(buy["source_date"], "2023-11-22")
                self.assertLess(datetime.fromisoformat(buy["decided_at"])+timedelta(seconds=60),
                    datetime.fromisoformat(buy["observed_at"]))
                self.assertLessEqual(Decimal(buy["budget_committed"]), 100)
                self.assertEqual(value["diagnostics"]["warmup_rows"], 14)
                self.assertFalse(value["deployable"])

    def test_diversified_ensemble_rotates_all_five_with_one_monthly_budget(self):
        import ariadne.options_campaign as module
        data = fixture(start=date(2023, 9, 1), end=date(2023, 12, 29),
            prices=[Decimal(100)]*len(session_dates(date(2023, 9, 1), date(2023, 12, 29))),
            quote_values=lambda index, day: ("4.9", "5") if index % 4 in (0, 1) else ("0.95", "0.98"))
        # Mechanical routing/budget fixture, not evidence that the signals occur together.
        with patch.object(module, "signal", return_value=True):
            value = result(data, "diversified_ensemble")
        buys = fills(value, "BUY")
        self.assertEqual([event["strategy_id"] for event in buys[:5]], list(module.SIGNALS))
        self.assertEqual(len({event["source_date"] for event in buys}), len(buys))
        committed = {}
        for event in buys:
            self.assertEqual(event["option_type"], module.SIGNAL_KINDS[event["strategy_id"]])
            month = event["source_date"][:7]
            committed[month] = committed.get(month, Fraction())+Fraction(event["budget_committed"])
        self.assertTrue(all(total <= 500 for total in committed.values()))
        self.assertTrue(all(Decimal(row["monthly_committed"]) <= 500 for row in value["curve"]))
        self.assertEqual(value["state"]["entries"]-value["state"]["closed_trades"], int(value["state"]["position"] is not None))

    def test_profitable_fills_cannot_recycle_exhausted_monthly500(self):
        data = fixture(start=date(2023, 10, 2), end=date(2023, 11, 30))
        november = 0
        for row in data["snapshots"]:
            index = november if row["observed_at"].startswith("2023-11") else 0
            if row["observed_at"].startswith("2023-11"): november += 1
            additions = []
            for quote in row["quotes"]:
                if quote["type"] != "call": continue
                cheap = index % 6 in (0, 1, 5)
                quote.update(bid="0.96" if cheap else "5", ask="0.987" if cheap else "5.1")
                additions.append({**quote, "strike": "104",
                    "contract_id": contract_id("QQQ", quote["expiry"], "call", "104"),
                    "bid": "5" if cheap else "0.96", "ask": "5.1" if cheap else "0.987"})
            row["quotes"] = sorted(row["quotes"]+additions, key=lambda quote: quote["contract_id"])
        value = result(data)
        buys = [event for event in fills(value, "BUY") if event["source_date"].startswith("2023-11")]
        self.assertEqual(len(buys), 5)
        self.assertEqual(sum((Fraction(event["budget_committed"]) for event in buys), Fraction()), 500)
        november_rows = [row for row in value["curve"] if row["month"] == "2023-11"]
        self.assertEqual(november_rows[-1]["monthly_committed"], "500")
        self.assertGreater(Decimal(november_rows[-1]["monthly_pnl"]), 0)
        self.assertGreater(Decimal(november_rows[-1]["cash"]), 500)
        self.assertGreater(value["diagnostics"]["budget_blocked"], 0)

    def test_external_decimal_context_and_inputs_stay_unchanged(self):
        data = fixture(); before = deepcopy(data)
        expected = campaign_profiles(data)
        with localcontext() as context:
            context.prec = 5
            self.assertEqual(campaign_profiles(data), expected)
            self.assertEqual(context.prec, 5)
        self.assertEqual(data, before)

    def test_caller_rounding_and_traps_cannot_change_ratios_or_reports(self):
        data = fixture(quote_values=lambda index, day: ("4.9", "5") if index in (15, 16) else ("0.95", "0.98"))
        expected = campaign_profiles(data)
        for rounding in (ROUND_DOWN, ROUND_UP):
            with self.subTest(rounding=rounding), localcontext() as context:
                context.rounding = rounding
                context.prec = 5
                context.traps[Inexact] = True
                self.assertEqual(campaign_profiles(data), expected)
                self.assertEqual(context.rounding, rounding)
                self.assertEqual(context.prec, 5)
                self.assertTrue(context.traps[Inexact])

    def test_row_context_keeps_full_reports_equal_to_uncached_checks(self):
        import ariadne.options_campaign as module
        reject = module._reject
        for size in (10, None, 0):
            data = fixture(sizes=size, quote_values=lambda index, day:
                ("4.9", "5") if index in (15, 16) else ("0.95", "0.98"))
            expected = campaign_profiles(data)
            contexts = {}
            calls = []
            def uncached(quote, row, kind, clock, profile, disposal=None, row_context=None):
                if row_context is not None:
                    source = row["observed_at"]
                    if source in contexts:
                        self.assertIs(contexts[source], row_context)
                    contexts[source] = row_context
                    calls.append(source)
                return reject(quote, row, kind, clock, profile, disposal)
            with self.subTest(size=size), patch.object(module, "_reject", side_effect=uncached):
                self.assertEqual(campaign_profiles(data), expected)
            self.assertEqual(len(calls), sum(len(row["quotes"]) for row in data["snapshots"])*4)
            self.assertEqual(len(contexts), len(data["snapshots"]))

    def test_cached_calendar_context_matches_uncached_dst_and_month_edges(self):
        import ariadne.options_campaign as module
        for source, available, expiry in (
            ("2024-03-10T06:30:00+00:00", "2024-03-10T07:30:00+00:00", "2024-03-20"),
            ("2024-11-03T05:30:00+00:00", "2024-11-03T07:30:00+00:00", "2024-11-13"),
            ("2024-01-31T23:30:00+00:00", "2024-02-01T06:30:00+00:00", "2024-02-09")):
            row = {"observed_at": source, "available_at": available, "underlying_price": "100"}
            clock = datetime.fromisoformat(available)
            context = (module._day_at(datetime.fromisoformat(source)), module._day_at(clock), Decimal(100))
            base = {"type": "call", "expiry": expiry, "strike": "100", "bid": "0.95", "ask": "0.98", "ask_size": 1}
            for delta in ({}, {"type": "put"}, {"strike": "106"}, {"ask": "1"},
                          {"bid": "0"}, {"bid": "0.5"}, {"ask_size": 0}, {"ask_size": None}):
                quote = {**base, **delta}
                for profile in module.LIQUIDITY_PROFILES:
                    with self.subTest(source=source, delta=delta, profile=profile):
                        self.assertEqual(module._reject(quote, row, "call", clock, profile, {}),
                            module._reject(quote, row, "call", clock, profile, {}, context))

    def test_limits_registry_mutation_and_empty_stream_fail(self):
        import ariadne.options_campaign as module
        data = fixture()
        with patch.object(module, "MAX_ROWS", 3), self.assertRaises(OptionsRecoveryError): campaign(data)
        with patch.object(module, "MAX_OUTPUT_BYTES", 100), self.assertRaises(OptionsRecoveryError): campaign(data)
        with patch.object(module, "MAX_CANDIDATES", 9), self.assertRaises(OptionsRecoveryError): campaign_profiles(data)
        with patch.dict(module.RULE, {"entry_maximum_debit": "101"}), self.assertRaises(OptionsRecoveryError): campaign(data)
        with self.assertRaises(OptionsRecoveryError): campaign(iter([]))


if __name__ == "__main__": unittest.main()
