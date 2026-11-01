"""Schema6: one explicit total-funded KO paper ledger, never real orders.

Original schemas3/4/5 and their monetary functions are not parameterized or
reinterpreted. Only pure date, snapshot and benchmark-data helpers are reused.
The host must bind the complete immutable schema5 cutoff and each publication.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from functools import wraps

from . import options_eod as base
from . import options_root_eod as old
from . import options_stress_context as context
from . import options_bankroll_plan as sizing

OptionsBankrollEODError = base.OptionsEODError
SOURCE_ID = old.SOURCE_ID
STRATEGY_ID = POLICY_ID = "ko_bankroll_stress_breakdown_put_v1"
KERNEL_STRATEGY_ID = old.KERNEL_STRATEGY_ID
TOTAL, MONTHLY, ENTRY, FEE = Decimal("25000"), Decimal("25000"), Decimal("1000"), Decimal(".65")
MAX_STATE_BYTES, MAX_GATE_HISTORY, MAX_GATE_RECORD_BYTES = 512*1024, 16, 12*1024
RULE = {"schema":6, "id":"ko-total-bankroll-stress-eod-v1", "source_id":SOURCE_ID,
    "old_rule_sha256":old.RULE_SHA256, "base_rule_sha256":base.RULE_SHA256,
    "context_rule_sha256":context.RULE_SHA256, "sizing_rule_sha256":sizing.POLICY_SHA256,
    "strategy_id":STRATEGY_ID, "kernel_strategy_id":KERNEL_STRATEGY_ID,
    "capital":"Exactly25000 total lifetime contributions including retained funding; activation credits only25000 minus old contributions. No subsequent deposits/topups. Original balances, realized PnL, fees, counts, source prefix and schema5 cutoff remain.",
    "risk":"One fully paid standard100 KO long-put position; integer quantity=min(supplied ask size,supplied bid size,affordable quantity) at decision/fill so the whole entry mark is supported. All-in min1000,4%flat preentry net equity,unreserved cash,unused shared25000 monthly gross commitment. Both0.65 fees per contract reserved. No profit recycling within month.",
    "month":"Only a verified flat/unheld new decision month resets gross commitment and monthly-start equity; zero funding. Pending or open prior risk holds the ledger, never silently resets or sells.",
    "entry":"Unchanged14-close breakdown and causal fresh stress_unwind benchmark; KOspot<100 at decision and fill;7-21DTE,current decisionNYmonth expiry,0-5%OTM,<=25%spread. Actual later source session and receipt only; no historical/same-day adoption.",
    "exit":"Bid>=5x or<=.5x entryask creates delayed decision. Precommitted earliest10thfollowing regularsession,preexpiry session,penultimate entrymonth session. Actual executable bid_size>=ENTIRE quantity, otherwise hold; no partial fill, fabricated settlement, exercise or margin.",
    "frames":"Frames beginning with pending/position manage that risk only. New unavailable context deniesBUY; exits/marks/disposal do not require new context. Corrupt retained state remains fatal.",
    "ancestry":"Schema5 financials, root_policy, source/history/cursor/counts sealed unchanged; fresh6 context gates are new permissions over immutable benchmarkDATA. No scaled state is ever validated as5.",
    "evidence":"Partial date-only price proxy, unknown quote-event clocks/deliverables; research_only,not executionqualified or proven profitable.",
    "bounds":"512KiBstate; unchangedKO2MiB/2000quote snapshot;15stock/cursor rows;16newgates/12KiB each. No old quota or source reset.",
    "research_only":True, "execution_qualified":False}
RULE_SHA256 = base._digest(RULE)
STATE_KEYS = frozenset(old.STATE_KEYS | {"bankroll_policy","bankroll_processed_count","contract_entry_count","contract_exit_count"})
_FIN = (*base._FINANCIAL,"funding_count")
_ACT = {"from_schema","from_rule_sha256","old_canonical_state_sha256","old_state_sha256","old_data_sha256","activated_at",
    "initial_financials","initial_financials_sha256","legacy_processed_count","legacy_root_processed_count","legacy_prefix_sha256",
    "legacy_last_source_date","legacy_last_received_at","legacy_last_snapshot_sha256","legacy_history","legacy_history_sha256",
    "legacy_cursor_history","legacy_cursor_sha256","legacy_root_policy_sha256","legacy_strategy_stats","legacy_strategy_stats_sha256",
    "legacy_milestone_counts","legacy_milestone_sha256","legacy_last_daily_result","legacy_migration_sha256","legacy_portfolio_activation_at","legacy_next_strategy","bankroll_total","new_funding","no_automatic_topups"}
_STATS = base._STATS | {"entry_contracts","closed_contracts"}
_POSITION = base._POSITION | {"entry_equity","entry_available_cash","entry_monthly_committed","entry_ask_size","entry_bid_size","last_marked_bid_size"}


def _precision(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        with localcontext(Context(prec=40)):
            return fn(*args, **kwargs)
    return guarded


def _require(ok, message):
    if not ok: raise OptionsBankrollEODError(message)


def _clock(value): return base._clock(value.isoformat() if isinstance(value,datetime) else value)
def _n(value, *, signed=False):
    amount=base._number(value,signed=signed)
    _require(amount*100 == (amount*100).to_integral_value(),"Ledger cash amounts must be exact cents.")
    return amount
def _t(value): return base._text(value)
def _start(digest): return base._digest({"rule_sha256":RULE_SHA256,"activation_sha256":digest})


def _verify():
    _require(base._digest(RULE)==RULE_SHA256 and RULE["source_id"]==SOURCE_ID and RULE["strategy_id"]==STRATEGY_ID
        and RULE["kernel_strategy_id"]==KERNEL_STRATEGY_ID and RULE["old_rule_sha256"]==old.RULE_SHA256
        and RULE["base_rule_sha256"]==base.RULE_SHA256 and RULE["context_rule_sha256"]==context.RULE_SHA256
        and RULE["sizing_rule_sha256"]==sizing.POLICY_SHA256 and base._digest(old.RULE)==old.RULE_SHA256
        and base._digest(base.RULE)==base.RULE_SHA256 and base._digest(sizing.POLICY)==sizing.POLICY_SHA256,
        "The fixed bankroll/base/context/sizing contract changed.")


def validate_snapshot(snapshot):
    _verify()
    return old.validate_snapshot(snapshot)


@_precision
def migrate_flat(old5, *, activated_at, old_state_sha256, old_data_sha256):
    _verify(); prior=old.validate_state(old5); clock=_clock(activated_at)
    _require(clock>=_clock(prior["last_decided_at"]) and base._hash(old_state_sha256) and base._hash(old_data_sha256),"Use actual nondecreasing activation clock and old evidence hashes.")
    _require(prior["position"] is None and prior["pending"] is None and prior["hold"] is None and prior["cap_qualified"],"Only a healthy flat schema5 account can change its total funding contract.")
    existing=_n(prior["contributions"])
    _require(existing<=TOTAL,"Retained lifetime contributions already exceed the total bankroll; never erase or debit them.")
    delta=TOTAL-existing
    financial={k:deepcopy(prior[k]) for k in _FIN}
    act={"from_schema":5,"from_rule_sha256":old.RULE_SHA256,"old_canonical_state_sha256":base._digest(prior),
        "old_state_sha256":old_state_sha256,"old_data_sha256":old_data_sha256,"activated_at":clock.isoformat(),
        "initial_financials":financial,"initial_financials_sha256":base._digest(financial),
        "legacy_processed_count":prior["processed_count"],"legacy_root_processed_count":prior["root_processed_count"],
        "legacy_prefix_sha256":prior["prefix_sha256"],"legacy_last_source_date":prior["last_source_date"],
        "legacy_last_received_at":prior["last_received_at"],"legacy_last_snapshot_sha256":prior["last_snapshot_sha256"],
        "legacy_history":deepcopy(prior["history"]),"legacy_history_sha256":base._digest(prior["history"]),
        "legacy_cursor_history":deepcopy(prior["cursor_history"]),"legacy_cursor_sha256":base._digest(prior["cursor_history"]),
        "legacy_root_policy_sha256":base._digest(prior["root_policy"]),"legacy_strategy_stats":deepcopy(prior["strategy_stats"]),
        "legacy_strategy_stats_sha256":base._digest(prior["strategy_stats"]),"legacy_milestone_counts":deepcopy(prior["milestone_counts"]),
        "legacy_milestone_sha256":base._digest(prior["milestone_counts"]),"legacy_last_daily_result":deepcopy(prior["last_daily_result"]),
        "legacy_migration_sha256":base._digest(prior["migration"]),"legacy_portfolio_activation_at":prior["portfolio_activation_at"],"legacy_next_strategy":prior["next_strategy"],
        "bankroll_total":"25000","new_funding":_t(delta),"no_automatic_topups":True}
    activation_hash=base._digest(act); result=deepcopy(prior)
    result.update(schema=6,rule_sha256=RULE_SHA256,cash=_t(_n(prior["cash"])+delta),contributions="25000",
        monthly_start_equity=_t(_n(prior["monthly_start_equity"])+delta),last_decided_at=clock.isoformat(),last_daily_result=None,
        contract_entry_count=prior["trade_count"],contract_exit_count=prior["closed_trade_count"],bankroll_processed_count=0,
        bankroll_policy={"schema":1,"rule_sha256":RULE_SHA256,"activation":act,"activation_sha256":activation_hash,
            "context_count":0,"context_prefix_sha256":_start(activation_hash),"context_history":[]})
    for stats in result["strategy_stats"].values():
        stats.update(entry_contracts=stats["entry_count"],closed_contracts=stats["closed_count"])
    return validate_state(result)


def _equity(state):
    position=state["position"]
    return _n(state["cash"])+(base._credit(base._number(position["marked_bid"])*100)*position["quantity"]-FEE*position["quantity"] if position else 0)


def _validate_gate(gate):
    keys={"schema","kind","policy_id","rule_sha256","underlying_symbol","benchmark_gate","allowed","research_only","execution_qualified","gate_sha256"}
    _require(type(gate) is dict and set(gate)==keys and type(gate["schema"]) is int and gate["schema"]==1
        and gate["kind"]=="bankroll_bound_stress_context_gate" and gate["policy_id"]==POLICY_ID and gate["rule_sha256"]==RULE_SHA256
        and gate["underlying_symbol"]=="KO" and gate["research_only"] is True and gate["execution_qualified"] is False,
        "Malformed bankroll-bound context gate.")
    benchmark=context.validate_gate_result(gate["benchmark_gate"])
    _require(type(gate["allowed"]) is bool and gate["allowed"]==benchmark["allowed"] and base._hash(gate["gate_sha256"])
        and gate["gate_sha256"]==base._digest({k:v for k,v in gate.items() if k!="gate_sha256"}),"Bankroll gate lost its actual benchmark query.")
    return gate


def _cursor(state, act, count, decided):
    history=base._references(state["history"])
    initial=base._references(act["legacy_history"],received_by=_clock(act["activated_at"]))
    _require(len(history)==min(len(initial)+count,15) and all(_clock(r["received_at"])<=decided for r in history),"Bankroll indicator history is discontinuous or unavailable.")
    cursor=state["cursor_history"]; legacy=act["legacy_cursor_history"]
    _require(type(legacy) is list and type(cursor) is list and len(cursor)==min(act["legacy_root_processed_count"]+count,15)
        and len(legacy)==min(act["legacy_root_processed_count"],15) and base._hash(state["prefix_sha256"]),"Bankroll source cursor cannot reset original counts.")
    previous=previous_day=None
    for row in cursor:
        _require(type(row) is dict and set(row)=={"source_date","snapshot_sha256","previous_sha256","prefix_sha256"}
            and all(base._hash(row[k]) for k in ("snapshot_sha256","previous_sha256","prefix_sha256")),"Malformed retained source cursor.")
        day=base._day(row["source_date"])
        _require(row["prefix_sha256"]==base._digest({"previous_sha256":row["previous_sha256"],"snapshot_sha256":row["snapshot_sha256"]})
            and (previous is None or row["previous_sha256"]==previous and len(base._sessions(previous_day+timedelta(days=1),day))==1),"Bankroll source prefix changed.")
        previous,previous_day=row["prefix_sha256"],day
    if count<15:
        prefix=legacy[-(15-count):]
        retained=max(0,len(history)-count)
        _require(cursor[:len(prefix)]==prefix and (not retained or history[:retained]==initial[-retained:]),"Retained pre-bankroll source/history prefix changed.")
    if count:
        _require(history and cursor and state["last_source_date"]==history[-1]["source_date"]==cursor[-1]["source_date"]
            and state["last_received_at"]==history[-1]["received_at"] and state["last_snapshot_sha256"]==cursor[-1]["snapshot_sha256"]
            and state["prefix_sha256"]==cursor[-1]["prefix_sha256"],"Bankroll current cursor and dated history disagree.")
        if count<=15:
            first=cursor[-count]
            _require(first["previous_sha256"]==act["legacy_prefix_sha256"] and (act["legacy_last_source_date"] is None or
                len(base._sessions(base._day(act["legacy_last_source_date"])+timedelta(days=1),base._day(first["source_date"])))==1),"New bankroll source lost the schema5 cutoff.")
    else:
        _require(history==initial and cursor==legacy and state["prefix_sha256"]==act["legacy_prefix_sha256"]
            and all(state[k]==act["legacy_"+k] for k in ("last_source_date","last_received_at","last_snapshot_sha256")),"Initial bankroll epoch cannot rewrite accepted observations.")


def _financial(state, act, active, decided):
    for key in ("processed_count","root_processed_count","bankroll_processed_count","trade_count","closed_trade_count","funding_count","contract_entry_count","contract_exit_count"):
        _require(type(state[key]) is int and 0<=state[key]<= (100000000 if key.startswith("contract_") else 100000),"Use bounded exact ledger counts.")
    for key in ("cash","contributions","monthly_committed","monthly_start_equity","fees"): _n(state[key])
    initial=act["initial_financials"]
    _require(type(initial) is dict and set(initial)==set(_FIN) and base._digest(initial)==act["initial_financials_sha256"],"Original financial witness changed.")
    _require(state["funding_count"]==initial["funding_count"] and _n(state["contributions"])==TOTAL
        and _n(initial["contributions"])+_n(act["new_funding"])==TOTAL and _n(initial["contributions"])<=TOTAL,
        "Lifetime total funding is25000; no monthly or duplicate credits.")
    _require(state["started_at"]==initial["started_at"] and state["trade_count"]>=initial["trade_count"]==initial["closed_trade_count"]
        and state["closed_trade_count"]>=initial["closed_trade_count"] and state["trade_count"]-state["closed_trade_count"] in (0,1)
        and _n(state["monthly_committed"])<=MONTHLY,"One paid position and one shared monthly gross allowance are required.")
    _require(base._month(initial["month"])<=base._month(state["month"])<=base._day_at(decided).strftime("%Y-%m")
        and state["enabled_strategies"]==list(base.STRATEGY_IDS) and type(state["next_strategy"]) is int and state["next_strategy"]==act["legacy_next_strategy"],
        "The retained month/strategy attribution cannot be replaced.")
    if state["month"]==initial["month"]:
        _require(_n(state["monthly_committed"])>=_n(initial["monthly_committed"]),"A funding upgrade cannot erase existing same-month gross commitments.")
    if state["bankroll_processed_count"]==0:
        _require(state["cash"]==_t(_n(initial["cash"])+_n(act["new_funding"])) and state["monthly_start_equity"]==_t(_n(initial["monthly_start_equity"])+_n(act["new_funding"]))
            and all(state[k]==initial[k] for k in ("month","monthly_committed","fees","trade_count","closed_trade_count")),"Initial funding cannot erase past losses, spending or fees.")
    _require(type(state["cap_qualified"]) is bool,"Accounting qualification must be explicit.")
    hold=state["hold"]
    if hold is not None:
        _require(type(hold) is dict and set(hold)=={"code","message","recorded_at"} and all(type(hold[k]) is str and 1<=len(hold[k])<=1000 for k in ("code","message"))
            and active<=_clock(hold["recorded_at"])<=decided and not state["cap_qualified"],"A substantive hold cannot certify accounting.")
    else: _require(state["cap_qualified"],"Unqualified accounting needs a retained hold.")
    stats=state["strategy_stats"]
    _require(type(stats) is dict and set(stats)==set(base.STRATEGY_IDS),"Retain exact strategy attribution.")
    base._milestones(state["milestone_counts"],state["closed_trade_count"])
    _require(all(state["milestone_counts"][k]>=act["legacy_milestone_counts"][k] for k in ("2x","5x","10x")),"Legacy payout milestone counters cannot be erased.")
    for identifier,value in stats.items():
        _require(type(value) is dict and set(value)==_STATS and all(type(value[k]) is int and 0<=value[k]<=100000000 for k in ("entry_count","closed_count","entry_contracts","closed_contracts"))
            and value["entry_count"]-value["closed_count"] in (0,1) and value["entry_contracts"]>=value["closed_contracts"]
            and value["entry_contracts"]>=value["entry_count"] and value["closed_contracts"]>=value["closed_count"],"Strategy contracts and position counts do not reconcile.")
        _require(_n(value["fees"])==FEE*(value["entry_contracts"]+value["closed_contracts"]) and _n(value["monthly_committed"])<=MONTHLY,"Per-contract fees or commitments changed.")
        _n(value["realized_pnl"],signed=True);base._milestones(value["milestone_counts"],value["closed_count"])
        before=act["legacy_strategy_stats"][identifier]
        _require(value["entry_count"]>=before["entry_count"] and value["closed_count"]>=before["closed_count"]
            and all(value["milestone_counts"][k]>=before["milestone_counts"][k] for k in ("2x","5x","10x"))
            and (state["month"]!=initial["month"] or _n(value["monthly_committed"])>=_n(before["monthly_committed"])),"Original strategy counters/spending cannot be reset.")
        if identifier!=KERNEL_STRATEGY_ID:
            _require(value["entry_count"]==before["entry_count"] and value["closed_count"]==before["closed_count"]
                and value["entry_contracts"]==before["entry_count"] and value["closed_contracts"]==before["closed_count"]
                and value["fees"]==before["fees"] and value["realized_pnl"]==before["realized_pnl"] and value["milestone_counts"]==before["milestone_counts"]
                and _n(value["monthly_committed"])==0,"Production bankroll policy cannot create call exposure or spending.")
    for field,total in (("entry_count","trade_count"),("closed_count","closed_trade_count"),("entry_contracts","contract_entry_count"),("closed_contracts","contract_exit_count")):
        _require(sum(v[field] for v in stats.values())==state[total],"Shared counts disagree with strategy attribution.")
    _require(_n(state["fees"])==FEE*(state["contract_entry_count"]+state["contract_exit_count"])
        and sum((_n(v["monthly_committed"]) for v in stats.values()),Decimal(0))==_n(state["monthly_committed"]),"Shared fees/gross commitment do not reconcile.")
    for key in ("2x","5x","10x"):
        _require(sum(v["milestone_counts"][key] for v in stats.values())==state["milestone_counts"][key],"Milestone attribution changed.")
    position=state["position"]
    if position is not None:
        _require(type(position) is dict and set(position)==_POSITION and position["strategy_id"]==KERNEL_STRATEGY_ID and position["type"]=="put"
            and type(position["quantity"]) is int and 1<=position["quantity"]<=1000 and type(position["multiplier"]) is int and position["multiplier"]==100
            and state["trade_count"]-state["closed_trade_count"]==1,"Only one fully paid KO put position is allowed.")
        old._identity(position);q=position["quantity"]
        entry=base._day(position["entry_source_date"]);expiry=base._day(position["expiry"]);decision=_clock(position["decision_at"]);recorded=_clock(position["entry_recorded_at"])
        _require(active<=decision<recorded<=decided and entry>base._day_at(decision) and entry<=base._day_at(recorded)
            and 7<=(expiry-entry).days<=21 and expiry.strftime("%Y-%m")==base._day_at(recorded).strftime("%Y-%m")==state["month"]
            and position["forced_exit_session"]==base._deadline(entry,expiry).isoformat() and base._day(position["forced_exit_session"])>base._day_at(decision),"Entry moved across its decision/month/disposal boundary.")
        _require(base._day(position["last_marked_source_date"])>=entry and position["last_marked_source_date"]<=state["last_source_date"],"Position mark predates entry or exceeds accepted evidence.")
        _require(all(type(position[k]) is int and q<=position[k]<=100000000 for k in ("entry_ask_size","entry_bid_size","last_marked_bid_size")),"The whole entry/current liquidation mark needs actual supplied side depth.")
        size=sizing.size(ask=position["entry_ask"],ask_size=min(position["entry_ask_size"],position["entry_bid_size"]),cash=position["entry_available_cash"],equity=position["entry_equity"],monthly_committed=position["entry_monthly_committed"])
        _require(size["quantity"]==q and _n(position["entry_equity"])==_n(position["entry_available_cash"])
            and _n(position["entry_available_cash"])==_n(state["cash"])+_n(position["premium"])+_n(position["entry_fee"])
            and _n(position["premium"])==_n(size["premium"]) and _n(position["entry_fee"])==_n(position["exit_fee_reserved"])==q*FEE
            and _n(position["budget_debit"])==_n(size["all_in_commitment"]) and _n(position["budget_debit"])<=_n(state["monthly_committed"])
            and _n(state["cash"])>=q*FEE,"Quantity/risk/cash/fee reserve exceeds the verified entry budget.")
        _require(state["contract_entry_count"]-state["contract_exit_count"]==q and stats[KERNEL_STRATEGY_ID]["entry_contracts"]-stats[KERNEL_STRATEGY_ID]["closed_contracts"]==q,"Open contract quantity is missing from shared attribution.")
    else:
        _require(state["trade_count"]==state["closed_trade_count"] and state["contract_entry_count"]==state["contract_exit_count"],"Flat state cannot hide an open trade/contract.")
    expected=TOTAL+sum((_n(v["realized_pnl"],signed=True) for v in stats.values()),Decimal(0))-(_n(position["premium"])+_n(position["entry_fee"]) if position else 0)
    _require(_n(state["cash"])==expected and _equity(state)>=_n(state["monthly_start_equity"])-MONTHLY,"Cash, realized results or monthly gross loss bound do not reconcile.")
    pending=state["pending"]
    if pending is not None:
        _require(type(pending) is dict and set(pending)==base._PENDING and pending["strategy_id"]==KERNEL_STRATEGY_ID and pending["type"]=="put"
            and pending["action"] in ("BUY","SELL") and (pending["action"]=="SELL")==bool(position),"Pending work disagrees with the one funded exposure.")
        old._identity(pending);stamp=_clock(pending["decided_at"])
        _require(active<=stamp<=decided and _clock(pending["signal_received_at"])<=stamp and base._day(pending["signal_source_date"])<=base._day_at(stamp)
            and pending["signal_source_date"]<=state["last_source_date"] and type(pending["reasons"]) is list and 1<=len(pending["reasons"])<=8
            and all(type(v) is str and 1<=len(v)<=200 for v in pending["reasons"]),"Pending work used unavailable evidence.")
        if pending["action"]=="BUY":
            _require(7<=(base._day(pending["expiry"])-base._day(pending["signal_source_date"])).days<=21 and pending["expiry"][:7]==base._day_at(stamp).strftime("%Y-%m"),"Pending BUY changed its month/DTE.")
        if position:_require(pending["contract_id"]==position["contract_id"],"Pending SELL is not the funded contract.")
    daily=state["last_daily_result"]
    if state["bankroll_processed_count"]:
        _require(type(daily) is dict and set(daily)==base._DAILY and daily["date"]==state["last_source_date"] and daily["month"]==state["month"]
            and daily["quote_observed_at"] is None and daily["simulated"] is True and daily["research_only"] is True and daily["execution_qualified"] is False
            and type(daily["partial_chain"]) is bool and active<=_clock(daily["recorded_at"])<=decided,"Dated results lost their source/account identity.")
        _require(daily["cash"]==state["cash"] and daily["contributions"]==state["contributions"] and _n(daily["equity"])==_equity(state)
            and daily["monthly_budget_used"]==state["monthly_committed"] and _n(daily["monthly_budget_remaining"])==MONTHLY-_n(state["monthly_committed"])
            and daily["fees"]==state["fees"] and daily["trade_count"]==state["trade_count"] and daily["closed_trade_count"]==state["closed_trade_count"],"Daily accounting disagrees with the shared ledger.")
        _require(_n(daily["trading_pnl"],signed=True)==_equity(state)-TOTAL and _n(daily["monthly_pnl"],signed=True)==_equity(state)-_n(state["monthly_start_equity"]),"Capital adjustment was misreported as trading PnL.")
        _n(daily["daily_pnl"],signed=True)
    else:_require(daily is None,"An activation is not a new market-data daily result.")


@_precision
def validate_state(state):
    _verify();old._bounded(state,MAX_STATE_BYTES)
    _require(type(state) is dict and set(state)==STATE_KEYS and type(state["schema"]) is int and state["schema"]==6
        and state["rule_sha256"]==RULE_SHA256 and state["symbol"]=="KO" and state["source_id"]==SOURCE_ID
        and state["research_only"] is True and state["execution_qualified"] is False,"Use the exact registered schema6 KO state.")
    policy=state["bankroll_policy"]
    _require(type(policy) is dict and set(policy)==old._POLICY and type(policy["schema"]) is int and policy["schema"]==1 and policy["rule_sha256"]==RULE_SHA256,"Malformed bankroll policy.")
    act=policy["activation"]
    _require(type(act) is dict and set(act)==_ACT and type(act["from_schema"]) is int and act["from_schema"]==5 and act["from_rule_sha256"]==old.RULE_SHA256
        and act["bankroll_total"]=="25000" and act["no_automatic_topups"] is True and base._digest(act)==policy["activation_sha256"],"The sealed old5 funding contract changed.")
    for key in ("old_canonical_state_sha256","old_state_sha256","old_data_sha256","initial_financials_sha256","legacy_prefix_sha256",
        "legacy_history_sha256","legacy_cursor_sha256","legacy_root_policy_sha256","legacy_strategy_stats_sha256","legacy_milestone_sha256","legacy_migration_sha256"):
        _require(base._hash(act[key]),"Bankroll ancestry requires exact retained evidence hashes.")
    _require(base._digest(act["legacy_history"])==act["legacy_history_sha256"] and base._digest(act["legacy_cursor_history"])==act["legacy_cursor_sha256"]
        and base._digest(state["root_policy"])==act["legacy_root_policy_sha256"] and base._digest(act["legacy_strategy_stats"])==act["legacy_strategy_stats_sha256"]
        and base._digest(act["legacy_milestone_counts"])==act["legacy_milestone_sha256"] and base._digest(state["migration"])==act["legacy_migration_sha256"]
        and state["portfolio_activation_at"]==act["legacy_portfolio_activation_at"],"Original root history/policy/financial attribution changed.")
    active,decided=_clock(act["activated_at"]),_clock(state["last_decided_at"])
    _require(_clock(state["started_at"])<=_clock(state["portfolio_activation_at"])<=active<=decided and state["last_observed_at"] is None,"Bankroll clocks or unknown quote-event time changed.")
    base._validate_migration(state,source_id=base.RULE["source_id"])
    count=state["bankroll_processed_count"]
    _require(type(count) is int and 0<=count<=100000 and all(type(act[k]) is int and 0<=act[k]<=100000 for k in ("legacy_processed_count","legacy_root_processed_count"))
        and state["processed_count"]==act["legacy_processed_count"]+count and state["root_processed_count"]==act["legacy_root_processed_count"]+count,"Funding upgrade cannot reset processed counts.")
    _cursor(state,act,count,decided);_financial(state,act,active,decided)
    gates,n=policy["context_history"],policy["context_count"]
    _require(type(n) is int and 2*(state["trade_count"]-act["initial_financials"]["trade_count"])<=n<=2*count
        and type(gates) is list and len(gates)==min(n,MAX_GATE_HISTORY) and base._hash(policy["context_prefix_sha256"]),"Fresh bankroll permission count changed.")
    previous=previous_clock=None
    for i,record in enumerate(gates):
        _require(type(record) is dict and set(record)==old._GATE and type(record["sequence"]) is int and record["sequence"]==n-len(gates)+i+1
            and record["stage"] in ("decision","fill") and type(record["context_input_rejected"]) is bool and base._hash(record["previous_sha256"]),"Malformed bankroll permission record.")
        old._bounded(record,MAX_GATE_RECORD_BYTES);stamp=_clock(record["recorded_at"]);base._day(record["source_date"])
        gate=_validate_gate(record["gate"]);benchmark=gate["benchmark_gate"]
        _require(active<=stamp<=decided and record["source_date"]<=state["last_source_date"] and (previous_clock is None or stamp>=previous_clock)
            and benchmark["stage"]==record["stage"] and _clock(benchmark["decision_at"])==stamp
            and benchmark["source_date"]==(record["source_date"] if record["stage"]=="fill" else None)
            and (not record["context_input_rejected"] or not gate["allowed"]),"Permission used a mismatched or unavailable context query.")
        _require(record["prefix_sha256"]==base._digest({k:v for k,v in record.items() if k!="prefix_sha256"}) and (previous is None or record["previous_sha256"]==previous),"Bankroll permission chain changed.")
        if i==0 and n<=MAX_GATE_HISTORY:_require(record["previous_sha256"]==_start(policy["activation_sha256"]),"Fresh bankroll gates lost their new activation anchor.")
        previous,previous_clock=record["prefix_sha256"],stamp
    _require(policy["context_prefix_sha256"]==(previous or _start(policy["activation_sha256"])),"Bankroll permission tail changed.")
    for exposure,stage,clock_key,source_key in ((state["pending"],"decision","decided_at","signal_source_date"),(state["position"],"fill","entry_recorded_at","entry_source_date")):
        if exposure is not None and (stage=="fill" or exposure["action"]=="BUY"):
            _require(any(r["stage"]==stage and r["gate"]["allowed"] and r["recorded_at"]==exposure[clock_key] and r["source_date"]==exposure[source_key] for r in gates),"Exposure lacks a fresh schema6-bound permission.")
    _verify();return deepcopy(state)


def _liquid(quote, side, quantity=1):
    bid,ask=base._number(quote["bid"]),base._number(quote["ask"])
    return bid>0 and ask>0 and bid<=ask and ask-bid<=(bid+ask)/2*Decimal(".25") and quote["ask_size" if side=="BUY" else "bid_size"]>=quantity


def _size(state,quote):
    return sizing.size(ask=quote["ask"],ask_size=min(quote["ask_size"],quote["bid_size"]),cash=state["cash"],equity=_t(_equity(state)),monthly_committed=state["monthly_committed"])


def _eligible(quote,row,kind,clock,state):
    day,expiry=base._day(row["source_date"]),base._day(quote["expiry"])
    spot,strike=base._number(row["underlying_close"]),base._number(quote["strike"])
    strike_ok=spot<=strike<=spot*Decimal("1.05") if kind=="call" else spot*Decimal(".95")<=strike<=spot
    return spot<100 and quote["type"]==kind and 7<=(expiry-day).days<=21 and expiry.strftime("%Y-%m")==base._day_at(clock).strftime("%Y-%m") and strike_ok and base._deadline(day,expiry)>max(day,base._day_at(clock)) and _liquid(quote,"BUY") and _size(state,quote)["quantity"]>0


def _hold_raw(state,code,message,clock):
    value=deepcopy(state);value.update(hold={"code":code,"message":message,"recorded_at":clock.isoformat()},cap_qualified=False,last_decided_at=clock.isoformat())
    return {"state":value,"status":"held","events":[{"type":"held",**value["hold"]}],"accepted":False}


def _sell(state,quote,row,clock,reasons,events):
    position=state["position"];q=position["quantity"];identifier=position["strategy_id"];stats=state["strategy_stats"][identifier]
    gross=base._credit(base._number(quote["bid"])*100)*q;fee=FEE*q;net=gross-fee;entry=_n(position["premium"])+_n(position["entry_fee"]);ratio=net/entry
    state.update(cash=_t(_n(state["cash"])+net),fees=_t(_n(state["fees"])+fee),closed_trade_count=state["closed_trade_count"]+1,contract_exit_count=state["contract_exit_count"]+q)
    stats.update(closed_count=stats["closed_count"]+1,closed_contracts=stats["closed_contracts"]+q,fees=_t(_n(stats["fees"])+fee),realized_pnl=_t(_n(stats["realized_pnl"],signed=True)+net-entry))
    for multiple in (2,5,10):
        if ratio>=multiple:
            key=str(multiple)+"x";state["milestone_counts"][key]+=1;stats["milestone_counts"][key]+=1
    events.append({"type":"fill","action":"SELL","strategy_id":identifier,"contract_id":quote["contract_id"],"quantity":q,"multiplier":100,"price":quote["bid"],"gross_credit":_t(gross),"fee":_t(fee),"net_credit":_t(net),"trade_pnl":_t(net-entry),"net_payout_multiple":_t(ratio),"reasons":list(reasons),"source_date":row["source_date"],"quote_observed_at":None,"recorded_at":clock.isoformat(),"simulated":True,"research_only":True,"execution_qualified":False})
    state["position"]=state["pending"]=None


@_precision
def _step_capital(row,state,*,decided_at,signal_ids=(KERNEL_STRATEGY_ID,),decision_gate=None,fill_gate=None):
    """Trusted pure money step; wrappers own source/identity/permission validation."""
    clock=_clock(decided_at);day=base._day(row["source_date"]);received=_clock(row["received_at"])
    prior=deepcopy(state);state=deepcopy(state);events=[];month=base._day_at(clock).strftime("%Y-%m")
    _require(month>=state["month"],"Decision predates the current accounting month.")
    manage_existing=state["position"] is not None or state["pending"] is not None
    def allowed(fn,identifier):
        value=True if fn is None else fn(identifier,row,clock)
        _require(type(value) is bool,"Trusted entry gate must return an exact boolean.");return value
    if month!=state["month"]:
        if manage_existing:return _hold_raw(prior,"unresolved_month_boundary","Prior risk blocks gross-budget rollover; no deposits or invented sale.",clock)
        state.update(month=month,monthly_start_equity=state["cash"],monthly_committed="0")
        for stats in state["strategy_stats"].values():stats["monthly_committed"]="0"
        events.append({"type":"monthly_allowance_reset","month":month,"amount":"0","funding_added":"0","recorded_at":clock.isoformat(),"simulated":True})
    quotes={q["contract_id"]:q for q in row["quotes"]};position=state["position"]
    if position:
        deadline=base._day(position["forced_exit_session"])
        if day>deadline:return _hold_raw(prior,"missed_disposal_source_session","Required exact disposal session was not verified; no backfill.",clock)
        quote=quotes.get(position["contract_id"])
        if quote is None:return _hold_raw(prior,"missing_position_quote","Held contract absent from partial source; retain its last dated mark.",clock)
        if not _liquid(quote,"SELL",position["quantity"]):return _hold_raw(prior,"insufficient_position_bid_size_or_quote","Actual executable bid/size does not cover the entire quantity; no partial fill or invented liquidation mark.",clock)
        position["marked_bid"]=quote["bid"];position["last_marked_source_date"]=row["source_date"];position["last_marked_bid_size"]=quote["bid_size"]
        pending=state["pending"]
        if day==deadline:
            if day>base._day_at(_clock(position["decision_at"])) and received>_clock(position["decision_at"]):_sell(state,quote,row,clock,["precommitted_disposal_source_session"],events)
            else:return _hold_raw(prior,"unexecutable_disposal","Mandatory source session is not later than the actual entry decision.",clock)
        elif pending and base._later(row,pending):_sell(state,quote,row,clock,pending["reasons"],events)
        if state["position"] is not None and state["pending"] is None:
            bid,ask=base._number(quote["bid"]),base._number(position["entry_ask"])
            reasons=["bid_reached_5x_entry_ask"] if bid>=ask*5 else ["bid_at_or_below_half_entry_ask"] if bid<=ask/2 else []
            if reasons:state["pending"]=base._pending("SELL",quote,row,clock,reasons,position["strategy_id"]);events.append({"type":"decision",**deepcopy(state["pending"])})
    elif state["pending"]:
        pending=state["pending"]
        if not base._later(row,pending):events.append({"type":"waiting","reason":"source_session_not_after_actual_decision_day","decision":deepcopy(pending)})
        elif not allowed(fill_gate,pending["strategy_id"]):events.append({"type":"cancelled","reason":"entry_policy_denied","decision":deepcopy(pending)});state["pending"]=None
        else:
            quote=quotes.get(pending["contract_id"])
            if quote is None or not _eligible(quote,row,pending["type"],clock,state):events.append({"type":"cancelled","reason":"entry_source_quote_unavailable_or_ineligible","decision":deepcopy(pending)});state["pending"]=None
            else:
                sized=_size(state,quote);q=sized["quantity"];identifier=pending["strategy_id"]
                premium,fee,reserve=_n(sized["premium"]),_n(sized["entry_fee"]),_n(sized["all_in_commitment"])
                entry_cash,entry_equity,entry_spent=state["cash"],_t(_equity(state)),state["monthly_committed"]
                state.update(cash=_t(_n(state["cash"])-premium-fee),monthly_committed=_t(_n(state["monthly_committed"])+reserve),fees=_t(_n(state["fees"])+fee),trade_count=state["trade_count"]+1,contract_entry_count=state["contract_entry_count"]+q)
                stats=state["strategy_stats"][identifier];stats.update(entry_count=stats["entry_count"]+1,entry_contracts=stats["entry_contracts"]+q,fees=_t(_n(stats["fees"])+fee),monthly_committed=_t(_n(stats["monthly_committed"])+reserve))
                state["position"]={"strategy_id":identifier,"contract_id":quote["contract_id"],"type":quote["type"],"expiry":quote["expiry"],"strike":quote["strike"],"multiplier":100,"quantity":q,"entry_source_date":row["source_date"],"entry_recorded_at":clock.isoformat(),"decision_at":pending["decided_at"],"entry_ask":quote["ask"],"premium":_t(premium),"entry_fee":_t(fee),"exit_fee_reserved":_t(fee),"budget_debit":_t(reserve),"marked_bid":quote["bid"],"last_marked_source_date":row["source_date"],"last_marked_bid_size":quote["bid_size"],"forced_exit_session":base._deadline(day,base._day(quote["expiry"])).isoformat(),"entry_equity":entry_equity,"entry_available_cash":entry_cash,"entry_monthly_committed":entry_spent,"entry_ask_size":quote["ask_size"],"entry_bid_size":quote["bid_size"]}
                events.append({"type":"fill","action":"BUY","strategy_id":identifier,"contract_id":quote["contract_id"],"quantity":q,"multiplier":100,"price":quote["ask"],"premium":_t(premium),"fee":_t(fee),"exit_fee_reserved":_t(fee),"budget_committed":_t(reserve),"source_date":row["source_date"],"quote_observed_at":None,"recorded_at":clock.isoformat(),"decided_at":pending["decided_at"],"simulated":True,"research_only":True,"execution_qualified":False});state["pending"]=None
    state["history"]=(state["history"]+[{k:row[k] for k in base._BASE}])[-15:]
    if not manage_existing and state["position"] is None and state["pending"] is None:
        for identifier in signal_ids:
            if not allowed(decision_gate,identifier) or not base._signal(identifier,state["history"]):continue
            quotes2=[q for q in row["quotes"] if _eligible(q,row,base.STRATEGIES[identifier]["type"],clock,state)]
            quotes2.sort(key=lambda q:(abs((base._day(q["expiry"])-day).days-14),abs(base._number(q["strike"])-base._number(row["underlying_close"])),q["contract_id"]))
            if quotes2:state["pending"]=base._pending("BUY",quotes2[0],row,clock,[identifier+"_registered_signal"],identifier);events.append({"type":"decision",**deepcopy(state["pending"])});break
    equity=_equity(state);previous=prior["last_daily_result"]
    previous_equity=_n(previous["equity"],signed=True) if previous and previous["month"]==month else _n(state["monthly_start_equity"])
    if previous is None and "bankroll_policy" in prior and prior["bankroll_processed_count"]==0:
        act=prior["bankroll_policy"]["activation"];legacy=act["legacy_last_daily_result"]
        if legacy and legacy["month"]==month:previous_equity=_n(legacy["equity"],signed=True)+_n(act["new_funding"])
    state["last_daily_result"]={"date":row["source_date"],"month":month,"quote_observed_at":None,"recorded_at":clock.isoformat(),"equity":_t(equity),"cash":state["cash"],"contributions":state["contributions"],"daily_pnl":_t(equity-previous_equity),"trading_pnl":_t(equity-_n(state["contributions"])),"monthly_pnl":_t(equity-_n(state["monthly_start_equity"])),"monthly_budget_used":state["monthly_committed"],"monthly_budget_remaining":_t(MONTHLY-_n(state["monthly_committed"])),"fees":state["fees"],"trade_count":state["trade_count"],"closed_trade_count":state["closed_trade_count"],"cap_qualified":True,"simulated":True,"research_only":True,"execution_qualified":False,"partial_chain":row["partial_chain"]}
    return {"state":state,"status":"completed","events":events,"accepted":True}


def _result(state,status,events):
    checked=validate_state(state)
    return {"state":checked,"status":status,"events":events,"summary":summarize(checked)}


@_precision
def advance_paper(snapshot,state,*,decided_at,contexts=()):
    checked=validate_state(state);clock=_clock(decided_at)
    _require(clock>=_clock(checked["last_decided_at"]),"Actual durable decision clock moved backwards.")
    if checked["hold"]:return _result(checked,"held",[])
    row=validate_snapshot(snapshot);day=base._day(row["source_date"]);received=_clock(row["received_at"])
    _require(received<=clock,"Future-received input is unavailable.")
    from .qqq_data import last_closed_session
    _require(day<=last_closed_session(clock),"Uncompleted source session.")
    digest=base._digest(row)
    if row["source_date"]==checked["last_source_date"]:
        if digest!=checked["last_snapshot_sha256"]:return _result(_hold_raw(checked,"accepted_source_changed","Accepted snapshot changed; no replay or revision.",clock)["state"],"held",[])
        return _result(checked,"unchanged",[])
    if day!=last_closed_session(clock):return _result(_hold_raw(checked,"stale_source","Source is not the latest closed session; no retrospective fill.",clock)["state"],"held",[])
    previous=checked["history"][-1]["source_date"] if checked["history"] else checked["last_source_date"]
    if previous is not None and (day<=base._day(previous) or len(base._sessions(base._day(previous)+timedelta(days=1),day))!=1):
        return _result(_hold_raw(checked,"missing_source_session","A required source session is missing; warmup cannot repair financial frames.",clock)["state"],"held",[])
    _require(checked["last_received_at"] is None or received>=_clock(checked["last_received_at"]),"Accepted receipt clock moved backwards.")
    policy=deepcopy(checked["bankroll_policy"]);evaluations=[]
    def gate(identifier,data,stamp,*,stage):
        if identifier!=KERNEL_STRATEGY_ID:return False
        query={"decision_at":clock,"source_date":data["source_date"] if stage=="fill" else None};rejected=False
        try:
            benchmark=context.validate_gate_result(context.select_gate(contexts,**query))
            if len(base._canonical(benchmark))>MAX_GATE_RECORD_BYTES-1536:raise context.StressContextError("Context exceeds retained gate bound.")
        except context.StressContextError:
            rejected=True;benchmark=context.validate_gate_result(context.select_gate((),**query))
        value={"schema":1,"kind":"bankroll_bound_stress_context_gate","policy_id":POLICY_ID,"rule_sha256":RULE_SHA256,"underlying_symbol":"KO","benchmark_gate":benchmark,"allowed":benchmark["allowed"],"research_only":True,"execution_qualified":False}
        value["gate_sha256"]=base._digest(value);_validate_gate(value)
        record={"sequence":policy["context_count"]+len(evaluations)+1,"stage":stage,"source_date":data["source_date"],"recorded_at":clock.isoformat(),"context_input_rejected":rejected,"gate":value,"previous_sha256":evaluations[-1]["prefix_sha256"] if evaluations else policy["context_prefix_sha256"]}
        record["prefix_sha256"]=base._digest(record);old._bounded(record,MAX_GATE_RECORD_BYTES);evaluations.append(record);return value["allowed"]
    transition=_step_capital(row,checked,decided_at=clock,decision_gate=lambda i,r,t:gate(i,r,t,stage="decision"),fill_gate=lambda i,r,t:gate(i,r,t,stage="fill"))
    updated,events=transition["state"],transition["events"]
    if not transition["accepted"]:return _result(updated,transition["status"],events)
    prefix=base._digest({"previous_sha256":updated["prefix_sha256"],"snapshot_sha256":digest})
    updated["cursor_history"]=(updated["cursor_history"]+[{"source_date":row["source_date"],"snapshot_sha256":digest,"previous_sha256":updated["prefix_sha256"],"prefix_sha256":prefix}])[-15:]
    updated.update(processed_count=updated["processed_count"]+1,root_processed_count=updated["root_processed_count"]+1,bankroll_processed_count=updated["bankroll_processed_count"]+1,prefix_sha256=prefix,last_source_date=row["source_date"],last_received_at=row["received_at"],last_snapshot_sha256=digest,last_decided_at=clock.isoformat())
    policy["context_history"]=(policy["context_history"]+evaluations)[-MAX_GATE_HISTORY:];policy["context_count"]+=len(evaluations)
    if evaluations:policy["context_prefix_sha256"]=evaluations[-1]["prefix_sha256"]
    updated["bankroll_policy"]=policy;events.append({"type":"paper_daily_result",**deepcopy(updated["last_daily_result"])})
    for event in events:event["underlying_symbol"]="KO"
    return _result(updated,"completed",events)


@_precision
def check_deadline(state,now):
    """Passive EOD validation, not a fabricated sale or prepublication hard hold."""
    checked=validate_state(state);_require(_clock(now)>=_clock(checked["last_decided_at"]),"Assurance clock predates last decision.")
    return checked


@_precision
def summarize(state,now=None):
    checked=validate_state(state) if now is None else check_deadline(state,now)
    equity=_equity(checked);policy=checked["bankroll_policy"];act=policy["activation"];history=policy["context_history"];disposal=None
    clock=_clock(checked["last_decided_at"] if now is None else now)
    if checked["position"]:
        from .qqq_data import last_closed_session
        from .briefing import _tz
        deadline=base._day(checked["position"]["forced_exit_session"]);following=base._sessions(deadline+timedelta(days=1),deadline+timedelta(days=8))[0]
        publication=datetime(following.year,following.month,following.day,8,35,tzinfo=_tz("America/Chicago"))+timedelta(seconds=35)
        missing=checked["last_source_date"]<deadline.isoformat()
        phase=("awaiting_source_session" if last_closed_session(clock)<deadline else "awaiting_next_session_publication" if clock<publication else "unverified_missing_required_source") if missing else "required_source_recorded"
        disposal={"required_source_date":deadline.isoformat(),"status":phase,"collection_window_ends_at":publication.isoformat(),"provider_publication_sla":False,"hard_clock_hold_applied":False}
    result={"schema":6,"rule_sha256":RULE_SHA256,"symbol":"KO","source_id":SOURCE_ID,"simulated":True,"research_only":True,"execution_qualified":False,
        "cash":checked["cash"],"equity":_t(equity),"contributions":checked["contributions"],"trading_pnl":_t(equity-TOTAL),"month":checked["month"],"monthly_pnl":_t(equity-_n(checked["monthly_start_equity"])),
        "monthly_budget_used":checked["monthly_committed"],"monthly_budget_remaining":_t(MONTHLY-_n(checked["monthly_committed"])),"bankroll_total":"25000","monthly_commitment_ceiling":"25000","entry_maximum":"1000","entry_equity_fraction":"0.04","funding_basis":"one_time_total_bankroll","automatic_topups":False,
        "funding_added_on_activation":act["new_funding"],"position":deepcopy(checked["position"]),"pending":deepcopy(checked["pending"]),"hold":deepcopy(checked["hold"]),"cap_qualified":checked["cap_qualified"],"fees":checked["fees"],"trade_count":checked["trade_count"],"closed_trade_count":checked["closed_trade_count"],"contract_entry_count":checked["contract_entry_count"],"contract_exit_count":checked["contract_exit_count"],"milestone_counts":deepcopy(checked["milestone_counts"]),
        "last_daily_result":deepcopy(checked["last_daily_result"]),"last_source_date":checked["last_source_date"],"last_observed_at":None,"processed_count":checked["processed_count"],"root_processed_count":checked["root_processed_count"],"bankroll_processed_count":checked["bankroll_processed_count"],"legacy_processed_count":act["legacy_processed_count"],"portfolio_activation_at":checked["portfolio_activation_at"],"bankroll_policy_activated_at":act["activated_at"],"stress_policy_activated_at":act["activated_at"],"strategy_id":STRATEGY_ID,"kernel_strategy_id":KERNEL_STRATEGY_ID,"enabled_strategies":[STRATEGY_ID],"next_strategy":STRATEGY_ID,"strategy_stats":deepcopy(checked["strategy_stats"]),"policy_stats":deepcopy(checked["strategy_stats"][KERNEL_STRATEGY_ID]),"context_count":policy["context_count"],"last_entry_gate":deepcopy(history[-1]["gate"]) if history else None,"context_input_rejected":history[-1]["context_input_rejected"] if history else False,
        "disposal":disposal,"cap_assurance":"unverified_missing_disposal_source" if disposal and disposal["status"]=="unverified_missing_required_source" else "held" if checked["hold"] else "modeled_as_recorded",
        "cap_scope":"One25000 total funded account;25000 monthlygross ceiling,1000/4%entry,percontractfees; no future deposits or within-month gross recycling. Entire bankroll may be lost.","execution":"Experimental partial date-only price proxy; no orders, execution qualification or proven profitable edge."}
    if not checked["bankroll_processed_count"]:result.update(last_source_date=None,last_daily_result=None,legacy_last_source_date=act["legacy_last_source_date"],legacy_last_daily_result=deepcopy(act["legacy_last_daily_result"]))
    return result
