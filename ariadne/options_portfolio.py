"""Registered long-option hypotheses sharing one cash and monthly-risk ledger.

The frozen 0.22 recovery engine remains unchanged. This engine adds directional
put/call hypotheses, attribution and a deterministic selector. It cannot submit
orders, acquire data, reset an old ledger, or make unsupported quotes executable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib

from .options_recovery import (OptionsRecoveryError, _canonical, _clock, _number,
    _text, _debit, _credit, _precision, _day_at, _sessions, _rows, _prefix,
    _history, _deadline, _quote_ok, _recovery)

STRATEGIES = {
    "recovery_call_v1": {"type":"call", "signal":"Above the preceding two observations after a 3% closing decline from the preceding ten-observation high in the current/prior four observations", "warmup":14},
    "breakout_call_v1": {"type":"call", "signal":"Above the preceding ten-observation closing high and preceding ten-observation arithmetic mean", "warmup":14},
    "breakdown_put_v1": {"type":"put", "signal":"Below the preceding ten-observation closing low and preceding ten-observation arithmetic mean", "warmup":14},
}
STRATEGY_IDS = tuple(STRATEGIES)
RULE = {
    "id":"aggressive-options-shared-portfolio-v1", "schema":2,
    "universe":["QQQ","SPY","AAPL"], "default_symbol":"QQQ",
    "strategies":STRATEGIES,
    "instrument":"One fully paid standard 100-share long call OR put at a time across the entire portfolio; no short legs, spreads, exercise or real orders",
    "selector":"Registered recovery-call, breakout-call, breakdown-put order rotates after each filled entry. Select first signaled strategy with an eligible quote. Never rank by backtest profit.",
    "selection":"7-21 calendar DTE; expiry in current calendar month; strike from spot to 5% above for calls or spot to 5% below for puts; nearest14DTE then closest strike to spot then contract ID",
    "entry":"One contract at next regular-session snapshot observed more than60seconds after durable decision; buy ask and sell bid",
    "liquidity":"Positive uncrossed bid/ask, spread<=25%midpoint; forward executable-side quoted size>=1; missing historical size remains an explicit assumption",
    "per_entry_maximum_debit":"125.00", "monthly_maximum_debit":"500.00", "fee_per_contract_per_side":"0.65",
    "monthly_cap":"ONE shared500 allowance across all strategies, premium plus entry fee and reserved exit fee; no profit recycling, verified flat month boundary; cap is modeled net loss excluding deposits, not a trailing drawdown limit",
    "exit":"Observe bid>=5x entry ask or bid<=0.5x entry ask; execute only at later eligible bid. Precommit mandatory disposal by earliest tenth following regular session, preceding expiry session, penultimate month session",
    "failure":"Missing contract, stale observations or absent mandatory disposal bid hold the entire shared portfolio; no fictional sale, expiry settlement or new-month reset",
    "funding":"500 simulated USD initially; add500 at an observed later month only after verified flat prior month; deposits are excluded from P&L",
    "migration":"Only pristine zero-trade flat legacy accounts can upgrade in place; preserve original cash, contributions, started_at and month; no fresh funding or allowance, actual portfolio activation cutoff is separate",
    "comparison":"Three separate counterfactual500-budget research ledgers and ONE actual shared500-budget portfolio use the same source sessions. Never sum counterfactual returns or select the winner for automatic trading",
    "registration":"Fixed before inspecting expanded historical outcomes; no parameter search or profitable execution qualification",
    "payout_milestones":[2,5,10], "taxes":"not modeled", "cash_interest":"0",
}
RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()

_COMMON = {"schema","rule_sha256","symbol","started_at","last_decided_at","processed_count","prefix_sha256",
    "last_observed_at","month","cash","contributions","monthly_committed","monthly_start_equity","position","pending",
    "fees","trade_count","closed_trade_count","hold","cap_qualified","last_daily_result","milestone_counts","history"}
_STATE_KEYS = _COMMON | {"portfolio_activation_at","enabled_strategies","next_strategy","strategy_stats","migration"}
_POSITION = {"strategy_id","contract_id","type","expiry","strike","multiplier","quantity","entry_at","decision_at",
    "entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid","last_marked_at","forced_exit_session"}
_PENDING = {"strategy_id","type","action","contract_id","decided_at","signal_at","signal_session","expiry","strike","reasons"}
_DAILY = {"date","month","observed_at","decided_at","equity","cash","contributions","daily_pnl","trading_pnl","monthly_pnl",
    "monthly_budget_used","monthly_budget_remaining","fees","trade_count","closed_trade_count","cap_qualified","simulated"}
_STATS = {"entry_count","closed_count","fees","realized_pnl","monthly_committed","milestone_counts"}


def _strategies(value=None):
    value = list(STRATEGY_IDS) if value is None else value
    if type(value) is not list or not value or any(type(item) is not str or item not in STRATEGIES for item in value) or len(set(value))!=len(value):
        raise OptionsRecoveryError("Choose unique registered strategy IDs.")
    # A subset is for counterfactual research only; its order is never caller-
    # selected. Forward workflow always enables the complete registered set.
    return [item for item in STRATEGY_IDS if item in value]


def _stats():
    return {"entry_count":0,"closed_count":0,"fees":"0","realized_pnl":"0","monthly_committed":"0","milestone_counts":{"2x":0,"5x":0,"10x":0}}


@_precision
def initial_state(dataset=None, *, started_at, symbol="QQQ", strategies=None):
    clock = _clock(started_at)
    if symbol not in RULE["universe"]:
        raise OptionsRecoveryError("Choose a registered options symbol.")
    rows = [] if dataset is None else _rows(dataset,symbol)[0]
    if rows and (_clock(rows[-1]["available_at"])>clock or _clock(dataset["as_of"])>clock):
        raise OptionsRecoveryError("Activation cannot use future evidence.")
    result = {"schema":2,"rule_sha256":RULE_SHA256,"symbol":symbol,"started_at":clock.isoformat(),
        "portfolio_activation_at":clock.isoformat(),"last_decided_at":clock.isoformat(),
        "processed_count":len(rows),"prefix_sha256":_prefix(rows),"last_observed_at":rows[-1]["observed_at"] if rows else None,
        "month":_day_at(clock).strftime("%Y-%m"),"cash":"500","contributions":"500","monthly_committed":"0","monthly_start_equity":"500",
        "position":None,"pending":None,"fees":"0","trade_count":0,"closed_trade_count":0,"hold":None,"cap_qualified":True,
        "last_daily_result":None,"milestone_counts":{"2x":0,"5x":0,"10x":0},"history":_history(rows),
        "enabled_strategies":_strategies(strategies),"next_strategy":0,"strategy_stats":{key:_stats() for key in STRATEGY_IDS},"migration":None}
    validate_state(result)
    return result


@_precision
def migrate_flat_legacy(state, *, activated_at):
    """Pure conversion only; the workflow must retain the immutable old state.

    An old spent/observed/pending account cannot silently join a different rule.
    Even zero-risk migration keeps the old ledger identity/funding/month rather
    than manufacturing a new month's contribution or a second500allowance.
    """
    from .options_recovery import validate_state as legacy_validate
    old = legacy_validate(state)
    clock = _clock(activated_at)
    if clock<_clock(old["last_decided_at"]):
        raise OptionsRecoveryError("Migration cannot precede the old decision clock.")
    if (old["position"] is not None or old["pending"] is not None or old["hold"] is not None or not old["cap_qualified"] or
        old["trade_count"] or old["closed_trade_count"] or old["processed_count"] or old["history"] or old["last_observed_at"] is not None or old["last_daily_result"] is not None or
        any(_number(old[key])!=0 for key in ("monthly_committed","fees")) or
        any(_number(old[key])!=500 for key in ("cash","contributions","monthly_start_equity")) or any(old["milestone_counts"].values())):
        raise OptionsRecoveryError("Only a pristine unobserved flat legacy account may migrate; preserve any prior activity for review.")
    result = initial_state(None,started_at=old["started_at"],symbol=old["symbol"])
    for key in ("started_at","month","cash","contributions","monthly_committed","monthly_start_equity","fees","trade_count","closed_trade_count"):
        result[key] = old[key]
    result["portfolio_activation_at"] = clock.isoformat()
    result["last_decided_at"] = clock.isoformat()
    result["migration"] = {"from_rule_sha256":old["rule_sha256"],"legacy_state_sha256":hashlib.sha256(_canonical(old)).hexdigest(),"activated_at":clock.isoformat(),"new_funding":"0"}
    validate_state(result)
    return result


def _milestones(value, closed):
    if type(value) is not dict or set(value)!={"2x","5x","10x"} or any(type(v) is not int or not 0<=v<=closed for v in value.values()) or not value["2x"]>=value["5x"]>=value["10x"]:
        raise OptionsRecoveryError("Realized payout counts are invalid.")


def _hash(value):
    return type(value) is str and len(value)==64 and all(char in "0123456789abcdef" for char in value)


@_precision
def validate_state(state):
    if type(state) is not dict or set(state)!=_STATE_KEYS or type(state["schema"]) is not int or state["schema"]!=2 or state["rule_sha256"]!=RULE_SHA256 or state["symbol"] not in RULE["universe"]:
        raise OptionsRecoveryError("The portfolio does not match its registered schema/rule.")
    start, active, decided = (_clock(state[key]) for key in ("started_at","portfolio_activation_at","last_decided_at"))
    if not start<=active<=decided:
        raise OptionsRecoveryError("Portfolio activation/decision clocks moved backwards.")
    if state["enabled_strategies"]!=_strategies(state["enabled_strategies"]):
        raise OptionsRecoveryError("The registered selector order changed.")
    if type(state["next_strategy"]) is not int or not 0<=state["next_strategy"]<len(STRATEGY_IDS):
        raise OptionsRecoveryError("The rotation cursor is invalid.")
    for key in ("processed_count","trade_count","closed_trade_count"):
        if type(state[key]) is not int or not 0<=state[key]<=100000:
            raise OptionsRecoveryError("Use bounded portfolio counts.")
    if state["trade_count"]-state["closed_trade_count"] not in (0,1):
        raise OptionsRecoveryError("Only one shared funded position is allowed.")
    for key in ("cash","contributions","monthly_committed","monthly_start_equity","fees"):
        _number(state[key])
    if _number(state["fees"])!=Decimal("0.65")*(state["trade_count"]+state["closed_trade_count"]) or _number(state["contributions"])<500 or _number(state["contributions"])%500 or _number(state["monthly_committed"])>500:
        raise OptionsRecoveryError("The aggregate fee/funding/monthly-cap invariant failed.")
    try:
        if type(state["month"]) is not str or date.fromisoformat(state["month"]+"-01").strftime("%Y-%m")!=state["month"]:
            raise ValueError
    except (ValueError,TypeError):
        raise OptionsRecoveryError("Use an ISO portfolio month.") from None
    if not _hash(state["prefix_sha256"]):
        raise OptionsRecoveryError("The accepted quote-prefix identity is invalid.")
    if (state["last_observed_at"] is None)!=(state["processed_count"]==0):
        raise OptionsRecoveryError("The observation cursor disagrees with its count.")
    if state["last_observed_at"] is not None:
        _clock(state["last_observed_at"])
    history = state["history"]
    if type(history) is not list or len(history)!=min(state["processed_count"],15):
        raise OptionsRecoveryError("The warmup length disagrees with the observation cursor.")
    previous = None
    for item in history:
        if type(item) is not dict or set(item)!={"observed_at","underlying_price"} or _number(item["underlying_price"])<=0:
            raise OptionsRecoveryError("Malformed portfolio warmup.")
        stamp = _clock(item["observed_at"])
        if previous is not None and stamp<=previous:
            raise OptionsRecoveryError("Warmup observations must strictly advance.")
        previous = stamp
    if history and history[-1]["observed_at"]!=state["last_observed_at"]:
        raise OptionsRecoveryError("Warmup does not end at the accepted cursor.")
    if type(state["cap_qualified"]) is not bool:
        raise OptionsRecoveryError("Cap qualification must be explicit.")
    if state["hold"] is not None:
        held=state["hold"]
        if type(held) is not dict or set(held)!={"code","message","observed_at"} or any(type(v) is not str or not 1<=len(v)<=2000 for v in held.values()) or state["cap_qualified"]:
            raise OptionsRecoveryError("A hold cannot certify the cap.")
        _clock(held["observed_at"])
    _milestones(state["milestone_counts"],state["closed_trade_count"])
    stats=state["strategy_stats"]
    if type(stats) is not dict or set(stats)!=set(STRATEGY_IDS):
        raise OptionsRecoveryError("Every registered strategy needs immutable attribution.")
    for identifier,item in stats.items():
        if type(item) is not dict or set(item)!=_STATS or any(type(item[k]) is not int or not 0<=item[k]<=100000 for k in ("entry_count","closed_count")) or item["entry_count"]-item["closed_count"] not in (0,1):
            raise OptionsRecoveryError("Strategy trade attribution is invalid.")
        for key in ("fees","monthly_committed"):_number(item[key])
        _number(item["realized_pnl"],signed=True)
        if _number(item["fees"])!=Decimal("0.65")*(item["entry_count"]+item["closed_count"]) or _number(item["monthly_committed"])>500:
            raise OptionsRecoveryError("Strategy fee/risk attribution is invalid.")
        _milestones(item["milestone_counts"],item["closed_count"])
        if identifier not in state["enabled_strategies"] and any(item[key] for key in ("entry_count","closed_count")):
            raise OptionsRecoveryError("A disabled counterfactual strategy owns trades.")
    for total,part in (("trade_count","entry_count"),("closed_trade_count","closed_count")):
        if sum(item[part] for item in stats.values())!=state[total]:
            raise OptionsRecoveryError("Strategy counts do not reconcile with the shared portfolio.")
    for key in ("fees","monthly_committed"):
        if sum((_number(item[key]) for item in stats.values()),Decimal(0))!=_number(state[key]):
            raise OptionsRecoveryError("Strategy risk/fees do not reconcile with the ONE shared budget.")
    for key in ("2x","5x","10x"):
        if sum(item["milestone_counts"][key] for item in stats.values())!=state["milestone_counts"][key]:
            raise OptionsRecoveryError("Strategy payout attribution does not reconcile.")
    position=state["position"]
    if position is not None:
        if type(position) is not dict or set(position)!=_POSITION or position["strategy_id"] not in state["enabled_strategies"] or position["type"]!=STRATEGIES[position["strategy_id"]]["type"] or type(position["quantity"]) is not int or position["quantity"]!=1 or type(position["multiplier"]) is not int or position["multiplier"]!=100:
            raise OptionsRecoveryError("Only one registered fully paid standard long option is allowed.")
        for key in ("strike","entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid"):_number(position[key])
        if _number(position["strike"])<=0 or _number(position["entry_ask"])<=0 or position["entry_fee"]!="0.65" or position["exit_fee_reserved"]!="0.65" or _number(state["cash"])<Decimal("0.65"):
            raise OptionsRecoveryError("Position price/fee reserves are invalid.")
        if _number(position["premium"])!=_debit(_number(position["entry_ask"])*100) or _number(position["budget_debit"])!=_number(position["premium"])+Decimal("1.30") or _number(position["budget_debit"])>125 or _number(position["budget_debit"])>_number(stats[position["strategy_id"]]["monthly_committed"]):
            raise OptionsRecoveryError("The position exceeds its shared premium/fee commitment.")
        observed,decision=_clock(position["entry_at"]),_clock(position["decision_at"])
        if observed<=decision+timedelta(seconds=60) or decision<active or _clock(position["last_marked_at"])<observed:
            raise OptionsRecoveryError("A position borrowed a past or pre-activation fill.")
        expiry=date.fromisoformat(position["expiry"])
        deadline=date.fromisoformat(position["forced_exit_session"])
        day=_day_at(observed)
        if day.strftime("%Y-%m")!=state["month"] or expiry.strftime("%Y-%m")!=state["month"] or not 7<=(expiry-day).days<=21 or deadline!=_deadline(day,expiry) or not day<deadline<expiry:
            raise OptionsRecoveryError("Position crossed the funded month/disposal contract.")
        from .options_data import contract_id
        if position["contract_id"]!=contract_id(state["symbol"],position["expiry"],position["type"],position["strike"]):
            raise OptionsRecoveryError("The position OCC identity is inconsistent.")
    elif state["trade_count"]!=state["closed_trade_count"]:
        raise OptionsRecoveryError("A shared open trade is missing its position.")
    for identifier,item in stats.items():
        if item["entry_count"]-item["closed_count"]!=int(position is not None and position["strategy_id"]==identifier):
            raise OptionsRecoveryError("An attributed strategy secretly owns another position.")
    realized=sum((_number(item["realized_pnl"],signed=True) for item in stats.values()),Decimal(0))
    at_risk=_number(position["premium"])+Decimal("0.65") if position else Decimal(0)
    if _number(state["cash"])-_number(state["contributions"])!=realized-at_risk:
        raise OptionsRecoveryError("Cash, contributions and realized P&L do not reconcile.")
    pending=state["pending"]
    if pending is not None:
        if type(pending) is not dict or set(pending)!=_PENDING or pending["strategy_id"] not in state["enabled_strategies"] or pending["type"]!=STRATEGIES[pending["strategy_id"]]["type"] or pending["action"] not in ("BUY","SELL") or type(pending["reasons"]) is not list or not pending["reasons"] or any(type(x) is not str or not 1<=len(x)<=200 for x in pending["reasons"]):
            raise OptionsRecoveryError("The tagged pending instruction is invalid.")
        if (pending["action"]=="SELL")!=(position is not None) or not active<=_clock(pending["decided_at"])<=decided or _clock(pending["signal_at"])>_clock(pending["decided_at"]):
            raise OptionsRecoveryError("The pending instruction borrowed unavailable/pre-activation evidence.")
        from .options_data import contract_id
        if pending["contract_id"]!=contract_id(state["symbol"],pending["expiry"],pending["type"],pending["strike"]) or date.fromisoformat(pending["signal_session"])>_day_at(_clock(pending["signal_at"])):
            raise OptionsRecoveryError("Pending contract/session identity is inconsistent.")
        if position and any(pending[key]!=position[key] for key in ("strategy_id","type","contract_id","expiry","strike")):
            raise OptionsRecoveryError("A sell instruction names another funded position.")
    daily=state["last_daily_result"]
    if daily is not None:
        if type(daily) is not dict or set(daily)!=_DAILY or daily["simulated"] is not True or type(daily["cap_qualified"]) is not bool:
            raise OptionsRecoveryError("Daily portfolio evidence is invalid.")
        for key in ("equity","cash","contributions","monthly_budget_used","monthly_budget_remaining","fees"):_number(daily[key])
        for key in ("daily_pnl","trading_pnl","monthly_pnl"):_number(daily[key],signed=True)
        if daily["observed_at"]!=state["last_observed_at"] or daily["date"]!=_day_at(_clock(daily["observed_at"])).isoformat() or daily["month"]!=state["month"] or _clock(daily["decided_at"])>decided or any(type(daily[k]) is not int or daily[k]!=state[k] for k in ("trade_count","closed_trade_count")):
            raise OptionsRecoveryError("The saved daily result disagrees with its quote cursor.")
    migration=state["migration"]
    if migration is not None:
        from .options_recovery import RULE_SHA256 as old_rule
        if type(migration) is not dict or set(migration)!={"from_rule_sha256","legacy_state_sha256","activated_at","new_funding"} or migration["from_rule_sha256"]!=old_rule or not _hash(migration["legacy_state_sha256"]) or migration["new_funding"]!="0" or _clock(migration["activated_at"])!=active:
            raise OptionsRecoveryError("The immutable migration provenance is invalid.")
    _canonical(state)
    return deepcopy(state)


def _equity(state):
    position=state["position"]
    return _number(state["cash"])+(max(Decimal(0),_credit(_number(position["marked_bid"])*100)-Decimal("0.65")) if position else Decimal(0))


@_precision
def check_deadline(state,now):
    from .qqq_data import session_close_utc
    state=validate_state(state)
    clock=_clock(now.isoformat() if isinstance(now,datetime) else now)
    if clock<_clock(state["last_decided_at"]):
        raise OptionsRecoveryError("The assurance clock precedes a decision.")
    if state["position"] and not state["hold"]:
        if clock>=session_close_utc(date.fromisoformat(state["position"]["forced_exit_session"]))+timedelta(minutes=15):
            state["hold"]={"code":"unverified_disposal_deadline","message":"The shared portfolio disposal deadline passed without verified closing evidence; no sale or new allowance is invented.","observed_at":clock.isoformat()}
            state["cap_qualified"]=False
    return state


@_precision
def summarize(state,now=None):
    state=validate_state(state) if now is None else check_deadline(state,now)
    equity=_equity(state)
    return {"schema":2,"symbol":state["symbol"],"simulated":True,"cash":state["cash"],"equity":_text(equity),
        "contributions":state["contributions"],"trading_pnl":_text(equity-_number(state["contributions"])),"month":state["month"],
        "monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),"monthly_budget_used":state["monthly_committed"],
        "monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),"position":deepcopy(state["position"]),"pending":deepcopy(state["pending"]),
        "hold":deepcopy(state["hold"]),"cap_qualified":state["cap_qualified"],"fees":state["fees"],"trade_count":state["trade_count"],
        "closed_trade_count":state["closed_trade_count"],"milestone_counts":deepcopy(state["milestone_counts"]),"last_daily_result":deepcopy(state["last_daily_result"]),
        "last_observed_at":state["last_observed_at"],"enabled_strategies":list(state["enabled_strategies"]),"strategy_stats":deepcopy(state["strategy_stats"]),
        "next_strategy":STRATEGY_IDS[state["next_strategy"]],"portfolio_activation_at":state["portfolio_activation_at"],
        "cap_scope":"ONE shared500 monthly modeled flat-start net-loss allowance across all strategies. Profit cannot replenish commitments; missing disposal suspends assurance. This is not a trailing drawdown or broker guarantee.",
        "execution":"Delayed quote-based paper simulation; no broker or real orders."}


def _hold(state,code,message,clock):
    state=deepcopy(state)
    state["hold"]={"code":code,"message":message,"observed_at":clock.isoformat()}
    state["cap_qualified"]=False
    return {"status":"held","state":state,"events":[{"type":"held",**state["hold"]}],"summary":summarize(state)}


def _eligible(quote,row,day,kind,*,forward):
    expiry=date.fromisoformat(quote["expiry"])
    spot,strike=_number(row["underlying_price"]),_number(quote["strike"])
    strike_ok=spot<=strike<=spot*Decimal("1.05") if kind=="call" else spot*Decimal("0.95")<=strike<=spot
    return (quote["type"]==kind and quote["multiplier"]==100 and 7<=(expiry-day).days<=21 and expiry.strftime("%Y-%m")==day.strftime("%Y-%m") and
        strike_ok and _debit(_number(quote["ask"])*100)+Decimal("1.30")<=125 and _deadline(day,expiry)>day and _quote_ok(quote,side="BUY",forward=forward))


def _signal(identifier,history):
    if len(history)<14:return False
    if identifier=="recovery_call_v1":return _recovery(history)
    prices=[_number(item["underlying_price"]) for item in history]
    before=prices[-11:-1]
    mean=sum(before,Decimal(0))/len(before)
    return prices[-1]>max(before) and prices[-1]>mean if identifier=="breakout_call_v1" else prices[-1]<min(before) and prices[-1]<mean


def _pending(action,quote,row,clock,reasons,identifier):
    return {"strategy_id":identifier,"type":quote["type"],"action":action,"contract_id":quote["contract_id"],"expiry":quote["expiry"],"strike":quote["strike"],
        "decided_at":clock.isoformat(),"signal_at":row["available_at"],"signal_session":_day_at(_clock(row["observed_at"])).isoformat(),"reasons":list(reasons)}


def _sell(state,quote,row,reasons,events):
    position=state["position"];identifier=position["strategy_id"];stats=state["strategy_stats"][identifier]
    gross=_credit(_number(quote["bid"])*100);net=gross-Decimal("0.65");entry=_number(position["premium"])+Decimal("0.65");ratio=net/entry
    state["cash"]=_text(_number(state["cash"])+net);state["fees"]=_text(_number(state["fees"])+Decimal("0.65"));state["closed_trade_count"]+=1
    stats["closed_count"]+=1;stats["fees"]=_text(_number(stats["fees"])+Decimal("0.65"));stats["realized_pnl"]=_text(_number(stats["realized_pnl"],signed=True)+net-entry)
    for multiple in (2,5,10):
        if ratio>=multiple:
            state["milestone_counts"][str(multiple)+"x"]+=1;stats["milestone_counts"][str(multiple)+"x"]+=1
    events.append({"type":"fill","action":"SELL","strategy_id":identifier,"option_type":quote["type"],"observed_at":row["observed_at"],"available_at":row["available_at"],
        "contract_id":quote["contract_id"],"quantity":1,"price":quote["bid"],"gross_credit":_text(gross),"fee":"0.65","net_credit":_text(net),
        "trade_pnl":_text(net-entry),"net_payout_multiple":_text(ratio),"reasons":list(reasons),"simulated":True})
    state["position"]=None;state["pending"]=None


def _step(state,row,clock,*,forward):
    prior=deepcopy(state);events=[];observed=_clock(row["observed_at"]);day=_day_at(observed);month=day.strftime("%Y-%m")
    if month<state["month"]:return _hold(prior,"backwards_month","A quote predates the funded portfolio month.",clock)
    if month!=state["month"]:
        if state["position"] is not None:return _hold(prior,"unresolved_month_boundary","An unresolved option crossed the required flat month boundary; no new allowance or funding.",clock)
        if state["pending"]:events.append({"type":"cancelled","reason":"month_boundary","decision":deepcopy(state["pending"])});state["pending"]=None
        state["month"]=month;state["cash"]=_text(_number(state["cash"])+500);state["contributions"]=_text(_number(state["contributions"])+500)
        state["monthly_start_equity"]=state["cash"];state["monthly_committed"]="0"
        for stats in state["strategy_stats"].values():stats["monthly_committed"]="0"
        events.append({"type":"contribution","month":month,"amount":"500","simulated":True})
    quotes={quote["contract_id"]:quote for quote in row["quotes"]};position=state["position"]
    if position is not None:
        deadline=date.fromisoformat(position["forced_exit_session"])
        if day>deadline:return _hold(prior,"missed_disposal_deadline","Shared portfolio disposal passed without a verified sale; no backfill.",clock)
        quote=quotes.get(position["contract_id"])
        if quote is None:return _hold(prior,"missing_position_quote","The funded contract is absent; retain prior dated state without inventing a mark.",clock)
        position["marked_bid"]=quote["bid"];position["last_marked_at"]=row["observed_at"]
        pending=state["pending"];due=day==deadline
        before=observed>_clock(position["decision_at"])+timedelta(seconds=60) if due else pending is not None and observed>_clock(pending["decided_at"])+timedelta(seconds=60)
        if (due or pending is not None) and before:
            if _quote_ok(quote,side="SELL",forward=forward):_sell(state,quote,row,["precommitted_disposal_deadline"] if due else pending["reasons"],events)
            elif due:return _hold(prior,"unexecutable_disposal","No executable bid at mandatory disposal; the entire shared portfolio stays unresolved.",clock)
        if state["position"] is not None and state["pending"] is None:
            bid,entry=_number(quote["bid"]),_number(position["entry_ask"])
            reasons=["bid_reached_5x_entry_ask"] if bid>=entry*5 else ["bid_at_or_below_half_entry_ask"] if bid<=entry/2 else []
            if reasons:
                state["pending"]=_pending("SELL",quote,row,clock,reasons,position["strategy_id"]);events.append({"type":"decision",**deepcopy(state["pending"])})
    elif state["pending"] is not None:
        pending=state["pending"];quote=quotes.get(pending["contract_id"])
        expected=_sessions(date.fromisoformat(pending["signal_session"])+timedelta(days=1),day)
        if len(expected)!=1 or observed<=_clock(pending["decided_at"])+timedelta(seconds=60):
            events.append({"type":"cancelled","reason":"late_or_unavailable_entry","decision":deepcopy(pending)});state["pending"]=None
        elif quote is None or not _eligible(quote,row,day,pending["type"],forward=forward):
            events.append({"type":"cancelled","reason":"entry_quote_no_longer_eligible","decision":deepcopy(pending)});state["pending"]=None
        else:
            premium=_debit(_number(quote["ask"])*100);reserve=premium+Decimal("1.30");identifier=pending["strategy_id"]
            if reserve+_number(state["monthly_committed"])>500 or reserve>_number(state["cash"]):
                events.append({"type":"cancelled","reason":"shared_monthly_or_cash_budget","decision":deepcopy(pending)});state["pending"]=None
            else:
                state["cash"]=_text(_number(state["cash"])-premium-Decimal("0.65"));state["monthly_committed"]=_text(_number(state["monthly_committed"])+reserve)
                state["fees"]=_text(_number(state["fees"])+Decimal("0.65"));state["trade_count"]+=1
                stats=state["strategy_stats"][identifier];stats["entry_count"]+=1;stats["fees"]=_text(_number(stats["fees"])+Decimal("0.65"));stats["monthly_committed"]=_text(_number(stats["monthly_committed"])+reserve)
                state["next_strategy"]=(STRATEGY_IDS.index(identifier)+1)%len(STRATEGY_IDS)
                state["position"]={"strategy_id":identifier,"contract_id":quote["contract_id"],"type":quote["type"],"expiry":quote["expiry"],"strike":quote["strike"],"multiplier":100,"quantity":1,
                    "entry_at":row["observed_at"],"decision_at":pending["decided_at"],"entry_ask":quote["ask"],"premium":_text(premium),"entry_fee":"0.65","exit_fee_reserved":"0.65","budget_debit":_text(reserve),
                    "marked_bid":quote["bid"],"last_marked_at":row["observed_at"],"forced_exit_session":_deadline(day,date.fromisoformat(quote["expiry"])).isoformat()}
                events.append({"type":"fill","action":"BUY","strategy_id":identifier,"option_type":quote["type"],"observed_at":row["observed_at"],"available_at":row["available_at"],"decided_at":pending["decided_at"],
                    "contract_id":quote["contract_id"],"quantity":1,"price":quote["ask"],"premium":_text(premium),"fee":"0.65","exit_fee_reserved":"0.65","budget_committed":_text(reserve),"simulated":True})
                state["pending"]=None
    state["history"]=(state["history"]+[{"observed_at":row["observed_at"],"underlying_price":row["underlying_price"]}])[-15:]
    if state["position"] is None and state["pending"] is None:
        order=STRATEGY_IDS[state["next_strategy"]:]+STRATEGY_IDS[:state["next_strategy"]]
        for identifier in order:
            if identifier not in state["enabled_strategies"] or not _signal(identifier,state["history"]):continue
            candidates=[quote for quote in row["quotes"] if _eligible(quote,row,day,STRATEGIES[identifier]["type"],forward=forward)]
            spot=_number(row["underlying_price"])
            candidates.sort(key=lambda quote:(abs((date.fromisoformat(quote["expiry"])-day).days-14),abs(_number(quote["strike"])-spot),quote["contract_id"]))
            if candidates:
                quote=candidates[0];reserve=_debit(_number(quote["ask"])*100)+Decimal("1.30")
                if reserve+_number(state["monthly_committed"])<=500 and reserve<=_number(state["cash"]):
                    state["pending"]=_pending("BUY",quote,row,clock,[identifier+"_registered_signal"],identifier);events.append({"type":"decision",**deepcopy(state["pending"])});break
    equity=_equity(state);previous=state["last_daily_result"]
    prior_equity=_number(previous["equity"]) if previous and previous["month"]==month else _number(state["monthly_start_equity"])
    state["last_daily_result"]={"date":day.isoformat(),"month":month,"observed_at":row["observed_at"],"decided_at":clock.isoformat(),"equity":_text(equity),"cash":state["cash"],"contributions":state["contributions"],
        "daily_pnl":_text(equity-prior_equity),"trading_pnl":_text(equity-_number(state["contributions"])),"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),
        "monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),"fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"cap_qualified":state["cap_qualified"],"simulated":True}
    if equity<_number(state["monthly_start_equity"])-500:raise OptionsRecoveryError("The shared500 monthly loss invariant failed.")
    state["last_decided_at"]=clock.isoformat();state["last_observed_at"]=row["observed_at"]
    events.append({"type":"paper_daily_result",**deepcopy(state["last_daily_result"])})
    return {"status":"completed","state":state,"events":events}


@_precision
def advance_paper(dataset,state,*,decided_at):
    state=validate_state(state);clock=_clock(decided_at)
    if clock<_clock(state["last_decided_at"]):raise OptionsRecoveryError("Paper decision clock moved backwards.")
    if state["hold"]:return {"status":"held","state":state,"events":[],"summary":summarize(state)}
    rows,_=_rows(dataset,state["symbol"])
    if dataset["evidence_kind"]!="historical_quotes" or dataset["source"]["quote_time_basis"]!="recorded":return _hold(state,"unrecorded_quote_times","Forward fills require recorded timestamps; assumed-EOD/synthetic evidence remains research only.",clock)
    if _clock(dataset["as_of"])>clock or any(_clock(row["available_at"])>clock for row in rows):raise OptionsRecoveryError("Forward evidence is unavailable at its decision clock.")
    if not rows:
        checked=check_deadline(state,clock);return {"status":"held" if checked["hold"] else "unchanged","state":checked,"events":[],"summary":summarize(checked)}
    from .qqq_data import last_closed_session
    if _day_at(_clock(rows[-1]["observed_at"]))!=last_closed_session(clock):return _hold(state,"stale_quotes","The latest option snapshot is not the latest finalized exchange session.",clock)
    count=state["processed_count"]
    if count==0:
        state.update(processed_count=len(rows),prefix_sha256=_prefix(rows),last_observed_at=rows[-1]["observed_at"],last_decided_at=clock.isoformat(),history=_history(rows))
        return {"status":"completed","state":state,"events":[{"type":"baseline","observed_at":rows[-1]["observed_at"],"historical_trades_adopted":0}],"summary":summarize(state)}
    if len(rows)<count or _prefix(rows[:count])!=state["prefix_sha256"]:return _hold(state,"accepted_history_changed","Accepted option observations changed or disappeared.",clock)
    if len(rows)==count:
        checked=check_deadline(state,clock);return {"status":"held" if checked["hold"] else "unchanged","state":checked,"events":[],"summary":summarize(checked)}
    if len(rows)!=count+1:return _hold(state,"missed_observations","Multiple unseen sessions cannot be replayed into forward paper fills.",clock)
    previous=_day_at(_clock(state["last_observed_at"]));day=_day_at(_clock(rows[-1]["observed_at"]))
    if len(_sessions(previous+timedelta(days=1),day))!=1:return _hold(state,"missing_session","A session is missing; do not fabricate a past fill.",clock)
    result=_step(state,rows[-1],clock,forward=True)
    if result["status"]=="completed":
        result["state"]["processed_count"]=len(rows);result["state"]["prefix_sha256"]=_prefix(rows);result["summary"]=summarize(result["state"])
    return result


def _report(state,events,curve,coverage,qualities):
    summary=summarize(state);peak=Decimal(0);drawdown=Decimal(0)
    for row in curve:
        pnl=_number(row["trading_pnl"],signed=True);peak=max(peak,pnl);drawdown=max(drawdown,peak-pnl)
    metrics={"trading_pnl":summary["trading_pnl"],"total_contributions":summary["contributions"],"ending_equity":summary["equity"],
        "trade_count":summary["trade_count"],"closed_trade_count":summary["closed_trade_count"],"fees":summary["fees"],"max_drawdown":_text(drawdown),
        "max_drawdown_basis":"Cashflow-adjusted dollar P&L drawdown, not annualized/time-weighted returns",
        "maximum_monthly_loss":_text(max((max(Decimal(0),-_number(row["monthly_pnl"],signed=True)) for row in curve),default=Decimal(0))),
        "payout_milestones":deepcopy(state["milestone_counts"]),"payout_basis":"Realized net exit cash / premium plus entry fee; quoted targets are not realized returns"}
    covered={**coverage,"processed_snapshots":len(curve),"last_processed_date":curve[-1]["date"] if curve else None,"complete":len(curve)==coverage["source_snapshots"] and not state["hold"]}
    gate="held_incomplete_evidence" if state["hold"] else "insufficient_history" if coverage["source_snapshots"]<14 else "no_completed_trades" if not state["closed_trade_count"] else "exploratory_not_proven"
    monthly=[]
    for row in curve:
        if monthly and monthly[-1]["month"]==row["month"]:monthly[-1]=deepcopy(row)
        else:monthly.append(deepcopy(row))
    return {"schema":2,"rule_sha256":RULE_SHA256,"symbol":state["symbol"],"enabled_strategies":list(state["enabled_strategies"]),"research_gate":gate,"execution_qualified":False,
        "coverage":covered,"metrics":metrics,"summary":summary,"state":deepcopy(state),"curve":curve,"events":events,"monthly_rows":monthly,
        "data_quality":qualities,"limitations":["All supplied bid/ask snapshots remain research assumptions; hashes do not authenticate prices or completeness.",
            "Each counterfactual is a separate500-funded research ledger. Only the shared portfolio enforces actual combined500paper allowance; never add comparison account returns.",
            "All hypotheses were registered before this expanded study. No prospective profitability qualification, annualized short-window claims or automatic winner selection.",
            "Missing mandatory exits stop the relevant ledger; its last marked P&L is not a completed full-window realized result.",
            "Source quote timing, missing sizes, gaps and corporate-action conventions remain explicit. Underlying prices never substitute for option contracts."]}


@_precision
def _backtests(datasets,*,symbol,modes):
    if symbol not in RULE["universe"]:raise OptionsRecoveryError("Choose a fixed registered research symbol.")
    datasets=[datasets] if type(datasets) is dict else datasets
    ledgers={};events={key:[] for key in modes};curves={key:[] for key in modes};quality=[];source_rows=0;first=None;last=None;basis=None;previous=None;prefix_hasher=hashlib.sha256(b"[")
    for chunk_count,dataset in enumerate(datasets,1):
        if chunk_count>96:raise OptionsRecoveryError("The study exceeds its96chunk bound.")
        rows,checked=_rows(dataset,symbol)
        current_basis=(dataset["evidence_kind"],dataset["source"]["quote_time_basis"],dataset["source"]["provider"])
        if basis is not None and current_basis!=basis:raise OptionsRecoveryError("Historical source/timing basis changed between chunks.")
        basis=current_basis;quality.append({**checked,"dataset_sha256":hashlib.sha256(_canonical(dataset)).hexdigest(),"source_sha256":dataset["source"]["sha256"]})
        for row in rows:
            day=_day_at(_clock(row["observed_at"]));first=first or day.isoformat();last=day.isoformat();source_rows+=1
            if source_rows>10000:raise OptionsRecoveryError("The study exceeds10000daily observations.")
            if previous is not None and day<=previous:raise OptionsRecoveryError("Chunks overlap, repeat or move backwards; no hidden resets.")
            gap=previous is not None and len(_sessions(previous+timedelta(days=1),day))!=1
            if not ledgers:
                ledgers={key:initial_state(None,started_at=row["available_at"],symbol=symbol,strategies=value) for key,value in modes.items()}
            if source_rows>1:prefix_hasher.update(b",")
            prefix_hasher.update(_canonical(row));cursor=prefix_hasher.copy();cursor.update(b"]")
            for key,state in list(ledgers.items()):
                if state["hold"]:continue
                if gap:
                    result=_hold(state,"historical_session_gap","The historical sequence contains missing sessions, including chunk boundaries; no unknown fill is assumed.",_clock(row["available_at"]))
                else:result=_step(state,row,_clock(row["available_at"]),forward=False)
                ledgers[key]=result["state"];events[key].extend(result["events"])
                if result["status"]=="completed":
                    ledgers[key]["processed_count"]=source_rows;ledgers[key]["prefix_sha256"]=cursor.hexdigest();curves[key].append(deepcopy(ledgers[key]["last_daily_result"]))
            previous=day
    if not ledgers:raise OptionsRecoveryError("No supported historical option observations were supplied.")
    coverage={"symbol":symbol,"source_snapshots":source_rows,"chunks":len(quality),"first_date":first,"last_source_date":last,"quote_time_basis":basis[1]}
    return {key:_report(state,events[key],curves[key],coverage,quality) for key,state in ledgers.items()}


def backtest(dataset_or_iterable,*,symbol="QQQ",strategies=None):
    return _backtests(dataset_or_iterable,symbol=symbol,modes={"portfolio":_strategies(strategies)})["portfolio"]


def backtest_comparison(dataset_or_iterable,*,symbol="QQQ"):
    modes={"portfolio":list(STRATEGY_IDS),**{identifier:[identifier] for identifier in STRATEGY_IDS}}
    reports=_backtests(dataset_or_iterable,symbol=symbol,modes=modes)
    portfolio=reports.pop("portfolio")
    return {"schema":2,"kind":"registered_strategy_comparison","rule":deepcopy(RULE),"rule_sha256":RULE_SHA256,"symbol":symbol,
        "research_gate":portfolio["research_gate"],"execution_qualified":False,"metrics":deepcopy(portfolio["metrics"]),"coverage":deepcopy(portfolio["coverage"]),
        "portfolio":portfolio,"strategy_results":reports,"comparison_scope":"Same source sessions; individual results are independent counterfactual500budgets, never summed or used for automatic winner selection.",
        "all_ledgers_complete":all(report["coverage"]["complete"] for report in (portfolio,*reports.values()))}
