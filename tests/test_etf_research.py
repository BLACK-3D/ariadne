"""Causal NAV research, funded fees, evidence boundaries and input failures."""
import copy
from datetime import date, timedelta
import hashlib
import json
import math
import unittest
from unittest import mock

from ariadne.etf_research import (ETFResearchError, _metrics, _rebalance, _simulate,
                                  run_study, validate_dataset)


def dataset(first=date(2010, 1, 1), last=date(2013, 12, 31), *, dual=False, flat=False):
    days = [first+timedelta(days=index) for index in range((last-first).days+1)]
    def entry(scale):
        return {"kind": "nav_total_return_index", "dividend_policy": "reinvested",
            "sources": [{"url": "https://example.test/synthetic-nav", "sha256": "a"*64,
                         "retrieved_at": "2026-10-30T01:00:00+00:00"}],
            "observations": [{"date": stamp.isoformat(), "value": 100.0 if flat else 100+index*scale}
                             for index, stamp in enumerate(days)]}
    result = {"schema": 1, "as_of": "2026-10-30T02:00:00Z", "currency": "USD",
              "roles": {"us": "US"}, "series": {"US": entry(.1)}}
    if dual:
        result["roles"].update(ex_us="EX", bond="BOND")
        result["series"].update(EX=entry(.15), BOND=entry(.01))
    return result


def selected(report, name):
    return next(row for row in report["strategies"]+report["benchmarks"] if row["id"] == name)


class ETFResearchTests(unittest.TestCase):
    def test_us_only_runs_two_rules_and_blocks_missing_dual_without_inventing_data(self):
        report = run_study(dataset())
        self.assertEqual(report["status"], "completed")
        self.assertEqual([row["status"] for row in report["strategies"]], ["completed", "completed", "blocked"])
        self.assertIn("ex_us", selected(report, "dual12")["reason"])
        self.assertEqual(report["calendar"]["evaluation_start"], "2011-02-01")
        for name in ("sma10", "absolute12", "buy_hold_us"):
            row = selected(report, name)
            self.assertEqual(len(row["equity"]), len(report["calendar"]["evaluation_dates"]))
            self.assertEqual(row["trades"][0]["date"], "2011-02-01")
        self.assertEqual(selected(report, "buy_hold_us")["metrics"]["trade_count"], 1)

    def test_fully_funded_fee_algebra_and_two_rotation_legs(self):
        entered, fee, legs, actual, units = _rebalance(100, "CASH", "A", 20, .0005)
        self.assertAlmostEqual(entered, 100/1.0005)
        self.assertAlmostEqual(entered+fee, 100)
        self.assertAlmostEqual(units*20, entered)
        self.assertEqual(legs, 1)
        rotated, fee, legs, actual, units = _rebalance(100, "A", "B", 20, .0005)
        self.assertAlmostEqual(rotated, 100*.9995/1.0005)
        self.assertAlmostEqual(rotated+fee, 100)
        self.assertEqual(legs, 2)
        self.assertAlmostEqual(actual, 1+.9995/1.0005)
        exited, fee, legs, _, units = _rebalance(100, "A", "CASH", 1, .0005)
        self.assertAlmostEqual(exited, 99.95)
        self.assertAlmostEqual(fee, .05)
        self.assertEqual((legs, units), (1, 0))

    def test_old_holding_earns_the_execution_interval_before_sale(self):
        days = [date(2020, 1, 31), date(2020, 2, 1), date(2020, 2, 2), date(2020, 3, 1), date(2020, 3, 2)]
        plans = {1: {"sma10": "US", "signal_date": "2020-01-31", "signal_month": "2020-01"},
                 3: {"sma10": "CASH", "signal_date": "2020-02-02", "signal_month": "2020-02"}}
        result = _simulate("sma10", days, {"US": [100, 110, 121, 242, 250]}, plans, 1, .0005, "US")
        self.assertAlmostEqual(result["equity"][-1], 2.2*.9995/1.0005)
        self.assertEqual(result["trades"][-1]["date"], "2020-03-01")
        self.assertAlmostEqual(result["equity"][-1], result["equity"][-2])

    def test_public_signals_are_monthly_and_never_fill_on_the_signal_close(self):
        report = run_study(dataset(dual=True))
        self.assertEqual(selected(report, "dual12")["status"], "completed")
        self.assertEqual(selected(report, "dual12")["trades"][0]["to"], "EX")
        for strategy in report["strategies"]:
            for trade in strategy["trades"]:
                self.assertGreater(trade["date"], trade["signal_date"])
                self.assertNotEqual(trade["date"][:7], trade["signal_month"])

    def test_future_prices_and_incomplete_month_cannot_change_prior_equity(self):
        full = dataset()
        report = run_study(full)
        cutoff = date(2012, 9, 15)
        prefix = copy.deepcopy(full)
        prefix["as_of"] = "2012-09-15T23:59:59Z"
        for row in prefix["series"]["US"]["observations"]:
            if row["date"] > cutoff.isoformat():
                row["value"] = 1e100
        truncated = run_study(prefix)
        n = len(truncated["calendar"]["evaluation_dates"])
        self.assertEqual(truncated["calendar"]["evaluation_dates"], report["calendar"]["evaluation_dates"][:n])
        for name in ("sma10", "absolute12", "buy_hold_us"):
            self.assertEqual(selected(truncated, name)["equity"], selected(report, name)["equity"][:n])
            self.assertFalse(any(item["signal_month"] == "2012-09" for item in selected(truncated, name)["trades"]))
        self.assertGreater(truncated["validation"]["symbols"]["US"]["future_rows_ignored"], 0)

    def test_exact_intersection_reports_dropped_dates_without_filling(self):
        data = dataset(dual=True)
        removed = data["series"]["EX"]["observations"].pop(700)["date"]
        report = run_study(data)
        self.assertNotIn(removed, report["calendar"]["evaluation_dates"])
        self.assertEqual(report["calendar"]["coverage"]["US"]["dropped_rows"], 1)
        self.assertEqual(report["calendar"]["coverage"]["EX"]["missing_from_union_inside_common_range"], 1)
        self.assertEqual(len(selected(report, "sma10")["equity"]), len(selected(report, "buy_hold_us")["equity"]))

    def test_missing_whole_month_blocks_every_path(self):
        data = dataset()
        data["series"]["US"]["observations"] = [row for row in data["series"]["US"]["observations"] if not row["date"].startswith("2012-02")]
        report = run_study(data)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["calendar"]["missing_whole_months"], ["2012-02"])
        self.assertTrue(all(row["metrics"] is None for row in report["strategies"]+report["benchmarks"]))

    def test_missing_month_tail_is_exposed_not_claimed_a_verified_month_end(self):
        data = dataset()
        data["series"]["US"]["observations"] = [row for row in data["series"]["US"]["observations"] if not (row["date"].startswith("2012-06") and row["date"] > "2012-06-10")]
        report = run_study(data)
        endpoint = next(row for row in report["calendar"]["monthly_endpoints"] if row["month"] == "2012-06")
        self.assertEqual(endpoint["last_observed_date"], "2012-06-10")
        self.assertEqual(endpoint["calendar_days_before_month_end"], 20)
        self.assertEqual(report["calendar"]["month_end_coverage_status"], "unverified_exchange_calendar")
        self.assertTrue(any("month-end" in item for item in report["qualification"]["blockers"]))

    def test_mixed_adjustment_bases_do_not_produce_a_dual_momentum_ranking(self):
        data = dataset(dual=True)
        data["series"]["EX"].update(kind="nav_price_only", dividend_policy="unknown")
        report = run_study(data)
        self.assertEqual(selected(report, "dual12")["status"], "blocked")
        self.assertIn("mixed", selected(report, "dual12")["reason"])
        self.assertEqual(selected(report, "sma10")["status"], "completed")

    def test_cost_monotonicity_uses_same_dates_and_never_optimizes(self):
        report = run_study(dataset())
        baseline = selected(report, "buy_hold_us")
        cases = {5: baseline["metrics"]["total_return"], **{row["bps_per_leg"]: row["metrics"]["total_return"] for row in baseline["cost_sensitivity"]}}
        self.assertEqual(sorted(cases), [5, 10, 25, 50])
        self.assertEqual(list(cases[value] for value in sorted(cases)), sorted(cases.values(), reverse=True))
        self.assertFalse(report["qualification"]["proven_winner"])
        self.assertFalse(report["qualification"]["performance_promotion_allowed"])

    def test_operator_primary_cost_can_be_zero_fractional_or_upper_bound(self):
        for cost in (0, 2.5, 100):
            with self.subTest(cost=cost):
                report = run_study(dataset(), cost_bps=cost)
                self.assertEqual(report["cost_model"]["baseline_bps_per_leg"], cost)
                self.assertEqual(report["cost_model"]["predeclared_cases_bps"], [5, 10, 25, 50])
                row = selected(report, "buy_hold_us")
                self.assertEqual([item["bps_per_leg"] for item in row["cost_sensitivity"]], [5, 10, 25, 50])
                if cost == 0:
                    self.assertEqual(row["trades"][0]["fee"], 0)
                    self.assertEqual(report["cost_model"]["primary_case"], "zero_cost_diagnostic")

    def test_price_only_positive_results_do_not_qualify_total_return_performance(self):
        data = dataset()
        data["series"]["US"].update(kind="nav_price_only", dividend_policy="excluded")
        report = run_study(data)
        self.assertGreater(selected(report, "buy_hold_us")["metrics"]["total_return"], 0)
        self.assertEqual(report["qualification"]["execution_status"], "unqualified_nav_proxy")
        self.assertEqual(report["qualification"]["dividend_status"], "missing_or_excluded_distributions")
        self.assertFalse(report["qualification"]["performance_promotion_allowed"])

    def test_flat_cash_zero_volatility_is_not_infinite_sharpe(self):
        report = run_study(dataset(flat=True))
        metrics = selected(report, "sma10")["metrics"]
        self.assertEqual(metrics["cagr"], 0)
        self.assertEqual(metrics["annualized_observed_interval_volatility"], 0)
        self.assertIsNone(metrics["sharpe_vs_zero"])
        self.assertEqual(metrics["trade_count"], 0)
        json.dumps(report, allow_nan=False)

    def test_actual_time_cagr_and_peak_to_recovery_underwater_duration(self):
        days = [date(2020, 1, 1), date(2021, 1, 1)]
        result = _metrics(days, [1, 2], [0, 1], ["CASH", "US"], [])
        self.assertAlmostEqual(result["cagr"], 2**(365.2425/366)-1)
        self.assertEqual(result["exposure_fraction"], 1)
        points = [date(2020, 1, 1)+timedelta(days=i) for i in range(4)]
        result = _metrics(points, [1, .8, .9, 1.1], [0, -.2, .125, 1.1/.9-1], ["CASH", "US", "US", "US"], [])
        self.assertAlmostEqual(result["maximum_close_drawdown"], .2)
        self.assertEqual(result["maximum_underwater_calendar_days"], 3)

    def test_folds_carry_positions_and_use_actual_prior_close_baseline(self):
        data = dataset(last=date(2023, 1, 10))
        report = run_study(data)
        row = selected(report, "buy_hold_us")
        fold = row["folds"]["validation"]
        self.assertEqual(fold["start_date"], "2014-12-31")
        self.assertEqual(fold["first_observation_date"], "2015-01-01")
        self.assertEqual(fold["trade_count"], 0)
        dates = report["calendar"]["evaluation_dates"]
        first, last = dates.index("2014-12-31"), dates.index("2021-12-31")
        self.assertAlmostEqual(fold["total_return"], row["equity"][last]/row["equity"][first]-1)
        self.assertEqual(row["folds"]["holdout"]["first_observation_date"], "2022-01-01")

    def test_integrity_is_deterministic_and_input_is_not_mutated(self):
        data = dataset()
        original = copy.deepcopy(data)
        with mock.patch("builtins.open", side_effect=AssertionError("No file I/O")), mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
            report = run_study(data)
            again = run_study(data)
        self.assertEqual(data, original)
        self.assertEqual(report, again)
        integrity = report.pop("integrity")
        body = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(body).hexdigest(), integrity["report_body_sha256"])
        self.assertEqual(integrity["dataset_sha256"], validate_dataset(data)["dataset_sha256"])

    def test_invalid_json_shapes_numbers_dates_clocks_and_sources_reject(self):
        mutations = [
            lambda d: d.update(schema=True), lambda d: d.update(currency="EUR"),
            lambda d: d.update(as_of="2026-10-30"),
            lambda d: d["roles"].update(us="cash"),
            lambda d: d["series"]["US"].update(kind=[]),
            lambda d: d["series"]["US"]["observations"][0].update(value=True),
            lambda d: d["series"]["US"]["observations"][0].update(value=float("nan")),
            lambda d: d["series"]["US"]["observations"][0].update(value=-1),
            lambda d: d["series"]["US"]["observations"][0].update(date="20100101"),
            lambda d: d["series"]["US"]["observations"].insert(1, dict(d["series"]["US"]["observations"][0])),
            lambda d: d["series"]["US"]["sources"][0].update(url="https://user:secret@example.test/a"),
            lambda d: d["series"]["US"]["sources"][0].update(url="http://example.test/a"),
            lambda d: d["series"]["US"]["sources"][0].update(sha256="a"*63),
            lambda d: d["series"]["US"].update(kind="nav_price_only", dividend_policy="reinvested"),
            lambda d: d["series"]["US"].update(kind="nav_total_return_index", dividend_policy="excluded"),
            lambda d: d["series"]["US"].update(kind="nav_total_return_index", dividend_policy="unknown"),
        ]
        for mutate in mutations:
            data = dataset(last=date(2010, 1, 5))
            mutate(data)
            with self.subTest(mutation=mutate), self.assertRaises(ETFResearchError):
                validate_dataset(data)
        for cost in (True, -5, 100.1, float("inf"), "5"):
            with self.subTest(cost=cost), self.assertRaises(ETFResearchError):
                run_study(dataset(), cost_bps=cost)

    def test_row_symbol_and_canonical_byte_bounds_reject(self):
        data = dataset(last=date(2010, 1, 2))
        row = data["series"]["US"]["observations"][0]
        data["series"]["US"]["observations"] = [row]*15_001
        with self.assertRaisesRegex(ETFResearchError, "15,000"):
            validate_dataset(data)
        data = dataset(last=date(2010, 1, 2))
        item = data["series"]["US"]
        data["series"] = {"US": item, **{f"S{i}": item for i in range(8)}}
        with self.assertRaisesRegex(ETFResearchError, "eight"):
            validate_dataset(data)
        data = dataset(first=date(1960, 1, 1), last=date(2000, 12, 31))
        item = data["series"]["US"]
        data["series"] = {"US": item, **{f"S{i}": item for i in range(7)}}
        with self.assertRaisesRegex(ETFResearchError, "4 MB"):
            validate_dataset(data)


if __name__ == "__main__":
    unittest.main()
