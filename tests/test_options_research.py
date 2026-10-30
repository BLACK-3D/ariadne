"""Independent fixtures for preregistered regime research and exact risk."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import unittest

from ariadne.options_data import contract_id
from ariadne.options_research import (OptionsRecoveryError, PREREGISTRATION_SHA256,
    RULE, RULE_SHA256, STRATEGY_IDS, signal, backtest, backtest_comparison,
    validate_state, summarize)
from ariadne.options_portfolio import RULE_SHA256 as OWNER_RULE, validate_state as owner_validate
from ariadne.options_recovery import RULE_SHA256 as LEGACY_RULE, _deadline
from ariadne.qqq_data import session_dates, session_close_utc


def fixture(count=100, *, bearish=False, historical=False, prices=None, quotes=None):
    days = session_dates(date(2023,8,1),date(2024,3,29))[:count]
    rows = []
    for index, day in enumerate(days):
        month_end = (day.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        month_sessions = session_dates(day.replace(day=1),month_end)
        expiries = sorted({item for item in month_sessions if item >= day and item.weekday() == 4} | {month_sessions[-1]})
        close = prices[index] if prices is not None else Decimal(100)+(Decimal(-index) if bearish else Decimal(index))/100
        observed = session_close_utc(day)
        bid, ask = quotes(index,day) if quotes else ("1.2","1.237")
        chain = [{"contract_id":contract_id("QQQ",expiry.isoformat(),kind,strike),"type":kind,"expiry":expiry.isoformat(),"strike":strike,"multiplier":100,
            "bid":bid,"ask":ask,"bid_size":10,"ask_size":10,"volume":100,"open_interest":1000}
            for expiry in expiries for kind,strike in (("call","103"),("put","98"))]
        rows.append({"observed_at":observed.isoformat(),"available_at":(observed+timedelta(minutes=15)).isoformat(),
            "underlying_symbol":"QQQ","underlying_price":str(close),"quotes":sorted(chain,key=lambda item:item["contract_id"])})
    as_of = (datetime.fromisoformat(rows[-1]["available_at"])+timedelta(minutes=1)).isoformat()
    return {"schema":1,"evidence_kind":"historical_quotes" if historical else "synthetic_fixture","as_of":as_of,"currency":"USD",
        "source":{"provider":"Regime regression fixture","url":"https://example.com/regime-fixture","sha256":hashlib.sha256(b"regime-fixture").hexdigest(),
            "retrieved_at":as_of,"usage":"Synthetic regression prices only; no market-performance evidence.","quote_time_basis":"recorded","independently_verified":False},"snapshots":rows}


def history(prices):
    return [{"underlying_price":str(value)} for value in prices]


def chunks(dataset,*breaks):
    start = 0
    for end in (*breaks,len(dataset["snapshots"])):
        chunk = deepcopy(dataset)
        chunk["snapshots"] = chunk["snapshots"][start:end]
        yield chunk
        start = end


def filled(report, action):
    return [event for event in report["events"] if event.get("type") == "fill" and event.get("action") == action]


class OptionsResearchTests(unittest.TestCase):
    def test_registration_and_execution_identity_are_distinct_and_frozen(self):
        self.assertEqual(STRATEGY_IDS,("regime_recovery_call_v1","trend_breakout_call_v1","trend_breakdown_put_v1"))
        self.assertEqual(PREREGISTRATION_SHA256,"53b4c503d7d896170e4b0fe790e02d147c9bdfa7965454204cb74a9c041152a7")
        self.assertEqual(RULE_SHA256,"91d1af60793d3df7149ad763d5243e18b8aa8255093e127cbdbc885333752661")
        self.assertEqual(OWNER_RULE,"e75bca82ac7c6a2a572e351c2bb7f4b4188c632153b3ef6b5358cc4a8357df62")
        self.assertEqual(LEGACY_RULE,"ff79c2ac811936eca0356883bcd698b63bd47383afe46464bedbe568c4e44a60")
        self.assertEqual(RULE["execution_reference_sha256"],OWNER_RULE)
        self.assertEqual(RULE["per_entry_maximum_debit"],"125.00")
        self.assertEqual(RULE["monthly_maximum_debit"],"500.00")

    def test_fifty_prior_closes_are_required_and_current_is_excluded(self):
        up = [Decimal(100)+Decimal(index)/10 for index in range(51)]
        self.assertFalse(signal("trend_breakout_call_v1",history(up[:-1])))
        self.assertTrue(signal("trend_breakout_call_v1",history(up)))
        # A huge current price cannot retrospectively turn a declining prior
        #20/50 trend into a bullish reference window.
        old_bear = [Decimal(200)]*30+[Decimal(100)]*20+[Decimal(10000)]
        self.assertFalse(signal("trend_breakout_call_v1",history(old_bear)))

    def test_bearish_signal_is_symmetric_and_not_a_call_alias(self):
        down = [Decimal(200)-Decimal(index) for index in range(51)]
        self.assertTrue(signal("trend_breakdown_put_v1",history(down)))
        self.assertFalse(signal("trend_breakout_call_v1",history(down)))
        with self.assertRaises(OptionsRecoveryError):signal("buy-anything",history(down))

    def test_recovery_requires_both_original_pattern_and_bull_regime(self):
        values = [Decimal(100)+Decimal(index)/10 for index in range(46)]+[Decimal(value) for value in (110,105,106,107,108)]
        self.assertTrue(signal("regime_recovery_call_v1",history(values)))
        self.assertFalse(signal("trend_breakout_call_v1",history(values)))
        wrong_regime = [Decimal(200)]*30+values[-21:]
        self.assertFalse(signal("regime_recovery_call_v1",history(wrong_regime)))

    def test_warmup_and_simulated_deposits_are_not_trading_returns(self):
        result = backtest(fixture(50))
        self.assertEqual(result["eligibility"]["warmup_sessions"],50)
        self.assertEqual(result["metrics"]["trade_count"],0)
        self.assertEqual(result["metrics"]["trading_pnl"],"0")
        self.assertEqual(result["metrics"]["ending_equity"],result["metrics"]["total_contributions"])
        self.assertGreater(Decimal(result["metrics"]["total_contributions"]),500)

    def test_decision_uses_a_later_ask_not_same_snapshot(self):
        data = fixture(55)
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        buy = filled(result,"BUY")[0]
        self.assertEqual(buy["observed_at"],data["snapshots"][51]["observed_at"])
        self.assertGreater(datetime.fromisoformat(buy["observed_at"]),datetime.fromisoformat(buy["decided_at"])+timedelta(seconds=60))
        self.assertEqual(buy["price"],"1.237")
        self.assertEqual(buy["premium"],"123.7")
        self.assertEqual(buy["budget_committed"],"125")
        self.assertEqual(Fraction(buy["budget_committed"]),Fraction(buy["premium"])+Fraction("1.30"))

    def test_put_fills_and_attribution_have_actual_put_contract_identity(self):
        result = backtest(fixture(55,bearish=True),strategies=["trend_breakdown_put_v1"])
        buy = filled(result,"BUY")[0]
        self.assertEqual(buy["option_type"],"put")
        self.assertIn("P00098000",buy["contract_id"])
        self.assertEqual(result["state"]["strategy_stats"]["trend_breakdown_put_v1"]["entry_count"],1)
        self.assertEqual(result["state"]["strategy_stats"]["trend_breakout_call_v1"]["entry_count"],0)

    def test_target_observation_cannot_sell_until_a_later_quote(self):
        def prices(index,day):return ("6.2","6.3") if index in (52,53) else ("1.2","1.237")
        data = fixture(54,quotes=prices)
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        sale = filled(result,"SELL")[0]
        self.assertEqual(sale["observed_at"],data["snapshots"][53]["observed_at"])
        self.assertEqual(sale["reasons"],["bid_reached_5x_entry_ask"])
        self.assertEqual(sale["gross_credit"],"620")
        self.assertEqual(sale["net_credit"],"619.35")
        self.assertEqual(sale["trade_pnl"],"495")
        # A gross-price5x target does not make net proceeds a realized5x.
        self.assertEqual(result["metrics"]["payout_milestones"],{"2x":1,"5x":0,"10x":0})

    def test_stop_observation_is_not_a_guaranteed_stop_fill(self):
        def prices(index,day):return ("0.5","0.55") if index == 52 else ("0.4","0.44") if index == 53 else ("1.2","1.237")
        data = fixture(54,quotes=prices)
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        sale = filled(result,"SELL")[0]
        self.assertEqual(sale["observed_at"],data["snapshots"][53]["observed_at"])
        self.assertEqual(sale["price"],"0.4")
        self.assertEqual(sale["reasons"],["bid_at_or_below_half_entry_ask"])
        self.assertEqual(sale["trade_pnl"],"-85")

    def test_four_exact125_entries_exhaust_one_month_even_after_profits(self):
        november = 0
        def prices(index,day):
            nonlocal november
            if day.strftime("%Y-%m") == "2023-11":
                phase = november%4
                november += 1
                return ("6.2","6.3") if phase in (2,3) else ("1.2","1.237")
            return "1.2","1.237"
        data = fixture(88,quotes=prices)
        # After the fourth sale the held103-strike quote is expensive. Add a
        # different affordable standard quote on that session so the cash-rich
        # selector really encounters the exhausted allowance, rather than
        # merely finding no eligible near-month-end contract.
        for row in data["snapshots"]:
            if row["observed_at"].startswith("2023-11-22"):
                base = next(item for item in row["quotes"] if item["type"] == "call" and item["expiry"] == "2023-11-30")
                row["quotes"].append({**base,"strike":"104","contract_id":contract_id("QQQ","2023-11-30","call","104"),"bid":"1.2","ask":"1.237"})
                row["quotes"].sort(key=lambda item:item["contract_id"])
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        buys = [event for event in filled(result,"BUY") if event["observed_at"].startswith("2023-11")]
        self.assertEqual(len(buys),4)
        self.assertEqual(sum((Fraction(event["budget_committed"]) for event in buys),Fraction(0)),500)
        november_rows = [row for row in result["curve"] if row["month"] == "2023-11"]
        self.assertEqual(november_rows[-1]["monthly_budget_used"],"500")
        self.assertGreater(Decimal(november_rows[-1]["monthly_pnl"]),0)
        self.assertTrue(all(Decimal(row["monthly_budget_used"]) <= 500 and Decimal(row["monthly_pnl"]) >= -500 for row in result["curve"]))
        self.assertGreater(result["eligibility"]["strategies"]["trend_breakout_call_v1"]["ranked_quote_budget_blocked"],0)

    def test_missing_mandatory_disposal_holds_and_never_funds_later_months(self):
        data = fixture(100,historical=True)
        first = filled(backtest(data,strategies=["trend_breakout_call_v1"]),"BUY")[0]
        entry = date.fromisoformat(first["observed_at"][:10])
        expiry = next(quote["expiry"] for row in data["snapshots"] for quote in row["quotes"] if quote["contract_id"] == first["contract_id"])
        deadline = _deadline(entry,date.fromisoformat(expiry)).isoformat()
        for row in data["snapshots"]:
            if row["observed_at"].startswith(deadline):
                for quote in row["quotes"]:
                    if quote["contract_id"] == first["contract_id"]:quote.update(bid="0",ask="0.1",bid_size=0)
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        self.assertEqual(result["research_gate"],"held_incomplete_evidence")
        self.assertEqual(result["state"]["hold"]["code"],"unexecutable_disposal")
        self.assertFalse(result["summary"]["cap_qualified"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertLess(result["coverage"]["processed_snapshots"],100)
        self.assertEqual(len(filled(result,"SELL")),0)
        self.assertEqual(result["state"]["contributions"],"1500")

    def test_missing_held_contract_retains_previous_dated_mark(self):
        data = fixture(57,historical=True)
        original = backtest(data,strategies=["trend_breakout_call_v1"])
        contract = filled(original,"BUY")[0]["contract_id"]
        data["snapshots"][52]["quotes"] = [item for item in data["snapshots"][52]["quotes"] if item["contract_id"] != contract]
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        self.assertEqual(result["state"]["hold"]["code"],"missing_position_quote")
        self.assertEqual(result["state"]["position"]["last_marked_at"],data["snapshots"][51]["observed_at"])
        self.assertEqual(result["coverage"]["processed_snapshots"],52)

    def test_chunk_boundaries_keep_warmup_cash_events_and_prefix_identity(self):
        data = fixture(100)
        whole = backtest_comparison(data)
        streamed = backtest_comparison(chunks(data,17,50,51,53,75))
        self.assertEqual(whole["metrics"],streamed["metrics"])
        for left,right in ((whole["portfolio"],streamed["portfolio"]),*((whole["strategy_results"][key],streamed["strategy_results"][key]) for key in STRATEGY_IDS)):
            for field in ("state","metrics","curve","events","eligibility","research_gate"):
                self.assertEqual(left[field],right[field],field)

    def test_session_gaps_hold_all_ledgers_without_synthetic_entries(self):
        data = fixture(80,historical=True)
        del data["snapshots"][35]
        result = backtest_comparison(data)
        self.assertEqual(result["coverage"]["source_snapshots"],79)
        for value in (result["portfolio"],*result["strategy_results"].values()):
            self.assertEqual(value["coverage"]["processed_snapshots"],35)
            self.assertEqual(value["state"]["hold"]["code"],"historical_session_gap")
            self.assertEqual(value["metrics"]["trade_count"],0)

    def test_repeated_or_backwards_chunks_fail_the_whole_comparison(self):
        data = fixture(60)
        with self.assertRaises(OptionsRecoveryError):backtest_comparison([data,data])
        left,right = list(chunks(data,30))
        with self.assertRaises(OptionsRecoveryError):backtest_comparison([right,left])

    def test_cross_chunk_publication_clock_cannot_move_backwards(self):
        data = fixture(60)
        left,right = list(chunks(data,30))
        left["snapshots"][-1]["available_at"] = right["snapshots"][1]["available_at"]
        with self.assertRaises(OptionsRecoveryError):backtest_comparison([left,right])

    def test_basis_changes_and_invalid_intraday_observations_fail_closed(self):
        data = fixture(60)
        left,right = list(chunks(data,30))
        right["source"]["quote_time_basis"] = "assumed_eod"
        with self.assertRaises(OptionsRecoveryError):backtest_comparison([left,right])
        data["snapshots"][20]["observed_at"] = (datetime.fromisoformat(data["snapshots"][20]["observed_at"])-timedelta(hours=2)).isoformat()
        with self.assertRaises(OptionsRecoveryError):backtest_comparison(data)

    def test_eligibility_reports_unaffordable_quotes_instead_of_raising_risk(self):
        result = backtest(fixture(60,quotes=lambda index,day:("4.9","5")),strategies=["trend_breakout_call_v1"])
        stats = result["eligibility"]["strategies"]["trend_breakout_call_v1"]
        self.assertGreater(stats["signals"],0)
        self.assertGreater(stats["quote_rejections"]["entry_above_125"],0)
        self.assertGreater(stats["no_eligible_quote_sessions"],0)
        self.assertEqual(result["metrics"]["trade_count"],0)
        self.assertEqual(result["metrics"]["trading_pnl"],"0")

    def test_no_matching_type_has_a_distinct_skip_reason(self):
        data = fixture(60)
        for row in data["snapshots"]:row["quotes"] = [item for item in row["quotes"] if item["type"] == "put"]
        result = backtest(data,strategies=["trend_breakout_call_v1"])
        stats = result["eligibility"]["strategies"]["trend_breakout_call_v1"]
        self.assertGreater(stats["no_type_quote_sessions"],0)
        self.assertEqual(stats["matching_type_quotes"],0)
        self.assertEqual(result["metrics"]["trade_count"],0)

    def test_single_strategy_accounts_are_not_extra_shared_funding(self):
        result = backtest_comparison(fixture(60))
        self.assertEqual(result["metrics"],result["portfolio"]["metrics"])
        self.assertEqual(set(result["strategy_results"]),set(STRATEGY_IDS))
        for value in (result["portfolio"],*result["strategy_results"].values()):
            self.assertEqual(value["curve"][0]["contributions"],"500")
            self.assertLessEqual(value["metrics"]["trade_count"]-value["metrics"]["closed_trade_count"],1)
            self.assertTrue(value["research_only"])
            self.assertFalse(value["execution_qualified"])
        self.assertEqual(result["portfolio"]["state"]["cash"],result["portfolio"]["summary"]["cash"])

    def test_research_state_cannot_be_adopted_as_owner_state(self):
        state = backtest(fixture(55))["state"]
        self.assertEqual(validate_state(state),state)
        with self.assertRaises(OptionsRecoveryError):owner_validate(state)
        import ariadne.options_research as research
        self.assertFalse(hasattr(research,"advance_paper"))
        self.assertFalse(hasattr(research,"migrate_flat_legacy"))

    def test_invalid_shared_funding_fee_and_attribution_are_rejected(self):
        state = backtest(fixture(55))["state"]
        for key,value in (("monthly_committed","500.01"),("contributions","501"),("fees","0"),("rule_sha256",OWNER_RULE),("research_only",False)):
            bad = deepcopy(state)
            bad[key] = value
            with self.subTest(key=key),self.assertRaises(OptionsRecoveryError):validate_state(bad)
        bad = deepcopy(state)
        bad["strategy_stats"]["trend_breakout_call_v1"]["entry_count"] += 1
        with self.assertRaises(OptionsRecoveryError):validate_state(bad)

    def test_nonnull_empty_position_cannot_masquerade_as_funded_research(self):
        state = backtest(fixture(55))["state"]
        for value in ({},False,0,[],""):
            bad = deepcopy(state)
            bad["position"] = value
            with self.subTest(value=value),self.assertRaises(OptionsRecoveryError):validate_state(bad)

    def test_decimal_context_does_not_change_money_or_signal_decisions(self):
        data = fixture(60)
        expected = backtest_comparison(data)
        with localcontext() as context:
            context.prec = 6
            observed = backtest_comparison(data)
        self.assertEqual(expected,observed)

    def test_input_is_unchanged_and_repeat_results_are_deterministic(self):
        data = fixture(60)
        original = deepcopy(data)
        first = backtest_comparison(data)
        self.assertEqual(data,original)
        self.assertEqual(first,backtest_comparison(data))
        self.assertEqual(first["research_gate"],"synthetic_fixture_only")
        self.assertFalse(first["execution_qualified"])

    def test_unknown_parameters_empty_history_and_duplicate_ids_are_rejected(self):
        data = fixture(55)
        for invalid in (["not-registered"],["trend_breakout_call_v1"]*2,[],True,[{}]):
            with self.subTest(invalid=invalid),self.assertRaises(OptionsRecoveryError):backtest(data,strategies=invalid)
        with self.assertRaises(OptionsRecoveryError):backtest(data,symbol="SPX")
        with self.assertRaises(OptionsRecoveryError):backtest([])


if __name__ == "__main__":
    unittest.main()
