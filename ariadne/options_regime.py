"""Fixed, causal QQQ/SPY price context; never option/account authority.

Historical publication clocks and price vintages are not authenticated by a
recent chart download. The default capture profile does not backdate them.
The explicit retrospective profile is a nondeployable research assumption.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import math

from .qqq_data import (MAX_ROWS, MarketDataError, canonical, last_closed_session,
                       session_close_utc, verify_saved_bundle)

UTC = timezone.utc
INTENT_SHA256 = "7cc1eda015a8b8aef5984e559375d3512f03619495754a52fdaba4403f594b13"
CLOCK_PROFILES = ("retained_capture", "assumed_eod_plus_15m")
LABELS = ("stress_unwind", "recovery_growth", "mixed_neutral", "insufficient_history")
FEATURE_NAMES = ("qqq_return1", "qqq_return5", "qqq_return20", "spy_return5", "spy_return20",
    "qqq_sma60_gap", "spy_sma60_gap", "qqq_drawdown20", "relative_return20", "qqq_return_variance20")
ANALOG_SCALES = (("qqq_return20", "0.10"), ("spy_return20", "0.10"),
    ("qqq_drawdown20", "0.10"), ("relative_return20", "0.05"), ("qqq_return_variance20", "0.0004"))
PERIODS = (("1999_2002", "1999-03-10", "2002-12-31"), ("2003_2007", "2003-01-01", "2007-12-31"),
    ("2008_2009", "2008-01-01", "2009-12-31"), ("2010_2019", "2010-01-01", "2019-12-31"),
    ("2020_2021", "2020-01-01", "2021-12-31"), ("2022", "2022-01-01", "2022-12-31"),
    ("2023_2025", "2023-01-01", "2025-12-31"), ("2026", "2026-01-01", "2026-12-31"))
RULE = {
    "id": "qqq-spy-two-sided-price-context-v1", "schema": 1, "intent_sha256": INTENT_SHA256,
    "benchmarks": ["QQQ", "SPY"], "warmup_sessions": 60, "clock_profiles": list(CLOCK_PROFILES),
    "default_clock_profile": "retained_capture", "research_only": True, "deployable": False,
    "stress_unwind": "QQQ return1<=-0.02 and return20<0; OR both below own SMA60 and QQQ drawdown20<=-0.05",
    "recovery_growth": "Notstress; either both above own SMA60 and QQQreturn20>0; OR QQQ exceeds prior2closes, both5-sessionreturns>0, QQQdrawdown20<=-0.03",
    "otherwise": "mixed_neutral; insufficient_history before60sessions",
    "features": list(FEATURE_NAMES), "arithmetic": "Exact Fraction classifications; deterministic half-even12-place descriptive values; population20daily-return variance",
    "price_bounds": "Positive finite source int/float between0.00000001 and10000000; bool rejected; Fraction(str(sourcevalue)) preserves its decimal representation",
    "availability": "At least sessionclose+15minutes; retained_capture additionally requires entire bundle capture/analysis. assumed_eod_plus_15m explicitly backdates unverified historical publication. No historical price vintages authenticated.",
    "freshness": "Latest available common observation must be the latest finalized session at the decision clock; unavailable/insufficient/stale context cannot grant a directional entry gate",
    "analog_scales": list(ANALOG_SCALES), "analog_selection": "Prior same-label warmed observations at least21sessions before target; fixed L1 distance, dateascending ties; max12, exclude20sessions around each selected match; no future returns/options outcomes",
    "periods": list(PERIODS), "financial_authority": "None: labels can gate new entries only; exits/disposal, source holds and one shared500/100all-in risk ledger remain independent",
    "source_limits": "Retained Yahoo public chart data is unofficial/revocable, structurally reconstructed but not independently authenticated; split-adjusted current-vintage closes are context, never option fills or raw-under100 eligibility",
}
RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class RegimeError(ValueError):
    """Unusable context never silently becomes a directional entry signal."""


def _clock(value):
    if type(value) is str:
        if not 1 <= len(value) <= 40:
            raise RegimeError("Use a bounded aware decision clock.")
        try: value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError: raise RegimeError("Use an aware decision clock.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RegimeError("Use an aware decision clock.")
    return value.astimezone(UTC)


def _price(value):
    if (type(value) not in (int, float) or not 0.00000001 <= value <= 10_000_000
            or not math.isfinite(value)):
        raise RegimeError("Benchmark closes require positive bounded finite numeric source values.")
    return Fraction(str(value))


def _render(value):
    # Fraction.__round__ supplies exact half-even tie handling without depending
    # on the caller's Decimal precision, traps or rounding mode.
    integer = round(value * 10**12)
    sign = "-" if integer < 0 else ""
    integer = abs(integer)
    return f"{sign}{integer//10**12}.{integer%10**12:012d}"


def _context(qqq, spy):
    if len(qqq) < 60:
        return "insufficient_history", (), ()
    q, s = qqq[-60:], spy[-60:]
    qr = lambda count: q[-1]/q[-1-count]-1
    sr = lambda count: s[-1]/s[-1-count]-1
    qm, sm = sum(q, Fraction())/60, sum(s, Fraction())/60
    drawdown = q[-1]/max(q[-20:])-1
    returns = [right/left-1 for left, right in zip(q[-21:-1], q[-20:])]
    mean = sum(returns, Fraction())/20
    variance = sum(((value-mean)**2 for value in returns), Fraction())/20
    stress = ((qr(1) <= Fraction(-2, 100) and qr(20) < 0)
        or (q[-1] < qm and s[-1] < sm and drawdown <= Fraction(-5, 100)))
    growth = q[-1] > qm and s[-1] > sm and qr(20) > 0
    rebound = (q[-1] > max(q[-3:-1]) and qr(5) > 0 and sr(5) > 0 and drawdown <= Fraction(-3, 100))
    label = "stress_unwind" if stress else "recovery_growth" if growth or rebound else "mixed_neutral"
    values = (qr(1), qr(5), qr(20), sr(5), sr(20), q[-1]/qm-1, s[-1]/sm-1,
              drawdown, (1+qr(20))/(1+sr(20))-1, variance)
    return label, tuple(zip(FEATURE_NAMES, map(_render, values))), (("stress", stress), ("growth", growth), ("rebound", rebound))


def classify(qqq_closes, spy_closes):
    """Classify generated/caller-owned prefixes; no source authenticity claim.

    The production timeline calls this same arithmetic after source rebuilding.
    This helper cannot create a source-qualified timeline or a paper account.
    """
    if (type(qqq_closes) not in (list, tuple) or type(spy_closes) not in (list, tuple)
            or len(qqq_closes) != len(spy_closes) or not 1 <= len(qqq_closes) <= MAX_ROWS):
        raise RegimeError("Use equal bounded nonempty QQQ/SPY close prefixes.")
    label, features, flags = _context(tuple(map(_price, qqq_closes)), tuple(map(_price, spy_closes)))
    return {"label": label, "features": dict(features), "flags": dict(flags), "rule_sha256": RULE_SHA256}


@dataclass(frozen=True)
class RegimeObservation:
    session: str
    observed_at: datetime
    available_at: datetime
    label: str
    features: tuple
    flags: tuple

    def as_dict(self):
        return {"source_session": self.session, "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(), "label": self.label,
            "features": dict(self.features), "flags": dict(self.flags)}


@dataclass(frozen=True)
class RegimeTimeline:
    """Trusted host-created immutable index, reused rather than rebuilt per row."""
    observations: tuple
    availability: tuple
    clock_profile: str
    actual_capture_at: datetime
    source_sha256: str
    bundle_sha256: str
    rule_sha256: str

    def _evidence(self):
        return {"clock_profile": self.clock_profile, "actual_capture_at": self.actual_capture_at.isoformat(),
            "source_sha256": self.source_sha256, "bundle_sha256": self.bundle_sha256,
            "rule_sha256": self.rule_sha256, "intent_sha256": INTENT_SHA256,
            "research_only": True, "deployable": False, "historical_publication_verified": False,
            "historical_revision_vintages_verified": False, "prices_independently_verified": False,
            "retrospective_availability_assumed": self.clock_profile == "assumed_eod_plus_15m"}

    def at(self, decision_at):
        """Only the latest causally available common benchmark observation."""
        now = _clock(decision_at)
        result = {"status": "unavailable", "label": None, "source_session": None,
            "available_at": None, "observed_at": None, "features": {}, "flags": {}, **self._evidence()}
        index = bisect_right(self.availability, now)-1
        if index < 0:
            result["reason"] = "benchmark_not_yet_available"
            return result
        observation = self.observations[index]
        result.update(observation.as_dict())
        try: expected = last_closed_session(now).isoformat()
        except MarketDataError as exc: raise RegimeError(str(exc)) from None
        result["expected_source_session"] = expected
        if observation.session != expected:
            result.update(status="stale", reason="latest_finalized_benchmark_session_missing")
        elif observation.label == "insufficient_history":
            result.update(status="insufficient", reason="requires60_common_sessions")
        else:
            result.update(status="ready", reason=None)
        return result

    def describe(self, *, include_observations=False):
        if type(include_observations) is not bool:
            raise RegimeError("Choose an explicit observation-output flag.")
        result = {"schema": 1, **self._evidence(), "sessions": len(self.observations),
            "first_source_session": self.observations[0].session,
            "last_source_session": self.observations[-1].session,
            "labels": {label: sum(value.label == label for value in self.observations) for label in LABELS},
            "limitations": ["Price-pattern hypotheses do not identify or time an AI bubble.",
                "Causal prefixes are not authenticated historical publication or revision vintages.",
                "Context never authorizes trades or repairs an unavailable option exit."]}
        if include_observations:
            result["observations"] = [value.as_dict() for value in self.observations]
        return result

    def analogs(self, target_at, *, limit=12):
        """Fixed-scale descriptive matches, with no subsequent-return input."""
        if type(limit) is not int or not 1 <= limit <= 12:
            raise RegimeError("Choose at most12 descriptive analogs.")
        target = self.at(target_at)
        result = {"schema": 1, "target": target, "selection_rule_sha256": self.rule_sha256,
            "matches": [], "candidate_count": 0, "eligible_same_label_count": 0,
            "period_counts": {value[0]: 0 for value in PERIODS}, "outside_registered_periods": 0,
            "label_counts": {label: 0 for label in LABELS},
            "forecast": False, "profitability_evidence": False}
        if target["status"] != "ready":
            result["status"] = "target_context_unavailable"
            return result
        target_index = bisect_right(self.availability, _clock(target_at))-1
        scored = []
        for index, observation in enumerate(self.observations[:max(0, target_index-20)]):
            if observation.label == "insufficient_history":
                continue
            result["candidate_count"] += 1
            result["label_counts"][observation.label] += 1
            for identifier, start, end in PERIODS:
                if start <= observation.session <= end:
                    result["period_counts"][identifier] += 1
                    break
            else:
                result["outside_registered_periods"] += 1
            if observation.label != target["label"]:
                continue
            result["eligible_same_label_count"] += 1
            features = dict(observation.features)
            distance = sum((abs(Fraction(features[key])-Fraction(target["features"][key]))/Fraction(scale)
                            for key, scale in ANALOG_SCALES), Fraction())
            scored.append((distance, observation.session, index, observation))
        selected = []
        for distance, _, index, observation in sorted(scored):
            if any(abs(index-existing) <= 20 for existing in selected):
                continue
            selected.append(index)
            result["matches"].append({**observation.as_dict(), "distance": _render(distance),
                "sessions_before_target": target_index-index})
            if len(selected) == limit:
                break
        result["status"] = "descriptive_matches" if selected else "no_eligible_analogs"
        return result


def build_timeline(bundle, *, clock_profile="retained_capture", checkpoint=None):
    """Rebuild exact retained source bytes offline, then calculate prefixes.

    No file, network, model or owner state is opened by this module. The frozen
    source/calendar helper reads its existing bundled timezone rules; the host
    must bind that dependency just as it does for other research engines.
    """
    if type(clock_profile) is not str or clock_profile not in CLOCK_PROFILES:
        raise RegimeError("Choose one explicit fixed benchmark clock profile.")
    if checkpoint is not None and not callable(checkpoint):
        raise RegimeError("Only a trusted host checkpoint callback is accepted.")
    if hashlib.sha256(canonical(RULE)).hexdigest() != RULE_SHA256:
        raise RegimeError("The fixed benchmark regime rule changed.")
    check = checkpoint or (lambda: None)
    check()
    try: dataset = verify_saved_bundle(bundle)
    except MarketDataError as exc: raise RegimeError(str(exc)) from None
    check()
    qqq, spy = dataset["symbols"]["QQQ"]["bars"], dataset["symbols"]["SPY"]["bars"]
    if len(qqq) != len(spy) or not 1 <= len(qqq) <= MAX_ROWS or any(q["date"] != s["date"] for q, s in zip(qqq, spy)):
        raise RegimeError("Benchmarks require the same bounded complete session calendar.")
    captured = _clock(dataset["as_of"])
    q_prices, s_prices, observations = [], [], []
    for q, s in zip(qqq, spy):
        check()
        q_prices.append(_price(q["close"]))
        s_prices.append(_price(s["close"]))
        observed = session_close_utc(date.fromisoformat(q["date"]))
        assumed = observed+timedelta(minutes=15)
        available = max(assumed, captured) if clock_profile == "retained_capture" else assumed
        label, features, flags = _context(q_prices, s_prices)
        observations.append(RegimeObservation(q["date"], observed, available, label, features, flags))
    result = RegimeTimeline(tuple(observations), tuple(value.available_at for value in observations), clock_profile,
        captured, hashlib.sha256(canonical(dataset)).hexdigest(), hashlib.sha256(canonical(bundle)).hexdigest(), RULE_SHA256)
    check()
    return result


def regime_at(timeline, decision_at):
    if type(timeline) is not RegimeTimeline:
        raise RegimeError("Use a trusted immutable benchmark timeline.")
    return timeline.at(decision_at)
