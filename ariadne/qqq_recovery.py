"""Fixed QQQ recovery research and forward-only paper accounting.

This module has no I/O, inference, networking, broker or scheduling capability.
Input OHLC is split-adjusted, but not dividend-adjusted. Distributions are cash
credits to carried-in holders before the ex-date open; adjusted-close is retained
as provenance only and never enters the ledger or signals.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import statistics
from urllib.parse import urlsplit

UTC = timezone.utc
MAX_ROWS = 15000
MAX_INPUT_BYTES = 12 * 1024 * 1024
COST_CASES = (5, 10, 25, 50)
RULE = {
    "id": "qqq-recovery-v1", "schema": 1, "universe": ["QQQ", "SPY"],
    "position": "QQQ or CASH; fully funded fractional paper shares, no leverage or shorting",
    "drawdown_reference_sessions": 63, "drawdown_fraction": "0.08", "setup_memory_sessions": 20,
    "entry": "QQQ close > highest QQQ high of prior 5 sessions AND SPY close > its 20-session SMA including current completed close; drawdown setup occurred within current/prior 19 sessions",
    "exit": "QQQ close < lowest QQQ low of prior 10 sessions OR position has held 20 completed sessions",
    "fill": "Next eligible observed regular-session open after the recorded completed-close decision; no same-bar fill",
    "forward_decision": "Actual decided_at clock is at least the accepted snapshot's as_of; a new durable decision and any later assumed fill require more than 60 seconds of lead before the next session open",
    "paper_activation": "Start in cash from the accepted baseline, adopt no historical signals or trades; unseen-session gaps, source revisions and split-basis transitions require explicit review",
    "dividends": "Split-adjusted cash distribution credited to carried-in holders before ex-date open fills; no reinvestment",
    "cash_interest": "0", "warmup_sessions": 82,
    "price_basis": "split_adjusted_ohlc", "dividend_policy": "split_adjusted_cash_on_ex_date",
    "selection": "Fixed before new study outcomes; no parameter optimization. Historical folds are retrospective, not prospective validation.",
}


class QQQRecoveryError(ValueError):
    """Held research/paper input; never permission to trade."""


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QQQRecoveryError("Use finite bounded JSON values.") from None


RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()


def _object(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise QQQRecoveryError("Use exactly the documented recovery fields.")


def _clock(value):
    if type(value) is not str or not 10 <= len(value) <= 64:
        raise QQQRecoveryError("Use a bounded timezone-aware ISO datetime.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise QQQRecoveryError("Use a timezone-aware ISO datetime.") from None


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise QQQRecoveryError("Use ISO session dates.")
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value or result.weekday() > 4:
            raise ValueError
        return result
    except ValueError:
        raise QQQRecoveryError("Use unique ordered weekday session dates.") from None


def _number(value, *, zero=False, maximum="1000000000", minimum_exponent=-20):
    if type(value) not in (str, int, float) or type(value) is bool:
        raise QQQRecoveryError("Prices and ledger values require finite decimal numbers.")
    if len(str(value)) > 64:
        raise QQQRecoveryError("A numeric value is too long.")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise QQQRecoveryError("Use ordinary finite decimal numbers.") from None
    if not result.is_finite() or result < 0 or (not zero and result == 0) or result > Decimal(maximum) or result.as_tuple().exponent < minimum_exponent:
        raise QQQRecoveryError("A numeric value exceeds positive finite bounds.")
    return result


def _decimal(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _session_clock(day, *, opening=False):
    # New York and Chicago share US equity-session DST transitions. The bundled
    # Chicago tz record avoids requiring an OS tz database on offline Windows.
    if not opening:
        from .qqq_data import session_close_utc
        return session_close_utc(day)
    from .briefing import _tz
    return datetime.combine(day, time(8, 30), _tz("America/Chicago")).astimezone(UTC)


def next_session_open(session):
    """UTC open after a known completed session, using the bundled calendar."""
    from .qqq_data import is_session
    day = _day(session) if type(session) is str else session
    if type(day) is not date or not is_session(day):
        raise QQQRecoveryError("Choose a known exchange session for the next open.")
    for offset in range(1,17):
        following = day+timedelta(days=offset)
        if is_session(following):
            return _session_clock(following,opening=True)
    raise QQQRecoveryError("No bounded next exchange session is available.")


def _parse(dataset):
    _object(dataset, ("schema", "as_of", "currency", "price_basis", "dividend_policy", "symbols"), ("calendar",))
    if type(dataset["schema"]) is not int or dataset["schema"] != 1 or dataset["currency"] != "USD" or dataset["price_basis"] != RULE["price_basis"] or dataset["dividend_policy"] != RULE["dividend_policy"]:
        raise QQQRecoveryError("Use schema 1 USD split-adjusted OHLC with separately credited split-adjusted cash dividends.")
    raw = _canonical(dataset)
    if len(raw) > MAX_INPUT_BYTES:
        raise QQQRecoveryError("The recovery dataset exceeds 12 MiB.")
    as_of = _clock(dataset["as_of"])
    _object(dataset["symbols"], ("QQQ", "SPY"))
    symbols, excluded = {}, {}
    for symbol in ("QQQ", "SPY"):
        entry = dataset["symbols"][symbol]
        _object(entry, ("source", "bars"))
        source = entry["source"]
        _object(source, ("url", "sha256", "retrieved_at"))
        try:
            parsed = urlsplit(source["url"])
            valid = (type(source["url"]) is str and len(source["url"]) <= 4096 and parsed.scheme == "https" and bool(parsed.hostname) and
                     parsed.username is None and parsed.password is None and parsed.fragment == "" and parsed.port in (None, 443) and not any(ord(c) < 33 for c in source["url"]))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid or type(source["sha256"]) is not str or len(source["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source["sha256"]):
            raise QQQRecoveryError("Use dated HTTPS source claims and lowercase SHA-256 identities.")
        retrieved = _clock(source["retrieved_at"])
        if retrieved > as_of:
            raise QQQRecoveryError("Source retrieval cannot follow this snapshot's analysis clock.")
        bars = entry["bars"]
        if type(bars) is not list or not 1 <= len(bars) <= MAX_ROWS:
            raise QQQRecoveryError("Supply one to 15,000 daily bars per symbol.")
        rows, previous, ignored = [], None, 0
        for supplied in bars:
            _object(supplied, ("date", "open", "high", "low", "close", "volume", "adj_close", "cash_dividend", "split_ratio"))
            day = _day(supplied["date"])
            if previous is not None and day <= previous:
                raise QQQRecoveryError("Session dates must be unique and strictly ascending.")
            previous = day
            row = {key: _number(supplied[key]) for key in ("open", "high", "low", "close", "adj_close", "split_ratio")}
            row["cash_dividend"] = _number(supplied["cash_dividend"], zero=True)
            volume = supplied["volume"]
            if type(volume) is not int or not 0 <= volume <= 10**13:
                raise QQQRecoveryError("Volume requires a bounded nonnegative integer.")
            if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]) or row["high"] < row["low"]:
                raise QQQRecoveryError("Each OHLC row must enclose its open and close.")
            if not Decimal("0.01") <= row["split_ratio"] <= Decimal("100"):
                raise QQQRecoveryError("Split ratios must be between 0.01 and 100.")
            if _session_clock(day)+timedelta(minutes=15) > as_of:
                ignored += 1
                continue
            rows.append({"date": day, "volume": volume, **row})
        symbols[symbol] = rows
        excluded[symbol] = ignored
    dates = [row["date"] for row in symbols["QQQ"]]
    if dates != [row["date"] for row in symbols["SPY"]]:
        raise QQQRecoveryError("QQQ and SPY must cover the exact same completed sessions; never fill or drop mismatched rows.")
    if not dates:
        raise QQQRecoveryError("No completed paired sessions are available.")
    from .qqq_data import session_dates
    if dates != session_dates(dates[0],dates[-1]):
        raise QQQRecoveryError("The paired history contains missing or non-session dates; no silent calendar repair is allowed.")
    return {"as_of": as_of, "symbols": symbols, "dates": dates, "excluded": excluded,
            "dataset_sha256": hashlib.sha256(raw).hexdigest(), "source_claims": deepcopy({symbol: dataset["symbols"][symbol]["source"] for symbol in symbols})}


def validate_dataset(dataset):
    parsed = _parse(dataset)
    return _summary(parsed)


def _summary(parsed):
    days = parsed["dates"]
    gaps = [(b-a).days for a, b in zip(days, days[1:])]
    return {"valid": True, "rule_sha256": RULE_SHA256, "dataset_sha256": parsed["dataset_sha256"],
            "as_of": parsed["as_of"].isoformat(), "first_date": days[0].isoformat(), "last_date": days[-1].isoformat(),
            "completed_sessions": len(days), "excluded_incomplete_or_future_bars": parsed["excluded"],
            "maximum_calendar_gap_days": max(gaps, default=0), "calendar_qualification": "Exact paired coverage against bundled 1999–2035 US equity sessions, including fixed known exceptional closures; later calendar changes require review.",
            "source_claims": parsed["source_claims"], "source_authenticity": "Declared source identities; pure engine does not fetch or independently authenticate prices.",
            "completion_policy": "Observed exchange close plus 15-minute finalization grace, including known early closes; no current unfinished session is used."}


def _indicators(parsed):
    """Compute each fixed historical window once, shared by cost stress paths."""
    if "indicators" in parsed:
        return parsed["indicators"]
    qqq, spy = parsed["symbols"]["QQQ"], parsed["symbols"]["SPY"]
    flags, values = [], []
    with localcontext() as context:
        context.prec = 40
        for index, row in enumerate(qqq):
            peak = max((item["close"] for item in qqq[index-63:index]), default=None) if index >= 63 else None
            flags.append(bool(peak is not None and row["close"] <= peak*Decimal("0.92")))
            values.append({
                "setup_dates":[qqq[j]["date"].isoformat() for j in range(max(0,index-19),index+1) if flags[j]],
                "prior_5_high":max((item["high"] for item in qqq[index-5:index]), default=None) if index >= 5 else None,
                "prior_10_low":min((item["low"] for item in qqq[index-10:index]), default=None) if index >= 10 else None,
                "spy_sma20":sum((item["close"] for item in spy[index-19:index+1]),Decimal(0))/Decimal(20) if index >= 19 else None,
            })
    parsed["indicators"] = values
    return values


def _signal(parsed, index, *, position=False, holding_sessions=0):
    qqq, spy = parsed["symbols"]["QQQ"], parsed["symbols"]["SPY"]
    current = qqq[index]
    indicators = _indicators(parsed)[index]
    result = {"session": current["date"].isoformat(), "action": "HOLD" if position else "NO_TRADE", "reasons": [],
              "ready": index >= RULE["warmup_sessions"], "rule_sha256": RULE_SHA256, "inputs": {}}
    if position:
        if index < 10:
            raise QQQRecoveryError("An open position lacks the required prior exit history.")
        floor = indicators["prior_10_low"]
        result["inputs"] = {"close": _decimal(current["close"]), "prior_10_low": _decimal(floor), "holding_sessions": holding_sessions}
        if current["close"] < floor:
            result["reasons"].append("close_below_prior_10_session_low")
        if holding_sessions >= 20:
            result["reasons"].append("20_completed_sessions")
        if result["reasons"]:
            result["action"] = "SELL"
        return result
    if index < RULE["warmup_sessions"]:
        result["reasons"] = ["insufficient_warmup"]
        return result
    setup_dates = indicators["setup_dates"]
    breakout = indicators["prior_5_high"]
    spy_sma = indicators["spy_sma20"]
    result["inputs"] = {"setup_dates": setup_dates, "close": _decimal(current["close"]), "prior_5_high": _decimal(breakout),
                        "spy_close": _decimal(spy[index]["close"]), "spy_sma20": _decimal(spy_sma)}
    if not setup_dates:
        result["reasons"].append("no_recent_8_percent_drawdown_setup")
    if not current["close"] > breakout:
        result["reasons"].append("no_5_session_high_breakout")
    if not spy[index]["close"] > spy_sma:
        result["reasons"].append("spy_not_above_20_session_sma")
    if not result["reasons"]:
        result["action"] = "BUY"
        result["reasons"] = ["drawdown_recovery_and_broad_market_confirmation"]
    return result


def signal(dataset, *, position=False, holding_sessions=0):
    if type(position) is not bool or type(holding_sessions) is not int or not 0 <= holding_sessions <= MAX_ROWS:
        raise QQQRecoveryError("Use explicit position state and bounded completed holding sessions.")
    with localcontext() as context:
        context.prec = 40
        parsed = _parse(dataset)
        return {**_signal(parsed, len(parsed["dates"])-1, position=position, holding_sessions=holding_sessions), "data": _summary(parsed)}


def _cost(value):
    result = _number(value, zero=True, maximum="100")
    return result / Decimal(10000)


def _fill(cash, shares, price, action, cost):
    if action == "BUY":
        if shares != 0:
            raise QQQRecoveryError("A BUY cannot add leverage or pyramid an open position.")
        notional = cash / (1+cost)
        fee = notional * cost
        return Decimal(0), notional/price, fee
    if action == "SELL":
        if shares <= 0:
            raise QQQRecoveryError("A SELL cannot short an empty account.")
        notional = shares*price
        fee = notional*cost
        return cash+notional-fee, Decimal(0), fee
    raise QQQRecoveryError("Only BUY and SELL are simulated fills.")


def _metrics(rows, initial, *, baseline_date=None):
    if not rows:
        return {"status": "no_coverage", "observations": 0}
    levels = [float(row["equity"]) for row in rows]
    start, end = rows[0]["date"], rows[-1]["date"]
    beginning = _session_clock(_day(baseline_date)) if baseline_date else _session_clock(_day(start),opening=True)
    years = (_session_clock(_day(end))-beginning).total_seconds()/(365.2425*86400)
    total = levels[-1]/float(initial)-1
    cagr = math.expm1(math.log(levels[-1]/float(initial))/years) if years > 0 else None
    peak, drawdown = float(initial), 0.0
    for level in levels:
        peak = max(peak, level)
        drawdown = max(drawdown, 1-level/peak)
    returns = [levels[0]/float(initial)-1]+[b/a-1 for a, b in zip(levels, levels[1:])]
    # Initial open-to-close return is a real session; annualization is an
    # observed-calendar estimate rather than an assumed padded daily series.
    frequency = len(returns)/years if years > 0 else None
    deviation = statistics.stdev(returns) if len(returns) >= 2 else None
    volatility = deviation*math.sqrt(frequency) if deviation and frequency else None
    sharpe = statistics.fmean(returns)/deviation*math.sqrt(frequency) if deviation and frequency else None
    if any(value is not None and not math.isfinite(value) for value in (cagr,total,drawdown,volatility,sharpe)):
        raise QQQRecoveryError("Research metrics exceed finite accounting bounds.")
    return {"status":"measured", "start_date":start, "end_date":end, "observations":len(rows), "cagr":cagr,
            "total_return":total, "maximum_close_drawdown":drawdown, "observed_interval_volatility":volatility,
            "sharpe_vs_zero":sharpe, "ending_equity":rows[-1]["equity"], "baseline_date":baseline_date,
            "annualization":"Elapsed calendar time from first evaluated open (or preceding fold close); observed sessions, no missing-return padding."}


def _simulate(parsed, rate, initial, *, benchmark=False):
    qqq = parsed["symbols"]["QQQ"]
    cash, shares, fees, dividends = initial, Decimal(0), Decimal(0), Decimal(0)
    pending, held, trade_entry = None, 0, None
    curve, fills, signals, round_trips = [], [], [], []
    for index in range(RULE["warmup_sessions"], len(qqq)):
        row = qqq[index]
        distribution = shares*row["cash_dividend"]
        cash += distribution
        dividends += distribution
        if benchmark and index == RULE["warmup_sessions"]:
            pending = {"action":"BUY", "session":None, "reasons":["same_calendar_buy_and_hold"]}
        if pending:
            action = pending["action"]
            before = cash+shares*row["open"]
            cash, shares, fee = _fill(cash,shares,row["open"],action,rate)
            fees += fee
            fill = {"session":row["date"].isoformat(),"action":action,"price":_decimal(row["open"]),"fee":_decimal(fee),
                    "signal_session":pending["session"],"reasons":pending["reasons"],"shares_after":_decimal(shares),"cash_after":_decimal(cash)}
            fills.append(fill)
            if action == "BUY":
                held = 0
                trade_entry = {"entry_session":row["date"].isoformat(),"entry_equity":before}
            else:
                held = 0
                round_trips.append({"entry_session":trade_entry["entry_session"],"exit_session":row["date"].isoformat(),"net_return":float(cash/trade_entry["entry_equity"]-1)})
                trade_entry = None
            pending = None
        if shares:
            held += 1
        equity = cash+shares*row["close"]
        if equity <= 0 or not equity.is_finite() or equity > Decimal("1e100"):
            raise QQQRecoveryError("Research equity exceeds safe positive accounting bounds.")
        curve.append({"date":row["date"].isoformat(),"equity":_decimal(equity),"position":"QQQ" if shares else "CASH"})
        if not benchmark:
            decision = _signal(parsed,index,position=bool(shares),holding_sessions=held)
            if decision["action"] in ("BUY","SELL"):
                pending = decision
                signals.append(decision)
    return {"curve":curve,"fills":fills,"signals":signals,"pending":pending,"round_trips":round_trips,
            "metrics":_metrics(curve,initial),"fees":_decimal(fees),"cash_dividends":_decimal(dividends),
            "trade_count":len(fills),"completed_round_trips":len(round_trips),
            "losing_round_trips":sum(row["net_return"] < 0 for row in round_trips),
            "win_rate_completed_round_trips":sum(row["net_return"] > 0 for row in round_trips)/len(round_trips) if round_trips else None,
            "final_holding":"QQQ" if shares else "CASH", "open_position_not_forcibly_liquidated":bool(shares)}


def _folds(curve, initial):
    results = []
    for label, low, high in (("train",None,"2014-12-31"),("validation","2015-01-01","2021-12-31"),("retrospective_holdout","2022-01-01",None)):
        included = [row for row in curve if (low is None or row["date"] >= low) and (high is None or row["date"] <= high)]
        prior = [row for row in curve if included and row["date"] < included[0]["date"]]
        base = Decimal(prior[-1]["equity"]) if prior else initial
        results.append({"id":label,"metrics":_metrics(included,base,baseline_date=prior[-1]["date"] if prior else None),"interpretation":"Retrospective slice of the continuous fixed-rule path; not separately optimized and not prospective evidence."})
    return results


def backtest(dataset, *, cost_bps=5, initial_cash="10000.00"):
    with localcontext() as context:
        context.prec = 40
        parsed = _parse(dataset)
        rate, initial = _cost(cost_bps), _number(initial_cash)
        if len(parsed["dates"]) <= RULE["warmup_sessions"]:
            raise QQQRecoveryError("At least 83 completed paired sessions are required for a recovery study.")
        study = _simulate(parsed,rate,initial)
        benchmark = _simulate(parsed,rate,initial,benchmark=True)
        cash_curve = [{"date":row["date"],"equity":_decimal(initial),"position":"CASH"} for row in study["curve"]]
        stress = []
        for bps in COST_CASES:
            replay = study if Decimal(str(cost_bps)) == bps else _simulate(parsed,_cost(bps),initial)
            stress.append({"bps_per_leg":bps,"metrics":replay["metrics"],"trade_count":replay["trade_count"]})
        return {"schema":1,"study_type":"fixed_qqq_recovery_daily_research","rule":deepcopy(RULE),"rule_sha256":RULE_SHA256,
                "data":_summary(parsed),"cost_bps_per_leg":_decimal(Decimal(str(cost_bps))),"initial_cash":_decimal(initial),
                "strategy":study,"benchmarks":{"qqq_buy_and_hold":benchmark,"cash":{ "metrics":_metrics(cash_curve,initial)}},
                "folds":_folds(study["curve"],initial),"cost_sensitivity":stress,
                "execution_qualified":False,"future_profit_proven":False,"orders_submitted":False,
                "accounting":"Fractional synthetic shares in the supplied common split basis; cash ex-date dividends, no cash interest. No second split adjustment, no adjusted-close total-return multiplier.",
                "limitations":["Daily vendor OHLC is an execution proxy; actual fills, spread, slippage, taxes and liquidity are not observed.","Source provenance and historical revision claims do not authenticate every price or publication time.","Exchange-calendar completeness requires collector/service checks.","No terminal forced liquidation; marked open positions may lose or gain before an actual exit.","No intraday protective stop is simulated. Exit is a completed-close decision followed by next-open execution; overnight gaps can exceed planned risk.","The fixed rule was chosen with knowledge of prior market history. Historical slices are not newly untouched prospective evidence."]}


_STATE = {"schema","rule_sha256","started_at","cash","shares","pending","last_processed","holding_sessions","last_mark_price","equity","total_fees","total_dividends","trade_count","completed_round_trips"}


def initial_state(dataset, started_at, *, initial_cash="10000.00"):
    parsed = _parse(dataset)
    started = _clock(started_at)
    if started < parsed["as_of"]:
        raise QQQRecoveryError("Paper activation cannot be backdated before the accepted snapshot.")
    cash = _number(initial_cash)
    return {"schema":1,"rule_sha256":RULE_SHA256,"started_at":started.isoformat(),"cash":_decimal(cash),"shares":"0", "pending":None,
            "last_processed":parsed["dates"][-1].isoformat(),"holding_sessions":0,"last_mark_price":_decimal(parsed["symbols"]["QQQ"][-1]["close"]),
            "equity":_decimal(cash),"total_fees":"0","total_dividends":"0","trade_count":0,"completed_round_trips":0}


def _state(value):
    _object(value,_STATE)
    if type(value["schema"]) is not int or value["schema"] != 1 or value["rule_sha256"] != RULE_SHA256:
        raise QQQRecoveryError("Paper state belongs to a different rule.")
    _clock(value["started_at"])
    _day(value["last_processed"])
    for key in ("cash","shares","last_mark_price","equity","total_fees","total_dividends"):
        number = _number(value[key],zero=key not in ("last_mark_price","equity"),maximum="1e100",minimum_exponent=-100)
        if type(value[key]) is not str or _decimal(number) != value[key]:
            raise QQQRecoveryError("Paper amounts must retain canonical exact decimal text.")
    for key in ("holding_sessions","trade_count","completed_round_trips"):
        if type(value[key]) is not int or not 0 <= value[key] <= 100000:
            raise QQQRecoveryError("Paper counts require bounded nonnegative integers.")
    if value["pending"] is not None:
        _object(value["pending"],("action","signal_session","decided_at","rule_sha256"))
        if value["pending"]["action"] not in ("BUY","SELL") or value["pending"]["rule_sha256"] != RULE_SHA256 or value["pending"]["signal_session"] != value["last_processed"]:
            raise QQQRecoveryError("Paper pending action is not bound to the last accepted decision.")
        decided = _clock(value["pending"]["decided_at"])
        if decided < _clock(value["started_at"]) or decided < _session_clock(_day(value["pending"]["signal_session"]))+timedelta(minutes=15):
            raise QQQRecoveryError("A paper decision cannot precede activation or its finalized signal close.")
    cash, shares = Decimal(value["cash"]), Decimal(value["shares"])
    if cash+shares*Decimal(value["last_mark_price"]) != Decimal(value["equity"]):
        raise QQQRecoveryError("Paper cash, shares and equity do not reconcile exactly.")
    if not shares and value["holding_sessions"]:
        raise QQQRecoveryError("Empty paper holdings cannot report holding sessions.")
    if shares and not value["holding_sessions"]:
        raise QQQRecoveryError("Carried-in paper shares need a completed holding session.")
    if value["pending"] and ((value["pending"]["action"] == "BUY") == bool(shares)):
        raise QQQRecoveryError("The pending paper side conflicts with the account's holdings.")
    return deepcopy(value)


def advance_paper(dataset, state, *, decision_after=None, decided_at=None, cost_bps=5):
    """Advance one newly finalized session, never replaying historical trades.

    The caller owns source-prefix integrity, expected exchange-session coverage,
    authorization, pause/failure receipts and atomic publication of returned events.
    """
    with localcontext() as context:
        context.prec = 40
        parsed, result, rate = _parse(dataset), _state(state), _cost(cost_bps)
        started = _clock(result["started_at"])
        decision_floor = max(started,_clock(decision_after)) if decision_after else started
        decision_clock = _clock(decided_at) if decided_at else parsed["as_of"]
        if decision_clock < parsed["as_of"]:
            raise QQQRecoveryError("The actual paper decision clock cannot precede the accepted source snapshot.")
        if parsed["as_of"] < started:
            raise QQQRecoveryError("A paper snapshot predates activation.")
        last = _day(result["last_processed"])
        if last not in parsed["dates"]:
            raise QQQRecoveryError("The prior accepted paper session disappeared.")
        prior = parsed["symbols"]["QQQ"][parsed["dates"].index(last)]
        if prior["close"] != Decimal(result["last_mark_price"]):
            raise QQQRecoveryError("The last accepted paper mark changed; source revisions require explicit review.")
        if result["pending"] and _clock(result["pending"]["decided_at"]) > decision_clock:
            raise QQQRecoveryError("The snapshot decision clock precedes an already recorded paper decision.")
        new = [index for index, day in enumerate(parsed["dates"]) if day > last]
        if not new:
            return {"status":"no_new_session","state":result,"events":[],"latest_signal":None,"rule_sha256":RULE_SHA256}
        if len(new) != 1:
            raise QQQRecoveryError("Multiple unseen sessions require explicit gap review; no paper catch-up fill is allowed.")
        index = new[0]
        row = parsed["symbols"]["QQQ"][index]
        if _session_clock(row["date"]) <= decision_floor:
            raise QQQRecoveryError("No new paper decision may use a close completed before activation or its decision floor.")
        if row["split_ratio"] != 1:
            raise QQQRecoveryError("A paper split event requires explicit basis-transition review; do not silently rebase holdings.")
        cash, shares = Decimal(result["cash"]), Decimal(result["shares"])
        events = []
        distribution = shares*row["cash_dividend"]
        if distribution:
            cash += distribution
            result["total_dividends"] = _decimal(Decimal(result["total_dividends"])+distribution)
            events.append({"type":"cash_dividend","session":row["date"].isoformat(),"amount":_decimal(distribution),"eligibility":"Carried-in holders before ex-date open fills."})
        pending = result["pending"]
        if pending:
            open_at = _session_clock(row["date"],opening=True)
            if open_at <= max(started,_clock(pending["decided_at"])+timedelta(seconds=60)):
                raise QQQRecoveryError("The eligible paper open already preceded the recorded decision; never fabricate a retrospective fill.")
            cash,shares,fee = _fill(cash,shares,row["open"],pending["action"],rate)
            result["total_fees"] = _decimal(Decimal(result["total_fees"])+fee)
            result["trade_count"] += 1
            result["holding_sessions"] = 0
            if pending["action"] == "SELL":
                result["completed_round_trips"] += 1
            events.append({"type":"paper_fill","session":row["date"].isoformat(),"assumed_fill_at":open_at.isoformat(),"action":pending["action"],"price":_decimal(row["open"]),"fee":_decimal(fee),"signal_session":pending["signal_session"],"decided_at":pending["decided_at"],"simulated":True,"actual_broker_fill":False})
        if shares:
            result["holding_sessions"] += 1
        decision = _signal(parsed,index,position=bool(shares),holding_sessions=result["holding_sessions"])
        result.update(cash=_decimal(cash),shares=_decimal(shares),last_processed=row["date"].isoformat(),last_mark_price=_decimal(row["close"]),equity=_decimal(cash+shares*row["close"]),pending=None)
        if decision["action"] in ("BUY","SELL"):
            if next_session_open(row["date"]) <= decision_clock+timedelta(seconds=60):
                raise QQQRecoveryError("The next paper open is too close or already passed; never backdate a late decision.")
            result["pending"] = {"action":decision["action"],"signal_session":decision["session"],"decided_at":decision_clock.isoformat(),"rule_sha256":RULE_SHA256}
            events.append({"type":"paper_decision",**deepcopy(result["pending"]),"reasons":decision["reasons"]})
        _state(result)
        return {"status":"completed","state":result,"events":events,"latest_signal":decision,"rule_sha256":RULE_SHA256,
                "scope":"Forward paper simulation only; daily open/close proxies and modeled costs, no submitted orders or actual fills."}
