"""Synthetic exact-root fixtures, never market-return or source qualification."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Inexact, ROUND_UP, localcontext
import hashlib
import unittest

from ariadne import options_eod as base, options_stress_eod as stress, options_root_eod as root
from ariadne.qqq_data import session_dates
from test_options_eod import state as state3, snapshot, quote, received, opened, baseline as f_baseline, H, J
from test_options_stress_eod import state as state4, step as stress_step, opened as stress_opened
from test_options_stress_context import witness

ACTIVATED = "2024-01-03T15:02:00+00:00"


def baseline():
    return [{**row, "underlying_close":"60", "stock_sha256":J} for row in f_baseline()]


def state(*, refs=True, old=None, activated_at=ACTIVATED, seed=None):
    return root.migrate_flat(state4() if old is None else old, activated_at=activated_at, old_state_sha256=H,
                             old_data_sha256=J, baseline=(baseline() if seed is None else seed) if refs else None)


def put(*, strike="59", expiry="2024-01-19", bid="0.8", ask="0.9", bid_size=4, ask_size=5):
    return {"contract_id":root.contract_id("KO",expiry,"put",strike), "expiry":expiry, "type":"put", "strike":strike,
            "bid":bid, "ask":ask, "bid_size":bid_size, "ask_size":ask_size}


def row(day=date(2024,1,2), *, price="59.8", quotes=None, **quote_args):
    return {**snapshot(day,price=price,quotes=[put(**quote_args)] if quotes is None else quotes), "underlying_symbol":"KO"}


def step(current, day=date(2024,1,2), *, contexts=(), **kwargs):
    value=row(day,**kwargs);clock=max(base._clock(value["received_at"]),base._clock(current["last_decided_at"]))
    return root.advance_paper(value,current,decided_at=clock.isoformat(),contexts=contexts)


def ko_opened():
    current=state();ring=[]
    for day,price in ((date(2024,1,2),"59.8"),(date(2024,1,3),"59.7"),(date(2024,1,4),"59.6")):
        ring.append(witness(day.isoformat()));current=step(current,day,price=price,contexts=tuple(ring))["state"]
    if current["position"] is None:raise AssertionError("Generated KO fixture must fill a put")
    return current


def legacy_corpus():
    result={}
    for refs in (True,False):
        current=state3(refs=refs);outputs=[]
        for day,price,bid,ask in ((date(2024,1,2),"12.05","0.8","0.9"),(date(2024,1,3),"12.1","0.8","0.9"),
            (date(2024,1,4),"12.15","0.8","0.9"),(date(2024,1,5),"12.2","4.5","4.6"),
            (date(2024,1,8),"12.2","4.4","4.5"),(date(2024,1,9),"12.2","4.4","4.5")):
            value=snapshot(day,price=price,quotes=[quote(bid=bid,ask=ask)])
            clock=max(base._clock(received(day)),base._clock(current["last_decided_at"]))
            output=base.advance_paper(value,current,decided_at=clock.isoformat());outputs.append(output);current=output["state"]
        result["schema3_refs_"+str(refs)]=outputs
    result["schema3_missing_funded_quote"]=base.advance_paper(snapshot(date(2024,1,5),quotes=[quote(strike="13")]),opened(),decided_at=received(date(2024,1,5)))
    result["schema4_empty_context"]=stress_step(state4(),contexts=())
    current=stress_opened();outputs=[]
    for day,bid,ask in ((date(2024,1,5),"4.5","4.6"),(date(2024,1,8),"4.4","4.5"),(date(2024,1,9),"4.4","4.5")):
        output=stress_step(current,day,price="11.6",bid=bid,ask=ask,contexts=());outputs.append(output);current=output["state"]
    result["schema4_existing_exposure_no_new_context"]=outputs
    return result


class RootEODTests(unittest.TestCase):
    def test_pre_extraction_full_legacy_states_events_summaries_are_byte_identical(self):
        expected={"schema3_refs_True":"b912d1fe599a03ca707d6117e8d5d4592897029335296d1aa0562b4a49f629db",
                  "schema3_refs_False":"b59f8ef0758b10b8d840f43a9f35bfe385beaec45ae0bccaa3d0ed6f54d7c861",
                  "schema3_missing_funded_quote":"bebc72275f6fa31e24cbd386529cac4271b7f6bfe9d27e83e8f462c0f890f0ed",
                  "schema4_empty_context":"170f6e8e608af1d85b1e37f29af92a6a3a0ebb6c970b1202e442b0b23910696b",
                  "schema4_existing_exposure_no_new_context":"d0447c3e03b5f3525df3ba0f4c26bf7b2525b8e1097e154a2410e9c8ce331924"}
        self.assertEqual({k:hashlib.sha256(base._canonical(v)).hexdigest() for k,v in legacy_corpus().items()},expected)
        self.assertEqual(base.RULE_SHA256,"22ea96117b89f2b7c38e3853071363449aacac0074bf83a113f4fa36aec3b0f0")

    def test_pristine_migration_changes_root_without_capital_or_old_evidence_reset(self):
        old=state4();before=deepcopy(old);current=state(old=old)
        self.assertEqual(old,before)
        for key in (*base._FINANCIAL,"funding_count","prefix_sha256","processed_count","migration","strategy_stats","milestone_counts"):
            self.assertEqual(current[key],old[key],key)
        self.assertEqual(current["symbol"],"KO");self.assertEqual(current["source_id"],"onclick_ko_eod_v1")
        self.assertEqual(current["root_processed_count"],0);self.assertEqual(current["cursor_history"],[])
        self.assertEqual(current["history"],baseline());self.assertNotEqual(current["history"],old["history"])
        act=current["root_policy"]["activation"]
        self.assertEqual(act["old_canonical_state_sha256"],base._digest(old))
        self.assertEqual(act["legacy_stress_policy_sha256"],base._digest(old["stress_policy"]))
        self.assertEqual(act["new_funding"],"0");self.assertIs(act["risk_limits_changed"],False)
        self.assertEqual(current["root_policy"]["context_count"],0)
        self.assertIsNone(root.summarize(current)["last_source_date"])

    def test_only_verified_supported_root_and_pristine4_can_migrate(self):
        for kwargs in ({"symbol":"F"},{"symbol":"INTC"},{"old_state_sha256":"bad"},{"activated_at":"2024-01-01T00:00:00Z"}):
            arguments=dict(activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J,baseline=baseline());arguments.update(kwargs)
            with self.assertRaises(root.OptionsRootEODError):root.migrate_flat(state4(),**arguments)
        for old in (state3(),ko_opened(),stress_opened()):
            with self.assertRaises(root.OptionsRootEODError):root.migrate_flat(old,activated_at="2024-01-09T15:00:00Z",old_state_sha256=H,old_data_sha256=J)

    def test_observed_F_cutoff_preserves_total_count_but_uses_separate_KO_ring(self):
        prior=stress_step(state4(),price="12",contexts=())["state"]
        seed=baseline()+[{"source_date":"2024-01-02","received_at":received(date(2024,1,2)),"underlying_close":"60","stock_sha256":J}]
        current=state(old=prior,seed=seed,activated_at="2024-01-03T15:03:00+00:00")
        self.assertEqual(current["processed_count"],1);self.assertEqual(current["root_processed_count"],0)
        display=root.summarize(current)
        self.assertIsNone(display["last_daily_result"]);self.assertIsNone(display["last_source_date"])
        self.assertEqual(display["legacy_last_daily_result"],prior["last_daily_result"])
        self.assertEqual(display["legacy_last_source_date"],prior["last_source_date"])
        result=step(current,date(2024,1,3),contexts=(witness("2024-01-03"),))
        self.assertEqual(result["state"]["processed_count"],2);self.assertEqual(result["state"]["root_processed_count"],1)
        self.assertEqual(result["state"]["cursor_history"][0]["previous_sha256"],prior["prefix_sha256"])
        held=step(current,date(2024,1,2))
        self.assertEqual(held["status"],"held");self.assertEqual(held["state"]["processed_count"],1)

    def test_reused_old_F_gate_is_not_an_initial_root_permission(self):
        current=state();self.assertEqual(current["root_policy"]["context_history"],[])
        denied=step(current,contexts=())
        self.assertIsNone(denied["state"]["pending"])
        self.assertEqual(denied["state"]["root_policy"]["context_count"],1)
        gate=denied["state"]["root_policy"]["context_history"][0]["gate"]
        self.assertEqual(gate["underlying_symbol"],"KO");self.assertEqual(gate["policy_id"],root.POLICY_ID)
        self.assertEqual(gate["benchmark_gate"]["policy_id"],"f_stress_breakdown_put_v1")
        self.assertFalse(gate["allowed"])

    def test_real_KO_ids_delayed_buy_and_one_shared_debit(self):
        current=state();ring=[];results=[]
        for day,price in ((date(2024,1,2),"59.8"),(date(2024,1,3),"59.7"),(date(2024,1,4),"59.6")):
            ring.append(witness(day.isoformat()));result=step(current,day,price=price,contexts=tuple(ring));results.append(result);current=result["state"]
        self.assertIsNotNone(results[0]["state"]["pending"]);self.assertIsNone(results[1]["state"]["position"])
        self.assertEqual(current["position"]["contract_id"],"KO240119P00059000")
        self.assertEqual(current["monthly_committed"],"91.3");self.assertEqual(current["cash"],"409.35")
        self.assertEqual(current["fees"],"0.65");self.assertEqual(current["contributions"],"500")
        self.assertEqual(current["trade_count"],1)
        buy=next(e for e in results[-1]["events"] if e.get("action")=="BUY")
        self.assertEqual(buy["underlying_symbol"],"KO");self.assertIs(buy["execution_qualified"],False)
        self.assertTrue(all(r["gate"]["underlying_symbol"]=="KO" for r in current["root_policy"]["context_history"]))

    def test_late_nonstress_or_malformed_new_context_cancels_only_pending_buy(self):
        first=step(state(),contexts=(witness("2024-01-02"),))["state"]
        second=step(first,date(2024,1,3),contexts=(witness("2024-01-03"),))["state"]
        for contexts in ((),({"bad":True},),(witness("2024-01-03",published="2024-01-04T15:00:00+00:00"),)):
            result=step(second,date(2024,1,4),contexts=contexts)
            self.assertIsNone(result["state"]["pending"]);self.assertIsNone(result["state"]["position"])
            self.assertEqual(result["state"]["monthly_committed"],"0");self.assertIsNone(result["state"]["hold"])

    def test_absent_new_context_still_allows_conditional_and_forced_sales(self):
        current=ko_opened()
        for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):
            result=step(current,day,bid="4.5",ask="4.6",contexts=());current=result["state"]
        self.assertIsNone(current["position"]);self.assertEqual(current["closed_trade_count"],1)
        self.assertTrue(any(e.get("action")=="SELL" and e["type"]=="fill" for e in result["events"]))
        self.assertIsNone(current["pending"])
        current=ko_opened();deadline=base._day(current["position"]["forced_exit_session"])
        for day in session_dates(date(2024,1,5),deadline):current=step(current,day,contexts=())["state"]
        self.assertIsNone(current["position"]);self.assertEqual(current["closed_trade_count"],1)

    def test_zero_sized_crossed_wrong_root_or_missing_funded_quotes_never_fill(self):
        for change in ({"bid_size":None},{"ask_size":1.0},{"bid":"1","ask":"0.9"}):
            with self.assertRaises(root.OptionsRootEODError):root.validate_snapshot(row(quotes=[put(**change)]))
        wrong=row();wrong["quotes"][0]["contract_id"]="F240119P00059000"
        with self.assertRaises(root.OptionsRootEODError):root.validate_snapshot(wrong)
        zero=step(state(),contexts=(witness("2024-01-02"),),ask_size=0)
        self.assertIsNone(zero["state"]["pending"])
        held=step(ko_opened(),date(2024,1,5),quotes=[])
        self.assertEqual(held["status"],"held");self.assertEqual(held["state"]["hold"]["code"],"missing_position_quote")
        self.assertEqual(held["state"]["root_processed_count"],3)

    def test_empty_flat_actual_candidate_chain_records_only_stock_and_no_money(self):
        result=step(state(),quotes=[],contexts=(witness("2024-01-02"),))
        self.assertEqual(result["status"],"completed");self.assertEqual(result["state"]["root_processed_count"],1)
        self.assertEqual(result["state"]["cash"],"500");self.assertIsNone(result["state"]["pending"])
        self.assertFalse(any(e.get("action")=="BUY" for e in result["events"]))

    def test_100_allin_and_underlying_boundary_remain_entry_gates(self):
        for kwargs in ({"ask":"0.99","bid":"0.98"},{"price":"100","strike":"99"},{"price":"105","strike":"100"}):
            result=step(state(),contexts=(witness("2024-01-02"),),**kwargs)
            self.assertIsNone(result["state"]["pending"]);self.assertEqual(result["state"]["monthly_committed"],"0")

    def test_no_same_frame_reentry_after_sale_even_when_fresh_stress_ready(self):
        current=ko_opened()
        for day in (date(2024,1,5),date(2024,1,8)):
            current=step(current,day,price="59.0",bid="4.5",ask="4.6",contexts=(witness(day.isoformat()),))["state"]
        result=step(current,date(2024,1,9),price="58.9",quotes=[put(bid="4.5",ask="4.6"),put(strike="58.5",bid="0.8",ask="0.9")],contexts=(witness("2024-01-09"),))
        self.assertIsNone(result["state"]["position"]);self.assertIsNone(result["state"]["pending"])
        self.assertFalse(any(e["type"]=="decision" and e.get("action")=="BUY" for e in result["events"]))

    def test_source_changed_stale_gap_future_and_replay_do_not_adopt_new_frames(self):
        first=step(state(),quotes=[])
        self.assertEqual(step(first["state"],quotes=[])["status"],"unchanged")
        self.assertEqual(step(first["state"],price="59.7",quotes=[])["state"]["hold"]["code"],"accepted_source_changed")
        self.assertEqual(step(first["state"],date(2024,1,4),quotes=[])["state"]["hold"]["code"],"missing_source_session")
        with self.assertRaises(root.OptionsRootEODError):root.advance_paper(row(),state(),decided_at="2024-01-02T15:00:00Z")

    def test_migration_and_current_policy_hash_tampering_is_fatal(self):
        current=ko_opened()
        for change in (lambda x:x["root_policy"]["activation"].update(new_funding="500"),
                       lambda x:x.update(root_processed_count=0),lambda x:x.update(symbol="F"),
                       lambda x:x["root_policy"]["context_history"][-1]["gate"].update(underlying_symbol="F"),
                       lambda x:x["root_policy"]["context_history"][-1]["gate"]["benchmark_gate"].update(allowed=False)):
            modified=deepcopy(current);change(modified)
            with self.assertRaises(root.OptionsRootEODError):root.validate_state(modified)

    def test_decimal_context_and_inputs_do_not_change_account_math(self):
        original=state();before=deepcopy(original);value=row();snapshot_before=deepcopy(value)
        result=root.advance_paper(value,original,decided_at=ACTIVATED,contexts=())
        with localcontext() as precision:
            precision.prec=1;precision.rounding=ROUND_UP;precision.traps[Inexact]=True
            self.assertEqual(root.advance_paper(value,original,decided_at=ACTIVATED,contexts=()),result)
            self.assertEqual(root.summarize(result["state"]),result["summary"])
        self.assertEqual(original,before);self.assertEqual(value,snapshot_before)

    def test_existing_funding_count_is_preserved_and_next_same_month_does_not_redeposit(self):
        current=state4()
        days=session_dates(date(2024,1,2),date(2024,1,31))
        for day in days:current=stress_step(current,day,price="12",expiry="2024-03-15",contexts=())["state"]
        self.assertEqual(current["cash"],"1000");self.assertEqual(current["funding_count"],1)
        seed=[{"source_date":day.isoformat(),"underlying_close":"60","received_at":received(day),"stock_sha256":J} for day in days[-14:]]
        changed=state(old=current,activated_at="2024-02-01T15:02:00+00:00",seed=seed)
        self.assertEqual(changed["processed_count"],len(days));self.assertEqual(changed["root_processed_count"],0)
        self.assertEqual(changed["cash"],"1000");self.assertEqual(changed["contributions"],"1000")
        self.assertEqual(changed["funding_count"],1);self.assertEqual(changed["month"],"2024-02")
        advanced=step(changed,date(2024,2,1),price="60",quotes=[])["state"]
        self.assertEqual(advanced["cash"],"1000");self.assertEqual(advanced["funding_count"],1)
        for day in session_dates(date(2024,2,2),date(2024,2,29)):
            advanced=step(advanced,day,price="60",quotes=[])["state"]
        self.assertEqual(advanced["cash"],"1500");self.assertEqual(advanced["contributions"],"1500")
        self.assertEqual(advanced["funding_count"],2)
        self.assertEqual(advanced["processed_count"],len(days)+advanced["root_processed_count"])

    def test_root_without_own_baseline_cannot_use_inherited_F_indicator_prices(self):
        result=step(state(refs=False),contexts=(witness("2024-01-02"),))
        self.assertEqual(len(result["state"]["history"]),1)
        self.assertEqual(result["state"]["history"][0]["underlying_close"],"59.8")
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["cash"],"500")

    def test_source_only_empty_quotes_cannot_hide_missing_funded_disposal(self):
        current=ko_opened();deadline=base._day(current["position"]["forced_exit_session"])
        for day in session_dates(date(2024,1,5),deadline-timedelta(days=1)):
            current=step(current,day)["state"]
        previous=deepcopy(current);result=step(current,deadline,quotes=[])
        self.assertEqual(result["status"],"held");self.assertIsNotNone(result["state"]["position"])
        self.assertEqual(result["state"]["cash"],previous["cash"])
        self.assertEqual(result["state"]["prefix_sha256"],previous["prefix_sha256"])
        self.assertFalse(result["state"]["cap_qualified"])

    def test_budget_commitment_is_not_replenished_after_a_profitable_sale(self):
        current=ko_opened()
        # Conservative synthetic accounting fixture: the whole allowance is
        # reserved already. No host artifact or actual fill is represented.
        current["monthly_committed"]="500";current["strategy_stats"][root.KERNEL_STRATEGY_ID]["monthly_committed"]="500"
        current["last_daily_result"].update(monthly_budget_used="500",monthly_budget_remaining="0")
        root.validate_state(current)
        for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):
            current=step(current,day,price="59",bid="4.5",ask="4.6")["state"]
        self.assertGreater(base._number(current["cash"]),500)
        self.assertEqual(current["monthly_committed"],"500")
        result=step(current,date(2024,1,10),price="58.8",strike="58.5",contexts=(witness("2024-01-10"),))
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["trade_count"],1)
        self.assertEqual(result["state"]["monthly_committed"],"500")

    def test_fixed_schema_and_boundaries_reject_custom_floats_and_recursive_state(self):
        original=state()
        for change in (lambda x:x.update(extra=True),lambda x:x.update(cash=500.0),lambda x:x.update(root_processed_count=True)):
            value=deepcopy(original);change(value)
            with self.assertRaises(root.OptionsRootEODError):root.validate_state(value)
        value=deepcopy(original);value["root_policy"]["cycle"]=value
        with self.assertRaises(root.OptionsRootEODError):root.validate_state(value)

    def test_original_policy_rule_mutation_cannot_affect_newroot_validation(self):
        before=deepcopy(stress.RULE)
        try:
            stress.RULE["risk"]="perroot500"
            with self.assertRaises(root.OptionsRootEODError):root.validate_state(state())
        finally:stress.RULE.clear();stress.RULE.update(before)


if __name__=="__main__":unittest.main()
