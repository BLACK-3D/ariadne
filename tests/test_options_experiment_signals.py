"""Generated causal predicate/threshold/parity tests, not market outcomes."""
from copy import deepcopy
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_experiment_signals as signals
from ariadne import options_campaign as legacy


def prices(values):
    return [Decimal(str(value)) for value in values]


def check(identifier, values):
    return signals.condition(identifier, prices(values))


class ExperimentSignalTests(unittest.TestCase):
    def test_explicit_twelve_candidates_kinds_and_only_two_regime_requirements(self):
        self.assertEqual(len(signals.CANDIDATES), 12)
        self.assertEqual(len(set(signals.CANDIDATES)), 12)
        self.assertEqual(signals.kind("cash"), None)
        self.assertEqual(signals.regime_label("stress_breakdown_put"), "stress_unwind")
        self.assertEqual(signals.regime_label("growth_recovery_call"), "recovery_growth")
        for identifier in signals.PATTERNS:
            self.assertEqual(signals.kind(identifier), signals.PATTERNS[identifier]["kind"])
            self.assertIsNone(signals.regime_label(identifier))
        self.assertIsNone(signals.regime_label("raw_breakdown_put"))

    def test_all_non_cash_require_fourteen_completed_closes(self):
        for identifier in signals.CANDIDATES:
            for size in (0, 1, 10, 13):
                with self.subTest(identifier=identifier, size=size):
                    self.assertFalse(signals.condition(identifier, [Decimal(100)]*size))
        self.assertFalse(signals.condition("cash", [Decimal(100)]*100))

    def test_three_day_breakdown_requires_strict_previous_three_and_ma10(self):
        self.assertTrue(check("three_day_breakdown_put", [100]*13+[99]))
        self.assertFalse(check("three_day_breakdown_put", [100]*13+[100]))
        # Current is below preceding3, but above the much lower10-close mean.
        self.assertFalse(check("three_day_breakdown_put", [10]*10+[100, 100, 100, 99]))

    def test_ma5_reclaim_uses_previous_and_current_means_without_future_closes(self):
        self.assertTrue(check("ma5_reclaim_call", [100]*12+[99, 100]))
        self.assertFalse(check("ma5_reclaim_call", [100]*12+[99, 99]))
        self.assertFalse(check("ma5_reclaim_call", [100]*12+[101, 102]))

    def test_five_day_momentum_includes_exact_three_percent_boundary(self):
        self.assertTrue(check("five_day_momentum_call", [100]*13+["103"]))
        self.assertFalse(check("five_day_momentum_call", [100]*13+["102.99999999"]))

    def test_uptrend_pullback_includes_one_and_three_percent_endpoints(self):
        prefix = [80]*4+[90]*6+[104, 103, 103]
        self.assertTrue(check("uptrend_pullback_call", prefix+["102.96"]))
        self.assertTrue(check("uptrend_pullback_call", prefix+["100.88"]))
        self.assertFalse(check("uptrend_pullback_call", prefix+["102.96000001"]))
        self.assertFalse(check("uptrend_pullback_call", prefix+["100.87999999"]))
        self.assertFalse(check("uptrend_pullback_call", [110]*10+[104, 103, 103, "102.96"]))

    def test_failed_bounce_uses_one_percent_prior_bounce_and_strict_current_reversal(self):
        prefix = [110]*10+[100, "100.5"]
        self.assertTrue(check("failed_bounce_put", prefix+[101, 100]))
        self.assertFalse(check("failed_bounce_put", prefix+["100.99999999", 100]))
        self.assertFalse(check("failed_bounce_put", prefix+[101, 101]))

    def test_tight_range_includes_two_percent_and_requires_strict_breakout(self):
        prefix = [100]*8+[100, 101, 102, 101, 100]
        self.assertTrue(check("tight_range_breakout_call", prefix+[103]))
        self.assertFalse(check("tight_range_breakout_call", prefix+[102]))
        self.assertFalse(check("tight_range_breakout_call", [100]*8+["99.99999999", 101, 102, 101, 100, 103]))

    def test_oversold_rebound_preserves_both_exact_thresholds(self):
        prefix = [100]*7+[100, 99, 98, 97, 96, 97]
        self.assertTrue(check("oversold_rebound_call", prefix+["97.485"]))
        self.assertFalse(check("oversold_rebound_call", prefix+["97.48499999"]))
        self.assertFalse(check("oversold_rebound_call", prefix[:-1]+["97.00000001", 100]))

    def test_volatile_downside_includes_six_percent_range_and_one_percent_drop(self):
        prefix = [100]*3+[100]*6+[106, 100, 100, 100]
        self.assertTrue(check("volatile_downside_put", prefix+[99]))
        self.assertFalse(check("volatile_downside_put", prefix+["99.00000001"]))
        self.assertFalse(check("volatile_downside_put", [100]*3+[100]*6+["105.99999999", 100, 100, 100, 99]))

    def test_legacy_breakdown_and_recovery_match_frozen_fifteen_close_input(self):
        histories = [prices([100]*13+[99]), prices([100]*10+[96, 96, 96, 97, 98]),
            prices([1000]*30+[100]*10+[96, 96, 96, 97, 98])]
        for length in (14, 15, 40, 125):
            value = 2000
            generated = []
            for index in range(length):
                value = (value*17+index*23) % 2400+100
                generated.append(Decimal(value)/Decimal(100))
            histories.append(generated)
        for values in histories:
            expected_rows = [{"underlying_price": format(value, "f")} for value in values[-15:]]
            expected_put = legacy.signal("breakdown_put", expected_rows)
            expected_call = legacy.signal("recovery_call", expected_rows)
            self.assertEqual(signals.condition("raw_breakdown_put", values), expected_put)
            self.assertEqual(signals.condition("stress_breakdown_put", values), expected_put)
            self.assertEqual(signals.condition("growth_recovery_call", values), expected_call)

    def test_legacy_recovery_has_strict_rebound_and_inclusive_three_percent_decline(self):
        self.assertTrue(check("growth_recovery_call", [100]*10+[97, 97, 97, 98, 99]))
        self.assertFalse(check("growth_recovery_call", [100]*10+[97, 97, 97, 98, 98]))
        self.assertFalse(check("growth_recovery_call", [100]*10+["97.00000001"]*3+[98, 99]))

    def test_conditions_are_prefix_causal_stateless_and_do_not_mutate_inputs(self):
        values = prices([100]*13+[99, 98, 97, 98, 99, 101, 104, 102, 103, 100, 96, 97, 98, 102])
        for identifier in signals.CANDIDATES:
            before = deepcopy(values)
            expected = [signals.condition(identifier, values[:index]) for index in range(len(values)+1)]
            signals.condition(identifier, values+[Decimal("1000000")])
            actual = [signals.condition(identifier, values[:index]) for index in range(len(values), -1, -1)][::-1]
            self.assertEqual(actual, expected)
            self.assertEqual(values, before)

    def test_new_patterns_consume_last_fourteen_and_legacy_last_fifteen_only(self):
        tail = prices([100]*10+[96, 97, 98, 99, 100])
        extra = prices([1000000]*25)
        for identifier in signals.CANDIDATES:
            self.assertEqual(signals.condition(identifier, extra+tail), signals.condition(identifier, tail))

    def test_isolated_decimal_context_preserves_threshold_results(self):
        values = prices([100]*13+[103])
        expected = {identifier: signals.condition(identifier, values) for identifier in signals.CANDIDATES}
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.traps[Inexact] = True
            self.assertEqual({identifier: signals.condition(identifier, values) for identifier in signals.CANDIDATES}, expected)

    def test_non_decimal_nonfinite_nonpositive_overbound_and_oversized_inputs_rejected(self):
        for bad in ([100]*14, [True]*14, [100.0]*14, ["100"]*14,
                    [Decimal("NaN")], [Decimal("sNaN")], [Decimal("Infinity")], [Decimal(0)],
                    [Decimal(-1)], [Decimal("1000001")], [Decimal("1e-41")],
                    tuple([Decimal(100)]*14), [Decimal(100)]*10001):
            with self.subTest(kind=type(bad)), self.assertRaises(ValueError): signals.condition("cash", bad)

    def test_unknown_identifiers_and_policy_mutation_rejected(self):
        for bad in ("winner_from_oracle", "", None, True, 1):
            for function in (signals.kind, signals.regime_label):
                with self.assertRaises(ValueError): function(bad)
            with self.assertRaises(ValueError): signals.condition(bad, [])
        with patch.dict(signals.RULE, {"warmup": "changed_after_outcomes"}):
            with self.assertRaises(ValueError): signals.kind("cash")


if __name__ == "__main__": unittest.main()
