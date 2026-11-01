"""Generated forward-policy ledger checks, never market-return evidence."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Inexact, ROUND_UP, localcontext
import unittest
from unittest.mock import patch

from ariadne import options_eod as base
from ariadne import options_stress_eod as stress
from ariadne import options_stress_context as context
from ariadne.qqq_data import session_dates
from test_options_eod import state as old_state, quote, snapshot, received, H, J
from test_options_stress_context import witness, fixture

UTC = timezone.utc
ACTIVATED = "2024-01-03T15:01:00+00:00"


def state(*, refs=True):
    return stress.migrate_flat(old_state(refs=refs), activated_at=ACTIVATED,
        old_state_sha256=H, old_data_sha256=J)


def put(**kwargs):
    return quote(kind="put", strike="11.5", **kwargs)


def row(day=date(2024, 1, 2), *, price="11.8", bid="0.8", ask="0.9", expiry="2024-01-19"):
    return snapshot(day, price=price, quotes=[put(bid=bid, ask=ask, expiry=expiry)])


def step(current, day=date(2024, 1, 2), *, contexts=(), **kwargs):
    value = row(day, **kwargs)
    clock = max(base._clock(value["received_at"]), base._clock(current["last_decided_at"]))
    return stress.advance_paper(value, current, decided_at=clock.isoformat(), contexts=contexts)


def opened():
    current = state()
    ring = []
    for day, price in ((date(2024, 1, 2), "11.8"), (date(2024, 1, 3), "11.7"), (date(2024, 1, 4), "11.6")):
        ring.append(witness(day.isoformat()))
        current = step(current, day, price=price, contexts=tuple(ring))["state"]
    if current["position"] is None:
        raise AssertionError("Generated stress fixture must open exactly one put")
    return current


class StressEODMigrationTests(unittest.TestCase):
    def test_only_pristine_schema3_migrates_without_any_funding_or_cursor_reset(self):
        previous = old_state(); before = deepcopy(previous)
        current = stress.migrate_flat(previous, activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J)
        self.assertEqual(previous, before)
        for key in base._STATE - {"schema", "rule_sha256", "last_decided_at"}:
            self.assertEqual(current[key], previous[key], key)
        self.assertEqual(current["schema"], 4)
        self.assertEqual(current["rule_sha256"], stress.RULE_SHA256)
        activation = current["stress_policy"]["activation"]
        self.assertEqual(activation["old_canonical_state_sha256"], base._digest(before))
        self.assertEqual(activation["old_state_sha256"], H)
        self.assertEqual(activation["old_data_sha256"], J)
        self.assertEqual(activation["new_funding"], "0")
        self.assertIs(activation["risk_limits_changed"], False)
        self.assertEqual(current["cash"], "500")
        self.assertEqual(current["contributions"], "500")
        self.assertEqual(current["stress_policy"]["context_history"], [])
        projected = stress.legacy_state(current)
        self.assertEqual(projected, {**previous, "last_decided_at": ACTIVATED})
        self.assertEqual(base.validate_state(projected), projected)

    def test_migration_refuses_wrong_schema_hash_backwards_clock_or_risk(self):
        for changes in ({"old_state_sha256": "bad"}, {"old_data_sha256": ""}, {"activated_at": "2024-01-02T15:00:00Z"}):
            args = dict(activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J, **{})
            args.update(changes)
            with self.assertRaises(stress.OptionsStressEODError): stress.migrate_flat(old_state(), **args)
        for change in (lambda value: value.update(cash="499"), lambda value: value.update(monthly_committed="1"),
                       lambda value: value.update(fees=".65"), lambda value: value.update(cap_qualified=False)):
            previous = old_state(); change(previous)
            with self.assertRaises(stress.OptionsStressEODError):
                stress.migrate_flat(previous, activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J)
        with self.assertRaises(stress.OptionsStressEODError):
            stress.migrate_flat(state(), activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J)

    def test_pending_position_or_held_schema3_cannot_be_adopted(self):
        first = base.advance_paper(snapshot(), old_state(), decided_at=ACTIVATED)["state"]
        self.assertIsNotNone(first["pending"])
        with self.assertRaises(stress.OptionsStressEODError):
            stress.migrate_flat(first, activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J)
        from test_options_eod import opened as old_opened
        with self.assertRaises(stress.OptionsStressEODError):
            stress.migrate_flat(old_opened(), activated_at="2024-01-05T15:00:00Z", old_state_sha256=H, old_data_sha256=J)
        held = base.advance_paper(snapshot(), old_state(), decided_at="2024-01-05T15:00:00Z")["state"]
        with self.assertRaises(stress.OptionsStressEODError):
            stress.migrate_flat(held, activated_at="2024-01-05T15:00:00Z", old_state_sha256=H, old_data_sha256=J)

    def test_observed_zero_trade_prefix_is_preserved_not_replayed(self):
        previous = base.advance_paper(snapshot(price="12"), old_state(), decided_at=ACTIVATED)["state"]
        self.assertIsNone(previous["pending"])
        current = stress.migrate_flat(previous, activated_at=ACTIVATED, old_state_sha256=H, old_data_sha256=J)
        self.assertEqual(current["processed_count"], 1)
        self.assertEqual(current["prefix_sha256"], previous["prefix_sha256"])
        self.assertEqual(current["history"], previous["history"])
        self.assertEqual(current["stress_policy"]["activation"]["initial_processed_count"], 1)

    def test_activation_lineage_and_financial_witness_tampering_rejected(self):
        current = state()
        for mutate in (lambda p: p["activation"].update(new_funding="500"),
                       lambda p: p["activation"]["initial_financials"].update(cash="1000"),
                       lambda p: p.update(activation_sha256=H),
                       lambda p: p["activation"].update(old_state_sha256="invalid")):
            changed = deepcopy(current); mutate(changed["stress_policy"])
            with self.assertRaises(stress.OptionsStressEODError): stress.validate_state(changed)

    def test_later_month_activation_does_not_grant_missed_month_contributions(self):
        current = stress.migrate_flat(old_state(refs=False), activated_at="2024-03-01T15:00:00Z",
            old_state_sha256=H, old_data_sha256=J)
        self.assertEqual(current["cash"], "500"); self.assertEqual(current["month"], "2024-01")
        result = step(current, date(2024, 3, 1), expiry="2024-03-15")
        self.assertEqual(result["state"]["contributions"], "1000")
        self.assertEqual(result["state"]["funding_count"], 1)
        self.assertEqual(len([e for e in result["events"] if e["type"] == "contribution"]), 1)


class StressEODEntryTests(unittest.TestCase):
    def test_missing_stale_growth_and_unpublished_benchmarks_deny_entry(self):
        cases = ((), (witness("2023-12-29"),), (witness("2024-01-02", "growth"),),
                 (witness("2024-01-02", published=datetime(2024, 1, 3, 16, tzinfo=UTC)),))
        for ring in cases:
            result = step(state(), contexts=ring)
            self.assertIsNone(result["state"]["pending"])
            self.assertEqual(result["state"]["cash"], "500")
            self.assertEqual(result["state"]["trade_count"], 0)
            self.assertFalse(result["summary"]["last_entry_gate"]["allowed"])

    def test_stress_breakdown_records_only_a_forward_put_decision(self):
        current = state(); before = deepcopy(current)
        result = step(current, contexts=(witness("2024-01-02"),))
        self.assertEqual(current, before)
        pending = result["state"]["pending"]
        self.assertEqual(pending["strategy_id"], stress.KERNEL_STRATEGY_ID)
        self.assertEqual(pending["type"], "put")
        self.assertEqual(pending["decided_at"], ACTIVATED)
        self.assertEqual(result["state"]["trade_count"], 0)
        self.assertEqual(result["state"]["monthly_committed"], "0")
        self.assertEqual(result["summary"]["enabled_strategies"], [stress.STRATEGY_ID])
        self.assertFalse(result["summary"]["execution_qualified"])

    def test_a_call_signal_cannot_enter_even_with_stress_context(self):
        result = step(state(), price="12.1", contexts=(witness("2024-01-02"),))
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["trade_count"], 0)
        for key in base.STRATEGY_IDS[:2]:
            self.assertEqual(result["state"]["strategy_stats"][key]["entry_count"], 0)

    def test_delayed_fill_uses_prior_preopen_context_and_same_shared_cap(self):
        current = opened()
        self.assertEqual(current["trade_count"], 1)
        self.assertEqual(current["position"]["type"], "put")
        self.assertEqual(current["position"]["quantity"], 1)
        self.assertEqual(current["monthly_committed"], "91.3")
        self.assertEqual(current["cash"], "409.35")
        self.assertEqual(current["fees"], "0.65")
        self.assertEqual(current["contributions"], "500")
        self.assertEqual(current["stress_policy"]["context_count"], 2)
        gate = current["stress_policy"]["context_history"][-1]["gate"]
        self.assertEqual(gate["stage"], "fill")
        self.assertEqual(gate["source_date"], "2024-01-04")
        self.assertEqual(gate["required_source_session"], "2024-01-03")
        self.assertLessEqual(base._clock(gate["witness"]["published_at"]), base._clock(gate["cutoff_at"]))

    def test_late_new_context_does_not_backdate_a_pending_fill(self):
        current = step(state(), contexts=(witness("2024-01-02"),))["state"]
        current = step(current, date(2024, 1, 3), price="11.7")["state"]
        late = witness("2024-01-03", published=datetime(2024, 1, 5, 12, tzinfo=UTC))
        result = step(current, date(2024, 1, 4), price="11.6", contexts=(late, witness("2024-01-04")))
        self.assertEqual(result["state"]["trade_count"], 0)
        self.assertIsNone(result["state"]["position"])
        self.assertTrue(any(e["type"] == "cancelled" and e["reason"] == "entry_policy_denied" for e in result["events"]))

    def test_unknown_benchmark_cancels_buy_without_any_budget_debit(self):
        current = step(state(), contexts=(witness("2024-01-02"),))["state"]
        current = step(current, date(2024, 1, 3), price="11.7")["state"]
        result = step(current, date(2024, 1, 4), price="11.6")
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["cash"], "500")
        self.assertEqual(result["state"]["monthly_committed"], "0")

    def test_cancelled_pending_buy_cannot_select_again_on_the_same_source_frame(self):
        current = step(state(), contexts=(witness("2024-01-02"),))["state"]
        current = step(current, date(2024, 1, 3), price="11.7")["state"]
        before = current["stress_policy"]["context_count"]
        # This current witness permits a new decision, but it was not available
        # before the modeled source session's open to permit the old pending fill.
        result = step(current, date(2024, 1, 4), price="11.6", contexts=(witness("2024-01-04"),))
        self.assertEqual(result["state"]["trade_count"], 0)
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["stress_policy"]["context_count"], before+1)
        self.assertEqual(result["state"]["stress_policy"]["context_history"][-1]["stage"], "fill")
        self.assertFalse(any(e["type"] == "decision" and e["action"] == "BUY" for e in result["events"]))

    def test_under100_and_all_in100_gates_remain_in_force(self):
        for changes in ({"price": "100"}, {"bid": "0.95", "ask": "0.99"}):
            result = step(state(), contexts=(witness("2024-01-02"),), **changes)
            self.assertIsNone(result["state"]["pending"])
            self.assertEqual(result["state"]["trade_count"], 0)
        current = opened(); changed = deepcopy(current)
        changed["position"]["budget_debit"] = "100.01"
        with self.assertRaises(stress.OptionsStressEODError): stress.validate_state(changed)

    def test_malformed_new_context_is_denied_and_large_original_receipt_stays_compact(self):
        bad = witness("2024-01-02"); bad["classification"]["label"] = "forged"
        for ring in ((bad,), (object(),), None):
            result = step(state(), contexts=ring)
            self.assertIsNone(result["state"]["pending"])
            self.assertTrue(result["summary"]["context_input_rejected"])
            self.assertFalse(result["summary"]["last_entry_gate"]["allowed"])
        data, receipt, captured = fixture("2024-01-02")
        receipt["extra_evaluation_note"] = ["x"*2500]*4
        large = context.build_witness(data, source_receipt=receipt, recorded_at=captured+timedelta(minutes=1))
        result = step(state(), contexts=(large,))
        self.assertFalse(result["summary"]["context_input_rejected"])
        self.assertIsNotNone(result["state"]["pending"])
        self.assertEqual(large["source_receipt_original_sha256"], context._digest(receipt, context.MAX_RECEIPT_BYTES))
        self.assertLessEqual(len(base._canonical(result["state"]["stress_policy"]["context_history"][-1])),
            stress.MAX_GATE_RECORD_BYTES)


class StressEODExitAndIntegrityTests(unittest.TestCase):
    def test_ready_stress_exit_frame_cannot_reenter_and_next_frame_can_decide(self):
        current = opened()
        current = step(current, date(2024, 1, 5), bid="4.5", ask="4.6", price="11.5")["state"]
        current = step(current, date(2024, 1, 8), bid="4.5", ask="4.6", price="11.5")["state"]
        self.assertEqual(current["pending"]["action"], "SELL")
        before = current["stress_policy"]["context_count"]
        fresh = quote(kind="put", strike="11", bid="0.8", ask="0.9")
        source = snapshot(date(2024, 1, 9), price="11.4", quotes=[put(bid="4.5", ask="4.6"), fresh])
        actual = source["received_at"]
        legacy = base.advance_paper(source, stress.legacy_state(current), decided_at=actual)
        self.assertIsNotNone(legacy["state"]["pending"], "Generated frame includes an affordable fresh entry signal")
        result = stress.advance_paper(source, current, decided_at=actual, contexts=(witness("2024-01-09"),))
        self.assertIsNone(result["state"]["position"])
        self.assertIsNone(result["state"]["pending"])
        self.assertEqual(result["state"]["closed_trade_count"], 1)
        self.assertEqual(result["state"]["stress_policy"]["context_count"], before)
        self.assertTrue(any(e["type"] == "fill" and e["action"] == "SELL" for e in result["events"]))
        self.assertFalse(any(e["type"] == "decision" and e["action"] == "BUY" for e in result["events"]))
        later = snapshot(date(2024, 1, 10), price="11.3", quotes=[fresh])
        following = stress.advance_paper(later, result["state"], decided_at=later["received_at"],
            contexts=(witness("2024-01-10"),))
        self.assertEqual(following["state"]["pending"]["action"], "BUY")
        self.assertEqual(following["state"]["pending"]["strategy_id"], stress.KERNEL_STRATEGY_ID)

    def test_missing_or_malformed_context_never_blocks_mark_conditional_sale_or_fee_accounting(self):
        current = opened()
        current = step(current, date(2024, 1, 5), bid="0.3", ask="0.35", price="11.5", contexts=(object(),))["state"]
        self.assertEqual(current["position"]["marked_bid"], "0.3")
        self.assertEqual(current["pending"]["action"], "SELL")
        current = step(current, date(2024, 1, 8), bid="0.5", ask="0.55", price="11.5")["state"]
        result = step(current, date(2024, 1, 9), bid="0.5", ask="0.55", price="11.4", contexts=(object(),))
        self.assertIsNone(result["state"]["position"])
        self.assertEqual(result["state"]["closed_trade_count"], 1)
        self.assertEqual(result["state"]["fees"], "1.3")
        self.assertEqual(result["state"]["monthly_committed"], "91.3")
        self.assertEqual(result["state"]["contributions"], "500")
        self.assertTrue(any(e["type"] == "fill" and e["action"] == "SELL" for e in result["events"]))

    def test_mandatory_exact_disposal_works_without_any_benchmark(self):
        current = opened()
        for day in session_dates(date(2024, 1, 5), date(2024, 1, 18)):
            result = step(current, day, price="11.6", contexts=(object(),))
            current = result["state"]
        self.assertIsNone(current["hold"])
        self.assertIsNone(current["position"])
        self.assertEqual(current["closed_trade_count"], 1)
        self.assertTrue(any(e["type"] == "fill" and "precommitted_disposal_source_session" in e["reasons"] for e in result["events"]))

    def test_source_integrity_holds_stay_held_and_never_retry(self):
        current = step(state(), contexts=(witness("2024-01-02"),))["state"]
        result = step(current, bid="0.7", contexts=(witness("2024-01-02"),))
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["state"]["hold"]["code"], "accepted_source_changed")
        again = step(result["state"], date(2024, 1, 3), contexts=(object(),))
        self.assertEqual(again["state"], result["state"])
        self.assertEqual(again["events"], [])

    def test_same_source_is_idempotent_without_new_gate_or_decision(self):
        current = step(state(), contexts=(witness("2024-01-02"),))["state"]
        result = step(current, contexts=(object(),))
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["state"], current)
        self.assertEqual(result["events"], [])

    def test_gate_chain_and_persisted_gate_corruption_is_fatal(self):
        current = opened()
        for change in (lambda p: p.update(context_prefix_sha256=H),
                       lambda p: p["context_history"][0].update(previous_sha256=H),
                       lambda p: p["context_history"][-1]["gate"].update(allowed=False),
                       lambda p: p.update(context_count=0)):
            changed = deepcopy(current); change(changed["stress_policy"])
            with self.assertRaises((stress.OptionsStressEODError, context.StressContextError)):
                stress.validate_state(changed)

    def test_ungated_pending_or_position_cannot_be_adopted_by_projection(self):
        current = opened(); policy = deepcopy(current["stress_policy"])
        policy.update(context_count=0, context_history=[],
            context_prefix_sha256=stress._initial_prefix(policy["activation_sha256"]))
        current["stress_policy"] = policy
        with self.assertRaises(stress.OptionsStressEODError): stress.validate_state(current)

    def test_bound_ring_prunes_only_old_gate_records_and_remains_valid(self):
        current = state()
        for day in session_dates(date(2024, 1, 2), date(2024, 2, 2)):
            current = step(current, day, price="12", expiry="2024-02-16")["state"]
        policy = current["stress_policy"]
        self.assertGreater(policy["context_count"], 16)
        self.assertEqual(len(policy["context_history"]), 16)
        self.assertEqual(policy["context_history"][-1]["sequence"], policy["context_count"])
        self.assertLessEqual(len(base._canonical(current)), stress.MAX_STATE_BYTES)
        self.assertEqual(stress.validate_state(current), current)

    def test_decimal_caller_context_and_input_copies_cannot_change_policy_accounting(self):
        initial = state(); ring = (witness("2024-01-02"),); before = deepcopy((initial, ring))
        normal = step(initial, contexts=ring)
        with localcontext() as settings:
            settings.prec = 1; settings.rounding = ROUND_UP; settings.traps[Inexact] = True
            self.assertEqual(step(initial, contexts=ring), normal)
        self.assertEqual((initial, ring), before)
        normal["state"]["stress_policy"]["activation"]["initial_financials"]["cash"] = "0"
        self.assertEqual(initial["stress_policy"]["activation"]["initial_financials"]["cash"], "500")

    def test_mutated_rule_registry_and_oversized_state_fail_closed(self):
        current = state()
        with patch.dict(stress.RULE, {"entry": "always allow"}):
            with self.assertRaises(stress.OptionsStressEODError): stress.validate_state(current)
        current["stress_policy"]["extra"] = "x"*stress.MAX_STATE_BYTES
        with self.assertRaises(stress.OptionsStressEODError): stress.validate_state(current)


if __name__ == "__main__": unittest.main()
