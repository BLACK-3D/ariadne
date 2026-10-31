"""Fixed aggressive-options experiment; pure calculations, never broker orders.

Every price used for a simulated fill is a supplied option bid/ask quote.  A
stock-price path, a pricing formula, and a current option chain are not a
historical options backtest.  The monthly cap is a conservative cash-debit
budget, including both transaction fees, with no recycling of trading profits
and no qualified cross-month positions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from functools import wraps

UTC = timezone.utc
RULE = {
    "id": "aggressive-options-recovery-v1", "schema": 1,
    "universe": ["QQQ", "SPY", "AAPL"], "default_symbol": "QQQ",
    "instrument": "Fully paid standard 100-share long calls only; no shorts, spreads, exercise or real orders",
    "signal": "Completed daily observation above both previous two observations; at least 3% closing decline from its preceding 10-observation high occurred in current/prior four observations",
    "warmup_sessions": 14,
    "entry": "Next regular-session snapshot observed more than 60 seconds after the durable decision; pay ask, one contract maximum",
    "selection": "7-21 calendar DTE; expiry in the current calendar month; strike from spot through 5% above spot; rank nearest 14 DTE, then lowest strike, then contract ID",
    "liquidity": "Positive bid and ask, no crossed quotes, spread <=25% of midpoint; forward fills require quoted size >=1 on the executable side",
    "per_entry_maximum_debit": "125.00", "monthly_maximum_debit": "500.00",
    "fee_per_contract_per_side": "0.65",
    "monthly_cap": "Premium plus entry fee plus reserved exit fee, committed without profit recycling; month must start flat and end verified flat; missing deadline exits hold and invalidate cap qualification",
    "exit": "Signal after bid reaches 5x entry ask or <=0.5x entry ask; execute only at a later eligible bid; precommit forced sale by earliest of tenth following regular session, preceding expiry session, penultimate regular session of entry month",
    "month_boundary": "No position carry. Unresolved position blocks new month, new contribution and new entries; no invented liquidation or reset",
    "contributions": "500 simulated USD initially and 500 at each observed later calendar-month boundary after verified flat prior month; contributions excluded from trading P&L",
    "forward_activation": "Start in cash; historical quotes only warm indicators; recorded timestamp source required; no historical signal or order adoption",
    "validation": "Fixed before examining this experiment's historical outcome; no parameter optimization; all returns hypothetical and unqualified quote-source assumptions disclosed",
    "cash_interest": "0", "taxes": "not modeled", "payout_milestones": [2, 5, 10],
}


class OptionsRecoveryError(ValueError):
    """An invalid or uncertain options research observation."""


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise OptionsRecoveryError("Use finite bounded JSON values.") from None


RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()


def _precision(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with localcontext() as context:
            context.prec=40
            return function(*args, **kwargs)
    return guarded


def _clock(value):
    if type(value) is not str or not 10 <= len(value) <= 64:
        raise OptionsRecoveryError("Use a bounded aware ISO clock.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise OptionsRecoveryError("Use an aware ISO clock.") from None


def _number(value, *, signed=False):
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise OptionsRecoveryError("Ledger numbers must be bounded decimal strings.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise OptionsRecoveryError("Use finite ledger decimal strings.") from None
    if not result.is_finite() or abs(result) > Decimal("1000000000000") or result.as_tuple().exponent < -12 or (not signed and result < 0):
        raise OptionsRecoveryError("A ledger number is outside finite bounds.")
    return result


def _text(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _debit(value):
    from decimal import ROUND_CEILING
    return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _credit(value):
    from decimal import ROUND_FLOOR
    return value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)


def _day_at(clock):
    from .qqq_data import exchange_day
    return exchange_day(clock)


def _sessions(start, end):
    from .qqq_data import session_dates
    return session_dates(start, end)


def _rows(dataset, symbol, *, allow_research_ko=False):
    from .options_data import validate_dataset as check
    from .qqq_data import is_session, session_close_utc
    if type(allow_research_ko) is not bool:
        raise OptionsRecoveryError("Research-only KO permission must be an explicit boolean.")
    quality = check(dataset)
    if symbol not in RULE["universe"] and not (allow_research_ko and symbol == "KO"):
        raise OptionsRecoveryError("Choose a fixed supported research symbol.")
    rows = [deepcopy(item) for item in dataset["snapshots"] if item["underlying_symbol"] == symbol]
    previous = None
    previous_available = None
    for row in rows:
        if symbol == "KO" and _number(row["underlying_price"]) >= 100:
            raise OptionsRecoveryError("Research KO requires an underlying price strictly below100.")
        observed = _clock(row["observed_at"])
        available = _clock(row["available_at"])
        day = _day_at(observed)
        if not is_session(day) or observed > session_close_utc(day) or observed < session_close_utc(day)-timedelta(minutes=30):
            raise OptionsRecoveryError("Use one recorded daily snapshot during the last 30 minutes of each regular session.")
        if previous is not None and day <= previous:
            raise OptionsRecoveryError("The options engine requires one ordered snapshot per symbol/session.")
        if available < observed:
            raise OptionsRecoveryError("A quote cannot be available before it is observed.")
        if previous_available is not None and available <= previous_available:
            raise OptionsRecoveryError("Daily quote availability must be strictly ordered.")
        previous = day
        previous_available = available
    return rows, quality


def validate_dataset(dataset):
    """Validate options evidence without asserting authenticity or returns."""
    from .options_data import validate_dataset as check
    result = check(dataset)
    for symbol in result["symbols"]:
        _rows(dataset, symbol)
    return result


def _prefix(rows):
    return hashlib.sha256(_canonical(rows)).hexdigest()


def _history(rows):
    return [{"observed_at":row["observed_at"], "underlying_price":row["underlying_price"]} for row in rows[-15:]]


@_precision
def initial_state(dataset=None, *, started_at, symbol="QQQ"):
    """Start simulated cash with no borrowed historical position or order."""
    clock = _clock(started_at)
    if symbol not in RULE["universe"]:
        raise OptionsRecoveryError("Choose a supported options symbol.")
    rows = [] if dataset is None else _rows(dataset, symbol)[0]
    if rows and (_clock(rows[-1]["available_at"]) > clock or _clock(dataset["as_of"]) > clock):
        raise OptionsRecoveryError("Paper activation cannot use future evidence.")
    state = {
        "schema":1, "rule_sha256":RULE_SHA256, "symbol":symbol,
        "started_at":clock.isoformat(), "last_decided_at":clock.isoformat(),
        "processed_count":len(rows), "prefix_sha256":_prefix(rows),
        "last_observed_at":rows[-1]["observed_at"] if rows else None,
        "month":_day_at(clock).strftime("%Y-%m"), "cash":"500", "contributions":"500",
        "monthly_committed":"0", "monthly_start_equity":"500",
        "position":None, "pending":None, "fees":"0", "trade_count":0, "closed_trade_count":0,
        "hold":None, "cap_qualified":True, "last_daily_result":None,
        "milestone_counts":{"2x":0,"5x":0,"10x":0}, "history":_history(rows),
    }
    validate_state(state)
    return state


_STATE_KEYS = {"schema","rule_sha256","symbol","started_at","last_decided_at","processed_count","prefix_sha256",
               "last_observed_at","month","cash","contributions","monthly_committed","monthly_start_equity",
               "position","pending","fees","trade_count","closed_trade_count","hold","cap_qualified","last_daily_result",
               "milestone_counts","history"}


@_precision
def validate_state(state):
    """Reject malformed or overcommitted ledgers before any transition."""
    if type(state) is not dict or set(state) != _STATE_KEYS or state["schema"] != 1 or type(state["schema"]) is not int or state["rule_sha256"] != RULE_SHA256 or state["symbol"] not in RULE["universe"]:
        raise OptionsRecoveryError("The paper state does not match the frozen rule contract.")
    start, decided = _clock(state["started_at"]), _clock(state["last_decided_at"])
    if decided < start:
        raise OptionsRecoveryError("Paper decision time moved backwards.")
    for key in ("processed_count","trade_count","closed_trade_count"):
        if type(state[key]) is not int or not 0 <= state[key] <= 100000:
            raise OptionsRecoveryError("Use bounded nonnegative ledger counts.")
    if state["closed_trade_count"] > state["trade_count"] or state["trade_count"]-state["closed_trade_count"] not in (0,1):
        raise OptionsRecoveryError("Only one fully funded option position is supported.")
    for key in ("cash","contributions","monthly_committed","monthly_start_equity","fees"):
        _number(state[key])
    if _number(state["fees"]) != Decimal("0.65")*(state["trade_count"]+state["closed_trade_count"]):
        raise OptionsRecoveryError("The accumulated fees disagree with recorded fills.")
    if _number(state["contributions"])%Decimal("500"):
        raise OptionsRecoveryError("Simulated contributions must be recorded in explicit $500 increments.")
    if _number(state["monthly_committed"]) > Decimal("500") or _number(state["contributions"]) < Decimal("500"):
        raise OptionsRecoveryError("The non-recycling monthly budget is overcommitted.")
    if type(state["month"]) is not str or len(state["month"]) != 7:
        raise OptionsRecoveryError("Use a ledger calendar month.")
    try:
        if date.fromisoformat(state["month"]+"-01").strftime("%Y-%m") != state["month"]:
            raise ValueError
    except ValueError:
        raise OptionsRecoveryError("Use an ISO calendar month.") from None
    digest = state["prefix_sha256"]
    if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise OptionsRecoveryError("The accepted quote-prefix hash is invalid.")
    if state["last_observed_at"] is not None:
        _clock(state["last_observed_at"])
        if not state["processed_count"]:
            raise OptionsRecoveryError("An empty ledger cannot claim an accepted observation.")
    elif state["processed_count"]:
        raise OptionsRecoveryError("A nonempty ledger needs its last observation.")
    if type(state["cap_qualified"]) is not bool or type(state["history"]) is not list or len(state["history"]) > 15:
        raise OptionsRecoveryError("Use bounded historical warmup and explicit cap qualification.")
    history_previous = None
    for item in state["history"]:
        if type(item) is not dict or set(item) != {"observed_at","underlying_price"} or _number(item["underlying_price"]) <= 0:
            raise OptionsRecoveryError("The warmup history is malformed.")
        stamp = _clock(item["observed_at"])
        if history_previous is not None and stamp <= history_previous:
            raise OptionsRecoveryError("The warmup observations must be ordered.")
        history_previous = stamp
    if state["history"] and state["history"][-1]["observed_at"] != state["last_observed_at"]:
        raise OptionsRecoveryError("Warmup does not end at the accepted cursor.")
    if len(state["history"])!=min(state["processed_count"],15):
        raise OptionsRecoveryError("Warmup length does not match the accepted quote cursor.")
    if type(state["milestone_counts"]) is not dict or set(state["milestone_counts"]) != {"2x","5x","10x"} or any(type(x) is not int or x<0 or x>state["closed_trade_count"] for x in state["milestone_counts"].values()):
        raise OptionsRecoveryError("Realized payout counts are malformed.")
    if not state["milestone_counts"]["2x"] >= state["milestone_counts"]["5x"] >= state["milestone_counts"]["10x"]:
        raise OptionsRecoveryError("Realized payout milestones are inconsistent.")
    if state["hold"] is not None and (type(state["hold"]) is not dict or set(state["hold"]) != {"code","message","observed_at"} or not all(type(v) is str and len(v)<=2000 for v in state["hold"].values())):
        raise OptionsRecoveryError("The hold receipt is malformed.")
    if state["hold"] and state["cap_qualified"]:
        raise OptionsRecoveryError("Held accounts cannot certify the cap.")
    if state["hold"]:
        _clock(state["hold"]["observed_at"])
    position = state["position"]
    if position is not None:
        keys={"contract_id","type","expiry","strike","multiplier","quantity","entry_at","decision_at","entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid","last_marked_at","forced_exit_session"}
        if type(position) is not dict or set(position)!=keys or position["type"]!="call" or position["multiplier"]!=100 or type(position["multiplier"]) is not int or position["quantity"]!=1 or type(position["quantity"]) is not int or state["trade_count"]-state["closed_trade_count"]!=1:
            raise OptionsRecoveryError("A position must be one standard fully paid long call.")
        for key in ("strike","entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid"):
            _number(position[key])
        if _number(position["strike"])<=0 or _number(position["entry_ask"])<=0 or _number(position["entry_fee"])!=Decimal("0.65") or _number(position["exit_fee_reserved"])!=Decimal("0.65"):
            raise OptionsRecoveryError("Position risk or fee reserve is invalid.")
        if _number(position["premium"]) != _debit(_number(position["entry_ask"])*100) or _number(position["budget_debit"]) != _number(position["premium"])+Decimal("1.30") or _number(position["budget_debit"])>Decimal("125") or _number(position["budget_debit"])>_number(state["monthly_committed"]) or _number(state["cash"])<Decimal("0.65"):
            raise OptionsRecoveryError("A position exceeds its funded debit and exit-fee reserve.")
        observed, decision = _clock(position["entry_at"]), _clock(position["decision_at"])
        if observed<=decision+timedelta(seconds=60) or _clock(position["last_marked_at"])<observed:
            raise OptionsRecoveryError("A fill must follow its decision by more than 60 seconds.")
        try:
            expiry=date.fromisoformat(position["expiry"])
            deadline=date.fromisoformat(position["forced_exit_session"])
        except (ValueError,TypeError):
            raise OptionsRecoveryError("The position expiry/deadline is invalid.") from None
        entry_day=_day_at(observed)
        if not entry_day<=deadline<expiry or entry_day.strftime("%Y-%m")!=state["month"] or expiry.strftime("%Y-%m")!=state["month"] or not 7<=(expiry-entry_day).days<=21 or deadline!=_deadline(entry_day,expiry):
            raise OptionsRecoveryError("The position crossed its funded month or disposal deadline.")
        from .options_data import contract_id
        if position["contract_id"]!=contract_id(state["symbol"],position["expiry"],"call",position["strike"]):
            raise OptionsRecoveryError("Position identity differs from its recorded economic contract.")
    elif state["trade_count"] != state["closed_trade_count"]:
        raise OptionsRecoveryError("A recorded open trade is missing its position.")
    pending=state["pending"]
    if pending is not None:
        keys={"action","contract_id","decided_at","signal_at","signal_session","expiry","strike","reasons"}
        if type(pending) is not dict or set(pending)!=keys or pending["action"] not in ("BUY","SELL") or type(pending["reasons"]) is not list or not pending["reasons"] or any(type(x) is not str or len(x)>200 for x in pending["reasons"]):
            raise OptionsRecoveryError("The pending decision is malformed.")
        if (pending["action"]=="SELL") != (position is not None):
            raise OptionsRecoveryError("A pending instruction disagrees with the funded position.")
        if _clock(pending["decided_at"])>decided or _clock(pending["signal_at"])>_clock(pending["decided_at"]):
            raise OptionsRecoveryError("A pending decision used unavailable evidence.")
        _number(pending["strike"])
        from .options_data import contract_id
        if pending["contract_id"]!=contract_id(state["symbol"],pending["expiry"],"call",pending["strike"]):
            raise OptionsRecoveryError("Pending contract identity differs from its economic terms.")
        try:signal_day=date.fromisoformat(pending["signal_session"])
        except (ValueError,TypeError):raise OptionsRecoveryError("The pending signal session is invalid.") from None
        if signal_day>_day_at(_clock(pending["signal_at"])):
            raise OptionsRecoveryError("The pending session follows its signal availability.")
        if position is not None and any(pending[k]!=position[k] for k in ("contract_id","expiry","strike")):
            raise OptionsRecoveryError("A sale instruction names another contract.")
    if state["last_daily_result"] is not None:
        daily=state["last_daily_result"]
        required={"date","month","observed_at","decided_at","equity","cash","contributions","daily_pnl","trading_pnl","monthly_pnl","monthly_budget_used","monthly_budget_remaining","fees","trade_count","closed_trade_count","cap_qualified","simulated"}
        if type(daily) is not dict or set(daily)!=required or daily["simulated"] is not True or type(daily["cap_qualified"]) is not bool:
            raise OptionsRecoveryError("The daily paper result is invalid.")
        for key in ("equity","cash","contributions","monthly_budget_used","monthly_budget_remaining","fees"):
            _number(daily[key])
        for key in ("daily_pnl","trading_pnl","monthly_pnl"):_number(daily[key],signed=True)
        if daily["observed_at"]!=state["last_observed_at"] or daily["date"]!=_day_at(_clock(daily["observed_at"])).isoformat() or daily["month"]!=state["month"] or _clock(daily["decided_at"])>decided:
            raise OptionsRecoveryError("The saved daily result does not match the accepted cursor.")
        if any(type(daily[k]) is not int or daily[k]!=state[k] for k in ("trade_count","closed_trade_count")):
            raise OptionsRecoveryError("The saved daily result disagrees with recorded trade counts.")
    _canonical(state)
    return deepcopy(state)


def _equity(state):
    cash = _number(state["cash"])
    if state["position"] is None:
        return cash
    position = state["position"]
    return cash+max(Decimal(0),_credit(_number(position["marked_bid"])*100)-Decimal("0.65"))


@_precision
def check_deadline(state, now):
    """Derive an overdue-evidence hold without inventing a sale or mutating input.

    A later genuine deadline snapshot may still settle an earlier precommitted
    disposal through ``advance_paper``. A passive view cannot certify an
    unverified sale merely because the collector has not run yet.
    """
    from .qqq_data import session_close_utc
    result=validate_state(state)
    stamp=now.isoformat() if isinstance(now,datetime) else now
    clock=_clock(stamp)
    if clock<_clock(result["last_decided_at"]):
        raise OptionsRecoveryError("The assurance clock cannot precede the latest decision.")
    if result["position"] and not result["hold"]:
        deadline=date.fromisoformat(result["position"]["forced_exit_session"])
        if clock>=session_close_utc(deadline)+timedelta(minutes=15):
            result["hold"]={"code":"unverified_disposal_deadline","message":"The disposal deadline passed without a verified closing quote in this ledger; cap assurance is suspended, with no invented proceeds.","observed_at":clock.isoformat()}
            result["cap_qualified"]=False
    return result


@_precision
def summarize(state, now=None):
    """Validated passive account summary; no quotes are refreshed here."""
    state=validate_state(state) if now is None else check_deadline(state,now)
    with localcontext() as context:
        context.prec=40
        equity=_equity(state)
        return {"symbol":state["symbol"],"simulated":True,"cash":state["cash"],"equity":_text(equity),
                "contributions":state["contributions"],"trading_pnl":_text(equity-_number(state["contributions"])),
                "month":state["month"],"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),
                "monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(Decimal("500")-_number(state["monthly_committed"])),
                "position":deepcopy(state["position"]),"pending":deepcopy(state["pending"]),"hold":deepcopy(state["hold"]),
                "cap_qualified":state["cap_qualified"],"fees":state["fees"],"trade_count":state["trade_count"],
                "closed_trade_count":state["closed_trade_count"],"milestone_counts":deepcopy(state["milestone_counts"]),
                "last_daily_result":deepcopy(state["last_daily_result"]),"last_observed_at":state["last_observed_at"],
                "cap_scope":"Conditional simulated calendar-month loss ceiling from a flat opening account; conservative premium and both-side fee budget, no profit recycling. Missing disposal evidence invalidates qualification. Peak-to-trough losses can exceed $500 after unrealized gains.",
                "execution":"Delayed quote-based simulation; no real orders, brokerage or execution guarantee."}


def _hold(state, code, message, clock):
    state=deepcopy(state)
    state["hold"]={"code":code,"message":message,"observed_at":clock.isoformat()}
    state["cap_qualified"]=False
    return {"status":"held","state":state,"events":[{"type":"held",**state["hold"]}],"summary":summarize(state)}


def _deadline(day, expiry):
    before_expiry=_sessions(day,expiry-timedelta(days=1))
    following_month=date(day.year+1,1,1) if day.month==12 else date(day.year,day.month+1,1)
    month=_sessions(date(day.year,day.month,1),following_month-timedelta(days=1))
    following=_sessions(day,day+timedelta(days=20))
    if not before_expiry or len(month)<2 or len(following)<11:
        raise OptionsRecoveryError("No bounded pre-expiry/month-end disposal session is available.")
    return min(before_expiry[-1],month[-2],following[10])


def _quote_ok(quote, *, side, forward):
    bid,ask=_number(quote["bid"]),_number(quote["ask"])
    if bid<=0 or ask<=0 or bid>ask or (ask-bid)>((ask+bid)/2)*Decimal("0.25"):
        return False
    size=quote["ask_size" if side=="BUY" else "bid_size"]
    return (size is not None and size>=1) if forward else size is None or size>=1


def _eligible(quote,row,day, *, forward):
    expiry=date.fromisoformat(quote["expiry"])
    dte=(expiry-day).days
    spot=_number(row["underlying_price"])
    cost=_debit(_number(quote["ask"])*100)+Decimal("1.30")
    return (quote["type"]=="call" and quote["multiplier"]==100 and 7<=dte<=21 and expiry.strftime("%Y-%m")==day.strftime("%Y-%m") and
            spot<=_number(quote["strike"])<=spot*Decimal("1.05") and cost<=Decimal("125") and _deadline(day,expiry)>day and _quote_ok(quote,side="BUY",forward=forward))


def _recovery(history):
    if len(history)<14:
        return False
    values=[_number(item["underlying_price"]) for item in history]
    if values[-1]<=max(values[-3:-1]):
        return False
    return any(values[index]<=max(values[index-10:index])*Decimal("0.97") for index in range(max(10,len(values)-5),len(values)))


def _pending(action, quote, row, clock, reasons):
    return {"action":action,"contract_id":quote["contract_id"],"expiry":quote["expiry"],"strike":quote["strike"],
            "decided_at":clock.isoformat(),"signal_at":row["available_at"],"signal_session":_day_at(_clock(row["observed_at"])).isoformat(),"reasons":list(reasons)}


def _sell(state, quote, row, reasons, events):
    position=state["position"]
    gross=_credit(_number(quote["bid"])*100)
    net=gross-Decimal("0.65")
    entry=_number(position["premium"])+Decimal("0.65")
    ratio=net/entry
    state["cash"]=_text(_number(state["cash"])+net)
    state["fees"]=_text(_number(state["fees"])+Decimal("0.65"))
    state["closed_trade_count"]+=1
    for multiple in (2,5,10):
        if ratio>=multiple:state["milestone_counts"][str(multiple)+"x"]+=1
    events.append({"type":"fill","action":"SELL","observed_at":row["observed_at"],"available_at":row["available_at"],
                   "contract_id":quote["contract_id"],"quantity":1,"price":quote["bid"],"gross_credit":_text(gross),"fee":"0.65",
                   "net_credit":_text(net),"trade_pnl":_text(net-entry),"net_payout_multiple":_text(ratio),"reasons":list(reasons),"simulated":True})
    state["position"]=None
    state["pending"]=None


def _step(state,row,clock,*,forward):
    """One chronologically newer daily observation, mutating only our copy."""
    prior=deepcopy(state)
    events=[]
    observed=_clock(row["observed_at"])
    day=_day_at(observed)
    month=day.strftime("%Y-%m")
    if month<state["month"]:
        return _hold(prior,"backwards_month","A quote predates the funded paper month.",clock)
    if month!=state["month"]:
        if state["position"] is not None:
            return _hold(prior,"unresolved_month_boundary","A position crossed its required verified flat month boundary; no budget reset, deposit or invented sale.",clock)
        if state["pending"] is not None:
            events.append({"type":"cancelled","reason":"month_boundary","decision":deepcopy(state["pending"])})
            state["pending"]=None
        state["month"]=month
        state["cash"]=_text(_number(state["cash"])+Decimal("500"))
        state["contributions"]=_text(_number(state["contributions"])+Decimal("500"))
        state["monthly_start_equity"]=state["cash"]
        state["monthly_committed"]="0"
        events.append({"type":"contribution","month":month,"amount":"500","simulated":True})
    quote_by_id={item["contract_id"]:item for item in row["quotes"]}
    position=state["position"]
    if position is not None:
        deadline=date.fromisoformat(position["forced_exit_session"])
        if day>deadline:
            return _hold(prior,"missed_disposal_deadline","The precommitted disposal deadline passed without a verified sale; do not backfill it.",clock)
        quote=quote_by_id.get(position["contract_id"])
        if quote is None:
            return _hold(prior,"missing_position_quote","The current contract is absent; retain prior dated state, do not invent a mark or fill.",clock)
        position["marked_bid"]=quote["bid"]
        position["last_marked_at"]=row["observed_at"]
        pending=state["pending"]
        due=day==deadline
        decided_before=(observed>_clock(position["decision_at"])+timedelta(seconds=60)) if due else pending is not None and observed>_clock(pending["decided_at"])+timedelta(seconds=60)
        if (due or pending is not None) and decided_before:
            if _quote_ok(quote,side="SELL",forward=forward):
                _sell(state,quote,row,["precommitted_disposal_deadline"] if due else pending["reasons"],events)
            elif due:
                return _hold(prior,"unexecutable_disposal","No positive sufficiently liquid bid at the mandatory disposal snapshot; the position remains unresolved.",clock)
        if state["position"] is not None and state["pending"] is None:
            bid=_number(quote["bid"])
            entry=_number(position["entry_ask"])
            reasons=(["bid_reached_5x_entry_ask"] if bid>=entry*5 else ["bid_at_or_below_half_entry_ask"] if bid<=entry/2 else [])
            if reasons:
                state["pending"]=_pending("SELL",quote,row,clock,reasons)
                events.append({"type":"decision",**deepcopy(state["pending"])})
    elif state["pending"] is not None:
        pending=state["pending"]
        # An entry signal expires after the immediately following exchange
        # session; a later import must not choose a favorable old fill.
        expected=_sessions(date.fromisoformat(pending["signal_session"])+timedelta(days=1),day)
        quote=quote_by_id.get(pending["contract_id"])
        if len(expected)!=1 or observed<=_clock(pending["decided_at"])+timedelta(seconds=60):
            events.append({"type":"cancelled","reason":"late_or_unavailable_entry","decision":deepcopy(pending)})
            state["pending"]=None
        elif quote is None or not _eligible(quote,row,day,forward=forward):
            events.append({"type":"cancelled","reason":"entry_quote_no_longer_eligible","decision":deepcopy(pending)})
            state["pending"]=None
        else:
            premium=_debit(_number(quote["ask"])*100)
            reserve=premium+Decimal("1.30")
            if reserve+_number(state["monthly_committed"])>Decimal("500") or reserve>_number(state["cash"]):
                events.append({"type":"cancelled","reason":"monthly_or_cash_budget","decision":deepcopy(pending)})
                state["pending"]=None
            else:
                state["cash"]=_text(_number(state["cash"])-premium-Decimal("0.65"))
                state["monthly_committed"]=_text(_number(state["monthly_committed"])+reserve)
                state["fees"]=_text(_number(state["fees"])+Decimal("0.65"))
                state["trade_count"]+=1
                deadline=_deadline(day,date.fromisoformat(quote["expiry"]))
                state["position"]={"contract_id":quote["contract_id"],"type":"call","expiry":quote["expiry"],"strike":quote["strike"],"multiplier":100,"quantity":1,
                    "entry_at":row["observed_at"],"decision_at":pending["decided_at"],"entry_ask":quote["ask"],"premium":_text(premium),"entry_fee":"0.65","exit_fee_reserved":"0.65","budget_debit":_text(reserve),
                    "marked_bid":quote["bid"],"last_marked_at":row["observed_at"],"forced_exit_session":deadline.isoformat()}
                events.append({"type":"fill","action":"BUY","observed_at":row["observed_at"],"available_at":row["available_at"],"decided_at":pending["decided_at"],
                    "contract_id":quote["contract_id"],"quantity":1,"price":quote["ask"],"premium":_text(premium),"fee":"0.65","exit_fee_reserved":"0.65","budget_committed":_text(reserve),"simulated":True})
                state["pending"]=None
    state["history"]=(state["history"]+[{"observed_at":row["observed_at"],"underlying_price":row["underlying_price"]}])[-15:]
    if state["position"] is None and state["pending"] is None and _recovery(state["history"]):
        candidates=[quote for quote in row["quotes"] if _eligible(quote,row,day,forward=forward)]
        candidates.sort(key=lambda quote:(abs((date.fromisoformat(quote["expiry"])-day).days-14),_number(quote["strike"]),quote["contract_id"]))
        if candidates:
            quote=candidates[0]
            reserve=_debit(_number(quote["ask"])*100)+Decimal("1.30")
            if reserve+_number(state["monthly_committed"])<=Decimal("500") and reserve<=_number(state["cash"]):
                state["pending"]=_pending("BUY",quote,row,clock,["fixed_recovery_breakout"])
                events.append({"type":"decision",**deepcopy(state["pending"])})
    prior_result=state["last_daily_result"]
    equity=_equity(state)
    prior_equity=_number(prior_result["equity"]) if prior_result and prior_result["month"]==month else _number(state["monthly_start_equity"])
    state["last_daily_result"]={"date":day.isoformat(),"month":month,"observed_at":row["observed_at"],"decided_at":clock.isoformat(),
        "equity":_text(equity),"cash":state["cash"],"contributions":state["contributions"],"daily_pnl":_text(equity-prior_equity),
        "trading_pnl":_text(equity-_number(state["contributions"])),"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),
        "monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(Decimal("500")-_number(state["monthly_committed"])),
        "fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"cap_qualified":state["cap_qualified"],"simulated":True}
    if equity<_number(state["monthly_start_equity"])-Decimal("500"):
        raise OptionsRecoveryError("The simulated calendar-month loss invariant failed.")
    state["last_decided_at"]=clock.isoformat()
    state["last_observed_at"]=row["observed_at"]
    events.append({"type":"paper_daily_result",**deepcopy(state["last_daily_result"])})
    return {"status":"completed","state":state,"events":events}


@_precision
def advance_paper(dataset,state,*,decided_at):
    """Advance exactly one new daily quote; never replay historical decisions."""
    state=validate_state(state)
    clock=_clock(decided_at)
    if clock<_clock(state["last_decided_at"]):
        raise OptionsRecoveryError("Paper decision time cannot move backwards.")
    if state["hold"]:
        return {"status":"held","state":state,"events":[],"summary":summarize(state)}
    rows,quality=_rows(dataset,state["symbol"])
    if dataset["evidence_kind"]!="historical_quotes" or dataset["source"]["quote_time_basis"]!="recorded":
        return _hold(state,"unrecorded_quote_times","Forward paper fills require actual recorded quote timestamps; fixture or assumed-EOD data is research-only.",clock)
    if not rows:
        checked=check_deadline(state,clock)
        return {"status":"held" if checked["hold"] else "unchanged","state":checked,"events":[],"summary":summarize(checked)}
    if _clock(dataset["as_of"])>clock or any(_clock(row["available_at"])>clock for row in rows):
        raise OptionsRecoveryError("A forward observation is not available at this decision time.")
    from .qqq_data import last_closed_session
    if _day_at(_clock(rows[-1]["observed_at"]))!=last_closed_session(clock):
        return _hold(state,"stale_quotes","The latest daily options quote does not cover the latest finalized exchange session.",clock)
    count=state["processed_count"]
    if count==0:
        # First actual source only establishes warmup.  No old signal becomes
        # a new order, even when a recently imported historical dataset wins.
        state["processed_count"]=len(rows)
        state["prefix_sha256"]=_prefix(rows)
        state["last_observed_at"]=rows[-1]["observed_at"]
        state["last_decided_at"]=clock.isoformat()
        state["history"]=_history(rows)
        return {"status":"completed","state":state,"events":[{"type":"baseline","observed_at":rows[-1]["observed_at"],"historical_trades_adopted":0}],"summary":summarize(state)}
    if len(rows)<count or _prefix(rows[:count])!=state["prefix_sha256"]:
        return _hold(state,"accepted_history_changed","Previously accepted option observations changed or disappeared; preserve the ledger for review.",clock)
    if len(rows)==count:
        checked=check_deadline(state,clock)
        return {"status":"held" if checked["hold"] else "unchanged","state":checked,"events":[],"summary":summarize(checked)}
    if len(rows)!=count+1:
        return _hold(state,"missed_observations","More than one unseen daily snapshot exists; do not backfill uncertain paper actions.",clock)
    previous=_day_at(_clock(state["last_observed_at"]))
    day=_day_at(_clock(rows[-1]["observed_at"]))
    if len(_sessions(previous+timedelta(days=1),day))!=1:
        return _hold(state,"missing_session","The next daily session is missing; no historical fill or return is manufactured.",clock)
    with localcontext() as context:
        context.prec=40
        result=_step(state,rows[-1],clock,forward=True)
        if result["status"]=="completed":
            result["state"]["processed_count"]=len(rows)
            result["state"]["prefix_sha256"]=_prefix(rows)
            result["summary"]=summarize(result["state"])
        return result


@_precision
def backtest(dataset,*,symbol=None):
    """Retrospective bid/ask simulation, with every limitation retained."""
    from .options_data import validate_dataset as check
    quality=check(dataset)
    if symbol is None:
        symbol=next((item for item in RULE["universe"] if item in quality["symbols"]),None)
    if symbol is None:
        raise OptionsRecoveryError("No supported fixed research symbol exists in this dataset.")
    rows,quality=_rows(dataset,symbol)
    if not rows:
        raise OptionsRecoveryError("The requested symbol has no quote snapshots.")
    state=initial_state(None,started_at=rows[0]["available_at"],symbol=symbol)
    events=[]
    curve=[]
    monthly=[]
    previous=None
    prefix_hasher=hashlib.sha256(b"[")
    with localcontext() as context:
        context.prec=40
        for index,row in enumerate(rows):
            day=_day_at(_clock(row["observed_at"]))
            if previous is not None and len(_sessions(previous+timedelta(days=1),day))!=1:
                held=_hold(state,"historical_session_gap","The historical chain contains missing daily sessions; the simulation stops before assuming unknown fills.",_clock(row["available_at"]))
                state=held["state"];events.extend(held["events"]);break
            result=_step(state,row,_clock(row["available_at"]),forward=False)
            state=result["state"]
            events.extend(result["events"])
            if result["status"]=="held":break
            state["processed_count"]=index+1
            # Equivalent to hash(canonical(rows[:index+1])) without repeatedly
            # serializing all earlier contracts for every historical session.
            if index:prefix_hasher.update(b",")
            prefix_hasher.update(_canonical(row))
            completed_prefix=prefix_hasher.copy()
            completed_prefix.update(b"]")
            state["prefix_sha256"]=completed_prefix.hexdigest()
            curve.append(deepcopy(state["last_daily_result"]))
            if monthly and monthly[-1]["month"]==state["month"]:monthly[-1]=deepcopy(state["last_daily_result"])
            else:monthly.append(deepcopy(state["last_daily_result"]))
            previous=day
        summary=summarize(state)
        pnls=[_number(item["trading_pnl"],signed=True) for item in curve]
        peak=Decimal(0);drawdown=Decimal(0)
        for pnl in pnls:
            peak=max(peak,pnl);drawdown=max(drawdown,peak-pnl)
        monthly_losses=[max(Decimal(0),-_number(item["monthly_pnl"],signed=True)) for item in curve]
        metrics={"trading_pnl":summary["trading_pnl"],"total_contributions":summary["contributions"],"ending_equity":summary["equity"],
            "trade_count":summary["trade_count"],"closed_trade_count":summary["closed_trade_count"],"fees":summary["fees"],
            "max_drawdown":_text(drawdown),"max_drawdown_basis":"Cashflow-adjusted dollar P&L drawdown, not an annualized or time-weighted return",
            "maximum_monthly_loss":_text(max(monthly_losses,default=Decimal(0))),"payout_milestones":deepcopy(state["milestone_counts"]),
            "payout_basis":"Realized net exit cash divided by premium plus entry fee; milestone counts exclude unrealized displayed bids"}
        gate="held_incomplete_evidence" if state["hold"] else "insufficient_history" if len(rows)<14 else "no_completed_trades" if not state["closed_trade_count"] else "exploratory_not_proven"
        limitations=["Retrospective hypothesis fixed before this run, not prospective validation or evidence of future profitability.",
            "Actual supplied option bid/ask prices are used; quoted liquidity does not establish executable historical fills.",
            "Unseen intraday paths and between-snapshot prices are unknown; target/stop observations do not guarantee the eventual exit price.",
            "$500 simulated monthly contributions are external funding, excluded from P&L. No tax, cash interest or broker exercise is modeled.",
            "A zero-entry result is valid; insufficient or stale data must never become a winner by substituting stock returns."]
        if dataset["source"]["quote_time_basis"]!="recorded":limitations.append("Quote timestamps were assumed from end-of-day dates. This is a timing-unqualified historical price scenario, not verified point-in-time execution.")
        if dataset["evidence_kind"]!="historical_quotes":limitations.append("Synthetic fixture data: software verification only; not historical performance evidence.")
        if any(q["bid_size"] is None or q["ask_size"] is None for row in rows for q in row["quotes"]):limitations.append("Some quote sizes are absent; historical price-only fills assume at least one contract. Forward paper fills require actual positive side size.")
        return {"schema":1,"rule":deepcopy(RULE),"rule_sha256":RULE_SHA256,"symbol":symbol,"research_gate":gate,"execution_qualified":False,
            "evidence_kind":dataset["evidence_kind"],"data_quality":quality,"dataset_sha256":hashlib.sha256(_canonical(dataset)).hexdigest(),
            "coverage":{"symbol":symbol,"source_snapshots":len(rows),"processed_snapshots":len(curve),"first_date":_day_at(_clock(rows[0]["observed_at"])).isoformat(),"last_source_date":_day_at(_clock(rows[-1]["observed_at"])).isoformat(),"last_processed_date":curve[-1]["date"] if curve else None,"quote_time_basis":dataset["source"]["quote_time_basis"]},
            "first_observed_at":rows[0]["observed_at"],"last_observed_at":rows[-1]["observed_at"],"snapshots_processed":len(curve),
            "metrics":metrics,"monthly_rows":monthly,"summary":summary,"state":state,"curve":curve,"events":events,"limitations":limitations}
