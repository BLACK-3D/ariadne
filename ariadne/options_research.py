"""Fixed regime-filtered option hypotheses; pure retrospective research only.

The owner portfolio and its rule stay unchanged.  These ledgers have their own
schema and rule identity, no forward/migration API, and never adopt owner cash.
Only the frozen portfolio's generic risk/price execution helpers are reused;
there is no state/rule alias, global patch, model, filesystem or network access.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib

from .options_recovery import (OptionsRecoveryError, _canonical, _clock,
    _number, _text, _debit, _day_at, _sessions, _rows, _deadline,
    _quote_ok, _recovery, _precision)
from .options_portfolio import (RULE_SHA256 as EXECUTION_REFERENCE_SHA256,
    _eligible as _risk_eligible, _pending as _execution_pending,
    _sell as _execution_sell, _equity as _execution_equity)

PREREGISTRATION_SHA256 = "53b4c503d7d896170e4b0fe790e02d147c9bdfa7965454204cb74a9c041152a7"
STRATEGIES = {
    "regime_recovery_call_v1": {"type":"call", "signal":"Frozen recovery(last15) AND current>prior50SMA AND prior20SMA>prior50SMA", "warmup":51},
    "trend_breakout_call_v1": {"type":"call", "signal":"Current>prior20 maximum AND current>prior50SMA AND prior20SMA>prior50SMA", "warmup":51},
    "trend_breakdown_put_v1": {"type":"put", "signal":"Current<prior20 minimum AND current<prior50SMA AND prior20SMA<prior50SMA", "warmup":51},
}
STRATEGY_IDS = tuple(STRATEGIES)
RULE = {
    "id":"regime-filtered-options-research-v1", "schema":1,
    "research_only":True, "universe":["QQQ","SPY","AAPL"],
    "preregistration_sha256":PREREGISTRATION_SHA256,
    "registered_at_utc":"2026-10-30T19:04:30.246222+00:00",
    "strategies":STRATEGIES,
    "window":"51 observed closes; exclude current close from each20/50 reference window; last15 includes current for frozen recovery",
    "execution_reference_sha256":EXECUTION_REFERENCE_SHA256,
    "instrument":"One standard100-share fully paid long call or put, one position total; no shorts/spreads/exercise/real orders",
    "selection":"Unchanged7-21 calendarDTE/current-month/0-5%OTM/nearest14DTE then closest spot then contractID",
    "entry":"Unchanged durable decision then later observation>60seconds; buy ask, sell bid; no same-snapshot fill",
    "per_entry_maximum_debit":"125.00", "monthly_maximum_debit":"500.00", "fee_per_contract_per_side":"0.65",
    "risk":"One shared500 research allowance includes premium and both fees; no profit recycling; hold missing exits and unresolved month boundaries",
    "exit":"Unchanged5x entry-ask target, half-entry-ask observation and precommitted earliest tenth-following/preceding-expiry/penultimate-month disposal",
    "selector":"Registered order rotates after a fill; no performance ranking or automatic owner-account adoption",
    "comparison":"Each individual candidate has an independent counterfactual500 account, compared with ONE shared500 research portfolio; never sum budgets or returns",
    "evidence":"Known2022 results are exploratory/in-sample. A newly fixed historical window is a retrospective holdout, not prospective or profitable execution qualification",
    "data":"Only supplied actual historical contract bid/ask rows; synthetic fixtures remain identified test evidence; no theoretical prices or fabricatedIV",
    "diagnostics":"Counts are per flat-session signal and inspected strategy quote; quote rejection uses one primary reason in fixed gate order, not trade probabilities",
    "sources":["https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf",
        "https://www.optionseducation.org/referencelibrary/faq/option-price-behavior",
        "https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf"],
}
RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()
_REASONS = ("dte_outside_7_21", "expiry_outside_current_month", "outside_0_5_percent_otm",
    "entry_above_125", "no_future_disposal_session", "unexecutable_bid_ask")
_STATE_KEYS = {"schema","kind","research_only","rule_sha256","symbol","started_at",
    "last_decided_at","last_observed_at","processed_count","prefix_sha256","month","cash",
    "contributions","monthly_start_equity","monthly_committed","fees","trade_count","closed_trade_count",
    "position","pending","hold","cap_qualified","last_daily_result","milestone_counts","history",
    "enabled_strategies","next_strategy","strategy_stats","eligibility"}
_POSITION_KEYS = {"strategy_id","contract_id","type","expiry","strike","multiplier","quantity",
    "entry_at","decision_at","entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit",
    "marked_bid","last_marked_at","forced_exit_session"}
_PENDING_KEYS = {"strategy_id","type","action","contract_id","expiry","strike","decided_at","signal_at","signal_session","reasons"}


def _hash(value):
    return type(value) is str and len(value) == 64 and all(item in "0123456789abcdef" for item in value)


def _count(value):
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise OptionsRecoveryError("Research diagnostics require bounded integer counts.")


def _milestones(value, closed):
    if type(value) is not dict or set(value) != {"2x","5x","10x"} or any(type(item) is not int or not 0 <= item <= closed for item in value.values()) or not value["2x"] >= value["5x"] >= value["10x"]:
        raise OptionsRecoveryError("Research realized payout counts are inconsistent.")


def _strategies(value=None):
    value = list(STRATEGY_IDS) if value is None else value
    if type(value) is not list or not value or any(type(item) is not str or item not in STRATEGIES for item in value) or len(set(value)) != len(value):
        raise OptionsRecoveryError("Choose unique registered research hypotheses.")
    return [item for item in STRATEGY_IDS if item in value]


def _stats():
    return {"entry_count":0,"closed_count":0,"fees":"0","realized_pnl":"0","monthly_committed":"0","milestone_counts":{"2x":0,"5x":0,"10x":0}}


def _diagnostics():
    return {"warmup_sessions":0,"flat_sessions":0,"busy_sessions":0,"entry_cancellations":{},
        "strategies":{key:{"signals":0,"inspected_sessions":0,"matching_type_quotes":0,"eligible_quotes":0,
            "no_type_quote_sessions":0,"no_eligible_quote_sessions":0,"ranked_quote_budget_blocked":0,
            "not_selected_priority":0,"quote_rejections":{reason:0 for reason in _REASONS}} for key in STRATEGY_IDS}}


def _initial(row, symbol, strategies):
    clock = _clock(row["available_at"])
    return {"schema":1,"kind":"regime_options_research_ledger","research_only":True,"rule_sha256":RULE_SHA256,
        "symbol":symbol,"started_at":clock.isoformat(),"last_decided_at":clock.isoformat(),"last_observed_at":None,
        "processed_count":0,"prefix_sha256":hashlib.sha256(b"[]").hexdigest(),"month":_day_at(clock).strftime("%Y-%m"),
        "cash":"500","contributions":"500","monthly_start_equity":"500","monthly_committed":"0","fees":"0",
        "trade_count":0,"closed_trade_count":0,"position":None,"pending":None,"hold":None,"cap_qualified":True,
        "last_daily_result":None,"milestone_counts":{"2x":0,"5x":0,"10x":0},"history":[],
        "enabled_strategies":_strategies(strategies),"next_strategy":0,
        "strategy_stats":{key:_stats() for key in STRATEGY_IDS},"eligibility":_diagnostics()}


@_precision
def signal(identifier, history):
    """Registered close-only signal; each prior window excludes today's close."""
    if identifier not in STRATEGIES:
        raise OptionsRecoveryError("Unknown registered research signal.")
    if type(history) is not list or len(history) > 10000 or any(type(item) is not dict or "underlying_price" not in item for item in history):
        raise OptionsRecoveryError("Use bounded observed-close research history.")
    if len(history) < 51:
        return False
    values = [_number(item["underlying_price"]) for item in history[-51:]]
    current, before = values[-1], values[:-1]
    mean20 = sum(before[-20:], Decimal(0))/20
    mean50 = sum(before, Decimal(0))/50
    if identifier == "trend_breakdown_put_v1":
        return current < min(before[-20:]) and current < mean50 and mean20 < mean50
    bull = current > mean50 and mean20 > mean50
    return bull and (_recovery(history[-15:]) if identifier == "regime_recovery_call_v1" else current > max(before[-20:]))


def _quote_rejection(quote, row, day, kind):
    expiry = date.fromisoformat(quote["expiry"])
    dte = (expiry-day).days
    if not 7 <= dte <= 21:
        return "dte_outside_7_21"
    if expiry.strftime("%Y-%m") != day.strftime("%Y-%m"):
        return "expiry_outside_current_month"
    spot, strike = _number(row["underlying_price"]), _number(quote["strike"])
    if not (spot <= strike <= spot*Decimal("1.05") if kind == "call" else spot*Decimal("0.95") <= strike <= spot):
        return "outside_0_5_percent_otm"
    if _debit(_number(quote["ask"])*100)+Decimal("1.30") > 125:
        return "entry_above_125"
    if _deadline(day, expiry) <= day:
        return "no_future_disposal_session"
    if not _quote_ok(quote, side="BUY", forward=False):
        return "unexecutable_bid_ask"
    if not _risk_eligible(quote, row, day, kind, forward=False):
        raise OptionsRecoveryError("Research quote gates disagree with the frozen execution reference.")
    return None


def _held(state, code, message, clock):
    state = deepcopy(state)
    state["hold"] = {"code":code,"message":message,"observed_at":clock.isoformat()}
    state["cap_qualified"] = False
    return {"status":"held","state":state,"events":[{"type":"held",**state["hold"]}]}


def _cancel(state, events, reason):
    events.append({"type":"cancelled","reason":reason,"decision":deepcopy(state["pending"])})
    counts = state["eligibility"]["entry_cancellations"]
    counts[reason] = counts.get(reason, 0)+1
    state["pending"] = None


def _select(state, row, day, clock, events):
    diagnostics = state["eligibility"]
    diagnostics["flat_sessions"] += 1
    signaled = {key for key in state["enabled_strategies"] if signal(key, state["history"])}
    for key in signaled:
        diagnostics["strategies"][key]["signals"] += 1
    order = STRATEGY_IDS[state["next_strategy"]:]+STRATEGY_IDS[:state["next_strategy"]]
    selected = None
    for identifier in order:
        if identifier not in signaled:
            continue
        stats = diagnostics["strategies"][identifier]
        if selected is not None:
            stats["not_selected_priority"] += 1
            continue
        stats["inspected_sessions"] += 1
        quotes = [quote for quote in row["quotes"] if quote["type"] == STRATEGIES[identifier]["type"]]
        stats["matching_type_quotes"] += len(quotes)
        if not quotes:
            stats["no_type_quote_sessions"] += 1
        candidates = []
        for quote in quotes:
            reason = _quote_rejection(quote, row, day, STRATEGIES[identifier]["type"])
            if reason:
                stats["quote_rejections"][reason] += 1
            else:
                candidates.append(quote)
        stats["eligible_quotes"] += len(candidates)
        if not candidates:
            stats["no_eligible_quote_sessions"] += 1
            continue
        spot = _number(row["underlying_price"])
        candidates.sort(key=lambda quote:(abs((date.fromisoformat(quote["expiry"])-day).days-14),abs(_number(quote["strike"])-spot),quote["contract_id"]))
        quote = candidates[0]
        reserve = _debit(_number(quote["ask"])*100)+Decimal("1.30")
        if reserve+_number(state["monthly_committed"]) > 500 or reserve > _number(state["cash"]):
            stats["ranked_quote_budget_blocked"] += 1
            continue
        selected = identifier
        state["pending"] = _execution_pending("BUY", quote, row, clock, [identifier+"_registered_signal"], identifier)
        events.append({"type":"decision",**deepcopy(state["pending"])})


def _step(state, row):
    prior, events = deepcopy(state), []
    clock, observed = _clock(row["available_at"]), _clock(row["observed_at"])
    day = _day_at(observed)
    month = day.strftime("%Y-%m")
    if month < state["month"]:
        return _held(prior,"backwards_month","A quote predates the funded research month.",clock)
    if month != state["month"]:
        if state["position"] is not None:
            return _held(prior,"unresolved_month_boundary","Unresolved exposure blocks new research funding or allowance.",clock)
        if state["pending"]:
            _cancel(state,events,"month_boundary")
        state.update(month=month,cash=_text(_number(state["cash"])+500),contributions=_text(_number(state["contributions"])+500),monthly_committed="0")
        state["monthly_start_equity"] = state["cash"]
        for stats in state["strategy_stats"].values():
            stats["monthly_committed"] = "0"
        events.append({"type":"contribution","month":month,"amount":"500","simulated":True})
    quotes = {quote["contract_id"]:quote for quote in row["quotes"]}
    position = state["position"]
    if position is not None:
        deadline = date.fromisoformat(position["forced_exit_session"])
        if day > deadline:
            return _held(prior,"missed_disposal_deadline","No verified sale before precommitted disposal; no backfill.",clock)
        quote = quotes.get(position["contract_id"])
        if quote is None:
            return _held(prior,"missing_position_quote","Held contract is absent; retain the last dated mark.",clock)
        position.update(marked_bid=quote["bid"],last_marked_at=row["observed_at"])
        pending, due = state["pending"], day == deadline
        eligible_time = observed > _clock(position["decision_at"])+timedelta(seconds=60) if due else pending is not None and observed > _clock(pending["decided_at"])+timedelta(seconds=60)
        if (due or pending is not None) and eligible_time:
            if _quote_ok(quote,side="SELL",forward=False):
                _execution_sell(state,quote,row,["precommitted_disposal_deadline"] if due else pending["reasons"],events)
            elif due:
                return _held(prior,"unexecutable_disposal","No executable bid at mandatory disposal; no invented exit or reset.",clock)
        if state["position"] is not None and state["pending"] is None:
            bid, entry = _number(quote["bid"]), _number(position["entry_ask"])
            reasons = ["bid_reached_5x_entry_ask"] if bid >= entry*5 else ["bid_at_or_below_half_entry_ask"] if bid <= entry/2 else []
            if reasons:
                state["pending"] = _execution_pending("SELL",quote,row,clock,reasons,position["strategy_id"])
                events.append({"type":"decision",**deepcopy(state["pending"])})
    elif state["pending"] is not None:
        pending = state["pending"]
        quote = quotes.get(pending["contract_id"])
        expected = _sessions(date.fromisoformat(pending["signal_session"])+timedelta(days=1),day)
        if len(expected) != 1 or observed <= _clock(pending["decided_at"])+timedelta(seconds=60):
            _cancel(state,events,"late_or_unavailable_entry")
        elif quote is None or not _risk_eligible(quote,row,day,pending["type"],forward=False):
            _cancel(state,events,"entry_quote_no_longer_eligible")
        else:
            premium = _debit(_number(quote["ask"])*100)
            reserve = premium+Decimal("1.30")
            identifier = pending["strategy_id"]
            if reserve+_number(state["monthly_committed"]) > 500 or reserve > _number(state["cash"]):
                _cancel(state,events,"shared_monthly_or_cash_budget")
            else:
                state.update(cash=_text(_number(state["cash"])-premium-Decimal("0.65")),monthly_committed=_text(_number(state["monthly_committed"])+reserve),fees=_text(_number(state["fees"])+Decimal("0.65")))
                state["trade_count"] += 1
                stats = state["strategy_stats"][identifier]
                stats.update(entry_count=stats["entry_count"]+1,fees=_text(_number(stats["fees"])+Decimal("0.65")),monthly_committed=_text(_number(stats["monthly_committed"])+reserve))
                state["next_strategy"] = (STRATEGY_IDS.index(identifier)+1)%len(STRATEGY_IDS)
                state["position"] = {"strategy_id":identifier,"contract_id":quote["contract_id"],"type":quote["type"],"expiry":quote["expiry"],"strike":quote["strike"],"multiplier":100,"quantity":1,
                    "entry_at":row["observed_at"],"decision_at":pending["decided_at"],"entry_ask":quote["ask"],"premium":_text(premium),"entry_fee":"0.65","exit_fee_reserved":"0.65","budget_debit":_text(reserve),
                    "marked_bid":quote["bid"],"last_marked_at":row["observed_at"],"forced_exit_session":_deadline(day,date.fromisoformat(quote["expiry"])).isoformat()}
                events.append({"type":"fill","action":"BUY","strategy_id":identifier,"option_type":quote["type"],"observed_at":row["observed_at"],"available_at":row["available_at"],"decided_at":pending["decided_at"],
                    "contract_id":quote["contract_id"],"quantity":1,"price":quote["ask"],"premium":_text(premium),"fee":"0.65","exit_fee_reserved":"0.65","budget_committed":_text(reserve),"simulated":True})
                state["pending"] = None
    state["history"] = (state["history"]+[{"observed_at":row["observed_at"],"underlying_price":row["underlying_price"]}])[-51:]
    if len(state["history"]) < 51:
        state["eligibility"]["warmup_sessions"] += 1
    if state["position"] is None and state["pending"] is None:
        _select(state,row,day,clock,events)
    else:
        state["eligibility"]["busy_sessions"] += 1
    equity = _execution_equity(state)
    previous = state["last_daily_result"]
    prior_equity = _number(previous["equity"]) if previous and previous["month"] == month else _number(state["monthly_start_equity"])
    state["last_daily_result"] = {"date":day.isoformat(),"month":month,"observed_at":row["observed_at"],"decided_at":clock.isoformat(),"equity":_text(equity),"cash":state["cash"],"contributions":state["contributions"],
        "daily_pnl":_text(equity-prior_equity),"trading_pnl":_text(equity-_number(state["contributions"])),"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),"monthly_budget_used":state["monthly_committed"],
        "monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),"fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"cap_qualified":state["cap_qualified"],"simulated":True}
    if equity < _number(state["monthly_start_equity"])-500:
        raise OptionsRecoveryError("Shared research monthly loss invariant failed.")
    state.update(last_decided_at=clock.isoformat(),last_observed_at=row["observed_at"])
    events.append({"type":"paper_daily_result",**deepcopy(state["last_daily_result"])})
    return {"status":"completed","state":state,"events":events}


@_precision
def validate_state(state):
    """Validate a research output; it can never qualify as an owner ledger."""
    if type(state) is not dict or set(state) != _STATE_KEYS or type(state["schema"]) is not int or state["schema"] != 1 or state["kind"] != "regime_options_research_ledger" or state["research_only"] is not True or state["rule_sha256"] != RULE_SHA256 or state["symbol"] not in RULE["universe"]:
        raise OptionsRecoveryError("The state is not a registered research-only ledger.")
    if state["enabled_strategies"] != _strategies(state["enabled_strategies"]):
        raise OptionsRecoveryError("The research selector order changed.")
    if type(state["next_strategy"]) is not int or not 0 <= state["next_strategy"] < len(STRATEGY_IDS):
        raise OptionsRecoveryError("Invalid research rotation cursor.")
    for key in ("processed_count","trade_count","closed_trade_count"):
        if type(state[key]) is not int or not 0 <= state[key] <= 10000:
            raise OptionsRecoveryError("Invalid research ledger count.")
    if state["trade_count"]-state["closed_trade_count"] not in (0,1) or (state["position"] is None) != (state["trade_count"] == state["closed_trade_count"]):
        raise OptionsRecoveryError("Only one shared funded research position is permitted.")
    for key in ("cash","contributions","monthly_start_equity","monthly_committed","fees"):
        _number(state[key])
    if _number(state["contributions"]) < 500 or _number(state["contributions"])%500 or _number(state["monthly_committed"]) > 500 or _number(state["fees"]) != Decimal("0.65")*(state["trade_count"]+state["closed_trade_count"]):
        raise OptionsRecoveryError("Research funding, fees or shared monthly cap is inconsistent.")
    if type(state["strategy_stats"]) is not dict or set(state["strategy_stats"]) != set(STRATEGY_IDS):
        raise OptionsRecoveryError("Unknown strategy attribution.")
    stats = state["strategy_stats"]
    for identifier, item in stats.items():
        if type(item) is not dict or set(item) != set(_stats()) or any(type(item[key]) is not int or not 0 <= item[key] <= 10000 for key in ("entry_count","closed_count")) or not 0 <= item["entry_count"]-item["closed_count"] <= 1:
            raise OptionsRecoveryError("Malformed strategy accounting.")
        if identifier not in state["enabled_strategies"] and item != _stats():
            raise OptionsRecoveryError("A disabled research strategy owns activity.")
        _number(item["realized_pnl"],signed=True)
        if _number(item["fees"]) != Decimal("0.65")*(item["entry_count"]+item["closed_count"]):
            raise OptionsRecoveryError("Strategy fees differ from exact filled legs.")
        _number(item["monthly_committed"])
        _milestones(item["milestone_counts"],item["closed_count"])
    if sum(item["entry_count"] for item in stats.values()) != state["trade_count"] or sum(item["closed_count"] for item in stats.values()) != state["closed_trade_count"] or sum((_number(item["fees"]) for item in stats.values()),Decimal(0)) != _number(state["fees"]) or sum((_number(item["monthly_committed"]) for item in stats.values()),Decimal(0)) != _number(state["monthly_committed"]):
        raise OptionsRecoveryError("Strategy attribution does not reconcile with the shared ledger.")
    _milestones(state["milestone_counts"],state["closed_trade_count"])
    if any(sum(item["milestone_counts"][key] for item in stats.values()) != state["milestone_counts"][key] for key in ("2x","5x","10x")):
        raise OptionsRecoveryError("Strategy payout counts do not reconcile.")
    try:
        valid_month = type(state["month"]) is str and date.fromisoformat(state["month"]+"-01").strftime("%Y-%m") == state["month"]
    except ValueError:
        valid_month = False
    if not valid_month or not _hash(state["prefix_sha256"]):
        raise OptionsRecoveryError("Research month or accepted-source hash is invalid.")
    history = state["history"]
    if type(history) is not list or len(history) != min(state["processed_count"],51):
        raise OptionsRecoveryError("Research warmup length disagrees with processed history.")
    for index, item in enumerate(history):
        if type(item) is not dict or set(item) != {"observed_at","underlying_price"} or _number(item["underlying_price"]) <= 0 or index and _clock(item["observed_at"]) <= _clock(history[index-1]["observed_at"]):
            raise OptionsRecoveryError("Malformed research close history.")
    if (history[-1]["observed_at"] if history else None) != state["last_observed_at"] or _clock(state["last_decided_at"]) < _clock(state["started_at"]):
        raise OptionsRecoveryError("Research observation/decision clocks disagree.")
    if type(state["cap_qualified"]) is not bool or bool(state["hold"]) == state["cap_qualified"]:
        raise OptionsRecoveryError("Research hold and cap assurance disagree.")
    if state["hold"] is not None and (type(state["hold"]) is not dict or set(state["hold"]) != {"code","message","observed_at"}):
        raise OptionsRecoveryError("Malformed research hold.")
    if state["position"] is not None:
        position = state["position"]
        if type(position) is not dict or set(position) != _POSITION_KEYS or position["strategy_id"] not in state["enabled_strategies"] or position["type"] != STRATEGIES[position["strategy_id"]]["type"] or type(position["quantity"]) is not int or position["quantity"] != 1 or type(position["multiplier"]) is not int or position["multiplier"] != 100:
            raise OptionsRecoveryError("Research position violates its registered instrument or entry cap.")
        from .options_data import contract_id
        entry, decision = _clock(position["entry_at"]), _clock(position["decision_at"])
        expiry = date.fromisoformat(position["expiry"])
        premium = _debit(_number(position["entry_ask"])*100)
        if (position["contract_id"] != contract_id(state["symbol"],position["expiry"],position["type"],position["strike"]) or entry <= decision+timedelta(seconds=60) or decision < _clock(state["started_at"]) or
            not entry <= _clock(position["last_marked_at"]) <= _clock(state["last_observed_at"]) or _day_at(entry).strftime("%Y-%m") != state["month"] or
            position["forced_exit_session"] != _deadline(_day_at(entry),expiry).isoformat() or _number(position["premium"]) != premium or _number(position["entry_fee"]) != Decimal("0.65") or
            _number(position["exit_fee_reserved"]) != Decimal("0.65") or _number(position["budget_debit"]) != premium+Decimal("1.30") or not premium+Decimal("1.30") <= _number(state["monthly_committed"]) <= 500 or premium+Decimal("1.30") > 125):
            raise OptionsRecoveryError("Research position provenance, exact premium or disposal changed.")
        _number(position["marked_bid"])
    expected_realized = _number(state["cash"])-_number(state["contributions"])
    if state["position"] is not None:
        expected_realized += _number(state["position"]["premium"])+Decimal("0.65")
    if sum((_number(item["realized_pnl"],signed=True) for item in stats.values()),Decimal(0)) != expected_realized:
        raise OptionsRecoveryError("Cash and attributed realized P/L do not reconcile.")
    if state["pending"] is not None:
        pending = state["pending"]
        if type(pending) is not dict or set(pending) != _PENDING_KEYS or pending["strategy_id"] not in state["enabled_strategies"] or pending["type"] != STRATEGIES[pending["strategy_id"]]["type"] or pending["action"] != ("SELL" if state["position"] else "BUY"):
            raise OptionsRecoveryError("Invalid research pending decision.")
        from .options_data import contract_id
        if pending["contract_id"] != contract_id(state["symbol"],pending["expiry"],pending["type"],pending["strike"]) or _clock(pending["signal_at"]) > _clock(pending["decided_at"]) or _clock(pending["decided_at"]) > _clock(state["last_decided_at"]):
            raise OptionsRecoveryError("Research decision identity or availability changed.")
        if state["position"] is not None and any(pending[key] != state["position"][key] for key in ("strategy_id","contract_id","type","expiry","strike")):
            raise OptionsRecoveryError("A pending research sale names a different position.")
    diagnostics = state["eligibility"]
    if type(diagnostics) is not dict or set(diagnostics) != set(_diagnostics()) or type(diagnostics["strategies"]) is not dict or set(diagnostics["strategies"]) != set(STRATEGY_IDS) or type(diagnostics["entry_cancellations"]) is not dict:
        raise OptionsRecoveryError("Malformed research eligibility evidence.")
    for key in ("warmup_sessions","flat_sessions","busy_sessions"):
        _count(diagnostics[key])
    for item in diagnostics["entry_cancellations"].values():
        _count(item)
    expected_diagnostic = _diagnostics()["strategies"][STRATEGY_IDS[0]]
    for item in diagnostics["strategies"].values():
        if type(item) is not dict or set(item) != set(expected_diagnostic) or type(item["quote_rejections"]) is not dict or set(item["quote_rejections"]) != set(_REASONS):
            raise OptionsRecoveryError("Malformed primary quote rejection counts.")
        for key,value in item.items():
            if key != "quote_rejections":
                _count(value)
        for value in item["quote_rejections"].values():
            _count(value)
    _canonical(state)
    return deepcopy(state)


@_precision
def summarize(state):
    state = validate_state(state)
    equity = _execution_equity(state)
    return {"research_only":True,"rule_sha256":RULE_SHA256,"symbol":state["symbol"],"simulated":True,
        "cash":state["cash"],"equity":_text(equity),"contributions":state["contributions"],"trading_pnl":_text(equity-_number(state["contributions"])),
        "month":state["month"],"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),"monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),
        **{key:deepcopy(state[key]) for key in ("fees","trade_count","closed_trade_count","position","pending","hold","cap_qualified","last_daily_result","milestone_counts","strategy_stats","eligibility")}}


def _report(state, events, curve, coverage, qualities):
    summary = summarize(state)
    peak, drawdown = Decimal(0), Decimal(0)
    for row in curve:
        pnl = _number(row["trading_pnl"],signed=True)
        peak = max(peak,pnl)
        drawdown = max(drawdown,peak-pnl)
    metrics = {"trading_pnl":summary["trading_pnl"],"total_contributions":summary["contributions"],"ending_equity":summary["equity"],"trade_count":summary["trade_count"],"closed_trade_count":summary["closed_trade_count"],"fees":summary["fees"],
        "max_drawdown":_text(drawdown),"maximum_monthly_loss":_text(max((max(Decimal(0),-_number(row["monthly_pnl"],signed=True)) for row in curve),default=Decimal(0))),"payout_milestones":deepcopy(state["milestone_counts"]),
        "max_drawdown_basis":"Cashflow-adjusted dollar P/L drawdown, not annualized/time-weighted return","payout_basis":"Realized net exit cash divided by entry premium plus entry fee; observations are not fills"}
    all_input = len(curve) == coverage["source_snapshots"] and not state["hold"]
    flat = state["position"] is None and state["pending"] is None
    covered = {**coverage,"processed_snapshots":len(curve),"last_processed_date":curve[-1]["date"] if curve else None,"all_input_processed":all_input,"finished_flat":flat,"complete":all_input and flat}
    gate = "held_incomplete_evidence" if state["hold"] else "insufficient_history" if coverage["source_snapshots"] < 51 else "open_at_input_end" if not flat else "no_completed_trades" if not state["closed_trade_count"] else "exploratory_not_proven"
    if coverage["evidence_kind"] == "synthetic_fixture" and not state["hold"]:
        gate = "synthetic_fixture_only"
    monthly = []
    for row in curve:
        if monthly and monthly[-1]["month"] == row["month"]:
            monthly[-1] = deepcopy(row)
        else:
            monthly.append(deepcopy(row))
    return {"schema":1,"kind":"registered_regime_options_research","research_only":True,"rule_sha256":RULE_SHA256,"symbol":state["symbol"],"enabled_strategies":list(state["enabled_strategies"]),"research_gate":gate,"execution_qualified":False,
        "coverage":covered,"metrics":metrics,"summary":summary,"state":deepcopy(state),"curve":curve,"events":events,"monthly_rows":monthly,"eligibility":deepcopy(state["eligibility"]),"data_quality":qualities,
        "limitations":["These are research-only hypotheses, not changes to or additional funding for the owner paper portfolio.",
            "Known2022 results are exploratory/in-sample; an untouched historical window remains retrospective, not prospective evidence.",
            "Underlying trend research does not establish profitable short-DTE options; theta, implied volatility, spreads and costs can defeat the direction.",
            "Actual supplied contract quotes are required. Source timestamps, sizes, corporate actions and market authenticity remain explicit assumptions.",
            "One shared500 research portfolio is compared with independent counterfactual500 ledgers; never sum their returns or select a winner automatically.",
            "Held ledgers stop at the last supported mark; no fictional exit, funding reset or completed full-window return."]}


@_precision
def _backtests(datasets, *, symbol, modes):
    if symbol not in RULE["universe"]:
        raise OptionsRecoveryError("Choose a registered options research symbol.")
    datasets = [datasets] if type(datasets) is dict else datasets
    ledgers, qualities = {}, []
    events, curves = {key:[] for key in modes}, {key:[] for key in modes}
    source_rows, first, last, previous, previous_available, basis = 0, None, None, None, None, None
    hasher = hashlib.sha256(b"[")
    for chunk_count, dataset in enumerate(datasets,1):
        if chunk_count > 96:
            raise OptionsRecoveryError("The research archive exceeds96 chunks.")
        rows, quality = _rows(dataset,symbol)
        current_basis = (dataset["evidence_kind"],dataset["source"]["quote_time_basis"],dataset["source"]["provider"])
        if basis is not None and basis != current_basis:
            raise OptionsRecoveryError("Research source/evidence/timing basis changed between chunks.")
        basis = current_basis
        qualities.append({**quality,"dataset_sha256":hashlib.sha256(_canonical(dataset)).hexdigest(),"source_sha256":dataset["source"]["sha256"]})
        for row in rows:
            observed, available = _clock(row["observed_at"]), _clock(row["available_at"])
            day = _day_at(observed)
            if previous is not None and (day <= previous or available <= previous_available):
                raise OptionsRecoveryError("Chunks overlap or observation/publication clocks move backwards.")
            source_rows += 1
            if source_rows > 10000:
                raise OptionsRecoveryError("The research archive exceeds10000 daily observations.")
            first, last = first or day.isoformat(), day.isoformat()
            gap = previous is not None and len(_sessions(previous+timedelta(days=1),day)) != 1
            if not ledgers:
                ledgers = {key:_initial(row,symbol,value) for key,value in modes.items()}
            if source_rows > 1:
                hasher.update(b",")
            hasher.update(_canonical(row))
            cursor = hasher.copy()
            cursor.update(b"]")
            for key, state in list(ledgers.items()):
                if state["hold"]:
                    continue
                result = _held(state,"historical_session_gap","An expected source session is missing; do not assume unknown fills.",available) if gap else _step(state,row)
                ledgers[key] = result["state"]
                events[key].extend(result["events"])
                if result["status"] == "completed":
                    ledgers[key].update(processed_count=source_rows,prefix_sha256=cursor.hexdigest())
                    curves[key].append(deepcopy(ledgers[key]["last_daily_result"]))
            previous, previous_available = day, available
    if not ledgers:
        raise OptionsRecoveryError("No supported historical option observations were supplied.")
    coverage = {"symbol":symbol,"source_snapshots":source_rows,"chunks":len(qualities),"first_date":first,"last_source_date":last,"quote_time_basis":basis[1],"evidence_kind":basis[0],"warmup_required":51}
    return {key:_report(state,events[key],curves[key],coverage,qualities) for key,state in ledgers.items()}


def backtest(dataset_or_iterable, *, symbol="QQQ", strategies=None):
    return _backtests(dataset_or_iterable,symbol=symbol,modes={"portfolio":_strategies(strategies)})["portfolio"]


def backtest_comparison(dataset_or_iterable, *, symbol="QQQ"):
    reports = _backtests(dataset_or_iterable,symbol=symbol,modes={"portfolio":list(STRATEGY_IDS),**{key:[key] for key in STRATEGY_IDS}})
    portfolio = reports.pop("portfolio")
    return {"schema":1,"kind":"registered_regime_strategy_comparison","research_only":True,"rule":deepcopy(RULE),"rule_sha256":RULE_SHA256,"symbol":symbol,
        "research_gate":portfolio["research_gate"],"execution_qualified":False,"metrics":deepcopy(portfolio["metrics"]),"coverage":deepcopy(portfolio["coverage"]),"eligibility":deepcopy(portfolio["eligibility"]),
        "portfolio":portfolio,"strategy_results":reports,"all_ledgers_complete":all(item["coverage"]["complete"] for item in (portfolio,*reports.values())),
        "comparison_scope":"ONE shared500 research portfolio versus separate counterfactual500 accounts; not owner paper funds, never summed or automatically adopted."}
