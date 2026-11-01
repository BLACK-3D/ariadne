"""Synthetic total-bankroll accounting/clock tests, never historical returns."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Inexact, ROUND_UP, localcontext
import unittest

from ariadne import options_eod as base, options_root_eod as old, options_bankroll_eod as bank
from ariadne.qqq_data import session_dates
from test_options_root_eod import state as old_state, row as ko_row, ko_opened, step as old_step
from test_options_eod import received, H, J
from test_options_stress_context import witness

ACTIVATED="2024-01-03T15:03:00+00:00"


def state(prior=None, *, activated_at=ACTIVATED):
    return bank.migrate_flat(old_state() if prior is None else prior,activated_at=activated_at,old_state_sha256=H,old_data_sha256=J)


def step(current,day=date(2024,1,2),*,contexts=(),**kwargs):
    kwargs={"bid":"0.60","ask":"0.65","bid_size":100,"ask_size":230,**kwargs}
    row=ko_row(day,**kwargs);clock=max(base._clock(row["received_at"]),base._clock(current["last_decided_at"]))
    return bank.advance_paper(row,current,decided_at=clock,contexts=contexts)


def opened(*,bid_size=100,ask_size=230):
    current=state();ring=[]
    for day,price in ((date(2024,1,2),"59.8"),(date(2024,1,3),"59.7"),(date(2024,1,4),"59.6")):
        ring.append(witness(day.isoformat()));current=step(current,day,price=price,contexts=tuple(ring),bid_size=bid_size,ask_size=ask_size)["state"]
    if current["position"] is None:raise AssertionError("Synthetic fixture must open a put")
    return current


def closed():
    current=opened()
    for day,bid,ask in ((date(2024,1,5),"3.25","3.30"),(date(2024,1,8),"3.20","3.25"),(date(2024,1,9),"3.20","3.25")):
        current=step(current,day,price="59.5",bid=bid,ask=ask)["state"]
    if current["position"] is not None:raise AssertionError("Synthetic fixture must close the put")
    return current


class BankrollEODTests(unittest.TestCase):
    def test_migration_credits_only_difference_without_reset_or_input_mutation(self):
        prior=old_state();before=deepcopy(prior);current=state(prior)
        self.assertEqual(prior,before)
        self.assertEqual(current["cash"],"25000");self.assertEqual(current["contributions"],"25000")
        self.assertEqual(current["bankroll_policy"]["activation"]["new_funding"],"24500")
        self.assertEqual(current["bankroll_processed_count"],0)
        for key in ("funding_count","processed_count","root_processed_count","prefix_sha256","history","cursor_history","migration","root_policy","milestone_counts","month","fees","trade_count","closed_trade_count","monthly_committed"):
            self.assertEqual(current[key],prior[key],key)
        self.assertEqual(current["bankroll_policy"]["activation"]["old_canonical_state_sha256"],base._digest(prior))
        summary=bank.summarize(current)
        self.assertIsNone(summary["last_daily_result"]);self.assertIsNone(summary["last_source_date"])
        self.assertEqual(summary["trading_pnl"],"0");self.assertFalse(summary["automatic_topups"])

    def test_migration_rejects_non5_exposure_hold_and_bad_hash_clock(self):
        for prior in (state(),ko_opened()):
            with self.assertRaises(bank.OptionsBankrollEODError):state(prior,activated_at="2024-01-10T15:00:00Z")
        prior=old_state();prior["hold"]={"code":"owner_hold","message":"Keep hold","recorded_at":prior["last_decided_at"]};prior["cap_qualified"]=False
        with self.assertRaises(bank.OptionsBankrollEODError):state(prior)
        for args in ({"old_state_sha256":"bad"},{"old_data_sha256":"bad"},{"activated_at":"2024-01-01T00:00:00Z"}):
            fields=dict(activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J);fields.update(args)
            with self.assertRaises(bank.OptionsBankrollEODError):bank.migrate_flat(old_state(),**fields)

    def test_prior_closed_profit_spending_fees_and_counts_are_preserved(self):
        prior=ko_opened()
        for day,bid,ask in ((date(2024,1,5),"4.5","4.6"),(date(2024,1,8),"4.4","4.5"),(date(2024,1,9),"4.4","4.5")):
            prior=old_step(prior,day,price="59.5",bid=bid,ask=ask)["state"]
        self.assertIsNone(prior["position"])
        current=state(prior,activated_at="2024-01-10T15:03:00Z")
        for key in ("trade_count","closed_trade_count","monthly_committed","fees","funding_count","processed_count","root_processed_count","prefix_sha256","history","cursor_history"):
            self.assertEqual(prior[key],current[key],key)
        self.assertEqual(base._number(current["cash"]),base._number(prior["cash"])+24500)
        self.assertEqual(bank.summarize(current)["trading_pnl"],old.summarize(prior)["trading_pnl"])
        result=step(current,date(2024,1,10),price="60",quotes=[])
        self.assertEqual(result["summary"]["last_daily_result"]["daily_pnl"],"0")

    def test_real_quantity_fifteen_fee_reserve_mark_and_delayed_buy(self):
        current=opened();position=current["position"]
        self.assertEqual(position["quantity"],15);self.assertEqual(position["premium"],"975")
        self.assertEqual(position["entry_fee"],"9.75");self.assertEqual(position["exit_fee_reserved"],"9.75")
        self.assertEqual(position["budget_debit"],"994.5");self.assertEqual(current["monthly_committed"],"994.5")
        self.assertEqual(current["cash"],"24015.25");self.assertEqual(current["fees"],"9.75")
        self.assertEqual(current["trade_count"],1);self.assertEqual(current["contract_entry_count"],15)
        self.assertEqual(bank.summarize(current)["equity"],"24905.5")
        self.assertGreater(position["entry_source_date"],base._day_at(base._clock(position["decision_at"])).isoformat())
        self.assertEqual(position["contract_id"],"KO240119P00059000")

    def test_entry_quantity_clipped_by_both_actual_sides(self):
        self.assertEqual(opened(bid_size=2)["position"]["quantity"],2)
        self.assertEqual(opened(ask_size=3)["position"]["quantity"],3)
        self.assertEqual(opened(bid_size=1,ask_size=230)["position"]["quantity"],1)

    def test_low_and_zero_entry_sizes_never_invent_liquidity(self):
        for sizes in ({"bid_size":0},{"ask_size":0}):
            result=step(state(),contexts=(witness("2024-01-02"),),**sizes)
            self.assertIsNone(result["state"]["pending"]);self.assertEqual(result["state"]["monthly_committed"],"0")

    def test_entry_debit_does_not_exceed_four_percent_cash_or_month(self):
        current=opened()
        p=current["position"]
        self.assertLessEqual(base._number(p["budget_debit"]),base._number(p["entry_equity"])*base._number(".04"))
        self.assertLessEqual(base._number(p["budget_debit"])+base._number(p["entry_monthly_committed"]),25000)
        self.assertEqual(bank._size({**state(),"cash":"12500","contributions":"12500"},{"ask":"0.65","ask_size":230,"bid_size":100})["quantity"],7)

    def test_full_quantity_sale_and_per_contract_fees_reconcile(self):
        current=closed()
        self.assertIsNone(current["position"]);self.assertIsNone(current["pending"])
        self.assertEqual(current["cash"],"28805.5");self.assertEqual(current["fees"],"19.5")
        self.assertEqual(current["closed_trade_count"],1);self.assertEqual(current["contract_exit_count"],15)
        self.assertEqual(current["monthly_committed"],"994.5")
        self.assertEqual(bank.summarize(current)["trading_pnl"],"3805.5")
        self.assertEqual(current["strategy_stats"][bank.KERNEL_STRATEGY_ID]["realized_pnl"],"3805.5")

    def test_undersized_new_bid_holds_without_partial_sale_or_mark_revision(self):
        current=opened();before=deepcopy(current)
        result=step(current,date(2024,1,5),price="59.5",bid="3.25",ask="3.30",bid_size=14)
        self.assertEqual(result["status"],"held")
        for key in ("cash","fees","monthly_committed","trade_count","closed_trade_count","position","processed_count","history","cursor_history","prefix_sha256"):
            self.assertEqual(result["state"][key],before[key],key)
        self.assertEqual(current,before);self.assertFalse(result["summary"]["cap_qualified"])

    def test_missing_held_quote_and_invalid_disposal_never_create_proceeds(self):
        current=opened();result=step(current,date(2024,1,5),quotes=[])
        self.assertEqual(result["status"],"held");self.assertEqual(result["state"]["cash"],current["cash"])

    def test_missing_new_context_cancels_buy_but_does_not_block_sale(self):
        current=state();ring=[]
        for day,price in ((date(2024,1,2),"59.8"),(date(2024,1,3),"59.7")):
            ring.append(witness(day.isoformat()));current=step(current,day,price=price,contexts=tuple(ring))["state"]
        result=step(current,date(2024,1,4),price="59.6",contexts=())
        self.assertIsNone(result["state"]["position"]);self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["cash"],"25000");self.assertIsNone(result["state"]["hold"])
        self.assertEqual(closed()["closed_trade_count"],1)

    def test_invalid_new_context_denies_only_new_buy(self):
        result=step(state(),contexts=({"bad":True},))
        self.assertIsNone(result["state"]["pending"]);self.assertIsNone(result["state"]["hold"])
        self.assertTrue(result["summary"]["context_input_rejected"])

    def test_fresh_six_gate_not_inherited_five_permission(self):
        current=state();self.assertEqual(current["bankroll_policy"]["context_count"],0)
        result=step(current,contexts=(witness("2024-01-02"),))
        gate=result["summary"]["last_entry_gate"]
        self.assertEqual(gate["rule_sha256"],bank.RULE_SHA256)
        self.assertEqual(gate["policy_id"],bank.STRATEGY_ID)
        self.assertEqual(gate["benchmark_gate"]["policy_id"],"f_stress_breakdown_put_v1")
        self.assertIsNone(current["pending"])

    def test_corrupt_stored_gate_is_fatal(self):
        current=step(state(),contexts=(witness("2024-01-02"),))["state"]
        current["bankroll_policy"]["context_history"][0]["gate"]["allowed"]=False
        with self.assertRaises(ValueError):bank.validate_state(current)

    def test_future_stale_changed_gap_and_repeated_sources(self):
        current=step(state(),quotes=[])["state"]
        same=step(current,quotes=[]);self.assertEqual(same["status"],"unchanged")
        self.assertEqual(step(current,price="59.7",quotes=[])["status"],"held")
        self.assertEqual(step(current,date(2024,1,4),quotes=[])["state"]["hold"]["code"],"missing_source_session")
        row=ko_row(date(2024,1,3));row["received_at"]="2024-01-10T16:00:00Z"
        with self.assertRaises(ValueError):bank.advance_paper(row,current,decided_at="2024-01-04T16:00:00Z")
        with self.assertRaises(ValueError):bank.advance_paper(ko_row(),current,decided_at="2024-01-01T16:00:00Z")

    def test_no_five_projection_and_no_extra_funding(self):
        current=state()
        with self.assertRaises(ValueError):old.validate_state(current)
        for key in ("contributions","cash"):
            changed=deepcopy(current);changed[key]="50000"
            with self.assertRaises(ValueError):bank.validate_state(changed)

    def test_ancestry_and_counts_cannot_be_reset(self):
        current=step(state(),quotes=[])["state"]
        for key in ("processed_count","root_processed_count","bankroll_processed_count"):
            changed=deepcopy(current);changed[key]=0
            with self.assertRaises(ValueError):bank.validate_state(changed)
        changed=deepcopy(current);changed["root_policy"]["context_count"]+=1
        with self.assertRaises(ValueError):bank.validate_state(changed)

    def test_original_migration_and_selector_are_immutable(self):
        current=state()
        for key,value in (("next_strategy",(current["next_strategy"]+1)%3),("portfolio_activation_at","2024-01-03T15:01:00Z")):
            changed=deepcopy(current);changed[key]=value
            with self.assertRaises(ValueError):bank.validate_state(changed)
        changed=deepcopy(current);changed["migration"]["old_data_sha256"]="9"*64
        with self.assertRaises(ValueError):bank.validate_state(changed)

    def test_fill_recomputes_quantity_from_later_observed_depth(self):
        current=state();ring=[]
        for day,price in ((date(2024,1,2),"59.8"),(date(2024,1,3),"59.7")):
            ring.append(witness(day.isoformat()));current=step(current,day,price=price,contexts=tuple(ring))["state"]
        ring.append(witness("2024-01-04"))
        filled=step(current,date(2024,1,4),price="59.6",bid_size=2,contexts=tuple(ring))["state"]
        self.assertEqual(filled["position"]["quantity"],2);self.assertEqual(filled["monthly_committed"],"132.6")
        cancelled=step(current,date(2024,1,4),price="59.6",bid_size=0,contexts=tuple(ring))["state"]
        self.assertIsNone(cancelled["position"]);self.assertIsNone(cancelled["pending"]);self.assertEqual(cancelled["cash"],"25000")

    def test_mandatory_disposal_full_quantity_or_durable_hold(self):
        current=opened();deadline=base._day(current["position"]["forced_exit_session"])
        days=session_dates(date(2024,1,5),deadline)
        for day in days[:-1]:current=step(current,day,price="59.5")["state"]
        ring=(witness(deadline.isoformat()),)
        sold=step(current,deadline,price="59.4",contexts=ring)["state"]
        self.assertIsNone(sold["position"]);self.assertIsNone(sold["pending"])
        self.assertEqual(sold["cash"],"24905.5");self.assertEqual(sold["fees"],"19.5")
        self.assertEqual(sold["bankroll_policy"]["context_count"],current["bankroll_policy"]["context_count"])
        held=step(current,deadline,price="59.4",bid_size=14)["state"]
        self.assertIsNotNone(held["hold"]);self.assertEqual(held["cash"],current["cash"])
        self.assertEqual(held["contract_exit_count"],0)

    def test_same_entry_fee_threshold_cannot_round_over_budget(self):
        current=state()
        exact=bank._size(current,{"ask":"9.987","ask_size":100,"bid_size":100})
        over=bank._size(current,{"ask":"9.98700001","ask_size":100,"bid_size":100})
        self.assertEqual(exact["quantity"],1);self.assertEqual(exact["all_in_commitment"],"1000.00")
        self.assertEqual(over["quantity"],0)

    def test_flat_month_rollover_event_is_not_a_contribution(self):
        current=closed();events=[]
        for day in session_dates(date(2024,1,10),date(2024,2,1)):
            result=step(current,day,price="60",quotes=[]);current=result["state"];events.extend(result["events"])
        resets=[e for e in events if e["type"]=="monthly_allowance_reset"]
        self.assertEqual(len(resets),1);self.assertEqual(resets[0]["funding_added"],"0")
        self.assertFalse(any(e["type"]=="contribution" for e in events))

    def test_quantity_fees_and_sizing_witness_tampering_rejected(self):
        current=opened()
        for field,value in (("quantity",14),("entry_fee","0.65"),("entry_equity","500000"),("entry_bid_size",14),("last_marked_bid_size",14)):
            changed=deepcopy(current);changed["position"][field]=value
            with self.assertRaises(ValueError):bank.validate_state(changed)

    def test_legacy_milestones_and_same_month_spending_cannot_be_erased(self):
        prior=ko_opened()
        for day,bid,ask in ((date(2024,1,5),"4.5","4.6"),(date(2024,1,8),"4.4","4.5"),(date(2024,1,9),"4.4","4.5")):
            prior=old_step(prior,day,price="59.5",bid=bid,ask=ask)["state"]
        current=state(prior,activated_at="2024-01-10T15:03:00Z")
        self.assertEqual(current["milestone_counts"]["2x"],1)
        changed=deepcopy(current);changed["milestone_counts"]["2x"]=0
        changed["strategy_stats"][bank.KERNEL_STRATEGY_ID]["milestone_counts"]["2x"]=0
        with self.assertRaises(ValueError):bank.validate_state(changed)
        current=step(current,date(2024,1,10),price="60",quotes=[])["state"]
        changed=deepcopy(current);changed["monthly_committed"]="0"
        changed["strategy_stats"][bank.KERNEL_STRATEGY_ID]["monthly_committed"]="0"
        changed["last_daily_result"].update(monthly_budget_used="0",monthly_budget_remaining="25000")
        with self.assertRaises(ValueError):bank.validate_state(changed)

    def test_month_rollover_resets_only_gross_no_cash_or_funding(self):
        current=closed();cash=current["cash"];funding=current["funding_count"]
        days=session_dates(date(2024,1,10),date(2024,2,1))
        for day in days:current=step(current,day,price="60",quotes=[])["state"]
        self.assertEqual(current["month"],"2024-02");self.assertEqual(current["monthly_committed"],"0")
        self.assertEqual(current["cash"],cash);self.assertEqual(current["contributions"],"25000");self.assertEqual(current["funding_count"],funding)

    def test_frames_starting_pending_or_exposure_do_not_reenter(self):
        current=closed()
        self.assertIsNone(current["pending"])
        self.assertEqual(current["trade_count"],1)

    def test_passive_deadline_does_not_fabricate_or_mutate(self):
        current=opened();before=deepcopy(current)
        result=bank.check_deadline(current,"2024-01-31T23:00:00Z")
        self.assertEqual(current,before);self.assertEqual(result,before)
        self.assertEqual(bank.summarize(current,now="2024-01-31T23:00:00Z")["cap_assurance"],"unverified_missing_disposal_source")

    def test_hostile_caller_precision_rounding_traps_isolated(self):
        current=opened();expected=bank.summarize(current)
        with localcontext() as ctx:
            ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True
            self.assertEqual(bank.summarize(current),expected)
            self.assertEqual(state()["cash"],"25000")

    def test_output_and_inputs_detached(self):
        current=opened();before=deepcopy(current);summary=bank.summarize(current)
        summary["position"]["quantity"]=999
        self.assertEqual(current,before)
        result=step(current,date(2024,1,5),price="59.5")
        result["state"]["root_policy"]["activation"]["symbol"]="F"
        self.assertEqual(current,before)


if __name__=="__main__":unittest.main()
