"""Chronology, funded monthly-loss bounds, and options-evidence distinctions."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import unittest

from ariadne.options_data import contract_id
from ariadne.options_recovery import (OptionsRecoveryError, RULE_SHA256,
    advance_paper, backtest, check_deadline, initial_state, summarize, validate_state)
from ariadne.qqq_data import session_dates, session_close_utc

UTC=timezone.utc


def fixture(count=21, *, historical=False):
    days=session_dates(date(2024,1,2),date(2024,4,30))[:count]
    rows=[]
    for index,day in enumerate(days):
        following=date(day.year+1,1,1) if day.month==12 else date(day.year,day.month+1,1)
        expiry=session_dates(day.replace(day=1),following-timedelta(days=1))[-1].isoformat()
        price=Decimal(100)
        if index==10:price=Decimal(96)
        if index==11:price=Decimal(97)
        if index==12:price=Decimal(98)
        if index>=13:price=Decimal(101)+Decimal(index-13)
        observed=session_close_utc(day)
        rows.append({"observed_at":observed.isoformat(),"available_at":(observed+timedelta(minutes=15)).isoformat(),
            "underlying_symbol":"QQQ","underlying_price":str(price),"quotes":[{
                "contract_id":contract_id("QQQ",expiry,"call","103"),"type":"call","expiry":expiry,"strike":"103","multiplier":100,
                "bid":"1","ask":"1.1","bid_size":10,"ask_size":10,"volume":100,"open_interest":1000}]})
    as_of=(datetime.fromisoformat(rows[-1]["available_at"])+timedelta(minutes=1)).isoformat()
    return {"schema":1,"evidence_kind":"historical_quotes" if historical else "synthetic_fixture","as_of":as_of,"currency":"USD",
        "source":{"provider":"Regression fixture","url":"https://example.com/fixture","sha256":hashlib.sha256(b"fixture").hexdigest(),
                  "retrieved_at":as_of,"usage":"Synthetic unit test only; not market performance evidence.","quote_time_basis":"recorded","independently_verified":False},"snapshots":rows}


def through(data,count):
    value=deepcopy(data)
    value["snapshots"]=value["snapshots"][:count]
    value["as_of"]=value["source"]["retrieved_at"]=(datetime.fromisoformat(value["snapshots"][-1]["available_at"])+timedelta(minutes=1)).isoformat()
    return value


def paper_to(data,count):
    baseline=through(data,13)
    state=initial_state(baseline,started_at=baseline["as_of"])
    for upto in range(14,count+1):
        current=through(data,upto)
        result=advance_paper(current,state,decided_at=current["as_of"])
        if result["status"]!="completed":raise AssertionError(result)
        state=result["state"]
    return state


class OptionsRecoveryTests(unittest.TestCase):
    def test_initial_cash_contribution_and_no_historical_adoption(self):
        data=fixture(14,historical=True)
        state=initial_state(data,started_at=data["as_of"])
        self.assertEqual(state["rule_sha256"],RULE_SHA256)
        self.assertEqual(state["cash"],"500")
        self.assertEqual(state["contributions"],"500")
        self.assertIsNone(state["position"])
        self.assertIsNone(state["pending"])
        self.assertEqual(summarize(state)["trading_pnl"],"0")
        empty=initial_state(None,started_at=through(data,13)["as_of"])
        first=advance_paper(data,empty,decided_at=data["as_of"])
        self.assertEqual(first["events"][0]["type"],"baseline")
        self.assertIsNone(first["state"]["pending"])
        self.assertEqual(first["state"]["trade_count"],0)

    def test_fixed_signal_is_not_filled_at_same_snapshot(self):
        data=fixture(15)
        before=backtest(through(data,13))
        signal=backtest(through(data,14))
        after=backtest(data)
        self.assertEqual(before["metrics"]["trade_count"],0)
        self.assertEqual(signal["state"]["pending"]["action"],"BUY")
        self.assertEqual(signal["metrics"]["trade_count"],0)
        fill=next(item for item in after["events"] if item["type"]=="fill")
        self.assertEqual(fill["observed_at"],data["snapshots"][14]["observed_at"])
        self.assertGreater(datetime.fromisoformat(fill["observed_at"]),datetime.fromisoformat(fill["decided_at"])+timedelta(seconds=60))
        self.assertEqual(fill["price"],"1.1")

    def test_entry_fee_and_exit_reserve_are_in_nonrecycling_budget(self):
        state=backtest(fixture(15))["state"]
        self.assertEqual(state["cash"],"389.35")
        self.assertEqual(state["monthly_committed"],"111.3")
        self.assertEqual(state["fees"],"0.65")
        self.assertEqual(state["position"]["exit_fee_reserved"],"0.65")
        self.assertEqual(summarize(state)["equity"],"488.7")

    def test_target_observation_is_not_target_execution_and_milestones_are_realized(self):
        data=fixture(17)
        data["snapshots"][15]["quotes"][0].update(bid="5.6",ask="5.7")
        data["snapshots"][16]["quotes"][0].update(bid="11.5",ask="11.6")
        at_target=backtest(through(data,16))
        report=backtest(data)
        self.assertEqual(at_target["state"]["milestone_counts"],{"2x":0,"5x":0,"10x":0})
        self.assertEqual(at_target["state"]["pending"]["action"],"SELL")
        sell=next(item for item in report["events"] if item["type"]=="fill" and item["action"]=="SELL")
        self.assertEqual(sell["price"],"11.5")
        self.assertEqual(report["metrics"]["trading_pnl"],"1038.7")
        self.assertEqual(report["metrics"]["payout_milestones"],{"2x":1,"5x":1,"10x":1})

    def test_stop_gap_can_lose_much_more_than_half_premium(self):
        data=fixture(17)
        data["snapshots"][15]["quotes"][0].update(bid="0.4",ask="0.5")
        data["snapshots"][16]["quotes"][0].update(bid="0.03",ask="0.035")
        result=backtest(data)
        self.assertEqual(result["metrics"]["trading_pnl"],"-108.3")
        self.assertGreaterEqual(Decimal(result["summary"]["monthly_pnl"]),Decimal(-500))
        self.assertEqual(result["metrics"]["payout_milestones"],{"2x":0,"5x":0,"10x":0})

    def test_profitable_cash_does_not_replenish_used_monthly_budget(self):
        data=fixture(15,historical=True)
        state=paper_to(data,14)
        state["cash"]="10000"
        state["monthly_committed"]="450"
        result=advance_paper(data,state,decided_at=data["as_of"])
        self.assertEqual(result["state"]["trade_count"],0)
        self.assertEqual(result["state"]["cash"],"10000")
        self.assertEqual(result["state"]["monthly_committed"],"450")
        self.assertTrue(any(event.get("reason")=="monthly_or_cash_budget" for event in result["events"]))

    def test_entry_cost_jump_does_not_chase_another_contract(self):
        data=fixture(15,historical=True)
        state=paper_to(data,14)
        data["snapshots"][14]["quotes"][0].update(bid="1.2",ask="1.24")
        result=advance_paper(data,state,decided_at=data["as_of"])
        self.assertIsNone(result["state"]["position"])
        self.assertEqual(result["state"]["cash"],"500")
        self.assertTrue(any(event.get("reason")=="entry_quote_no_longer_eligible" for event in result["events"]))

    def test_zero_sizes_never_fill_and_unknown_sizes_are_only_historical_assumption(self):
        for size in (0,None):
            data=fixture(15,historical=True)
            state=paper_to(data,14)
            data["snapshots"][14]["quotes"][0]["ask_size"]=size
            result=advance_paper(data,state,decided_at=data["as_of"])
            self.assertIsNone(result["state"]["position"])
            historic=backtest(data)
            self.assertEqual(historic["metrics"]["trade_count"],int(size is None))
            self.assertFalse(historic["execution_qualified"])

    def test_expiry_and_month_end_forced_disposal_are_precommitted(self):
        data=fixture(21)
        result=backtest(data)
        self.assertEqual(result["metrics"]["closed_trade_count"],1)
        sell=next(event for event in result["events"] if event["type"]=="fill" and event["action"]=="SELL")
        self.assertEqual(sell["reasons"],["precommitted_disposal_deadline"])
        self.assertEqual(sell["observed_at"][:10],"2024-01-30")
        self.assertIsNone(result["state"]["position"])
        self.assertTrue(result["state"]["cap_qualified"])

    def test_missing_deadline_bid_holds_without_fake_sale_or_deposit(self):
        data=fixture(23)
        deadline=next(row for row in data["snapshots"] if row["observed_at"].startswith("2024-01-30"))
        deadline["quotes"][0].update(bid="0",ask="0.01",bid_size=0)
        report=backtest(data)
        self.assertEqual(report["state"]["hold"]["code"],"unexecutable_disposal")
        self.assertFalse(report["summary"]["cap_qualified"])
        self.assertEqual(report["summary"]["contributions"],"500")
        self.assertEqual(report["metrics"]["closed_trade_count"],0)
        self.assertIsNotNone(report["state"]["position"])

    def test_monthly_contribution_is_not_trading_profit(self):
        data=fixture(24)
        for row in data["snapshots"]:row["underlying_price"]="100"
        report=backtest(data)
        self.assertEqual(report["metrics"]["total_contributions"],"1000")
        self.assertEqual(report["metrics"]["ending_equity"],"1000")
        self.assertEqual(report["metrics"]["trading_pnl"],"0")
        self.assertEqual(report["metrics"]["maximum_monthly_loss"],"0")
        self.assertEqual(len(report["monthly_rows"]),2)

    def test_month_boundary_open_position_blocks_reset(self):
        data=fixture(23,historical=True)
        state=paper_to(data,15)
        # Explicitly test the invariant with a direct next-month observation:
        # the historical-gap guard also refuses this in the public path.
        from ariadne.options_recovery import _step
        feb=next(row for row in data["snapshots"] if row["observed_at"].startswith("2024-02-01"))
        before=deepcopy(state)
        result=_step(state,feb,datetime.fromisoformat(feb["available_at"]),forward=True)
        self.assertEqual(result["state"]["hold"]["code"],"unresolved_month_boundary")
        self.assertEqual(result["state"]["monthly_committed"],before["monthly_committed"])
        self.assertEqual(result["state"]["contributions"],"500")
        self.assertEqual(result["state"]["cash"],before["cash"])

    def test_no_history_revisions_or_missed_session_replays(self):
        data=fixture(17,historical=True)
        state=paper_to(data,14)
        before=deepcopy(state)
        revised=through(data,15)
        revised["snapshots"][1]["quotes"][0]["open_interest"]=999
        result=advance_paper(revised,state,decided_at=revised["as_of"])
        self.assertEqual(result["state"]["hold"]["code"],"accepted_history_changed")
        missed=advance_paper(through(data,17),state,decided_at=data["as_of"])
        self.assertEqual(missed["state"]["hold"]["code"],"missed_observations")
        self.assertEqual(state,before)

    def test_missing_current_contract_is_not_a_zero_mark_or_fictitious_sale(self):
        data=fixture(16,historical=True)
        state=paper_to(data,15)
        row=data["snapshots"][15]
        row["quotes"][0].update(strike="104",contract_id=contract_id("QQQ",row["quotes"][0]["expiry"],"call","104"))
        result=advance_paper(data,state,decided_at=data["as_of"])
        self.assertEqual(result["state"]["hold"]["code"],"missing_position_quote")
        self.assertEqual(result["state"]["cash"],state["cash"])
        self.assertEqual(result["state"]["position"],state["position"])
        self.assertFalse(result["summary"]["cap_qualified"])

    def test_assumed_eod_and_synthetic_fixture_never_forward_fill(self):
        for fixture_kind,basis in ((True,"recorded"),(False,"assumed_eod")):
            data=fixture(15,historical=not fixture_kind)
            state=initial_state(through(data,13),started_at=through(data,13)["as_of"])
            data["source"]["quote_time_basis"]=basis
            result=advance_paper(data,state,decided_at=data["as_of"])
            self.assertEqual(result["status"],"held")
            self.assertEqual(result["state"]["trade_count"],0)
            report=backtest(data)
            self.assertFalse(report["execution_qualified"])
            self.assertTrue(any(("Synthetic" if fixture_kind else "assumed") in item for item in report["limitations"]))

    def test_late_actual_decision_cannot_borrow_already_observed_quote(self):
        data=fixture(15,historical=True)
        baseline=through(data,13)
        state=initial_state(baseline,started_at=baseline["as_of"])
        signal=through(data,14)
        late=(datetime.fromisoformat(data["snapshots"][14]["observed_at"])-timedelta(seconds=30)).isoformat()
        # The public path correctly regards yesterday's observation as stale
        # once another session is finalized; before then it may form a late
        # signal, whose fill still must have >60 seconds of lead.
        result=advance_paper(signal,state,decided_at=late)
        self.assertEqual(result["status"],"completed")
        future=advance_paper(data,result["state"],decided_at=data["as_of"])
        self.assertEqual(future["state"]["trade_count"],0)
        self.assertTrue(any(event.get("reason")=="late_or_unavailable_entry" for event in future["events"]))

    def test_passive_deadline_clock_suspends_assurance_without_mutation(self):
        state=paper_to(fixture(15,historical=True),15)
        before=deepcopy(state)
        later="2024-01-31T12:00:00+00:00"
        checked=check_deadline(state,later)
        self.assertEqual(checked["hold"]["code"],"unverified_disposal_deadline")
        self.assertFalse(summarize(state,now=later)["cap_qualified"])
        self.assertEqual(checked["cash"],state["cash"])
        self.assertEqual(checked["position"],state["position"])
        self.assertEqual(state,before)

    def test_stale_source_does_not_leave_live_cap_assurance(self):
        data=fixture(15,historical=True)
        state=paper_to(data,15)
        result=advance_paper(data,state,decided_at="2024-01-31T12:00:00+00:00")
        self.assertEqual(result["status"],"held")
        self.assertFalse(result["summary"]["cap_qualified"])
        self.assertEqual(result["state"]["cash"],state["cash"])

    def test_backtest_prefix_invariance_and_missing_session_fail_stop(self):
        data=fixture(21)
        short=backtest(through(data,17))
        full=backtest(data)
        cutoff=data["snapshots"][16]["observed_at"]
        self.assertEqual(short["curve"],[item for item in full["curve"] if item["observed_at"]<=cutoff])
        self.assertEqual([e for e in short["events"] if e["type"]=="fill"],[e for e in full["events"] if e["type"]=="fill" and e["observed_at"]<=cutoff])
        missing=deepcopy(data);missing["snapshots"].pop(15)
        held=backtest(missing)
        self.assertEqual(held["state"]["hold"]["code"],"historical_session_gap")
        self.assertEqual(held["metrics"]["closed_trade_count"],0)

    def test_no_annualized_or_proven_winner_claim_for_short_history(self):
        report=backtest(fixture(1))
        self.assertEqual(report["research_gate"],"insufficient_history")
        self.assertFalse(report["execution_qualified"])
        self.assertEqual(report["metrics"]["trading_pnl"],"0")
        self.assertNotIn("cagr",report["metrics"])
        self.assertNotIn("annualized_return",report["metrics"])
        self.assertEqual(report["evidence_kind"],"synthetic_fixture")

    def test_money_is_exact_even_if_global_decimal_precision_is_low(self):
        normal=backtest(fixture(21))
        with localcontext() as context:
            context.prec=3
            actual=backtest(fixture(21))
        self.assertEqual(actual["metrics"],normal["metrics"])
        buy=next(event for event in normal["events"] if event["type"]=="fill" and event["action"]=="BUY")
        expected=Fraction(11,10)*100+Fraction(65,100)*2
        self.assertEqual(Fraction(buy["budget_committed"]),expected)

    def test_malformed_state_cannot_disable_limits_or_drop_fee_reserve(self):
        base=backtest(fixture(15))["state"]
        cases=[]
        for key,value in (("monthly_committed","500.01"),("cash","0"),("fees","0"),("contributions","501"),("trade_count",True)):
            current=deepcopy(base);current[key]=value;cases.append(current)
        current=deepcopy(base);current["position"]["exit_fee_reserved"]="0";cases.append(current)
        current=deepcopy(base);current["position"]["contract_id"]="QQQ240131C00999000";cases.append(current)
        current=deepcopy(base);current["position"]["decision_at"]=current["position"]["entry_at"];cases.append(current)
        for current in cases:
            with self.subTest(key=list(current)):
                with self.assertRaises(ValueError):validate_state(current)


if __name__=="__main__":unittest.main()
