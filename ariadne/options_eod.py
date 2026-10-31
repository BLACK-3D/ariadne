"""Preregistered, date-only EOD options experiment; never orders or live execution.

The publisher supplies a partial chain and no option quote-event clock.  This
separate model deliberately cannot qualify execution or alter the timestamped
engines.  Its price assumptions are visible in every result and fill receipt.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib

from .options_recovery import (OptionsRecoveryError, _canonical, _clock, _number,
    _text, _debit, _credit, _precision, _day_at, _sessions, _deadline)

OptionsEODError = OptionsRecoveryError
STRATEGIES = {
    "eod_recovery_call_v1": {"type":"call", "warmup":14, "signal":"Close above preceding two closes; a close at least 3% below its preceding ten-close high occurred in the current/prior four observations"},
    "eod_breakout_call_v1": {"type":"call", "warmup":14, "signal":"Close above preceding ten closing prices and their arithmetic mean"},
    "eod_breakdown_put_v1": {"type":"put", "warmup":14, "signal":"Close below preceding ten closing prices and their arithmetic mean"},
}
STRATEGY_IDS = tuple(STRATEGIES)
RULE = {
    "id":"partial-date-only-eod-options-v1", "schema":3, "symbol":"F", "source_id":"onclick_f_eod_v1",
    "evidence":"Experimental lower-evidence research only: date-only partial returned chain, unknown quote-event time, no execution qualification, broker or real orders",
    "underlying":"Actual same-session unadjusted regular last stock bar; underlying strictly below USD100 at each entry, not a live-price claim; never substitute stock prices for option quotes",
    "strategies":STRATEGIES,
    "selector":"Rotate registered recovery-call, breakout-call, breakdown-put order after each entry; first signaled eligible hypothesis, never outcome ranking",
    "entry":"One fully paid standard 100-share long call OR put across the shared account; ask from a source session strictly after the durable pending decision NY day, received strictly after that decision; no same-day or historical decision adoption",
    "selection":"Only actually returned contracts: 7-21 calendar DTE at signal and fill source sessions, expiry in actual recorded decision NY month, call strike spot..1.05spot or put strike .95spot..spot; nearest14DTE then closest strike then contract ID",
    "liquidity":"Actual positive uncrossed bid/ask, spread<=25% midpoint; executable-side actual size>=1; unavailable quotes or sizes never synthesized",
    "per_entry_maximum_debit":"100.00", "monthly_maximum_debit":"500.00", "fee_per_contract_per_side":"0.65",
    "budget":"One shared USD500 gross monthly commitment across all strategies: premium plus entry fee and reserved exit fee; profits never replenish commitments; no shorts, margin, exercise, or rollover",
    "conditional_exit":"Bid>=5x entry ask or bid<=.5x entry ask creates durable decision; sell only at bid from a source session strictly after that actual decision NY day and received strictly after decision",
    "disposal":"At entry precommit sale at earliest tenth following regular session, session preceding expiry, penultimate entry-source-month session; mandatory source session must be strictly after actual entry decision NY day; require that exact source-date executable bid, otherwise hold whole account, no invented settlement or backfill",
    "clock":"Source date is not observed_at; quote_observed_at remains null. Modeled fill source_date and actual recorded_at are separate. Conditional decisions use actual durable clocks, never backdated clocks",
    "sessions":"Completed consecutive regular US source sessions only; retain a 15-reference indicator ring and hash-chain cursor. Changed accepted snapshots or missing sessions hold the whole account",
    "month":"Budget/funding month is actual recorded decision NY month. At a later month only verified flat, no pending decision or hold permits one USD500 contribution; unresolved prior risk blocks funding. Never grant missed-month catch-up deposits",
    "migration":"Only healthy schema2 zero-trade/fee/spent flat accounts; observations allowed. Copy financial fields without new funding and seal original state/data hashes, original canonical-state hash, and optional <=15 prior actual stock references",
    "baseline":"Consecutive actual stock references received by activation and source dates before activation NY day and before first options snapshot; indicator seeds only, never historical fills",
    "registration":"Fixed before expanded historical outcomes; no parameter optimization or proven-winner claims; results cannot establish execution or profitability",
    "payout_milestones":[2,5,10], "cash_interest":"0", "taxes":"not modeled",
}
RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()

_FINANCIAL = ("started_at","month","cash","contributions","monthly_committed","monthly_start_equity","fees","trade_count","closed_trade_count")
_BASE = {"source_date","underlying_close","received_at","stock_sha256"}
_SNAPSHOT = {"source_date","received_at","underlying_close","quote_observed_at","underlying_basis","quotes","partial_chain","source_sha256","stock_sha256"}
_QUOTE = {"contract_id","expiry","type","strike","bid","ask","bid_size","ask_size"}
_MIGRATION = {"from_schema","from_rule_sha256","old_state_sha256","old_data_sha256","old_canonical_state_sha256","activated_at","source_id","baseline","baseline_sha256","initial_financials","new_funding"}
_STATE = {"schema","rule_sha256","symbol","source_id","started_at","portfolio_activation_at","last_decided_at","processed_count","prefix_sha256","last_source_date","last_received_at","last_observed_at","last_snapshot_sha256","month","cash","contributions","monthly_committed","monthly_start_equity","fees","trade_count","closed_trade_count","position","pending","hold","cap_qualified","last_daily_result","milestone_counts","history","cursor_history","enabled_strategies","next_strategy","strategy_stats","migration","funding_count","execution_qualified","research_only"}
_POSITION = {"strategy_id","contract_id","type","expiry","strike","multiplier","quantity","entry_source_date","entry_recorded_at","decision_at","entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid","last_marked_source_date","forced_exit_session"}
_PENDING = {"strategy_id","type","action","contract_id","decided_at","signal_source_date","signal_received_at","expiry","strike","reasons"}
_STATS = {"entry_count","closed_count","fees","realized_pnl","monthly_committed","milestone_counts"}
_DAILY = {"date","month","quote_observed_at","recorded_at","equity","cash","contributions","daily_pnl","trading_pnl","monthly_pnl","monthly_budget_used","monthly_budget_remaining","fees","trade_count","closed_trade_count","cap_qualified","simulated","research_only","execution_qualified","partial_chain"}


def _hash(value):
    return type(value) is str and len(value)==64 and all(c in "0123456789abcdef" for c in value)


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _day(value):
    if type(value) is not str or len(value)!=10:
        raise OptionsRecoveryError("Use an ISO regular-session date.")
    try:
        day=date.fromisoformat(value)
        from .qqq_data import is_session
        if not is_session(day) or day.isoformat()!=value:raise ValueError
    except (TypeError, ValueError):
        raise OptionsRecoveryError("Use a reviewed regular US exchange session.") from None
    return day


def _month(value):
    try:
        if type(value) is not str or len(value)!=7 or date.fromisoformat(value+"-01").strftime("%Y-%m")!=value:raise ValueError
    except (ValueError,TypeError):
        raise OptionsRecoveryError("Use a calendar ledger month.") from None
    return value


@_precision
def contract_id(expiry,kind,strike):
    """F-only standard OSI identity; do not expand the frozen engines' universe."""
    day=_day(expiry);value=_number(strike)*1000
    if kind not in ("call","put") or value!=value.to_integral_value() or not 0<value<=99999999:
        raise OptionsRecoveryError("Use a positive standard OSI strike and option type.")
    return "F"+day.strftime("%y%m%d")+("C" if kind=="call" else "P")+str(int(value)).zfill(8)


def _identity(item):
    if item["type"] not in ("call","put") or _number(item["strike"])<=0:
        raise OptionsRecoveryError("Only positive-strike long call/put identities are supported.")
    _day(item["expiry"])
    if item["contract_id"]!=contract_id(item["expiry"],item["type"],item["strike"]):
        raise OptionsRecoveryError("An option identity disagrees with its economic terms.")


def _references(rows, *, before=None, received_by=None):
    if type(rows) is not list or len(rows)>15:raise OptionsRecoveryError("At most fifteen actual stock references are allowed.")
    prior=None
    for row in rows:
        if type(row) is not dict or set(row)!=_BASE or _number(row["underlying_close"])<=0 or not _hash(row["stock_sha256"]):
            raise OptionsRecoveryError("A stock reference lacks actual dated price/source evidence.")
        day=_day(row["source_date"]);received=_clock(row["received_at"])
        from .qqq_data import session_close_utc
        if received<session_close_utc(day) or (before is not None and day>=before) or (received_by is not None and received>received_by):
            raise OptionsRecoveryError("Stock warmup evidence was not available before activation.")
        if prior is not None and (day<=prior or len(_sessions(prior+timedelta(days=1),day))!=1):
            raise OptionsRecoveryError("Stock references must be consecutive regular sessions.")
        prior=day
    return rows


@_precision
def validate_snapshot(snapshot):
    """Structural checks only; collection provenance belongs to the data adapter."""
    if type(snapshot) is not dict or set(snapshot)!=_SNAPSHOT or snapshot["quote_observed_at"] is not None or snapshot["underlying_basis"]!="unadjusted_regular_last_bar" or type(snapshot["partial_chain"]) is not bool:
        raise OptionsRecoveryError("Use the explicit date-only EOD snapshot contract.")
    day=_day(snapshot["source_date"]);received=_clock(snapshot["received_at"])
    from .qqq_data import session_close_utc
    if received<session_close_utc(day) or _number(snapshot["underlying_close"])<=0 or not all(_hash(snapshot[key]) for key in ("source_sha256","stock_sha256")):
        raise OptionsRecoveryError("A completed snapshot requires actual positive stock evidence and source hashes.")
    quotes=snapshot["quotes"]
    if type(quotes) is not list or not 1<=len(quotes)<=2000:raise OptionsRecoveryError("Use a bounded nonempty actual returned chain.")
    ids=set()
    for quote in quotes:
        if type(quote) is not dict or set(quote)!=_QUOTE:raise OptionsRecoveryError("A quote has unsupported or missing fields.")
        _identity(quote)
        if quote["contract_id"] in ids or _day(quote["expiry"])<day:raise OptionsRecoveryError("Duplicate or expired source contracts are not supported.")
        ids.add(quote["contract_id"])
        for key in ("bid","ask"):_number(quote[key])
        if any(type(quote[key]) is not int or not 0<=quote[key]<=100000000 for key in ("bid_size","ask_size")):
            raise OptionsRecoveryError("Executable-side sizes must be actual nonnegative integers.")
    return deepcopy(snapshot)


def _stats():
    return {"entry_count":0,"closed_count":0,"fees":"0","realized_pnl":"0","monthly_committed":"0","milestone_counts":{"2x":0,"5x":0,"10x":0}}


def _initial_prefix(migration):
    return _digest({"rule_sha256":RULE_SHA256,"migration":migration})


@_precision
def migrate_flat(old_schema2, *, activated_at, source_id="onclick_f_eod_v1", old_state_sha256, old_data_sha256, baseline=None):
    """Convert one proven zero-risk ledger without adding capital or allowance."""
    from .options_portfolio import validate_state as old_validate
    old=old_validate(old_schema2);clock=_clock(activated_at)
    if source_id!=RULE["source_id"] or not _hash(old_state_sha256) or not _hash(old_data_sha256):raise OptionsRecoveryError("Migration requires the registered source and immutable old evidence hashes.")
    if clock<_clock(old["last_decided_at"]):raise OptionsRecoveryError("Activation cannot precede the original account clock.")
    if (old["position"] is not None or old["pending"] is not None or old["hold"] is not None or not old["cap_qualified"] or old["trade_count"] or old["closed_trade_count"] or
        any(_number(old[key])!=0 for key in ("monthly_committed","fees")) or any(old["milestone_counts"].values()) or
        any(any(_number(stats[key],signed=key=="realized_pnl")!=0 for key in ("fees","realized_pnl","monthly_committed")) or stats["entry_count"] or stats["closed_count"] or any(stats["milestone_counts"].values()) for stats in old["strategy_stats"].values()) or
        _number(old["cash"])!=_number(old["contributions"]) or _number(old["monthly_start_equity"])!=_number(old["cash"])):
        raise OptionsRecoveryError("Only a healthy zero-trade, zero-fee, unspent flat shared account may migrate.")
    refs=deepcopy([] if baseline is None else baseline)
    _references(refs,before=_day_at(clock),received_by=clock)
    financial={key:deepcopy(old[key]) for key in _FINANCIAL}
    migration={"from_schema":2,"from_rule_sha256":old["rule_sha256"],"old_state_sha256":old_state_sha256,"old_data_sha256":old_data_sha256,
        "old_canonical_state_sha256":_digest(old),"activated_at":clock.isoformat(),"source_id":source_id,"baseline":refs,"baseline_sha256":_digest(refs),"initial_financials":financial,"new_funding":"0"}
    result={**financial,"schema":3,"rule_sha256":RULE_SHA256,"symbol":"F","source_id":source_id,"portfolio_activation_at":clock.isoformat(),"last_decided_at":clock.isoformat(),
        "processed_count":0,"prefix_sha256":_initial_prefix(migration),"last_source_date":None,"last_received_at":None,"last_observed_at":None,"last_snapshot_sha256":None,
        "position":None,"pending":None,"hold":None,"cap_qualified":True,"last_daily_result":None,"milestone_counts":{"2x":0,"5x":0,"10x":0},"history":deepcopy(refs),"cursor_history":[],
        "enabled_strategies":list(STRATEGY_IDS),"next_strategy":0,"strategy_stats":{key:_stats() for key in STRATEGY_IDS},"migration":migration,"funding_count":0,"execution_qualified":False,"research_only":True}
    return validate_state(result)


def _milestones(value,closed):
    if type(value) is not dict or set(value)!={"2x","5x","10x"} or any(type(v) is not int or not 0<=v<=closed for v in value.values()) or not value["2x"]>=value["5x"]>=value["10x"]:
        raise OptionsRecoveryError("Realized net-payout milestones are inconsistent.")


def _equity(state):
    position=state["position"]
    return _number(state["cash"])+(max(Decimal(0),_credit(_number(position["marked_bid"])*100))-Decimal("0.65") if position else 0)


@_precision
def validate_state(state):
    """Validate lineage, bounded history, exact economics and clock separation."""
    if type(state) is not dict or set(state)!=_STATE or type(state["schema"]) is not int or state["schema"]!=3 or state["rule_sha256"]!=RULE_SHA256 or state["symbol"]!="F" or state["source_id"]!=RULE["source_id"] or state["execution_qualified"] is not False or state["research_only"] is not True:
        raise OptionsRecoveryError("The EOD ledger does not match its registered research-only contract.")
    active=_clock(state["portfolio_activation_at"]);decided=_clock(state["last_decided_at"])
    if not _clock(state["started_at"])<=active<=decided:raise OptionsRecoveryError("EOD account clocks moved backwards.")
    migration=state["migration"]
    if type(migration) is not dict or set(migration)!=_MIGRATION or migration["from_schema"]!=2 or type(migration["from_schema"]) is not int or migration["new_funding"]!="0" or migration["source_id"]!=state["source_id"] or migration["activated_at"]!=state["portfolio_activation_at"]:
        raise OptionsRecoveryError("The immutable in-place migration witness is malformed.")
    if not all(_hash(migration[key]) for key in ("from_rule_sha256","old_state_sha256","old_data_sha256","old_canonical_state_sha256","baseline_sha256")) or _digest(migration["baseline"])!=migration["baseline_sha256"]:
        raise OptionsRecoveryError("Original evidence/baseline hashes are invalid.")
    _references(migration["baseline"],before=_day_at(active),received_by=active)
    initial=migration["initial_financials"]
    if type(initial) is not dict or set(initial)!=set(_FINANCIAL) or initial["started_at"]!=state["started_at"] or initial["trade_count"]!=0 or initial["closed_trade_count"]!=0 or any(_number(initial[key])!=0 for key in ("fees","monthly_committed")):
        raise OptionsRecoveryError("Migration cannot erase old risk or change the original financial lineage.")
    _month(initial["month"])
    if _number(initial["cash"])!=_number(initial["contributions"]) or _number(initial["monthly_start_equity"])!=_number(initial["cash"]):raise OptionsRecoveryError("The initial zero-risk capital does not reconcile.")
    for key in ("processed_count","trade_count","closed_trade_count","funding_count"):
        if type(state[key]) is not int or not 0<=state[key]<=100000:raise OptionsRecoveryError("Use bounded nonnegative EOD ledger counts.")
    if state["trade_count"]-state["closed_trade_count"] not in (0,1):raise OptionsRecoveryError("Only one fully funded long option is supported.")
    if type(state["next_strategy"]) is not int or not 0<=state["next_strategy"]<3 or state["enabled_strategies"]!=list(STRATEGY_IDS):raise OptionsRecoveryError("The registered selector cannot be replaced or reordered.")
    for key in ("cash","contributions","monthly_committed","monthly_start_equity","fees"):_number(state[key])
    if _number(state["fees"])!=Decimal("0.65")*(state["trade_count"]+state["closed_trade_count"]) or _number(state["monthly_committed"])>500:
        raise OptionsRecoveryError("Fees or the shared non-recycling USD500 commitment are inconsistent.")
    if _number(state["contributions"])!=_number(initial["contributions"])+500*state["funding_count"] or _number(initial["contributions"])<500 or _number(initial["contributions"])%500:
        raise OptionsRecoveryError("Capital must reconcile to retained funding plus explicit USD500 contributions.")
    month=_month(state["month"])
    if not initial["month"]<=month<=_day_at(decided).strftime("%Y-%m") or (state["funding_count"]==0 and month!=initial["month"]):raise OptionsRecoveryError("The funded month does not reconcile with activation/decisions.")
    if not _hash(state["prefix_sha256"]) or state["last_observed_at"] is not None:raise OptionsRecoveryError("Do not invent option quote-event timestamps.")
    history=_references(state["history"])
    if any(_clock(row["received_at"])>decided for row in history) or len(history)!=min(len(migration["baseline"])+state["processed_count"],15):raise OptionsRecoveryError("The available indicator ring does not match the accepted cursor.")
    cursor=state["cursor_history"]
    if type(cursor) is not list or len(cursor)!=min(state["processed_count"],15):raise OptionsRecoveryError("Use a bounded accepted hash-chain ring.")
    previous=None;previous_day=None
    for index,row in enumerate(cursor):
        if type(row) is not dict or set(row)!={"source_date","snapshot_sha256","previous_sha256","prefix_sha256"} or not all(_hash(row[key]) for key in ("snapshot_sha256","previous_sha256","prefix_sha256")):
            raise OptionsRecoveryError("The accepted cursor witness is malformed.")
        day=_day(row["source_date"])
        if previous is not None and (row["previous_sha256"]!=previous or len(_sessions(previous_day+timedelta(days=1),day))!=1):raise OptionsRecoveryError("The accepted hash-chain is discontinuous.")
        if row["prefix_sha256"]!=_digest({"previous_sha256":row["previous_sha256"],"snapshot_sha256":row["snapshot_sha256"]}):raise OptionsRecoveryError("The accepted source prefix hash was changed.")
        if index==0 and state["processed_count"]<=15 and row["previous_sha256"]!=_initial_prefix(migration):raise OptionsRecoveryError("The accepted cursor lost its original migration anchor.")
        previous=row["prefix_sha256"];previous_day=day
    if state["processed_count"]:
        if not history or not _hash(state["last_snapshot_sha256"]) or cursor[-1]["source_date"]!=state["last_source_date"] or cursor[-1]["snapshot_sha256"]!=state["last_snapshot_sha256"] or cursor[-1]["prefix_sha256"]!=state["prefix_sha256"] or history[-1]["source_date"]!=state["last_source_date"] or history[-1]["received_at"]!=state["last_received_at"]:
            raise OptionsRecoveryError("The EOD cursor and indicator ring disagree.")
        _clock(state["last_received_at"])
    elif any(state[key] is not None for key in ("last_source_date","last_received_at","last_snapshot_sha256")) or state["prefix_sha256"]!=_initial_prefix(migration) or history!=migration["baseline"]:
        raise OptionsRecoveryError("An empty EOD cursor cannot claim observations or changed warmup.")
    if type(state["cap_qualified"]) is not bool:raise OptionsRecoveryError("Cap accounting qualification must be explicit.")
    hold=state["hold"]
    if hold is not None:
        if type(hold) is not dict or set(hold)!={"code","message","recorded_at"} or any(type(hold[key]) is not str or not 1<=len(hold[key])<=1000 for key in ("code","message")) or _clock(hold["recorded_at"])>decided or state["cap_qualified"]:
            raise OptionsRecoveryError("A held shared account cannot certify its cap accounting.")
    elif not state["cap_qualified"]:raise OptionsRecoveryError("Unqualified cap accounting requires a durable hold.")
    _milestones(state["milestone_counts"],state["closed_trade_count"])
    stats=state["strategy_stats"]
    if type(stats) is not dict or set(stats)!=set(STRATEGY_IDS):raise OptionsRecoveryError("Strategy attribution must cover all registered hypotheses.")
    for value in stats.values():
        if type(value) is not dict or set(value)!=_STATS or any(type(value[key]) is not int or not 0<=value[key]<=100000 for key in ("entry_count","closed_count")) or value["entry_count"]-value["closed_count"] not in (0,1):raise OptionsRecoveryError("Strategy counts are invalid.")
        if _number(value["fees"])!=Decimal("0.65")*(value["entry_count"]+value["closed_count"]) or _number(value["monthly_committed"])>500:raise OptionsRecoveryError("Strategy fees/commitments are invalid.")
        _number(value["realized_pnl"],signed=True);_milestones(value["milestone_counts"],value["closed_count"])
    if sum(value["entry_count"] for value in stats.values())!=state["trade_count"] or sum(value["closed_count"] for value in stats.values())!=state["closed_trade_count"] or sum((_number(value["monthly_committed"]) for value in stats.values()),Decimal(0))!=_number(state["monthly_committed"]):raise OptionsRecoveryError("Strategy attribution does not reconcile to the one shared ledger.")
    for key in ("2x","5x","10x"):
        if sum(value["milestone_counts"][key] for value in stats.values())!=state["milestone_counts"][key]:raise OptionsRecoveryError("Net payout attribution is inconsistent.")
    position=state["position"]
    if position is not None:
        if type(position) is not dict or set(position)!=_POSITION or position["strategy_id"] not in STRATEGIES or STRATEGIES[position["strategy_id"]]["type"]!=position["type"] or type(position["quantity"]) is not int or position["quantity"]!=1 or type(position["multiplier"]) is not int or position["multiplier"]!=100 or state["trade_count"]-state["closed_trade_count"]!=1:raise OptionsRecoveryError("Only one standard long contract is permitted.")
        _identity(position)
        for key in ("entry_ask","premium","entry_fee","exit_fee_reserved","budget_debit","marked_bid"):_number(position[key])
        entry=_day(position["entry_source_date"]);expiry=_day(position["expiry"]);decision=_clock(position["decision_at"]);recorded=_clock(position["entry_recorded_at"])
        if not active<=decision<recorded<=decided or entry<=_day_at(decision) or entry>_day_at(recorded) or not 7<=(expiry-entry).days<=21 or expiry.strftime("%Y-%m")!=_day_at(recorded).strftime("%Y-%m") or expiry.strftime("%Y-%m")!=month or position["forced_exit_session"]!=_deadline(entry,expiry).isoformat() or _day(position["forced_exit_session"])<=_day_at(decision):raise OptionsRecoveryError("A modeled entry crossed its actual decision/month/disposal boundary.")
        if _day(position["last_marked_source_date"])<entry or position["last_marked_source_date"]>state["last_source_date"]:raise OptionsRecoveryError("A mark must follow the entry source date.")
        if _number(position["entry_ask"])<=0 or _number(position["entry_fee"])!=Decimal("0.65") or _number(position["exit_fee_reserved"])!=Decimal("0.65") or _number(position["premium"])!=_debit(_number(position["entry_ask"])*100) or _number(position["budget_debit"])!=_number(position["premium"])+Decimal("1.30") or _number(position["budget_debit"])>100 or _number(position["budget_debit"])>_number(state["monthly_committed"]) or _number(state["cash"])<Decimal("0.65"):raise OptionsRecoveryError("The entry debit/reserved exit fee exceeds funded shared limits.")
        if stats[position["strategy_id"]]["entry_count"]-stats[position["strategy_id"]]["closed_count"]!=1:raise OptionsRecoveryError("The open strategy attribution is inconsistent.")
    elif state["trade_count"]!=state["closed_trade_count"]:raise OptionsRecoveryError("The open trade is missing its funded position.")
    expected_cash=_number(state["contributions"])+sum((_number(value["realized_pnl"],signed=True) for value in stats.values()),Decimal(0))-( _number(position["premium"])+Decimal("0.65") if position else 0)
    if _number(state["cash"])!=expected_cash or _equity(state)<_number(state["monthly_start_equity"])-500:raise OptionsRecoveryError("Cash, realized outcomes or the modeled monthly loss invariant do not reconcile.")
    pending=state["pending"]
    if pending is not None:
        if type(pending) is not dict or set(pending)!=_PENDING or pending["strategy_id"] not in STRATEGIES or pending["type"]!=STRATEGIES[pending["strategy_id"]]["type"] or pending["action"] not in ("BUY","SELL") or (pending["action"]=="SELL")!=(position is not None):raise OptionsRecoveryError("A durable pending decision disagrees with the funded risk.")
        _identity(pending);stamp=_clock(pending["decided_at"])
        if not active<=stamp<=decided or _clock(pending["signal_received_at"])>stamp or _day(pending["signal_source_date"])>_day_at(stamp) or pending["signal_source_date"]>state["last_source_date"] or type(pending["reasons"]) is not list or not pending["reasons"] or any(type(value) is not str or not 1<=len(value)<=200 for value in pending["reasons"]):raise OptionsRecoveryError("A pending decision used unavailable evidence.")
        if pending["action"]=="BUY" and (not 7<=(_day(pending["expiry"])-_day(pending["signal_source_date"])).days<=21 or pending["expiry"][:7]!=_day_at(stamp).strftime("%Y-%m")):
            raise OptionsRecoveryError("A pending entry disagrees with its registered signal DTE/month.")
        if position and pending["contract_id"]!=position["contract_id"]:raise OptionsRecoveryError("Pending sale must reference the funded contract.")
    daily=state["last_daily_result"]
    if daily is not None:
        if type(daily) is not dict or set(daily)!=_DAILY or daily["quote_observed_at"] is not None or daily["simulated"] is not True or daily["research_only"] is not True or daily["execution_qualified"] is not False or type(daily["partial_chain"]) is not bool or daily["date"]!=state["last_source_date"] or _clock(daily["recorded_at"])>decided:raise OptionsRecoveryError("Daily results must disclose the actual date-only model and cursor.")
        for key in ("equity","cash","contributions","monthly_budget_used","monthly_budget_remaining","fees"):_number(daily[key],signed=key=="equity")
        for key in ("daily_pnl","trading_pnl","monthly_pnl"):_number(daily[key],signed=True)
        if daily["cash"]!=state["cash"] or daily["contributions"]!=state["contributions"] or _number(daily["equity"],signed=True)!=_equity(state) or daily["monthly_budget_used"]!=state["monthly_committed"] or _number(daily["monthly_budget_remaining"])!=500-_number(state["monthly_committed"]):raise OptionsRecoveryError("The dated daily result does not reconcile to its financial ledger.")
    elif state["processed_count"]:raise OptionsRecoveryError("Accepted EOD evidence requires its dated daily result.")
    return deepcopy(state)


@_precision
def summarize(state,now=None):
    state=validate_state(state)
    clock=_clock(state["last_decided_at"])
    if now is not None:
        clock=_clock(now.isoformat() if isinstance(now,datetime) else now)
        if clock<_clock(state["last_decided_at"]):raise OptionsRecoveryError("A summary cannot precede the last decision.")
    equity=_equity(state)
    disposal=None
    if state["position"]:
        from .briefing import _tz
        from .qqq_data import last_closed_session
        deadline=_day(state["position"]["forced_exit_session"])
        following=_sessions(deadline+timedelta(days=1),deadline+timedelta(days=8))[0]
        # An operational collection window, never a provider publication SLA.
        publication=datetime(following.year,following.month,following.day,8,35,tzinfo=_tz("America/Chicago"))+timedelta(seconds=35)
        missing=state["last_source_date"]<deadline.isoformat()
        phase=("awaiting_source_session" if last_closed_session(clock)<deadline else
            "awaiting_next_session_publication" if clock<publication else "unverified_missing_required_source") if missing else "required_source_recorded"
        disposal={"required_source_date":deadline.isoformat(),"status":phase,"collection_window_ends_at":publication.isoformat(),"provider_publication_sla":False,"hard_clock_hold_applied":False}
    return {"schema":3,"rule_sha256":RULE_SHA256,"symbol":"F","source_id":state["source_id"],"simulated":True,"research_only":True,"execution_qualified":False,
        "cash":state["cash"],"equity":_text(equity),"contributions":state["contributions"],"trading_pnl":_text(equity-_number(state["contributions"])),"month":state["month"],"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),
        "monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),"position":deepcopy(state["position"]),"pending":deepcopy(state["pending"]),"hold":deepcopy(state["hold"]),"cap_qualified":state["cap_qualified"],
        "fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"milestone_counts":deepcopy(state["milestone_counts"]),"last_daily_result":deepcopy(state["last_daily_result"]),"last_source_date":state["last_source_date"],"last_observed_at":None,
        "processed_count":state["processed_count"],"enabled_strategies":list(STRATEGY_IDS),"strategy_stats":deepcopy(state["strategy_stats"]),"next_strategy":STRATEGY_IDS[state["next_strategy"]],"portfolio_activation_at":state["portfolio_activation_at"],
        "disposal":disposal,"cap_assurance":"unverified_missing_disposal_source" if disposal and disposal["status"]=="unverified_missing_required_source" else "held" if state["hold"] else "modeled_as_recorded",
        "execution":"Experimental EOD price proxy, unknown quote-event time, actual returned partial chain only; never live execution or real orders.",
        "cap_scope":"One shared USD500 gross monthly commitment including premium, entry fee and reserved exit fee; no profit recycling. Accounting qualification is not execution qualification or a broker guarantee."}


def _result(state,status,events):
    checked=validate_state(state)
    return {"state":checked,"status":status,"events":events,"summary":summarize(checked)}


def _hold(state,code,message,clock):
    result=deepcopy(state)
    result["hold"]={"code":code,"message":message,"recorded_at":clock.isoformat()};result["cap_qualified"]=False;result["last_decided_at"]=clock.isoformat()
    return _result(result,"held",[{"type":"held",**result["hold"]}])


def _liquid(quote,side):
    bid,ask=_number(quote["bid"]),_number(quote["ask"])
    return bid>0 and ask>0 and bid<=ask and ask-bid<=(ask+bid)/2*Decimal(".25") and quote["ask_size" if side=="BUY" else "bid_size"]>=1


def _eligible(quote,row,kind,clock):
    day=_day(row["source_date"]);expiry=_day(quote["expiry"]);spot=_number(row["underlying_close"]);strike=_number(quote["strike"])
    strike_ok=spot<=strike<=spot*Decimal("1.05") if kind=="call" else spot*Decimal(".95")<=strike<=spot
    return (spot<100 and quote["type"]==kind and 7<=(expiry-day).days<=21 and expiry.strftime("%Y-%m")==_day_at(clock).strftime("%Y-%m") and
        strike_ok and _debit(_number(quote["ask"])*100)+Decimal("1.30")<=100 and _deadline(day,expiry)>max(day,_day_at(clock)) and _liquid(quote,"BUY"))


def _signal(identifier,history):
    if len(history)<14:return False
    values=[_number(row["underlying_close"]) for row in history]
    if identifier==STRATEGY_IDS[0]:
        return values[-1]>max(values[-3:-1]) and any(values[i]<=max(values[i-10:i])*Decimal(".97") for i in range(max(10,len(values)-5),len(values)))
    before=values[-11:-1];mean=sum(before,Decimal(0))/10
    return values[-1]>max(before) and values[-1]>mean if identifier==STRATEGY_IDS[1] else values[-1]<min(before) and values[-1]<mean


def _pending(action,quote,row,clock,reasons,identifier):
    return {"strategy_id":identifier,"type":quote["type"],"action":action,"contract_id":quote["contract_id"],"decided_at":clock.isoformat(),"signal_source_date":row["source_date"],"signal_received_at":row["received_at"],"expiry":quote["expiry"],"strike":quote["strike"],"reasons":list(reasons)}


def _later(row,pending):
    return _day(row["source_date"])>_day_at(_clock(pending["decided_at"])) and _clock(row["received_at"])>_clock(pending["decided_at"])


def _sell(state,quote,row,clock,reasons,events):
    position=state["position"];identifier=position["strategy_id"];stats=state["strategy_stats"][identifier]
    gross=_credit(_number(quote["bid"])*100);net=gross-Decimal(".65");entry=_number(position["premium"])+Decimal(".65");ratio=net/entry
    state["cash"]=_text(_number(state["cash"])+net);state["fees"]=_text(_number(state["fees"])+Decimal(".65"));state["closed_trade_count"]+=1
    stats["closed_count"]+=1;stats["fees"]=_text(_number(stats["fees"])+Decimal(".65"));stats["realized_pnl"]=_text(_number(stats["realized_pnl"],signed=True)+net-entry)
    for multiple in (2,5,10):
        if ratio>=multiple:
            key=str(multiple)+"x";state["milestone_counts"][key]+=1;stats["milestone_counts"][key]+=1
    events.append({"type":"fill","action":"SELL","strategy_id":identifier,"contract_id":quote["contract_id"],"quantity":1,"multiplier":100,"price":quote["bid"],"gross_credit":_text(gross),"fee":"0.65","net_credit":_text(net),"trade_pnl":_text(net-entry),"net_payout_multiple":_text(ratio),"reasons":list(reasons),"source_date":row["source_date"],"quote_observed_at":None,"recorded_at":clock.isoformat(),"simulated":True,"research_only":True,"execution_qualified":False})
    state["position"]=None;state["pending"]=None


@_precision
def advance_paper(snapshot,state,*,decided_at):
    """Accept one current source session; never replay an unseen historical run."""
    state=validate_state(state);clock=_clock(decided_at)
    if clock<_clock(state["last_decided_at"]):raise OptionsRecoveryError("Actual durable decision clocks cannot move backwards.")
    if state["hold"]:return _result(state,"held",[])
    row=validate_snapshot(snapshot);day=_day(row["source_date"]);received=_clock(row["received_at"])
    if received>clock:raise OptionsRecoveryError("Future-received evidence is unavailable to this decision.")
    from .qqq_data import last_closed_session
    if day>last_closed_session(clock):raise OptionsRecoveryError("Uncompleted source sessions cannot be accepted.")
    digest=_digest(row)
    if state["last_source_date"]==row["source_date"]:
        if digest!=state["last_snapshot_sha256"]:return _hold(state,"accepted_source_changed","The accepted date-only snapshot changed; do not revise prior modeled work.",clock)
        return _result(state,"unchanged",[])
    if day!=last_closed_session(clock):return _hold(state,"stale_source","Source date is not the latest completed session; no historical fill adoption.",clock)
    prior_day=_day(state["history"][-1]["source_date"]) if state["history"] else None
    if prior_day is not None and (day<=prior_day or len(_sessions(prior_day+timedelta(days=1),day))!=1):return _hold(state,"missing_source_session","A required source session is missing or moved backwards; no retrospective fill.",clock)
    if state["last_received_at"] is not None and received<_clock(state["last_received_at"]):raise OptionsRecoveryError("Accepted receipt clocks cannot move backwards.")
    prior=deepcopy(state);events=[];month=_day_at(clock).strftime("%Y-%m")
    if month<state["month"]:raise OptionsRecoveryError("Actual decision predates the funded ledger month.")
    if month!=state["month"]:
        if state["position"] is not None or state["pending"] is not None:return _hold(prior,"unresolved_month_boundary","Prior-month position or durable pending decision blocks new funding and allowance.",clock)
        state["month"]=month;state["cash"]=_text(_number(state["cash"])+500);state["contributions"]=_text(_number(state["contributions"])+500);state["funding_count"]+=1
        state["monthly_start_equity"]=state["cash"];state["monthly_committed"]="0"
        for value in state["strategy_stats"].values():value["monthly_committed"]="0"
        events.append({"type":"contribution","amount":"500","month":month,"recorded_at":clock.isoformat(),"simulated":True})
    quotes={quote["contract_id"]:quote for quote in row["quotes"]};position=state["position"]
    if position:
        deadline=_day(position["forced_exit_session"])
        if day>deadline:return _hold(prior,"missed_disposal_source_session","Mandatory exact source-date disposal was not verified; no backfill or invented settlement.",clock)
        quote=quotes.get(position["contract_id"])
        if quote is None:return _hold(prior,"missing_position_quote","The held contract is absent from the returned partial chain; retain its previous dated mark.",clock)
        position["marked_bid"]=quote["bid"];position["last_marked_source_date"]=row["source_date"]
        pending=state["pending"];due=day==deadline
        eligible_due=day>_day_at(_clock(position["decision_at"])) and received>_clock(position["decision_at"])
        if due:
            if eligible_due and _liquid(quote,"SELL"):_sell(state,quote,row,clock,["precommitted_disposal_source_session"],events)
            else:return _hold(prior,"unexecutable_disposal","No actual positive sized executable bid for the mandatory source session; hold the shared account.",clock)
        elif pending and _later(row,pending):
            if _liquid(quote,"SELL"):_sell(state,quote,row,clock,pending["reasons"],events)
            else:events.append({"type":"waiting","reason":"conditional_exit_bid_unavailable","contract_id":quote["contract_id"]})
        if state["position"] and state["pending"] is None:
            bid,entry=_number(quote["bid"]),_number(position["entry_ask"])
            reasons=["bid_reached_5x_entry_ask"] if bid>=entry*5 else ["bid_at_or_below_half_entry_ask"] if bid<=entry/2 else []
            if reasons:
                state["pending"]=_pending("SELL",quote,row,clock,reasons,position["strategy_id"]);events.append({"type":"decision",**deepcopy(state["pending"])})
    elif state["pending"]:
        pending=state["pending"]
        if not _later(row,pending):events.append({"type":"waiting","reason":"source_session_not_after_actual_decision_day","decision":deepcopy(pending)})
        else:
            quote=quotes.get(pending["contract_id"])
            if quote is None or not _eligible(quote,row,pending["type"],clock):
                events.append({"type":"cancelled","reason":"entry_source_quote_unavailable_or_ineligible","decision":deepcopy(pending)});state["pending"]=None
            else:
                premium=_debit(_number(quote["ask"])*100);reserve=premium+Decimal("1.30");identifier=pending["strategy_id"]
                if reserve+_number(state["monthly_committed"])>500 or reserve>_number(state["cash"]):
                    events.append({"type":"cancelled","reason":"shared_budget_or_cash_exhausted","decision":deepcopy(pending)});state["pending"]=None
                else:
                    state["cash"]=_text(_number(state["cash"])-premium-Decimal(".65"));state["monthly_committed"]=_text(_number(state["monthly_committed"])+reserve);state["fees"]=_text(_number(state["fees"])+Decimal(".65"));state["trade_count"]+=1
                    stats=state["strategy_stats"][identifier];stats["entry_count"]+=1;stats["fees"]=_text(_number(stats["fees"])+Decimal(".65"));stats["monthly_committed"]=_text(_number(stats["monthly_committed"])+reserve);state["next_strategy"]=(STRATEGY_IDS.index(identifier)+1)%3
                    state["position"]={"strategy_id":identifier,"contract_id":quote["contract_id"],"type":quote["type"],"expiry":quote["expiry"],"strike":quote["strike"],"multiplier":100,"quantity":1,
                        "entry_source_date":row["source_date"],"entry_recorded_at":clock.isoformat(),"decision_at":pending["decided_at"],"entry_ask":quote["ask"],"premium":_text(premium),"entry_fee":"0.65","exit_fee_reserved":"0.65","budget_debit":_text(reserve),"marked_bid":quote["bid"],"last_marked_source_date":row["source_date"],"forced_exit_session":_deadline(day,_day(quote["expiry"])).isoformat()}
                    events.append({"type":"fill","action":"BUY","strategy_id":identifier,"contract_id":quote["contract_id"],"quantity":1,"multiplier":100,"price":quote["ask"],"premium":_text(premium),"fee":"0.65","exit_fee_reserved":"0.65","budget_committed":_text(reserve),"source_date":row["source_date"],"quote_observed_at":None,"recorded_at":clock.isoformat(),"decided_at":pending["decided_at"],"simulated":True,"research_only":True,"execution_qualified":False});state["pending"]=None
    state["history"]=(state["history"]+[{key:row[key] for key in _BASE}])[-15:]
    if state["position"] is None and state["pending"] is None:
        order=STRATEGY_IDS[state["next_strategy"]:]+STRATEGY_IDS[:state["next_strategy"]]
        for identifier in order:
            if not _signal(identifier,state["history"]):continue
            candidates=[quote for quote in row["quotes"] if _eligible(quote,row,STRATEGIES[identifier]["type"],clock)]
            candidates.sort(key=lambda quote:(abs((_day(quote["expiry"])-day).days-14),abs(_number(quote["strike"])-_number(row["underlying_close"])),quote["contract_id"]))
            if candidates:
                quote=candidates[0];reserve=_debit(_number(quote["ask"])*100)+Decimal("1.30")
                if reserve+_number(state["monthly_committed"])<=500 and reserve<=_number(state["cash"]):
                    state["pending"]=_pending("BUY",quote,row,clock,[identifier+"_registered_signal"],identifier);events.append({"type":"decision",**deepcopy(state["pending"])});break
    equity=_equity(state);previous=prior["last_daily_result"];prior_equity=_number(previous["equity"],signed=True) if previous and previous["month"]==month else _number(state["monthly_start_equity"])
    state["last_daily_result"]={"date":row["source_date"],"month":month,"quote_observed_at":None,"recorded_at":clock.isoformat(),"equity":_text(equity),"cash":state["cash"],"contributions":state["contributions"],"daily_pnl":_text(equity-prior_equity),"trading_pnl":_text(equity-_number(state["contributions"])),"monthly_pnl":_text(equity-_number(state["monthly_start_equity"])),"monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_text(500-_number(state["monthly_committed"])),"fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"cap_qualified":True,"simulated":True,"research_only":True,"execution_qualified":False,"partial_chain":row["partial_chain"]}
    prefix=_digest({"previous_sha256":state["prefix_sha256"],"snapshot_sha256":digest})
    state["cursor_history"]=(state["cursor_history"]+[{"source_date":row["source_date"],"snapshot_sha256":digest,"previous_sha256":state["prefix_sha256"],"prefix_sha256":prefix}])[-15:]
    state.update(processed_count=state["processed_count"]+1,prefix_sha256=prefix,last_source_date=row["source_date"],last_received_at=row["received_at"],last_snapshot_sha256=digest,last_decided_at=clock.isoformat())
    events.append({"type":"paper_daily_result",**deepcopy(state["last_daily_result"])})
    return _result(state,"completed",events)
