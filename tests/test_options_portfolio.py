"""Shared-risk options hypotheses: chronology, migration and counterfactuals."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
import unittest

from ariadne.options_data import contract_id
from ariadne.options_portfolio import (OptionsRecoveryError, RULE, RULE_SHA256,
    STRATEGY_IDS, initial_state, migrate_flat_legacy, validate_state, summarize,
    advance_paper, check_deadline, backtest, backtest_comparison)
from ariadne.options_recovery import initial_state as legacy_initial
from ariadne.qqq_data import session_dates, session_close_utc
from test_options_recovery import fixture, through


def paper_to(data, count):
    baseline=through(data,13)
    state=initial_state(baseline,started_at=baseline["as_of"])
    for upto in range(14,count+1):
        current=through(data,upto)
        result=advance_paper(current,state,decided_at=current["as_of"])
        if result["status"]!="completed":raise AssertionError(result)
        state=result["state"]
    return state


def chunks(data, *breaks):
    start=0
    for end in (*breaks,len(data["snapshots"])):
        value=deepcopy(data);value["snapshots"]=value["snapshots"][start:end]
        value["as_of"]=value["source"]["retrieved_at"]=(datetime.fromisoformat(value["snapshots"][-1]["available_at"])+timedelta(minutes=1)).isoformat()
        yield value
        start=end


def put_fixture(count=21, *, historical=False):
    data=fixture(count,historical=historical)
    for index,row in enumerate(data["snapshots"]):
        row["underlying_price"]=str(Decimal(100) if index<13 else Decimal(98)-Decimal(index-13)/10)
        quote=row["quotes"][0]
        quote.update(type="put",strike="95",contract_id=contract_id("QQQ",quote["expiry"],"put","95"))
    return data


def active_month():
    """Many profitable, liquid breakout opportunities; fees still exhaust risk."""
    data=fixture(42)
    days=session_dates(date(2023,12,1),date(2024,1,31))
    data["snapshots"]=data["snapshots"][:len(days)]
    for index,(row,day) in enumerate(zip(data["snapshots"],days)):
        expiry=session_dates(day.replace(day=1),(day.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1))[-1].isoformat()
        observed=session_close_utc(day)
        row.update(observed_at=observed.isoformat(),available_at=(observed+timedelta(minutes=15)).isoformat(),underlying_price=str(Decimal(100)+Decimal(index)/100))
        quote=row["quotes"][0]
        # Each cycle has two affordable observations followed by a target and
        # its later fill. A realized gain never replenishes committed premium.
        bid,ask=("1.1","1.2") if index%4 in (0,1) else ("6.1","6.2")
        quote.update(expiry=expiry,contract_id=contract_id("QQQ",expiry,"call","103"),bid=bid,ask=ask)
        expiries={expiry,*[value.isoformat() for value in session_dates(day.replace(day=1),date.fromisoformat(expiry)) if value.weekday()==4 and value>=day]}
        row["quotes"]=[{**quote,"expiry":value,"contract_id":contract_id("QQQ",value,"call","103")} for value in sorted(expiries)]
    data["as_of"]=data["source"]["retrieved_at"]=(datetime.fromisoformat(data["snapshots"][-1]["available_at"])+timedelta(minutes=1)).isoformat()
    return data


def mixed_strategies_month():
    data=active_month();january=0
    for row in data["snapshots"]:
        if row["observed_at"].startswith("2024-01"):
            phase=january%4;block=january//4
            # Alternating fresh highs and lows trigger directionally distinct
            # hypotheses. This source path is fixed, never selected by profit.
            spot=Decimal("101")+Decimal(block)/10+Decimal(phase)/100 if block%2==0 else Decimal("98")-Decimal(block)/10-Decimal(phase)/100
            row["underlying_price"]=str(spot)
            bid,ask=("1.2","1.237") if phase in (0,1) else ("6.2","6.3")
            january+=1
        else:bid,ask="1.2","1.237"
        quotes=[]
        for original in row["quotes"]:
            for kind,strike in (("call","103"),("put","95")):
                quotes.append({**original,"type":kind,"strike":strike,"contract_id":contract_id("QQQ",original["expiry"],kind,strike),"bid":bid,"ask":ask})
        row["quotes"]=sorted(quotes,key=lambda item:item["contract_id"])
    return data


class OptionsPortfolioTests(unittest.TestCase):
    def test_registered_catalog_and_shared_account_are_fixed(self):
        self.assertEqual(STRATEGY_IDS,("recovery_call_v1","breakout_call_v1","breakdown_put_v1"))
        self.assertEqual(RULE_SHA256,"e75bca82ac7c6a2a572e351c2bb7f4b4188c632153b3ef6b5358cc4a8357df62")
        state=initial_state(started_at="2024-01-22T22:00:00+00:00")
        self.assertEqual(state["cash"],"500")
        self.assertEqual(state["contributions"],"500")
        self.assertEqual(state["enabled_strategies"],list(STRATEGY_IDS))
        self.assertEqual(RULE["per_entry_maximum_debit"],"125.00")
        with self.assertRaises(OptionsRecoveryError):initial_state(started_at=state["started_at"],strategies=[{}])

    def test_tie_uses_registered_rotation_and_never_performance(self):
        data=fixture(15)
        comparison=backtest_comparison(data)
        shared=comparison["portfolio"]["state"]
        self.assertEqual(shared["position"]["strategy_id"],"recovery_call_v1")
        self.assertEqual(shared["next_strategy"],1)
        # The identical source is also a breakout signal. Separate research
        # counts are not extra positions or extra cash in the shared ledger.
        self.assertEqual(comparison["strategy_results"]["breakout_call_v1"]["metrics"]["trade_count"],1)
        self.assertEqual(shared["trade_count"],1)
        historical=fixture(15,historical=True)
        state=paper_to(historical,14);state["pending"]=None;state["next_strategy"]=1
        # Reinitialize at the earlier baseline with a different *registered*
        # cursor to test selector order independently of any profit outcome.
        base=through(historical,13);state=initial_state(base,started_at=base["as_of"]);state["next_strategy"]=1
        result=advance_paper(through(historical,14),state,decided_at=through(historical,14)["as_of"])
        self.assertEqual(result["state"]["pending"]["strategy_id"],"breakout_call_v1")

    def test_put_has_its_own_occ_identity_and_same_shared_cost(self):
        result=backtest(put_fixture(15))
        position=result["state"]["position"]
        self.assertEqual(position["strategy_id"],"breakdown_put_v1")
        self.assertEqual(position["type"],"put")
        self.assertIn("P",position["contract_id"])
        self.assertEqual(result["state"]["monthly_committed"],"111.3")
        self.assertEqual(result["summary"]["cash"],"389.35")

    def test_signal_is_not_same_snapshot_fill_and_reserves_both_fees(self):
        data=fixture(15)
        signal=backtest(through(data,14));filled=backtest(data)
        self.assertEqual(signal["metrics"]["trade_count"],0)
        self.assertEqual(signal["state"]["pending"]["action"],"BUY")
        fill=next(event for event in filled["events"] if event["type"]=="fill")
        self.assertGreater(datetime.fromisoformat(fill["observed_at"]),datetime.fromisoformat(fill["decided_at"])+timedelta(seconds=60))
        self.assertEqual(fill["price"],"1.1")
        self.assertEqual(filled["summary"]["monthly_budget_used"],"111.3")
        self.assertEqual(filled["summary"]["fees"],"0.65")
        self.assertEqual(filled["summary"]["equity"],"488.7")

    def test_target_is_observation_until_a_later_actual_bid(self):
        data=fixture(17)
        data["snapshots"][15]["quotes"][0].update(bid="5.6",ask="5.7")
        data["snapshots"][16]["quotes"][0].update(bid="11.5",ask="11.6")
        observed=backtest(through(data,16));closed=backtest(data)
        self.assertEqual(observed["state"]["pending"]["action"],"SELL")
        self.assertEqual(observed["metrics"]["payout_milestones"],{"2x":0,"5x":0,"10x":0})
        sell=next(event for event in closed["events"] if event["type"]=="fill" and event["action"]=="SELL")
        self.assertEqual(sell["price"],"11.5")
        self.assertEqual(closed["metrics"]["trading_pnl"],"1038.7")
        self.assertEqual(closed["state"]["strategy_stats"]["recovery_call_v1"]["realized_pnl"],"1038.7")
        self.assertEqual(closed["metrics"]["payout_milestones"],{"2x":1,"5x":1,"10x":1})

    def test_stop_gap_can_lose_almost_the_entire_premium(self):
        data=put_fixture(17)
        data["snapshots"][15]["quotes"][0].update(bid="0.4",ask="0.5")
        data["snapshots"][16]["quotes"][0].update(bid="0.03",ask="0.035")
        result=backtest(data)
        self.assertEqual(result["metrics"]["trading_pnl"],"-108.3")
        self.assertGreaterEqual(Decimal(result["summary"]["monthly_pnl"]),-500)

    def test_profitable_exits_do_not_recycle_monthly_premium_budget(self):
        report=backtest(active_month())
        january=[event for event in report["events"] if event["type"]=="fill" and event["action"]=="BUY" and event["observed_at"].startswith("2024-01")]
        self.assertEqual(len(january),4)
        self.assertEqual(report["summary"]["monthly_budget_used"],"485.2")
        self.assertGreater(Decimal(report["summary"]["cash"]),500)
        self.assertEqual(report["summary"]["monthly_budget_remaining"],"14.8")
        self.assertEqual(sum(Decimal(item["monthly_committed"]) for item in report["state"]["strategy_stats"].values()),Decimal("485.2"))

    def test_attributed_cap_is_one_aggregate_not_per_strategy(self):
        state=backtest(fixture(15))["state"]
        bad=deepcopy(state)
        bad["strategy_stats"]["breakout_call_v1"]["monthly_committed"]="400"
        bad["monthly_committed"]="511.3"
        with self.assertRaises(OptionsRecoveryError):validate_state(bad)
        bad=deepcopy(state);bad["strategy_stats"]["breakdown_put_v1"]["monthly_committed"]="1"
        with self.assertRaises(OptionsRecoveryError):validate_state(bad)

    def test_actual_different_strategies_share_exact_500_debits_and_fees(self):
        report=backtest(mixed_strategies_month())
        january=[event for event in report["events"] if event["type"]=="fill" and event["action"]=="BUY" and event["observed_at"].startswith("2024-01")]
        self.assertEqual(len(january),4)
        self.assertEqual({event["strategy_id"] for event in january},set(STRATEGY_IDS))
        self.assertEqual({event["budget_committed"] for event in january},{"125"})
        self.assertEqual(report["summary"]["monthly_budget_used"],"500")
        self.assertEqual(report["summary"]["monthly_budget_remaining"],"0")
        self.assertGreater(Decimal(report["summary"]["cash"]),1000)
        stats=report["state"]["strategy_stats"]
        self.assertEqual(sum(Decimal(item["monthly_committed"]) for item in stats.values()),500)

    def test_missing_funded_quote_holds_without_fake_exit_or_new_funding(self):
        data=fixture(24)
        quote=data["snapshots"][15]["quotes"][0]
        quote.update(strike="104",contract_id=contract_id("QQQ",quote["expiry"],"call","104"))
        report=backtest(data)
        self.assertEqual(report["state"]["hold"]["code"],"missing_position_quote")
        self.assertEqual(report["state"]["closed_trade_count"],0)
        self.assertEqual(report["state"]["contributions"],"500")
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["coverage"]["source_snapshots"],24)
        self.assertEqual(report["coverage"]["processed_snapshots"],15)

    def test_required_disposal_needs_executable_bid_and_suspends_cap(self):
        data=fixture(24)
        deadline=next(row for row in data["snapshots"] if row["observed_at"].startswith("2024-01-30"))
        deadline["quotes"][0].update(bid="0",ask="0.01",bid_size=0)
        report=backtest(data)
        self.assertEqual(report["state"]["hold"]["code"],"unexecutable_disposal")
        self.assertFalse(report["summary"]["cap_qualified"])
        self.assertEqual(report["summary"]["contributions"],"500")
        self.assertIsNotNone(report["state"]["position"])

    def test_month_contribution_is_external_and_excluded_from_pnl(self):
        data=fixture(24)
        for row in data["snapshots"]:row["underlying_price"]="100"
        report=backtest(data)
        self.assertEqual(report["metrics"]["total_contributions"],"1000")
        self.assertEqual(report["metrics"]["ending_equity"],"1000")
        self.assertEqual(report["metrics"]["trading_pnl"],"0")
        self.assertEqual(report["metrics"]["maximum_monthly_loss"],"0")
        self.assertEqual(len(report["monthly_rows"]),2)
        self.assertEqual(report["monthly_rows"][1]["daily_pnl"],"0")

    def test_assurance_deadline_is_passive_and_never_clears_hold(self):
        state=backtest(fixture(15))["state"]
        original=deepcopy(state)
        checked=check_deadline(state,"2024-01-30T22:00:00+00:00")
        self.assertEqual(state,original)
        self.assertEqual(checked["hold"]["code"],"unverified_disposal_deadline")
        self.assertFalse(summarize(checked)["cap_qualified"])
        self.assertIsNotNone(checked["position"])

    def test_forward_baseline_does_not_adopt_past_signals_or_trades(self):
        data=fixture(15,historical=True)
        state=initial_state(started_at=data["as_of"])
        result=advance_paper(data,state,decided_at=data["as_of"])
        self.assertEqual(result["events"][0]["type"],"baseline")
        self.assertEqual(result["state"]["trade_count"],0)
        self.assertIsNone(result["state"]["pending"])

    def test_forward_requires_recorded_real_evidence_and_size(self):
        for basis,kind in (("assumed_eod","historical_quotes"),("recorded","synthetic_fixture")):
            data=fixture(15,historical=True);state=paper_to(data,14)
            data["source"]["quote_time_basis"]=basis;data["evidence_kind"]=kind
            result=advance_paper(data,state,decided_at=data["as_of"])
            self.assertEqual(result["state"]["hold"]["code"],"unrecorded_quote_times")
        for size in (None,0):
            data=fixture(15,historical=True);state=paper_to(data,14)
            data["snapshots"][-1]["quotes"][0]["ask_size"]=size
            result=advance_paper(data,state,decided_at=data["as_of"])
            self.assertEqual(result["state"]["trade_count"],0)
            self.assertIsNone(result["state"]["position"])

    def test_forward_prefix_future_and_missing_sessions_are_not_replayed(self):
        data=fixture(17,historical=True);state=paper_to(data,14)
        changed=through(data,15);changed["snapshots"][0]["underlying_price"]="100.01"
        self.assertEqual(advance_paper(changed,state,decided_at=changed["as_of"])["state"]["hold"]["code"],"accepted_history_changed")
        self.assertEqual(advance_paper(data,state,decided_at=data["as_of"])["state"]["hold"]["code"],"missed_observations")
        with self.assertRaises(OptionsRecoveryError):advance_paper(through(data,15),state,decided_at=through(data,14)["as_of"])
        missing=through(data,16);missing["snapshots"].pop(14)
        self.assertEqual(advance_paper(missing,state,decided_at=missing["as_of"])["state"]["hold"]["code"],"missing_session")

    def test_pristine_migration_preserves_original_500_and_activation_cutoff(self):
        old=legacy_initial(started_at="2024-01-02T22:00:00+00:00")
        before=deepcopy(old)
        state=migrate_flat_legacy(old,activated_at="2024-02-01T22:00:00+00:00")
        self.assertEqual(old,before)
        for key in ("cash","contributions","month","monthly_start_equity","started_at","monthly_committed"):
            self.assertEqual(state[key],old[key])
        self.assertEqual(state["portfolio_activation_at"],"2024-02-01T22:00:00+00:00")
        self.assertEqual(state["migration"]["new_funding"],"0")
        expected=hashlib.sha256(json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        self.assertEqual(state["migration"]["legacy_state_sha256"],expected)
        self.assertEqual(state["trade_count"],0)

    def test_migration_rejects_observed_used_held_or_refunded_accounts(self):
        data=fixture(15,historical=True)
        for old in (legacy_initial(data,started_at=data["as_of"]),):
            with self.assertRaises(OptionsRecoveryError):migrate_flat_legacy(old,activated_at=data["as_of"])
        pristine=legacy_initial(started_at="2024-01-02T22:00:00+00:00")
        for mutation in ({"cash":"1000","contributions":"1000","monthly_start_equity":"1000"},{"monthly_committed":"1"},{"hold":{"code":"stop","message":"Owner review","observed_at":pristine["started_at"]},"cap_qualified":False}):
            old=deepcopy(pristine);old.update(mutation)
            with self.assertRaises(OptionsRecoveryError):migrate_flat_legacy(old,activated_at="2024-01-03T22:00:00+00:00")
        with self.assertRaises(OptionsRecoveryError):migrate_flat_legacy(pristine,activated_at="2024-01-01T22:00:00+00:00")

    def test_chunk_boundaries_preserve_one_ledger_and_exact_results(self):
        data=active_month();single=backtest_comparison(data);stream=backtest_comparison(chunks(data,13,20,29))
        for key in ("metrics","state","curve","events","monthly_rows"):
            self.assertEqual(single["portfolio"][key],stream["portfolio"][key])
        for identifier in STRATEGY_IDS:
            self.assertEqual(single["strategy_results"][identifier]["metrics"],stream["strategy_results"][identifier]["metrics"])
        self.assertEqual(stream["coverage"]["chunks"],4)
        self.assertEqual(stream["coverage"]["source_snapshots"],len(data["snapshots"]))

    def test_archive_gap_holds_all_ledgers_and_overlap_or_basis_change_rejects(self):
        data=fixture(24);parts=list(chunks(data,14));parts[1]["snapshots"].pop(0)
        report=backtest_comparison(parts)
        self.assertFalse(report["all_ledgers_complete"])
        self.assertEqual(report["portfolio"]["state"]["hold"]["code"],"historical_session_gap")
        for result in report["strategy_results"].values():
            self.assertEqual(result["coverage"]["source_snapshots"],23)
            self.assertEqual(result["coverage"]["processed_snapshots"],14)
        with self.assertRaises(OptionsRecoveryError):backtest([data,data])
        parts=list(chunks(data,14));parts[1]["source"]["provider"]="Different source"
        with self.assertRaises(OptionsRecoveryError):backtest(parts)

    def test_comparison_reports_same_full_sources_without_summing_accounts(self):
        report=backtest_comparison(fixture(24))
        self.assertEqual(set(report["strategy_results"]),set(STRATEGY_IDS))
        self.assertEqual(report["metrics"],report["portfolio"]["metrics"])
        self.assertFalse(report["execution_qualified"])
        self.assertIn("independent counterfactual",report["comparison_scope"])
        self.assertNotIn("winner",report)
        self.assertNotIn("ranking",report)
        for identifier,result in report["strategy_results"].items():
            self.assertEqual(result["enabled_strategies"],[identifier])
            for key in ("source_snapshots","first_date","last_source_date","quote_time_basis"):
                self.assertEqual(result["coverage"][key],report["coverage"][key])

    def test_state_rejects_unfunded_cash_and_mismatched_position_attribution(self):
        state=backtest(put_fixture(15))["state"]
        mutations=(lambda x:x.update(cash="9999"),lambda x:x["position"].update(type="call"),lambda x:x["position"].update(contract_id=contract_id("SPY",x["position"]["expiry"],"put","95")),lambda x:x.update(trade_count=2))
        for mutate in mutations:
            bad=deepcopy(state);mutate(bad)
            with self.assertRaises(OptionsRecoveryError):validate_state(bad)

    def test_low_process_decimal_precision_cannot_change_money_or_rules(self):
        expected=backtest_comparison(fixture(21))
        with localcontext() as context:
            context.prec=3
            actual=backtest_comparison(fixture(21))
        self.assertEqual(actual["metrics"],expected["metrics"])
        self.assertEqual(actual["portfolio"]["state"],expected["portfolio"]["state"])


if __name__=="__main__":unittest.main()
