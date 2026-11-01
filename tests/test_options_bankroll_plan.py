"""Money/authority boundaries for an isolated total-capital proposal."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_DOWN
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_bankroll_plan as capital
from scripts.options_bankroll_plan import main


class CapitalProposalTests(unittest.TestCase):
    def test_total_capital_is_not_monthly_funding(self):
        rule = capital.plan()["policy"]
        self.assertEqual(rule["initial_bankroll"], "25000.00")
        self.assertEqual(rule["additional_contributions"], "0.00")
        self.assertEqual(rule["funding_basis"], "one_time_total_bankroll")
        self.assertFalse(rule["deployable"])
        self.assertFalse(rule["automatic_adoption"])
        self.assertFalse(rule["real_orders"])

    def test_rule_result_is_detached_and_hash_is_stable(self):
        proposal = capital.plan()
        proposal["policy"]["comparators"].append("bad")
        self.assertNotIn("bad", capital.plan()["policy"]["comparators"])
        self.assertEqual(capital.POLICY_SHA256, capital.plan()["policy_sha256"])

    def test_modified_process_policy_is_rejected(self):
        original = deepcopy(capital.POLICY)
        try:
            capital.POLICY["entry_all_in_ceiling"] = "25000.00"
            with self.assertRaises(ValueError):
                capital.plan()
            with self.assertRaises(ValueError):
                capital.size(ask="1", ask_size=1)
        finally:
            capital.POLICY.clear()
            capital.POLICY.update(original)

    def test_ko_sized_without_forgetting_either_per_contract_fee(self):
        value = capital.size(ask="0.65", ask_size=230)
        self.assertEqual(value["quantity"], 15)
        self.assertEqual(value["premium"], "975.00")
        self.assertEqual(value["entry_fee"], "9.75")
        self.assertEqual(value["reserved_exit_fee"], "9.75")
        self.assertEqual(value["all_in_commitment"], "994.50")
        self.assertEqual(value["cash_after_entry_and_exit_reserve"], "24005.50")
        self.assertEqual(value["shared_monthly_commitment_after"], "994.50")
        self.assertFalse(value["executable_quote_or_order"])
        self.assertFalse(value["account_mutation"])

    def test_more_expensive_contract_can_fit_larger_proposal(self):
        value = capital.size(ask="2.85", ask_size=5)
        self.assertEqual(value["quantity"], 3)
        self.assertEqual(value["all_in_commitment"], "858.90")

    def test_actual_ask_size_limits_quantity(self):
        value = capital.size(ask="0.65", ask_size=2)
        self.assertEqual(value["quantity"], 2)
        self.assertEqual(value["all_in_commitment"], "132.60")

    def test_zero_supplied_size_does_not_fill(self):
        value = capital.size(ask="0.65", ask_size=0)
        self.assertEqual(value["quantity"], 0)
        self.assertEqual(value["status"], "no_affordable_supplied_quantity")
        self.assertEqual(value["all_in_commitment"], "0.00")

    def test_unknown_bool_negative_fractional_or_unbounded_size_rejected(self):
        for size in (None, True, False, -1, 0.5, "2", 1_000_000_001):
            with self.subTest(size=size), self.assertRaises(ValueError):
                capital.size(ask="0.65", ask_size=size)

    def test_losing_equity_reduces_entry_ceiling(self):
        value = capital.size(ask="2.85", ask_size=100, equity="12500.00", cash="12500.00")
        self.assertEqual(value["entry_risk_ceiling"], "500.00")
        self.assertEqual(value["quantity"], 1)

    def test_winning_equity_never_increases_fixed_entry_ceiling(self):
        value = capital.size(ask="2.85", ask_size=100, equity="50000.00", cash="50000.00")
        self.assertEqual(value["entry_risk_ceiling"], "1000.00")
        self.assertEqual(value["quantity"], 3)

    def test_cash_and_gross_remaining_are_independent_constraints(self):
        value = capital.size(ask="0.65", ask_size=230, cash="100.00", monthly_committed="24900.00")
        self.assertEqual(value["quantity"], 1)
        self.assertEqual(value["entry_risk_ceiling"], "100.00")
        self.assertEqual(value["shared_monthly_commitment_after"], "24966.30")

    def test_near_full_month_does_not_use_a_fractional_contract(self):
        value = capital.size(ask="0.65", ask_size=230, monthly_committed="24933.71")
        self.assertEqual(value["quantity"], 0)
        self.assertEqual(value["entry_risk_ceiling"], "66.29")

    def test_fully_committed_month_cannot_recycle_positive_equity(self):
        value = capital.size(ask="0.65", ask_size=230, cash="50000.00", equity="50000.00", monthly_committed="25000.00")
        self.assertEqual(value["quantity"], 0)

    def test_exact_contract_threshold_includes_exit_fee(self):
        value = capital.size(ask="9.987", ask_size=100)
        self.assertEqual(value["all_in_commitment"], "1000.00")
        self.assertEqual(value["quantity"], 1)
        value = capital.size(ask="9.98700001", ask_size=100)
        self.assertEqual(value["quantity"], 0)

    def test_debit_rounds_up_not_to_nearest(self):
        value = capital.size(ask="0.65000001", ask_size=1)
        self.assertEqual(value["premium"], "65.01")
        self.assertEqual(value["all_in_commitment"], "66.31")

    def test_zero_equity_never_creates_capital(self):
        value = capital.size(ask="0.65", ask_size=230, cash="0", equity="0")
        self.assertEqual(value["quantity"], 0)

    def test_invalid_prices_rejected(self):
        for ask in ("0", "-1", "NaN", "Infinity", "1e2", "+1", " 1", "1 ", "01", ".5", "0.123456789", 1, True):
            with self.subTest(ask=ask), self.assertRaises(ValueError):
                capital.size(ask=ask, ask_size=1)

    def test_invalid_cash_equity_and_commitment_rejected(self):
        for key in ("cash", "equity", "monthly_committed"):
            for bad in ("-1", "NaN", "Infinity", "1e2", "1.001", " 1", "1000000000", 100, True):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    capital.size(ask="1", ask_size=1, **{key: bad})

    def test_inconsistent_cash_or_overcommitted_account_rejected(self):
        with self.assertRaises(ValueError):
            capital.size(ask="1", ask_size=1, cash="25001")
        with self.assertRaises(ValueError):
            capital.size(ask="1", ask_size=1, monthly_committed="25000.01")

    def test_caller_decimal_precision_and_rounding_do_not_change_result(self):
        expected = capital.size(ask="0.65000001", ask_size=230)
        with localcontext() as ctx:
            ctx.prec = 2
            ctx.rounding = ROUND_DOWN
            self.assertEqual(expected, capital.size(ask="0.65000001", ask_size=230))

    def test_streak_illustrations_are_not_probability_or_backtest(self):
        rows = capital.losing_streaks()
        self.assertEqual([row["consecutive_full_allocation_losses"] for row in rows], [5, 10, 20])
        self.assertEqual(rows[0]["idealized_remaining_bankroll"], "20384.32")
        self.assertTrue(all(not row["probability_estimated"] for row in rows))
        self.assertTrue(all(Decimal(row["idealized_remaining_bankroll"]) + Decimal(row["idealized_loss"]) == 25000 for row in rows))

    def test_cli_plan_is_explicitly_inactive(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["plan"]), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["policy"]["status"], "research_proposal_not_activated")

    def test_cli_size_and_failure_have_honest_exit_status(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(main(["size", "--ask", "0.65", "--ask-size", "2"]), 0)
            self.assertEqual(main(["size", "--ask", "0.65"]), 2)
            self.assertEqual(main(["plan", "--cash", "1"]), 2)
        self.assertEqual(json.loads(output.getvalue())["quantity"], 2)
        self.assertIn("Proposal rejected", error.getvalue())


if __name__ == "__main__":
    unittest.main()
