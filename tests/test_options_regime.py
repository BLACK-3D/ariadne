"""Generated causal context checks; no option prices or profitability evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from fractions import Fraction
import hashlib
import random
import unittest
from unittest.mock import patch

from ariadne.options_regime import (ANALOG_SCALES, CLOCK_PROFILES, INTENT_SHA256,
    RULE, RULE_SHA256, RegimeError, RegimeObservation, RegimeTimeline,
    build_timeline, classify, regime_at)
from ariadne.qqq_data import canonical, session_dates, session_close_utc

UTC = timezone.utc


def dataset_fixture(count=180):
    days = session_dates(date(2023, 1, 3), date(2024, 12, 31))[:count]
    captured = session_close_utc(days[-1])+timedelta(hours=1)
    return {"as_of": captured.isoformat(), "symbols": {
        symbol: {"bars": [{"date": day.isoformat(), "close": 100+index/10} for index, day in enumerate(days)]}
        for symbol in ("QQQ", "SPY")}}


def timeline_fixture(count=180, profile="assumed_eod_plus_15m"):
    data = dataset_fixture(count)
    with patch("ariadne.options_regime.verify_saved_bundle", return_value=data):
        return build_timeline({"generated_fixture": True}, clock_profile=profile)


class RegimeMathTests(unittest.TestCase):
    def test_registered_profiles_no_financial_authority(self):
        self.assertEqual(INTENT_SHA256, "7cc1eda015a8b8aef5984e559375d3512f03619495754a52fdaba4403f594b13")
        self.assertEqual(hashlib.sha256(canonical(RULE)).hexdigest(), RULE_SHA256)
        self.assertEqual(CLOCK_PROFILES, ("retained_capture", "assumed_eod_plus_15m"))
        self.assertFalse(RULE["deployable"])
        self.assertEqual(RULE["warmup_sessions"], 60)
        self.assertEqual(len(ANALOG_SCALES), 5)

    def test_warmup_flat_growth_and_stress_precedence(self):
        self.assertEqual(classify([100]*59, [100]*59)["label"], "insufficient_history")
        self.assertEqual(classify([100]*60, [100]*60)["label"], "mixed_neutral")
        rising = list(range(100, 160))
        self.assertEqual(classify(rising, rising)["label"], "recovery_growth")
        # Both benchmarks remain above SMA60; a sudden shock still grants
        # stress rather than inferring safety from those moving averages.
        q = [80]*39+[120]*20+[117]
        s = [80]*39+[120]*21
        value = classify(q, s)
        self.assertGreater(Fraction(value["features"]["qqq_sma60_gap"]), 0)
        self.assertTrue(value["flags"]["stress"])
        self.assertEqual(value["label"], "stress_unwind")

    def test_daily_stress_exact_threshold_and_strict_negative20return(self):
        for close, expected in ((98.000001, False), (98, True), (97.999999, True)):
            q = [100]*59+[close]
            self.assertEqual(classify(q, [100]*60)["flags"]["stress"], expected)
        # A two-percent daily fall without a negative20-session return is not
        # the shock branch, and a rising SPY prevents the other stress branch.
        q = [80]*40+[100]*19+[98]
        self.assertFalse(classify(q, list(range(100, 160)))["flags"]["stress"])

    def test_drawdown_threshold_and_two_benchmarks(self):
        for close, expected in ((95.000001, False), (95, True), (94.999999, True)):
            q = [110]*40+[100]*18+[95.5, close]
            self.assertEqual(classify(q, [110]*59+[100])["flags"]["stress"], expected)
            self.assertFalse(classify(q, list(range(100, 160)))["flags"]["stress"])

    def test_rebound_branch_and_strict_prior_two_closes(self):
        q = [110]*40+[100]*14+[90, 91, 92, 93, 94, 95]
        s = [90]*54+[90, 91, 92, 93, 94, 95]
        value = classify(q, s)
        self.assertFalse(value["flags"]["growth"])
        self.assertTrue(value["flags"]["rebound"])
        self.assertEqual(value["label"], "recovery_growth")
        q[-1] = q[-2]
        self.assertFalse(classify(q, s)["flags"]["rebound"])

    def test_exact_features_match_independent_fraction_calculation(self):
        randomizer = random.Random(3931)
        for _ in range(40):
            q = [randomizer.randrange(8000, 12000)/100 for _ in range(60)]
            s = [randomizer.randrange(8000, 12000)/100 for _ in range(60)]
            actual = classify(q, s)["features"]
            qf, sf = [Fraction(str(value)) for value in q], [Fraction(str(value)) for value in s]
            returns = [qf[index]/qf[index-1]-1 for index in range(40, 60)]
            mean = sum(returns)/20
            expected = {
                "qqq_return20": qf[-1]/qf[-21]-1,
                "spy_return20": sf[-1]/sf[-21]-1,
                "relative_return20": (qf[-1]/qf[-21])/(sf[-1]/sf[-21])-1,
                "qqq_drawdown20": qf[-1]/max(qf[-20:])-1,
                "qqq_return_variance20": sum((value-mean)**2 for value in returns)/20}
            for name, value in expected.items():
                # Exact mathematical quantity lies within half the final unit.
                self.assertLessEqual(abs(Fraction(actual[name])-value), Fraction(1, 2*10**12))

    def test_decimal_host_context_does_not_change_output(self):
        q, s = list(range(100, 160)), list(range(110, 170))
        baseline = classify(q, s)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            self.assertEqual(classify(q, s), baseline)

    def test_invalid_price_types_sizes_and_nonfinite_are_rejected(self):
        for value in (True, "100", Decimal(100), 0, -1, float("nan"), float("inf"), 1e-9, 10_000_001, 10**400):
            with self.subTest(value=value), self.assertRaises(RegimeError): classify([100]*59+[value], [100]*60)
        for q, s in (([], []), ([100], []), ("100", [100]), ([100]*8001, [100]*8001)):
            with self.assertRaises(RegimeError): classify(q, s)


class RegimeTimelineTests(unittest.TestCase):
    def test_default_capture_does_not_backdate_historical_publication(self):
        timeline = timeline_fixture(profile="retained_capture")
        historical = timeline.observations[-2].observed_at+timedelta(minutes=15)
        self.assertEqual(timeline.at(historical)["status"], "unavailable")
        current = timeline.at(timeline.actual_capture_at)
        self.assertEqual(current["status"], "ready")
        self.assertFalse(current["retrospective_availability_assumed"])
        self.assertFalse(current["historical_publication_verified"])
        self.assertEqual(current["actual_capture_at"], timeline.actual_capture_at.isoformat())

    def test_assumed_profile_exposes_its_actual_capture_and_vintage_limits(self):
        timeline = timeline_fixture()
        current = timeline.observations[100]
        before = timeline.at(current.available_at-timedelta(microseconds=1))
        exact = timeline.at(current.available_at)
        self.assertEqual(before["source_session"], timeline.observations[99].session)
        self.assertEqual(exact["source_session"], current.session)
        self.assertTrue(exact["retrospective_availability_assumed"])
        self.assertFalse(exact["historical_revision_vintages_verified"])
        self.assertGreater(timeline.actual_capture_at, current.available_at)
        self.assertFalse(exact["deployable"])

    def test_prefixes_match_and_later_prices_cannot_change_prior_labels(self):
        short, long = timeline_fixture(100), timeline_fixture(180)
        self.assertEqual(short.observations, long.observations[:100])
        data = dataset_fixture(180)
        for symbol in ("QQQ", "SPY"):
            data["symbols"][symbol]["bars"][-1]["close"] *= 2
        with patch("ariadne.options_regime.verify_saved_bundle", return_value=data):
            changed = build_timeline({}, clock_profile="assumed_eod_plus_15m")
        self.assertEqual(long.observations[:100], changed.observations[:100])
        self.assertNotEqual(long.observations[-1].features, changed.observations[-1].features)

    def test_stale_source_is_not_a_gate_and_insufficient_is_explicit(self):
        timeline = timeline_fixture()
        self.assertEqual(timeline.at(timeline.observations[58].available_at)["status"], "insufficient")
        day = date.fromisoformat(timeline.observations[-1].session)
        following = session_dates(day+timedelta(days=1), day+timedelta(days=10))[0]
        stale = timeline.at(session_close_utc(following)+timedelta(minutes=15))
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["reason"], "latest_finalized_benchmark_session_missing")
        self.assertNotEqual(stale["source_session"], stale["expected_source_session"])

    def test_immutable_index_and_return_copies(self):
        timeline = timeline_fixture()
        with self.assertRaises(FrozenInstanceError): timeline.clock_profile = "live"
        with self.assertRaises(FrozenInstanceError): timeline.observations[-1].label = "stress_unwind"
        value = timeline.at(timeline.actual_capture_at)
        value["features"]["qqq_return1"] = "999"
        self.assertNotEqual(timeline.at(timeline.actual_capture_at)["features"]["qqq_return1"], "999")
        described = timeline.describe(include_observations=True)
        described["observations"][-1]["label"] = "invented"
        self.assertEqual(timeline.observations[-1].label, "recovery_growth")
        self.assertEqual(regime_at(timeline, timeline.actual_capture_at)["status"], "ready")
        with self.assertRaises(RegimeError): regime_at({}, timeline.actual_capture_at)

    def test_checkpoints_stop_before_source_and_between_every_row(self):
        data = dataset_fixture(65)
        class Stop(Exception): pass
        calls = []
        def check():
            calls.append(1)
            if len(calls) == 5: raise Stop()
        with patch("ariadne.options_regime.verify_saved_bundle", return_value=data) as verify:
            with self.assertRaises(Stop): build_timeline({}, checkpoint=check)
            self.assertEqual(verify.call_count, 1)
        with patch("ariadne.options_regime.verify_saved_bundle") as verify:
            with self.assertRaises(Stop): build_timeline({}, checkpoint=lambda: (_ for _ in ()).throw(Stop()))
            verify.assert_not_called()

    def test_invalid_profile_clock_and_source_refuse_no_silent_fallback(self):
        for profile in (None, "live", True, []):
            with self.assertRaises(RegimeError): build_timeline({}, clock_profile=profile)
        with self.assertRaises(RegimeError): build_timeline({}, checkpoint="code")
        with self.assertRaises(RegimeError): build_timeline({})
        timeline = timeline_fixture()
        for clock in ("2023-01-01", "x"*41, datetime(2023, 1, 1), True):
            with self.assertRaises(RegimeError): timeline.at(clock)
        with self.assertRaises(RegimeError): timeline.describe(include_observations=1)

    def test_mismatched_sessions_and_bad_prices_are_rejected(self):
        data = dataset_fixture(65)
        for mutation in ("missing", "date", "price"):
            changed = deepcopy(data)
            if mutation == "missing": changed["symbols"]["SPY"]["bars"].pop()
            if mutation == "date": changed["symbols"]["SPY"]["bars"][-1]["date"] = "2024-01-01"
            if mutation == "price": changed["symbols"]["QQQ"]["bars"][-1]["close"] = 0
            with patch("ariadne.options_regime.verify_saved_bundle", return_value=changed), self.assertRaises(RegimeError):
                build_timeline({})

    def test_actual_source_reconstruction_of_full_generated_charts(self):
        from ariadne.qqq_data import collect_bundle
        clock = datetime(2020, 12, 31, 22, tzinfo=UTC)
        days = session_dates(date(1999, 3, 10), date(2020, 12, 31))
        stamps = [int(datetime(day.year, day.month, day.day, 14, tzinfo=UTC).timestamp()) for day in days]
        closes = [100+index/1000 for index in range(len(days))]
        quotes = {"open": closes, "close": closes, "high": [value+1 for value in closes],
            "low": [value-1 for value in closes], "volume": [1000]*len(days)}
        sources = {}
        for symbol in ("QQQ", "SPY"):
            chart = {"chart": {"error": None, "result": [{"meta": {"symbol": symbol, "currency": "USD",
                "instrumentType": "ETF", "exchangeTimezoneName": "America/New_York", "dataGranularity": "1d"},
                "timestamp": stamps, "indicators": {"quote": [quotes], "adjclose": [{"adjclose": closes}]},
                "events": {"dividends": {str(stamp): {"date": stamp, "amount": 0.1} for stamp in stamps[:20]}}}]}}
            sources[symbol] = canonical(chart)
        bundle = collect_bundle(clock, fetch=lambda url: sources["QQQ" if "/QQQ?" in url else "SPY"])
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            timeline = build_timeline(bundle)
        self.assertEqual(timeline.describe()["sessions"], len(days))
        self.assertEqual(timeline.at(clock)["status"], "ready")
        self.assertEqual(timeline.source_sha256, hashlib.sha256(canonical(bundle["dataset"])).hexdigest())
        self.assertEqual(timeline.bundle_sha256, hashlib.sha256(canonical(bundle)).hexdigest())
        self.assertEqual(timeline.at(datetime(2019, 1, 1, 23, tzinfo=UTC))["status"], "unavailable")
        forged = deepcopy(bundle)
        forged["dataset"]["symbols"]["QQQ"]["bars"][-1]["close"] += 0.00001
        with self.assertRaisesRegex(RegimeError, "retained chart bytes"):
            build_timeline(forged)


class RegimeAnalogTests(unittest.TestCase):
    def test_future_sessions_remain_counted_outside_frozen_eight_eras(self):
        data = dataset_fixture(180)
        days = session_dates(date(2027, 1, 4), date(2027, 12, 31))[:180]
        for symbol in ("QQQ", "SPY"):
            for row, day in zip(data["symbols"][symbol]["bars"], days): row["date"] = day.isoformat()
        data["as_of"] = (session_close_utc(days[-1])+timedelta(hours=1)).isoformat()
        with patch("ariadne.options_regime.verify_saved_bundle", return_value=data):
            timeline = build_timeline({}, clock_profile="assumed_eod_plus_15m")
        result = timeline.analogs(timeline.actual_capture_at)
        self.assertEqual(result["status"], "descriptive_matches")
        self.assertGreater(result["candidate_count"], 0)
        self.assertEqual(result["outside_registered_periods"], result["candidate_count"])
        self.assertEqual(sum(result["period_counts"].values()), 0)

    def test_fixed_distance_prior_only_embargo_spacing_and_no_forecast(self):
        timeline = timeline_fixture(180)
        target = timeline.observations[-1].available_at
        value = timeline.analogs(target)
        self.assertEqual(value["status"], "descriptive_matches")
        self.assertFalse(value["profitability_evidence"])
        self.assertFalse(value["forecast"])
        self.assertEqual(value, timeline.analogs(target))
        indices = []
        dates = [observation.session for observation in timeline.observations]
        for match in value["matches"]:
            self.assertGreaterEqual(match["sessions_before_target"], 21)
            self.assertLess(match["source_session"], value["target"]["source_session"])
            self.assertEqual(match["label"], value["target"]["label"])
            indices.append(dates.index(match["source_session"]))
            distance = sum(abs(Fraction(match["features"][key])-Fraction(value["target"]["features"][key]))/Fraction(scale)
                           for key, scale in ANALOG_SCALES)
            self.assertLessEqual(abs(Fraction(match["distance"])-distance), Fraction(1, 2*10**12))
        self.assertTrue(all(abs(left-right) > 20 for index, left in enumerate(indices) for right in indices[index+1:]))
        self.assertEqual(sum(value["period_counts"].values()), value["candidate_count"])
        self.assertEqual(value["outside_registered_periods"], 0)
        self.assertEqual(sum(value["label_counts"].values()), value["candidate_count"])
        self.assertLessEqual(len(value["matches"]), 12)

    def test_ties_use_dates_and_empty_results_remain_explicit(self):
        timeline = timeline_fixture(180)
        zero_features = tuple((name, "0.000000000000") for name, _ in timeline.observations[-1].features)
        observations = tuple(RegimeObservation(row.session, row.observed_at, row.available_at,
            "recovery_growth" if index >= 59 else "insufficient_history", zero_features, ())
            for index, row in enumerate(timeline.observations))
        tied = RegimeTimeline(observations, timeline.availability, timeline.clock_profile,
            timeline.actual_capture_at, timeline.source_sha256, timeline.bundle_sha256, RULE_SHA256)
        result = tied.analogs(tied.actual_capture_at)
        self.assertEqual(result["matches"][0]["source_session"], observations[59].session)
        self.assertEqual(timeline.analogs(timeline.observations[58].available_at)["status"], "target_context_unavailable")
        self.assertEqual(timeline.analogs(timeline.observations[59].available_at)["status"], "no_eligible_analogs")
        for count in (0, 13, True, "12"):
            with self.assertRaises(RegimeError): timeline.analogs(timeline.actual_capture_at, limit=count)


if __name__ == "__main__":
    unittest.main()
