"""Bounded, deterministic ETF NAV research; no network, orders or model calls.

Monthly rules use last observed closes from earlier calendar months; actual
exchange month-end coverage requires source review. A changed target fills
at the first following available NAV close, after the old holding earns that
interval. All amounts are normalized research units, not an executable account.
"""
from __future__ import annotations

import calendar as _calendar_module
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 4_000_000
MAX_REPORT_BYTES = 4_000_000
MAX_SYMBOLS = 8
MAX_ROWS = 15_000
COST_CASES = (5.0, 10.0, 25.0, 50.0)
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,23}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_KINDS = {"nav_total_return_index", "nav_price_only"}
_POLICIES = {"reinvested", "excluded", "unknown"}
_FOLDS = (("train", None, "2014-12-31"), ("validation", "2015-01-01", "2021-12-31"), ("holdout", "2022-01-01", None))


class ETFResearchError(ValueError):
    """Input, accounting or bounded-output failure; never a trading signal."""


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ETFResearchError("Provide a finite JSON dataset with ordinary string keys.") from None


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _object(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ETFResearchError("Dataset objects require exactly the documented fields.")


def _clock(value):
    if type(value) is not str or not 10 <= len(value) <= 64:
        raise ETFResearchError("Clocks require an aware ISO datetime.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ETFResearchError("Clocks require an aware ISO datetime.") from None


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise ETFResearchError("Observations require ISO session dates YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise ETFResearchError("Observations require ISO session dates YYYY-MM-DD.") from None


def _positive(value):
    if type(value) not in (int, float):
        raise ETFResearchError("NAV levels must be positive finite numbers, not strings or booleans.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ETFResearchError("NAV levels must be positive finite numbers.") from None
    if not math.isfinite(result) or result <= 0:
        raise ETFResearchError("NAV levels must be positive finite numbers.")
    return result


def _finite(value):
    if not math.isfinite(value):
        raise ETFResearchError("The supplied levels exceed safe finite accounting bounds.")
    return value


@dataclass(frozen=True)
class _Series:
    name: str
    rows: tuple
    kind: str
    policy: str
    sources: tuple
    input_rows: int
    ignored_future: int
    observations_sha256: str


def _parse(dataset):
    _object(dataset, ("schema", "as_of", "currency", "roles", "series"))
    if type(dataset["schema"]) is not int or dataset["schema"] != 1 or dataset["currency"] != "USD":
        raise ETFResearchError("Only schema 1 and USD research are supported.")
    as_of = _clock(dataset["as_of"])
    roles = dataset["roles"]
    _object(roles, ("us",), ("ex_us", "bond"))
    if any(type(item) is not str or not _SYMBOL.fullmatch(item) or item.upper() == "CASH" for item in roles.values()) or len(set(roles.values())) != len(roles):
        raise ETFResearchError("Roles need distinct ordinary symbols; CASH is reserved.")
    supplied = dataset["series"]
    if type(supplied) is not dict or not 1 <= len(supplied) <= MAX_SYMBOLS:
        raise ETFResearchError("Provide one to eight symbol series.")
    parsed = {}
    for symbol, entry in supplied.items():
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or symbol.upper() == "CASH":
            raise ETFResearchError("Series names require ordinary symbols; CASH is reserved.")
        _object(entry, ("kind", "dividend_policy", "sources", "observations"))
        if type(entry["kind"]) is not str or type(entry["dividend_policy"]) is not str or entry["kind"] not in _KINDS or entry["dividend_policy"] not in _POLICIES:
            raise ETFResearchError("Declare NAV kind and dividend policy explicitly.")
        if entry["kind"] == "nav_price_only" and entry["dividend_policy"] == "reinvested":
            raise ETFResearchError("Price-only NAV cannot claim reinvested distributions.")
        if entry["kind"] == "nav_total_return_index" and entry["dividend_policy"] != "reinvested":
            raise ETFResearchError("A total-return index must explicitly declare reinvested distributions.")
        sources = entry["sources"]
        if type(sources) is not list or not 1 <= len(sources) <= 16:
            raise ETFResearchError("Each series needs one to sixteen dated source records.")
        clean_sources = []
        for item in sources:
            _object(item, ("url", "sha256", "retrieved_at"))
            if type(item["url"]) is not str or not 8 <= len(item["url"]) <= 4096:
                raise ETFResearchError("Sources need bounded HTTPS URLs without credentials.")
            try:
                url = urlsplit(item["url"])
                safe = url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None and not url.fragment and not any(ord(char) < 33 for char in item["url"])
            except ValueError:
                safe = False
            if not safe or type(item["sha256"]) is not str or not _SHA.fullmatch(item["sha256"]):
                raise ETFResearchError("Sources need HTTPS URLs without credentials and lowercase SHA-256 identities.")
            retrieved = _clock(item["retrieved_at"])
            clean_sources.append({"url": item["url"], "sha256": item["sha256"], "retrieved_at": retrieved.isoformat(),
                                  "retrieved_after_analysis_cutoff": retrieved > as_of})
        observations = entry["observations"]
        if type(observations) is not list or not 1 <= len(observations) <= MAX_ROWS:
            raise ETFResearchError("Each symbol needs one to 15,000 observations.")
        rows, previous, ignored = [], None, 0
        for row in observations:
            _object(row, ("date", "value"))
            stamp, level = _day(row["date"]), _positive(row["value"])
            if previous is not None and stamp <= previous:
                raise ETFResearchError("Session dates must be unique and strictly ascending.")
            previous = stamp
            if stamp > as_of.date():
                ignored += 1
            else:
                rows.append((stamp, level))
        parsed[symbol] = _Series(symbol, tuple(rows), entry["kind"], entry["dividend_policy"], tuple(clean_sources),
                                 len(observations), ignored, _hash(observations))
    if roles["us"] not in parsed or not parsed[roles["us"]].rows:
        raise ETFResearchError("The US role needs observations on or before the analysis cutoff.")
    raw = _canonical(dataset)
    if len(raw) > MAX_INPUT_BYTES:
        raise ETFResearchError("Canonical dataset exceeds the 4 MB limit.")
    return as_of, dict(roles), parsed, hashlib.sha256(raw).hexdigest(), len(raw)


def _summary(parts):
    as_of, roles, series, digest, size = parts
    symbols = {}
    for symbol, item in series.items():
        last = item.rows[-1][0] if item.rows else None
        symbols[symbol] = {"kind": item.kind, "dividend_policy": item.policy,
            "input_rows": item.input_rows, "usable_rows": len(item.rows), "future_rows_ignored": item.ignored_future,
            "first_date": item.rows[0][0].isoformat() if item.rows else None, "last_date": last.isoformat() if last else None,
            "latest_observation_age_calendar_days": (as_of.date()-last).days if last else None,
            "observations_sha256": item.observations_sha256, "sources": list(item.sources)}
    return {"valid": True, "schema": 1, "as_of": as_of.isoformat(), "currency": "USD", "roles": roles,
            "canonical_bytes": size, "dataset_sha256": digest, "symbols": symbols,
            "date_policy": "ISO NAV session dates; rows after the UTC analysis date are ignored. Historical publication availability is not independently verified.",
            "source_policy": "Source-file digests and retrieval clocks are caller-supplied provenance claims; this pure engine does not fetch or read those files."}


def validate_dataset(dataset: dict) -> dict:
    """Return bounded metadata/identity summary, or raise ETFResearchError."""
    return _summary(_parse(dataset))


def _month(stamp):
    return stamp.year*12+stamp.month-1


def _month_label(value):
    year, month = divmod(value, 12)
    return f"{year:04d}-{month+1:02d}"


def _calendar(series, roles):
    missing = [role for role in ("ex_us", "bond") if not roles.get(role) or roles[role] not in series or not series[roles[role]].rows]
    names = [roles["us"]] if missing else [roles[key] for key in ("us", "ex_us", "bond")]
    sets = {name: {day for day, _ in series[name].rows} for name in names}
    shared = sorted(set.intersection(*sets.values()))
    union = set.union(*sets.values())
    coverage = {}
    for name in names:
        inside = {day for day in sets[name] if shared and shared[0] <= day <= shared[-1]}
        coverage[name] = {"available_rows": len(sets[name]), "retained_common_rows": len(shared),
                          "dropped_rows": len(sets[name])-len(shared), "dropped_inside_common_range": len(inside-set(shared)),
                          "missing_from_union_inside_common_range": len({day for day in union if shared and shared[0] <= day <= shared[-1]}-sets[name])}
    months = {_month(day) for day in shared}
    missing_months = [_month_label(value) for value in range(min(months), max(months)+1) if value not in months] if months else []
    gaps = [(right-left).days for left, right in zip(shared, shared[1:])]
    endpoints = {}
    for stamp in shared:
        label = _month_label(_month(stamp))
        previous = endpoints.get(label, {}).get("observations", 0)
        endpoints[label] = {"month": label, "last_observed_date": stamp.isoformat(),
            "calendar_days_before_month_end": _calendar_module.monthrange(stamp.year, stamp.month)[1]-stamp.day,
            "observations": previous+1}
    return shared, names, missing, {"policy": "Exact intersection of the three complete roles, otherwise US-only; no gap filling.",
        "symbols": names, "first_date": shared[0].isoformat() if shared else None, "last_date": shared[-1].isoformat() if shared else None,
        "rows": len(shared), "coverage": coverage, "missing_whole_months": missing_months,
        "monthly_endpoints": list(endpoints.values()), "month_end_coverage_status": "unverified_exchange_calendar",
        "monthly_endpoint_policy": "Last observed close in each earlier calendar month, not independently verified exchange month-end. Calendar-day gaps include weekends/holidays and are not asserted to be missing sessions.",
        "maximum_observed_interval_calendar_days": max(gaps, default=0),
        "intervals_longer_than_four_calendar_days": sum(value > 4 for value in gaps),
        "gap_note": "No exchange-holiday calendar is inferred. Multi-day intervals are observed returns, not padded daily zeros."}


def _plans(days, values, roles, as_of):
    month_ends = {}
    for index, stamp in enumerate(days):
        month_ends[_month(stamp)] = index
    plans, start = {}, None
    for index in range(1, len(days)):
        before, current = _month(days[index-1]), _month(days[index])
        if before == current or before >= _month(as_of.date()):
            continue
        needed = list(range(before-12, before+1))
        if any(month not in month_ends for month in needed):
            continue
        # All monthly levels used here precede this fill date.
        ends = [month_ends[month] for month in needed]
        if max(ends) >= index:
            raise ETFResearchError("A monthly signal attempted to use an unavailable observation.")
        if start is None:
            start = index
        us = roles["us"]
        current_level = values[us][ends[-1]]
        sma = math.fsum(values[us][item] for item in ends[-10:])/10
        absolute = _finite(current_level/values[us][ends[0]]-1)
        choice = None
        if roles.get("ex_us") in values and roles.get("bond") in values:
            foreign = roles["ex_us"]
            relative = _finite(values[foreign][ends[-1]]/values[foreign][ends[0]]-1)
            choice = roles["bond"] if absolute <= 0 else (us if absolute >= relative else foreign)
        plans[index] = {"signal_month": _month_label(before), "signal_date": days[index-1].isoformat(),
                        "sma10": us if current_level > sma else ("CASH" if current_level < sma else None),
                        "absolute12": us if absolute > 0 else "CASH", "dual12": choice}
    return plans, start


def _rebalance(equity, old, target, price, rate):
    """Exact all-in fractional-unit ledger, fully funding each transaction fee."""
    if old == target:
        return equity, 0.0, 0, 0.0, 0.0
    cash, fees, actual, legs = equity, 0.0, 0.0, 0
    if old != "CASH":
        fee = equity*rate
        cash -= fee
        fees += fee
        actual += 1.0
        legs += 1
    units = 0.0
    if target != "CASH":
        notional = cash/(1+rate)
        fee = notional*rate
        units = notional/price
        fees += fee
        actual += notional/equity
        cash = notional
        legs += 1
    _finite(cash); _finite(units); _finite(fees)
    if cash <= 0 or units < 0 or fees < 0:
        raise ETFResearchError("Research accounting violated fully funded positive-equity bounds.")
    return cash, fees, legs, actual, units


def _metrics(days, equity, returns, holdings, trades, before=1.0, before_date=None):
    count = len(days)
    if not count:
        return {"observations": 0, "status": "no_coverage"}
    baseline_date = before_date or days[0]
    years = (days[-1]-baseline_date).days/365.2425
    total = equity[-1]/before-1
    cagr = math.expm1(math.log(equity[-1]/before)/years) if years > 0 else None
    # The initial fee is instantaneous at the first close. Combine it with
    # the first positive-duration interval instead of inventing an extra day.
    observed = list(returns)
    if before_date is None:
        observed = ([(1+returns[0])*(1+returns[1])-1]+list(returns[2:])) if count > 1 else []
    frequency = len(observed)/years if years > 0 and observed else None
    std = statistics.stdev(observed) if len(observed) >= 2 else None
    vol = std*math.sqrt(frequency) if std is not None and frequency else None
    mean = statistics.fmean(observed) if observed else 0.0
    sharpe = mean/std*math.sqrt(frequency) if std and frequency else None
    downside = math.sqrt(statistics.fmean(min(value, 0.0)**2 for value in observed)) if observed else 0.0
    sortino = mean/downside*math.sqrt(frequency) if downside and frequency else None
    peak, peak_date, max_dd, underwater_start, longest = before, baseline_date, 0.0, None, 0
    for stamp, level in zip(days, equity):
        if level >= peak:
            peak = level
            if underwater_start is not None:
                longest = max(longest, (stamp-underwater_start).days)
            underwater_start = None
            peak_date = stamp
        else:
            max_dd = max(max_dd, 1-level/peak)
            if underwater_start is None:
                underwater_start = peak_date
            longest = max(longest, (stamp-underwater_start).days)
    annual = {}
    for stamp, value in zip(days, returns):
        annual.setdefault(stamp.year, []).append(value)
    yearly = [{"year": year, "return": math.prod(1+item for item in observations)-1,
               "boundary_year": year in {days[0].year, days[-1].year}} for year, observations in annual.items()]
    exposed_days, previous_date = 0, baseline_date
    for stamp, holding in zip(days, holdings):
        if holding != "CASH":
            exposed_days += (stamp-previous_date).days
        previous_date = stamp
    span = (days[-1]-baseline_date).days
    result = {"status": "measured", "observations": count, "return_intervals": len(observed), "start_date": baseline_date.isoformat(),
        "first_observation_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
        "elapsed_years": years, "total_return": total, "cagr": cagr, "annualized_observed_interval_volatility": vol,
        "sharpe_vs_zero": sharpe, "sortino_vs_zero": sortino, "observed_periods_per_year": frequency,
        "maximum_close_drawdown": max_dd, "maximum_underwater_calendar_days": longest,
        "exposure_fraction": exposed_days/span if span else None,
        "observation_exposure_fraction": sum(item != "CASH" for item in holdings)/count,
        "weight_turnover": math.fsum(item["weight_turnover"] for item in trades),
        "actual_notional_turnover": math.fsum(item["actual_notional_turnover"] for item in trades),
        "trade_count": len(trades), "transaction_legs": sum(item["transaction_legs"] for item in trades),
        "fees_in_initial_equity_units": math.fsum(item["fee"] for item in trades), "year_returns": yearly,
        "worst_observed_year_return": min(item["return"] for item in yearly),
        "positive_year_segments": sum(item["return"] > 0 for item in yearly),
        "negative_year_segments": sum(item["return"] < 0 for item in yearly),
        "year_note": "First and last year segments may be incomplete; no missing-date returns were inserted."}
    result["annual_weight_turnover"] = result["weight_turnover"]/years if years > 0 else None
    for value in result.values():
        if type(value) is float:
            _finite(value)
    return result


def _simulate(name, days, values, plans, start, rate, us):
    dates = days[start:]
    holding, cash, units, last = "CASH", 1.0, 0.0, 1.0
    equity, returns, holdings, trades = [], [], [], []
    for index in range(start, len(days)):
        earning_holding = holding
        before = cash if holding == "CASH" else _finite(units*values[holding][index])
        target = us if name == "buy_hold_us" and index == start else (plans.get(index, {}).get(name) if name != "buy_hold_us" else holding)
        if target is not None and target != holding:
            new, fee, legs, actual, new_units = _rebalance(before, holding, target, 1.0 if target == "CASH" else values[target][index], rate)
            trades.append({"date": days[index].isoformat(), "signal_date": plans.get(index, {}).get("signal_date"),
                "signal_month": plans.get(index, {}).get("signal_month"), "from": holding, "to": target,
                "equity_before_fee": before, "fee": fee, "equity_after_fee": new,
                "weight_turnover": legs, "transaction_legs": legs, "actual_notional_turnover": actual})
            holding, units = target, new_units
            cash = new if target == "CASH" else 0.0
            before = new
        if not math.isfinite(before) or before <= 0:
            raise ETFResearchError("Research equity is not finite and positive.")
        value = _finite(before/last-1)
        equity.append(before); returns.append(value); holdings.append(earning_holding)
        last = before
    metrics = _metrics(dates, equity, returns, holdings, trades)
    folds = {}
    for fold, lower, upper in _FOLDS:
        positions = [index for index, stamp in enumerate(dates) if (lower is None or stamp.isoformat() >= lower) and (upper is None or stamp.isoformat() <= upper)]
        if positions:
            first, stop = positions[0], positions[-1]+1
            fold_trades = [item for item in trades if dates[first].isoformat() <= item["date"] <= dates[stop-1].isoformat()]
            folds[fold] = _metrics(dates[first:stop], equity[first:stop], returns[first:stop], holdings[first:stop], fold_trades,
                                   equity[first-1] if first else 1.0, dates[first-1] if first else None)
        else:
            folds[fold] = {"status": "no_coverage", "observations": 0}
    return {"metrics": metrics, "folds": folds, "equity": equity, "trades": trades}


def _run_study(dataset: dict, *, cost_bps=5.0) -> dict:
    """Run three frozen monthly hypotheses and fair US buy/hold comparisons.

The four predeclared cost cases are sensitivity checks, never an optimizer.
The result always retains NAV-execution and future-profit qualification limits.
"""
    if type(cost_bps) not in (int, float) or not math.isfinite(cost_bps) or not 0 <= cost_bps <= 100:
        raise ETFResearchError("The fixed primary cost must be finite and between 0 and 100 basis points.")
    cost_bps = float(cost_bps)
    parts = _parse(dataset)
    as_of, roles, series, digest, _ = parts
    days, names, missing, calendar = _calendar(series, roles)
    lookup = {name: dict(series[name].rows) for name in names}
    values = {name: [lookup[name][day] for day in days] for name in names}
    plans, start = _plans(days, values, roles, as_of) if days and not calendar["missing_whole_months"] else ({}, None)
    bad_dividends = [name for name in names if series[name].kind != "nav_total_return_index" or series[name].policy != "reinvested"]
    report = {"schema": 1, "study_type": "offline_etf_nav_research", "analysis_as_of": as_of.isoformat(),
        "validation": _summary(parts), "calendar": calendar,
        "execution_model": {"decision": "Last observed supplied NAV/index levels in earlier calendar months, never the fill day's level; price-only data is a sensitivity, not a total-return replication.",
            "fill": "First following available NAV close; old holding earns the complete interval before fee and switch.",
            "warmup": "Thirteen consecutive earlier calendar months, using their last observed levels, shared by candidates and benchmark; exchange month-end coverage remains unverified.",
            "cash": "Constant level 1, zero interest; not Treasury bills or an observed cash yield.",
            "positions": "Long-only, fully funded fractional research units; one ETF or CASH, no leverage or shorting.",
            "fees": "Exact sell-then-funded-buy accounting; cash-to-ETF spends E/(1+k) on units. Cross-ETF target-weight turnover is 2; actual funded notional is reported separately.",
            "risk_metrics": "Close-marked equity includes first-entry fees against initial wealth 1. Volatility/Sharpe use observed-interval annualization, approximate for irregular gaps; zero-volatility Sharpe is null.",
            "terminal_position": "Marked at final NAV, not liquidated or represented as realized profit.",
            "missing_data": "No filling or synthetic OHLC. Missing whole calendar months block the research path."},
        "cost_model": {"baseline_bps_per_leg": cost_bps, "predeclared_cases_bps": list(COST_CASES),
            "primary_case": "zero_cost_diagnostic" if cost_bps == 0 else "operator_supplied_cost_scenario",
            "status": "Illustrative proportional transaction-cost scenarios, not observed spreads or broker quotes.",
            "excluded": ["Taxes", "Market-price/NAV premium and discount", "Observed spreads and market impact", "Broker-specific minimum commissions"]},
        "qualification": {"proven_winner": False, "execution_status": "unqualified_nav_proxy", "performance_promotion_allowed": False,
            "dividend_status": "missing_or_excluded_distributions" if bad_dividends else "declared_reinvested_total_return",
            "price_only_symbols": bad_dividends,
            "blockers": ["NAV observations do not prove executable market fills.", "Last observed monthly endpoints are not independently verified exchange month-ends.", "No prospective or live performance validation."] +
                (["Missing/unknown distributions alter both signals and returns; price-only sensitivity cannot qualify total-return performance."] if bad_dividends else []),
            "interpretation": "Historical research hypotheses with correlated trend exposure; no guaranteed future profit or personalized allocation."},
        "split_policy": {"train_through": "2014-12-31", "validation": ["2015-01-01", "2021-12-31"], "holdout_from": "2022-01-01",
            "fitting": "None; fixed retrospective rules. The holdout never selects parameters or the displayed winner.",
            "boundary_accounting": "Continuous positions and prior-close equity carry into each split; folds do not restart capital."},
        "strategies": [], "benchmarks": []}
    if start is not None:
        report["calendar"]["evaluation_dates"] = [day.isoformat() for day in days[start:]]
        report["calendar"]["evaluation_start"] = days[start].isoformat()
    else:
        report["calendar"]["evaluation_dates"] = []
        report["calendar"]["evaluation_start"] = None
    descriptions = {"sma10": "Monthly 10-month SMA, equity above, CASH below; exact tie retains prior target.",
        "absolute12": "Monthly 12-month US level growth positive => US, otherwise zero-yield CASH; cash-proxy adaptation.",
        "dual12": "US 12-month growth must be positive before US/ex-US relative choice; otherwise aggregate-bond role. Not canonical GEM: zero-yield cash filter and supplied ETF substitutions."}
    for name in ("sma10", "absolute12", "dual12", "buy_hold_us"):
        item = {"id": name, "description": descriptions.get(name, "US buy/hold on identical dates with the same funded entry-fee convention.")}
        reason = ("Missing required dual roles/series: "+", ".join(missing)) if name == "dual12" and missing else None
        if name == "dual12" and not missing and len({(series[symbol].kind, series[symbol].policy) for symbol in names}) != 1:
            reason = "Dual momentum requires consistently declared dividend-adjustment bases across its roles; mixed price-only and total-return series are not ranked."
        if calendar["missing_whole_months"]:
            reason = "Missing whole months; no monthly decision path was invented."
        if start is None and reason is None:
            reason = "Insufficient consecutive month-end warmup and a later available fill date."
        if reason:
            item.update(status="blocked", reason=reason, metrics=None, folds={}, cost_sensitivity=[])
        else:
            simulations = {bps: _simulate(name, days, values, plans, start, bps/10_000, roles["us"]) for bps in sorted(set(COST_CASES+(cost_bps,)))}
            primary = simulations[cost_bps]
            item.update(status="completed", **primary,
                cost_sensitivity=[{"bps_per_leg": bps, "metrics": simulations[bps]["metrics"], "folds": simulations[bps]["folds"]} for bps in COST_CASES if bps != cost_bps])
        report["benchmarks" if name == "buy_hold_us" else "strategies"].append(item)
    report["status"] = "completed" if any(item["status"] == "completed" for item in report["strategies"]) else "blocked"
    body = _canonical(report)
    if len(body) > MAX_REPORT_BYTES:
        raise ETFResearchError("Research report exceeds the bounded 4 MB output limit.")
    report["integrity"] = {"algorithm": "sha256", "canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=False, allow_nan=False",
        "dataset_sha256": digest, "report_body_sha256": hashlib.sha256(body).hexdigest(),
        "scope": "Report body excludes this integrity object; provenance file digests remain caller claims."}
    return report


def run_study(dataset: dict, *, cost_bps=5.0) -> dict:
    """Pure bounded entry point; arithmetic overflow is a failed study, not data."""
    try:
        return _run_study(dataset, cost_bps=cost_bps)
    except ETFResearchError:
        raise
    except (ArithmeticError, ValueError):
        raise ETFResearchError("The supplied levels exceed safe finite research accounting bounds.") from None
