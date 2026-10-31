"""Date-only model: real chronology, one shared budget, no fabricated quotes."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
import hashlib
import unittest

from ariadne.options_eod import (OptionsEODError, RULE, RULE_SHA256, STRATEGY_IDS,
    migrate_flat, validate_state, validate_snapshot, summarize, advance_paper,
    contract_id, _canonical)
from ariadne.options_portfolio import initial_state as old_initial
from ariadne.qqq_data import session_dates, session_close_utc

H="a"*64
J="b"*64
ACTIVATED="2024-01-03T15:00:00+00:00"


def next_session(day):
    return session_dates(day+timedelta(days=1),day+timedelta(days=8))[0]


def received(day):
    return next_session(day).isoformat()+"T14:35:00+00:00"


def reference(day,price="12"):
    return {"source_date":day.isoformat(),"underlying_close":price,"received_at":received(day),"stock_sha256":H}


def baseline():
    days=session_dates(date(2023,12,1),date(2023,12,29))[-13:]
    return [reference(day) for day in days]


def state(*,refs=True,old=None):
    old=old_initial(None,started_at="2024-01-02T22:00:00+00:00",symbol="AAPL") if old is None else old
    return migrate_flat(old,activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J,baseline=baseline() if refs else None)


def quote(*,expiry="2024-01-19",kind="call",strike="12.5",bid="0.8",ask="0.9",bid_size=4,ask_size=5):
    return {"contract_id":contract_id(expiry,kind,strike),"expiry":expiry,"type":kind,"strike":strike,"bid":bid,"ask":ask,"bid_size":bid_size,"ask_size":ask_size}


def snapshot(day=date(2024,1,2),*,price="12.05",quotes=None,partial=True):
    return {"source_date":day.isoformat(),"received_at":received(day),"underlying_close":price,"quote_observed_at":None,"underlying_basis":"unadjusted_regular_last_bar","quotes":[quote()] if quotes is None else quotes,"partial_chain":partial,"source_sha256":H,"stock_sha256":J}


def step(current,day=date(2024,1,2),**kwargs):
    row=snapshot(day,**kwargs)
    return advance_paper(row,current,decided_at=row["received_at"] if row["received_at"]>current["last_decided_at"] else current["last_decided_at"])


def opened():
    current=state()
    for day,price in ((date(2024,1,2),"12.05"),(date(2024,1,3),"12.1"),(date(2024,1,4),"12.15")):
        result=step(current,day,price=price)
        if result["status"]!="completed":raise AssertionError(result)
        current=result["state"]
    if current["position"] is None:raise AssertionError(current)
    return current


class MigrationTests(unittest.TestCase):
    def test_rule_registered_digest_and_fixed_risk_contract(self):
        self.assertEqual(RULE_SHA256,"22ea96117b89f2b7c38e3853071363449aacac0074bf83a113f4fa36aec3b0f0")
        self.assertEqual(hashlib.sha256(_canonical(RULE)).hexdigest(),RULE_SHA256)
        self.assertEqual(RULE["per_entry_maximum_debit"],"100.00")
        self.assertEqual(RULE["monthly_maximum_debit"],"500.00")

    def test_migration_copies_all_financial_fields_without_funding(self):
        old=old_initial(None,started_at="2024-01-02T22:00:00+00:00",symbol="AAPL")
        oldcopy=deepcopy(old);current=state(old=old)
        for key in ("started_at","cash","contributions","month","monthly_start_equity","monthly_committed","fees","trade_count","closed_trade_count"):
            self.assertEqual(current[key],old[key])
        self.assertEqual(current["migration"]["new_funding"],"0")
        self.assertEqual(current["funding_count"],0)
        self.assertEqual(current["migration"]["old_state_sha256"],H)
        self.assertEqual(current["migration"]["old_data_sha256"],J)
        self.assertEqual(old,oldcopy)
        self.assertEqual(current["symbol"],"F")

    def test_existing_observations_do_not_duplicate_capital_or_become_fills(self):
        old=old_initial(None,started_at="2024-01-02T22:00:00+00:00",symbol="AAPL")
        from ariadne.options_recovery import _prefix
        old["processed_count"]=1;old["last_observed_at"]="2024-01-02T21:00:00+00:00"
        old["history"]=[{"observed_at":old["last_observed_at"],"underlying_price":"180"}]
        old["prefix_sha256"]=_prefix([])
        current=state(old=old)
        self.assertEqual(current["cash"],"500");self.assertEqual(current["processed_count"],0)
        self.assertIsNone(current["pending"]);self.assertIsNone(current["position"])
        self.assertEqual(current["history"],baseline())

    def test_reject_pending_held_or_unspent_capital_mismatch(self):
        original=old_initial(None,started_at="2024-01-02T22:00:00+00:00",symbol="AAPL")
        for key,value in (("cash","499"),("monthly_committed","1"),("fees","0.65"),("cap_qualified",False)):
            old=deepcopy(original);old[key]=value
            with self.subTest(key=key),self.assertRaises(OptionsEODError):state(old=old)

    def test_invalid_hash_source_or_backwards_activation_rejected(self):
        old=old_initial(None,started_at="2024-01-02T22:00:00+00:00")
        for changes in ({"old_state_sha256":"x"},{"old_data_sha256":""},{"source_id":"other"},{"activated_at":"2023-12-29T22:00:00Z"}):
            args={"activated_at":ACTIVATED,"old_state_sha256":H,"old_data_sha256":J,**changes}
            with self.subTest(changes=changes),self.assertRaises(OptionsEODError):migrate_flat(old,**args)

    def test_baseline_future_receipt_activation_date_gap_and_oversize_rejected(self):
        old=old_initial(None,started_at="2024-01-02T22:00:00Z")
        bad=baseline();bad[-1]["received_at"]="2024-01-04T15:00:00Z"
        examples=[bad,baseline()+[reference(date(2024,1,3))],baseline()[::2],baseline()+baseline()]
        for refs in examples:
            with self.subTest(refs=refs),self.assertRaises(OptionsEODError):migrate_flat(old,activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J,baseline=refs)

    def test_baseline_and_lineage_are_immutable_copies(self):
        refs=baseline();old=old_initial(None,started_at="2024-01-02T22:00:00Z")
        current=migrate_flat(old,activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J,baseline=refs)
        refs[0]["underlying_close"]="100"
        self.assertEqual(current["history"][0]["underlying_close"],"12")
        changed=deepcopy(current);changed["migration"]["baseline"][0]["underlying_close"]="99"
        with self.assertRaises(OptionsEODError):validate_state(changed)


class EvidenceTests(unittest.TestCase):
    def test_unknown_quote_clock_is_preserved_not_synthesized(self):
        result=step(state())
        self.assertIsNone(result["state"]["last_observed_at"])
        self.assertIsNone(result["summary"]["last_observed_at"])
        self.assertIsNone(result["summary"]["last_daily_result"]["quote_observed_at"])
        self.assertIs(result["summary"]["execution_qualified"],False)
        self.assertIs(result["summary"]["research_only"],True)

    def test_f_only_identity_does_not_modify_frozen_universe(self):
        from ariadne.options_data import SYMBOLS
        self.assertNotIn("F",SYMBOLS)
        self.assertEqual(quote()["contract_id"],"F240119C00012500")
        with self.assertRaises(OptionsEODError):contract_id("2024-01-19","call","12.00001")

    def test_snapshot_rejects_synthetic_clock_bad_size_price_duplicate_or_unreviewed_day(self):
        for changes in ({"quote_observed_at":"2024-01-02T21:00:00Z"},{"underlying_close":"NaN"},{"source_date":"2024-01-01"},{"underlying_basis":"adjusted"}):
            row=snapshot();row.update(changes)
            with self.subTest(changes=changes),self.assertRaises(OptionsEODError):validate_snapshot(row)
        for changes in ({"bid_size":None},{"ask_size":True},{"ask":"Infinity"},{"contract_id":"AAPL240119C00012500"}):
            row=snapshot();row["quotes"][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(OptionsEODError):validate_snapshot(row)
        row=snapshot();row["quotes"]*=2
        with self.assertRaises(OptionsEODError):validate_snapshot(row)

    def test_future_receipt_and_uncompleted_source_rejected(self):
        row=snapshot()
        with self.assertRaises(OptionsEODError):advance_paper(row,state(),decided_at="2024-01-03T14:00:00Z")
        row=snapshot(date(2024,1,3));row["received_at"]="2024-01-03T21:00:00Z"
        with self.assertRaises(OptionsEODError):advance_paper(row,state(),decided_at=row["received_at"])

    def test_first_source_must_follow_baseline_and_be_current(self):
        result=step(state(),date(2024,1,3))
        self.assertEqual(result["status"],"held");self.assertEqual(result["state"]["hold"]["code"],"missing_source_session")
        row=snapshot()
        result=advance_paper(row,state(),decided_at="2024-01-05T15:00:00Z")
        self.assertEqual(result["state"]["hold"]["code"],"stale_source")

    def test_accepted_snapshot_idempotent_but_changed_quote_holds(self):
        current=step(state())["state"];row=snapshot()
        result=advance_paper(row,current,decided_at=ACTIVATED)
        self.assertEqual(result["status"],"unchanged");self.assertEqual(result["state"],current)
        row["quotes"][0]["bid"]="0.79"
        changed=advance_paper(row,current,decided_at=ACTIVATED)
        self.assertEqual(changed["state"]["hold"]["code"],"accepted_source_changed")
        self.assertEqual(changed["state"]["processed_count"],1)

    def test_gap_stops_account_without_accepting_later_fills(self):
        current=step(state())["state"]
        result=step(current,date(2024,1,4))
        self.assertEqual(result["status"],"held");self.assertEqual(result["state"]["cash"],"500")
        self.assertEqual(result["state"]["processed_count"],1)
        self.assertFalse(result["summary"]["cap_qualified"])
        again=step(result["state"],date(2024,1,5))
        self.assertEqual(again["state"],result["state"])

    def test_no_seed_means_no_retroactive_signal_or_fill(self):
        result=step(state(refs=False))
        self.assertEqual(result["state"]["processed_count"],1)
        self.assertIsNone(result["state"]["pending"]);self.assertEqual(result["state"]["trade_count"],0)

    def test_accepted_hash_chain_rejects_cursor_tampering(self):
        current=opened()
        for mutate in (lambda s:s["cursor_history"][1].update(previous_sha256=H),lambda s:s.update(prefix_sha256=H),lambda s:s["history"][-1].update(source_date="2024-01-03")):
            changed=deepcopy(current);mutate(changed)
            with self.assertRaises(OptionsEODError):validate_state(changed)


class TradingTests(unittest.TestCase):
    def test_decision_durable_day_waits_through_same_day_source(self):
        original=state();first=step(original)
        self.assertIsNotNone(first["state"]["pending"]);self.assertEqual(original["processed_count"],0)
        self.assertEqual(first["state"]["pending"]["decided_at"],ACTIVATED)
        waiting=step(first["state"],date(2024,1,3),price="12.1")
        self.assertEqual(waiting["state"]["trade_count"],0)
        self.assertEqual(waiting["state"]["pending"],first["state"]["pending"])
        self.assertTrue(any(event["type"]=="waiting" for event in waiting["events"]))
        filled=step(waiting["state"],date(2024,1,4),price="12.15")
        position=filled["state"]["position"]
        self.assertEqual(position["entry_source_date"],"2024-01-04")
        self.assertEqual(position["entry_recorded_at"],received(date(2024,1,4)))
        self.assertEqual(position["decision_at"],ACTIVATED)
        self.assertEqual(filled["state"]["cash"],"409.35")
        self.assertEqual(filled["state"]["monthly_committed"],"91.3")
        buy=[event for event in filled["events"] if event["type"]=="fill"][0]
        self.assertIsNone(buy["quote_observed_at"]);self.assertFalse(buy["execution_qualified"])

    def test_underlying_at_or_above_100_blocks_new_entry(self):
        for price in ("100","100.01","500"):
            result=step(state(),price=price,quotes=[quote(strike=price)])
            self.assertIsNone(result["state"]["pending"])
        current=step(state())["state"];current=step(current,date(2024,1,3))["state"]
        result=step(current,date(2024,1,4),price="100")
        self.assertEqual(result["state"]["trade_count"],0)

    def test_one_entry_is_at_most_100_including_both_fees(self):
        for ask,allowed in (("0.98",True),("0.987",True),("0.98701",False),("0.99",False),("1",False)):
            result=step(state(),quotes=[quote(bid=".9",ask=ask)])
            self.assertEqual(result["state"]["pending"] is not None,allowed,ask)

    def test_actual_executable_side_size_and_spread_required(self):
        for changes in ({"ask_size":0},{"bid":"0"},{"bid":"0.91"},{"bid":"0.5"}):
            result=step(state(),quotes=[quote(**changes)])
            self.assertIsNone(result["state"]["pending"],changes)

    def test_partial_chain_selects_only_returned_nearest_dte_strike(self):
        choices=[quote(expiry="2024-01-12",strike="12.2"),quote(expiry="2024-01-19",strike="12.5"),quote(expiry="2024-01-19",strike="12.25")]
        result=step(state(),quotes=choices)
        self.assertEqual(result["state"]["pending"]["contract_id"],choices[2]["contract_id"])
        self.assertTrue(result["state"]["last_daily_result"]["partial_chain"])

    def test_expiry_outside_actual_decision_month_is_never_selected(self):
        result=step(state(),quotes=[quote(expiry="2024-02-02")])
        self.assertIsNone(result["state"]["pending"])

    def test_missing_pending_entry_contract_cancels_without_fill(self):
        current=step(state())["state"];current=step(current,date(2024,1,3))["state"]
        result=step(current,date(2024,1,4),quotes=[quote(kind="put",strike="12")])
        self.assertEqual(result["state"]["trade_count"],0);self.assertIsNone(result["state"]["pending"])

    def test_rotation_changes_only_after_fill_and_is_not_return_ranked(self):
        before=step(state())["state"]
        self.assertEqual(before["next_strategy"],0)
        current=opened()
        self.assertEqual(current["position"]["strategy_id"],STRATEGY_IDS[1])
        self.assertEqual(current["next_strategy"],2)

    def test_conditional_exit_uses_later_actual_decision_day_not_same_day_price(self):
        current=opened()
        target=step(current,date(2024,1,5),price="100",quotes=[quote(bid="4.5",ask="4.6")])
        self.assertEqual(target["state"]["pending"]["action"],"SELL")
        self.assertEqual(target["state"]["closed_trade_count"],0)
        waiting=step(target["state"],date(2024,1,8),price="100",quotes=[quote(bid="4.4",ask="4.5")])
        self.assertEqual(waiting["state"]["closed_trade_count"],0)
        sold=step(waiting["state"],date(2024,1,9),price="100",quotes=[quote(bid="4.3",ask="4.4")])
        self.assertIsNone(sold["state"]["position"])
        self.assertEqual(sold["state"]["closed_trade_count"],1)
        self.assertEqual(sold["state"]["monthly_committed"],"91.3")
        self.assertEqual(sold["state"]["cash"],"838.7")
        self.assertEqual(sold["state"]["fees"],"1.3")

    def test_stop_also_creates_durable_later_exit(self):
        result=step(opened(),date(2024,1,5),quotes=[quote(bid=".4",ask=".45")])
        self.assertIn("bid_at_or_below_half_entry_ask",result["state"]["pending"]["reasons"])
        self.assertEqual(result["state"]["closed_trade_count"],0)

    def test_missing_held_contract_stops_whole_account_not_fabricated_mark(self):
        current=opened();result=step(current,date(2024,1,5),quotes=[quote(kind="put",strike="12")])
        self.assertEqual(result["state"]["hold"]["code"],"missing_position_quote")
        self.assertEqual(result["state"]["position"],current["position"])
        self.assertEqual(result["state"]["cash"],current["cash"])

    def test_disposal_is_exact_precommitted_session(self):
        current=opened();deadline=current["position"]["forced_exit_session"]
        self.assertEqual(deadline,"2024-01-18")
        for day in session_dates(date(2024,1,5),date(2024,1,18)):
            result=step(current,day,quotes=[quote(bid=".8",ask=".9")]);current=result["state"]
        self.assertIsNone(current["position"]);self.assertEqual(current["closed_trade_count"],1)
        fills=[event for event in result["events"] if event["type"]=="fill"]
        self.assertEqual(fills[0]["source_date"],deadline)
        self.assertEqual(fills[0]["reasons"],["precommitted_disposal_source_session"])

    def test_due_source_waits_for_next_open_collection_without_premature_hold(self):
        current=opened()
        for day in session_dates(date(2024,1,5),date(2024,1,17)):
            current=step(current,day)["state"]
        before=deepcopy(current)
        waiting=summarize(current,now="2024-01-19T12:00:00Z")
        self.assertEqual(waiting["disposal"]["status"],"awaiting_next_session_publication")
        self.assertIsNone(waiting["hold"])
        overdue=summarize(current,now="2024-01-19T14:36:00Z")
        self.assertEqual(overdue["disposal"]["status"],"unverified_missing_required_source")
        self.assertEqual(overdue["cap_assurance"],"unverified_missing_disposal_source")
        self.assertIsNone(overdue["hold"])
        self.assertEqual(current,before)
        self.assertIsNone(step(current,date(2024,1,18))["state"]["position"])

    def test_missing_disposal_size_holds_and_never_settles_at_zero(self):
        current=opened()
        for day in session_dates(date(2024,1,5),date(2024,1,17)):
            current=step(current,day)["state"]
        result=step(current,date(2024,1,18),quotes=[quote(bid_size=0)])
        self.assertEqual(result["state"]["hold"]["code"],"unexecutable_disposal")
        self.assertEqual(result["state"]["closed_trade_count"],0)
        self.assertEqual(result["state"]["position"],current["position"])

    def test_missing_disposal_session_is_a_hold_not_late_sale(self):
        current=opened()
        for day in session_dates(date(2024,1,5),date(2024,1,17)):
            current=step(current,day)["state"]
        result=step(current,date(2024,1,19))
        self.assertEqual(result["status"],"held");self.assertEqual(result["state"]["closed_trade_count"],0)

    def test_validation_rejects_financial_and_position_tampering(self):
        current=opened()
        for mutate in (lambda s:s.update(cash="500"),lambda s:s.update(monthly_committed="0"),lambda s:s["position"].update(quantity=2),lambda s:s["position"].update(decision_at=s["position"]["entry_recorded_at"]),lambda s:s.update(execution_qualified=True),lambda s:s.update(research_only=False)):
            changed=deepcopy(current);mutate(changed)
            with self.subTest(changed=changed),self.assertRaises(OptionsEODError):validate_state(changed)

    def test_shared_budget_resists_profit_recycling(self):
        current=opened();identifier=current["position"]["strategy_id"]
        # A reconciled prior-cycle profit does not grant additional commitments.
        current["cash"]="1309.35";current["trade_count"]+=4;current["closed_trade_count"]+=4
        current["fees"]="5.85";current["monthly_committed"]="491.3"
        stats=current["strategy_stats"][identifier];stats.update(entry_count=5,closed_count=4,fees="5.85",realized_pnl="900",monthly_committed="491.3")
        current["last_daily_result"].update(cash="1309.35",equity="1388.7",monthly_budget_used="491.3",monthly_budget_remaining="8.7",fees="5.85",trade_count=5,closed_trade_count=4)
        validate_state(current)
        for day in session_dates(date(2024,1,5),date(2024,1,18)):
            current=step(current,day)["state"]
        self.assertEqual(current["monthly_committed"],"491.3")
        self.assertIsNone(current["pending"])
        self.assertGreater(Decimal(current["cash"]),500)

    def test_decimal_context_cannot_round_away_budget_limit(self):
        with localcontext() as context:
            context.prec=3
            result=step(state(),quotes=[quote(bid=".9",ask=".98701")])
        self.assertIsNone(result["state"]["pending"])

    def test_recovery_hypothesis_gets_fixed_first_precedence(self):
        refs=baseline();refs[-3]["underlying_close"]="11.5";refs[-2]["underlying_close"]="11.4";refs[-1]["underlying_close"]="11.6"
        old=old_initial(None,started_at="2024-01-02T22:00:00Z")
        current=migrate_flat(old,activated_at=ACTIVATED,old_state_sha256=H,old_data_sha256=J,baseline=refs)
        result=step(current)
        self.assertEqual(result["state"]["pending"]["strategy_id"],STRATEGY_IDS[0])

    def test_breakdown_hypothesis_buys_one_long_put(self):
        current=state();q=quote(kind="put",strike="11.5")
        for day in (date(2024,1,2),date(2024,1,3),date(2024,1,4)):
            current=step(current,day,price="11.5",quotes=[q])["state"]
        self.assertEqual(current["position"]["strategy_id"],STRATEGY_IDS[2])
        self.assertEqual(current["position"]["type"],"put")
        self.assertEqual(current["position"]["quantity"],1)
        self.assertEqual(current["next_strategy"],0)

    def test_fill_rechecks_dte_after_durable_wait(self):
        current=state();q=quote(expiry="2024-01-10")
        for day in (date(2024,1,2),date(2024,1,3),date(2024,1,4)):
            current=step(current,day,quotes=[q])["state"]
        self.assertIsNone(current["position"]);self.assertEqual(current["trade_count"],0)

    def test_reserved_exit_fee_covers_sale_smaller_than_fee(self):
        current=opened();q=quote(bid=".001",ask=".0011")
        for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):
            current=step(current,day,quotes=[q])["state"]
        self.assertIsNone(current["position"])
        self.assertEqual(current["cash"],"408.8")
        self.assertEqual(current["monthly_committed"],"91.3")
        self.assertGreaterEqual(Decimal(current["cash"]),500-Decimal(current["monthly_committed"]))

    def test_history_and_hash_chain_rings_remain_bounded_and_connected(self):
        current=state(refs=False)
        for day in session_dates(date(2024,1,2),date(2024,1,30)):
            current=step(current,day,price="12",quotes=[quote(expiry="2024-02-16",strike="100")])["state"]
        self.assertEqual(len(current["history"]),15)
        self.assertEqual(len(current["cursor_history"]),15)
        self.assertEqual(current["processed_count"],20)
        self.assertEqual(current["history"][-1]["source_date"],"2024-01-30")
        self.assertEqual(current["trade_count"],0)
        validate_state(current)


class MonthTests(unittest.TestCase):
    def test_flat_new_month_adds_one_contribution_not_missed_month_backfill(self):
        old=old_initial(None,started_at="2023-11-01T22:00:00Z",symbol="AAPL")
        current=state(old=old)
        self.assertEqual(current["month"],"2023-11");self.assertEqual(current["cash"],"500")
        result=step(current)
        self.assertEqual(result["state"]["month"],"2024-01")
        self.assertEqual(result["state"]["contributions"],"1000")
        self.assertEqual(result["state"]["funding_count"],1)
        self.assertEqual(result["summary"]["trading_pnl"],"0")
        again=advance_paper(snapshot(),result["state"],decided_at=ACTIVATED)
        self.assertEqual(again["state"]["contributions"],"1000")

    def test_actual_recorded_month_controls_funding_not_old_source_month(self):
        old=old_initial(None,started_at="2023-12-28T22:00:00Z",symbol="AAPL")
        current=migrate_flat(old,activated_at="2023-12-29T15:00:00Z",old_state_sha256=H,old_data_sha256=J)
        row=snapshot(date(2023,12,29),quotes=[quote(expiry="2024-01-12")])
        result=advance_paper(row,current,decided_at=row["received_at"])
        self.assertEqual(result["state"]["month"],"2024-01")
        self.assertEqual(result["state"]["cash"],"1000")

    def test_pending_prior_month_blocks_new_funding(self):
        current=step(state())["state"]
        # Keep a fully valid source cursor through month-end; pending is a real
        # durable instruction and must not silently acquire a fresh allowance.
        current["pending"]["decided_at"]="2024-01-31T15:00:00+00:00"
        current["last_decided_at"]="2024-01-31T15:00:00+00:00"
        # A source gap still stops before any funding, preserving all money.
        row=snapshot(date(2024,1,31),quotes=[quote(expiry="2024-02-16")])
        result=advance_paper(row,current,decided_at="2024-02-01T15:00:00+00:00")
        self.assertEqual(result["status"],"held")
        self.assertEqual(result["state"]["contributions"],"500")
        self.assertEqual(result["state"]["month"],"2024-01")


if __name__=="__main__":unittest.main()
