"""One explicit KO paper root over the existing shared EOD money kernel.

This pure engine cannot qualify a source, activate an owner account or fetch
quotes. The host must bind the original schema4 cutoff and every new published
source/context artifact. Original F benchmark witnesses remain data, never an
inherited permission to enter a KO position.
"""
from copy import deepcopy
from datetime import datetime, timedelta

from . import options_eod as base
from . import options_stress_eod as old_policy
from . import options_stress_context as context

OptionsRootEODError = base.OptionsEODError
SUPPORTED_ROOTS = ("KO",)
SOURCE_ID = "onclick_ko_eod_v1"
STRATEGY_ID = POLICY_ID = "ko_stress_breakdown_put_v1"
KERNEL_STRATEGY_ID = "eod_breakdown_put_v1"
MAX_STATE_BYTES, MAX_SNAPSHOT_BYTES, MAX_GATE_RECORD_BYTES, MAX_GATE_HISTORY = 256*1024, 2*1024*1024, 12*1024, 16
RULE = {"schema": 5, "id": "single-root-shared-stress-eod-v1", "roots": list(SUPPORTED_ROOTS), "source_id": SOURCE_ID,
        "base_rule_sha256": base.RULE_SHA256, "old_policy_rule_sha256": old_policy.RULE_SHA256,
        "context_rule_sha256": context.RULE_SHA256, "strategy_id": STRATEGY_ID,
        "kernel_strategy_id": KERNEL_STRATEGY_ID,
        "capital": "Same original account,500 shared gross monthly commitment,100 all-in entry, one fully paid standard100 long put; no new account, funding/reset, profit recycling, margin, exercise or changed fees/exits.",
        "migration": "Healthy pristine schema4 only; original financials, funding count, total processed count/source prefix and immutable old4 cutoff remain. New root baseline/history/cursor are separate and never F aliases.",
        "entry": "Fixed breakdown-put signal with fresh stress context. Actual KO identity and underlying<100 at decision and fill; same delayed source-session/receipt/month/DTE/liquidity/disposal rules as the base kernel.",
        "context": "Original verified F-tagged normalized benchmark witnesses are DATA only. Fresh5 root-bound gates wrap their unchanged causal calculation, actual query clock and publication proof; no oldF allowed gate is adopted as a KO permission.",
        "frames": "A frame starting with position or pending manages that existing risk only; no new selection after exit/cancel/fill. Missing new context denies BUY, never marks/exits/disposal.",
        "empty_chain": "A source-qualified flat empty candidate chain can record stock observation/no entry. Missing funded quote remains a hold; no manufactured strike/size or historical fill.",
        "evidence": "Experimental partial date-only price proxy. Source qualification is external and continuing reliability, event clocks, deliverables and profitability are unverified.",
        "bounds": "State256KiB; actual snapshot2MiB/2000quotes;15 stock references/cursor rows;16 gates/12KiB each; inherited counts remain in global quotas.",
        "research_only": True, "execution_qualified": False}
RULE_SHA256 = base._digest(RULE)
STATE_KEYS = frozenset(base._STATE | {"root_policy", "root_processed_count"})
SNAPSHOT_KEYS = frozenset(base._SNAPSHOT | {"underlying_symbol"})
_FINANCIAL = (*base._FINANCIAL, "funding_count")
_ACTIVATION = {"from_schema", "from_rule_sha256", "old_canonical_state_sha256", "old_state_sha256", "old_data_sha256",
               "activated_at", "symbol", "source_id", "initial_financials", "initial_financials_sha256",
               "legacy_processed_count", "legacy_prefix_sha256", "legacy_last_source_date", "legacy_last_received_at",
               "legacy_last_snapshot_sha256", "legacy_history_sha256", "legacy_cursor_sha256", "legacy_stress_policy_sha256",
               "legacy_last_daily_result_sha256", "baseline", "baseline_sha256", "new_funding", "risk_limits_changed"}
_POLICY = {"schema", "rule_sha256", "activation", "activation_sha256", "context_count", "context_prefix_sha256", "context_history"}
_GATE = {"sequence", "stage", "source_date", "recorded_at", "context_input_rejected", "gate", "previous_sha256", "prefix_sha256"}


def _require(value, message):
    if not value: raise OptionsRootEODError(message)


def _clock(value):
    return base._clock(value.isoformat() if isinstance(value, datetime) else value)


def _bounded(value, maximum):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop(); count += 1
        _require(depth <= 20 and count <= 100000, "Root evidence exceeds its structural bound.")
        if type(item) is dict:
            _require(len(item) <= 128 and all(type(k) is str and len(k) <= 128 for k in item), "Root metadata needs bounded string keys.")
            pending.extend((v, depth+1) for v in item.values())
        elif type(item) is list:
            _require(len(item) <= 2000, "Root evidence list exceeds its bound.")
            pending.extend((v, depth+1) for v in item)
        elif type(item) is str: _require(len(item) <= 4096, "Root text exceeds its bound.")
        elif type(item) is int: _require(abs(item) <= 10**14, "Root integer exceeds its bound.")
        else: _require(item is None or type(item) is bool, "Root state uses exact ordinary JSON without floats.")
    raw = base._canonical(value)
    _require(len(raw) <= maximum, "Root evidence exceeds its encoded byte bound.")
    return raw


def _verify():
    _require(base._digest(RULE) == RULE_SHA256 and RULE["roots"] == list(SUPPORTED_ROOTS) and SUPPORTED_ROOTS == ("KO",)
             and RULE["source_id"] == SOURCE_ID and RULE["strategy_id"] == STRATEGY_ID and RULE["kernel_strategy_id"] == KERNEL_STRATEGY_ID
             and base._digest(base.RULE) == base.RULE_SHA256 == RULE["base_rule_sha256"]
             and base._digest(old_policy.RULE) == old_policy.RULE_SHA256 == RULE["old_policy_rule_sha256"] and context.RULE_SHA256 == RULE["context_rule_sha256"],
             "The fixed root policy/base/context contract changed.")


@base._precision
def contract_id(symbol, expiry, kind, strike):
    _require(symbol in SUPPORTED_ROOTS and type(symbol) is str, "Only the explicitly supported KO root is registered.")
    day, amount = base._day(expiry), base._number(strike)*1000
    _require(kind in ("call", "put") and amount == amount.to_integral_value() and 0 < amount <= 99999999, "Use exact positive standard OSI terms.")
    return symbol + day.strftime("%y%m%d") + ("C" if kind == "call" else "P") + str(int(amount)).zfill(8)


def _identity(item):
    _require(item["contract_id"] == contract_id("KO", item["expiry"], item["type"], item["strike"]), "KO identity disagrees with actual economic terms.")


@base._precision
def validate_snapshot(snapshot):
    _verify(); _bounded(snapshot, MAX_SNAPSHOT_BYTES)
    _require(type(snapshot) is dict and set(snapshot) == SNAPSHOT_KEYS and snapshot["underlying_symbol"] == "KO"
             and snapshot["quote_observed_at"] is None and snapshot["underlying_basis"] == "unadjusted_regular_last_bar"
             and type(snapshot["partial_chain"]) is bool, "Use the exact explicit KO date-only snapshot.")
    from .qqq_data import session_close_utc
    day, received = base._day(snapshot["source_date"]), _clock(snapshot["received_at"])
    _require(received >= session_close_utc(day) and base._number(snapshot["underlying_close"]) > 0
             and all(base._hash(snapshot[k]) for k in ("source_sha256", "stock_sha256")), "Root snapshot lacks actual dated stock/source evidence.")
    _require(type(snapshot["quotes"]) is list and len(snapshot["quotes"]) <= 2000, "At most2000 actual quotes are allowed; an empty flat candidate chain grants no entry.")
    seen = set()
    for quote in snapshot["quotes"]:
        _require(type(quote) is dict and set(quote) == base._QUOTE, "Use exact root quote fields.")
        _identity(quote)
        _require(quote["contract_id"] not in seen and base._day(quote["expiry"]) >= day, "Duplicate/expired root contract.")
        seen.add(quote["contract_id"])
        bid, ask = base._number(quote["bid"]), base._number(quote["ask"])
        _require(bid <= ask and bid <= base._number(snapshot["underlying_close"] if quote["type"] == "call" else quote["strike"]), "Crossed/economically impossible root quote.")
        _require(all(type(quote[k]) is int and 0 <= quote[k] <= 100000000 for k in ("bid_size", "ask_size")), "Actual nonnegative side sizes are required.")
    return deepcopy(snapshot)


def _gate_start(activation_sha):
    return base._digest({"rule_sha256": RULE_SHA256, "activation_sha256": activation_sha})


@base._precision
def migrate_flat(old4, *, activated_at, old_state_sha256, old_data_sha256, baseline=None, symbol="KO"):
    _verify(); old = old_policy.validate_state(old4); clock = _clock(activated_at)
    _require(type(symbol) is str and symbol in SUPPORTED_ROOTS and base._hash(old_state_sha256) and base._hash(old_data_sha256), "Root migration requires the supported symbol and original evidence hashes.")
    _require(clock >= _clock(old["last_decided_at"]), "Root migration cannot move actual clocks backwards.")
    _require(old["position"] is None and old["pending"] is None and old["hold"] is None and old["cap_qualified"]
             and old["trade_count"] == old["closed_trade_count"] == 0 and base._number(old["monthly_committed"]) == base._number(old["fees"]) == 0
             and base._number(old["cash"]) == base._number(old["contributions"]) == base._number(old["monthly_start_equity"]), "Only the healthy pristine flat schema4 account may change its active root.")
    refs = deepcopy([] if baseline is None else baseline)
    base._references(refs, before=base._day_at(clock), received_by=clock)
    _require(not refs or old["last_source_date"] is None or refs[-1]["source_date"] == old["last_source_date"], "Root seed must align with the existing financial cutoff; it cannot repair missing option frames.")
    financial = {k: deepcopy(old[k]) for k in _FINANCIAL}
    activation = {"from_schema": 4, "from_rule_sha256": old_policy.RULE_SHA256,
        "old_canonical_state_sha256": base._digest(old), "old_state_sha256": old_state_sha256, "old_data_sha256": old_data_sha256,
        "activated_at": clock.isoformat(), "symbol": symbol, "source_id": SOURCE_ID,
        "initial_financials": financial, "initial_financials_sha256": base._digest(financial),
        "legacy_processed_count": old["processed_count"], "legacy_prefix_sha256": old["prefix_sha256"],
        "legacy_last_source_date": old["last_source_date"], "legacy_last_received_at": old["last_received_at"],
        "legacy_last_snapshot_sha256": old["last_snapshot_sha256"], "legacy_history_sha256": base._digest(old["history"]),
        "legacy_cursor_sha256": base._digest(old["cursor_history"]), "legacy_stress_policy_sha256": base._digest(old["stress_policy"]),
        "legacy_last_daily_result_sha256": base._digest(old["last_daily_result"]),
        "baseline": refs, "baseline_sha256": base._digest(refs), "new_funding": "0", "risk_limits_changed": False}
    digest = base._digest(activation)
    result = {k: deepcopy(v) for k, v in old.items() if k != "stress_policy"}
    result.update(schema=5, rule_sha256=RULE_SHA256, symbol=symbol, source_id=SOURCE_ID, last_decided_at=clock.isoformat(),
                  root_processed_count=0, history=refs, cursor_history=[], root_policy={"schema": 1, "rule_sha256": RULE_SHA256,
                  "activation": activation, "activation_sha256": digest, "context_count": 0,
                  "context_prefix_sha256": _gate_start(digest), "context_history": []})
    return validate_state(result)


def _validate_gate(gate):
    keys = {"schema", "kind", "policy_id", "rule_sha256", "underlying_symbol", "benchmark_gate", "allowed", "research_only", "execution_qualified", "gate_sha256"}
    _require(type(gate) is dict and set(gate) == keys and type(gate["schema"]) is int and gate["schema"] == 1
             and gate["kind"] == "root_bound_stress_context_gate" and gate["policy_id"] == POLICY_ID and gate["rule_sha256"] == RULE_SHA256
             and gate["underlying_symbol"] == "KO" and gate["research_only"] is True and gate["execution_qualified"] is False,
             "Malformed root-bound gate.")
    try:
        benchmark = context.validate_gate_result(gate["benchmark_gate"])
    except context.StressContextError as error:
        raise OptionsRootEODError(str(error)) from None
    _require(type(gate["allowed"]) is bool and gate["allowed"] == benchmark["allowed"] and
             base._hash(gate["gate_sha256"]) and gate["gate_sha256"] == base._digest({k:v for k,v in gate.items() if k != "gate_sha256"}), "Root gate lost its exact benchmark data/query binding.")
    return deepcopy(gate)


@base._precision
def validate_state(state):
    _verify(); _bounded(state, MAX_STATE_BYTES)
    _require(type(state) is dict and set(state) == STATE_KEYS and type(state["schema"]) is int and state["schema"] == 5
             and state["rule_sha256"] == RULE_SHA256 and state["symbol"] == "KO" and state["source_id"] == SOURCE_ID
             and state["research_only"] is True and state["execution_qualified"] is False, "Use the exact registered root5 state.")
    policy = state["root_policy"]
    _require(type(policy) is dict and set(policy) == _POLICY and type(policy["schema"]) is int and policy["schema"] == 1 and policy["rule_sha256"] == RULE_SHA256, "Malformed root policy.")
    act = policy["activation"]
    _require(type(act) is dict and set(act) == _ACTIVATION and type(act["from_schema"]) is int and act["from_schema"] == 4
             and act["from_rule_sha256"] == old_policy.RULE_SHA256 and act["symbol"] == "KO" and act["source_id"] == SOURCE_ID
             and act["new_funding"] == "0" and act["risk_limits_changed"] is False and base._hash(policy["activation_sha256"])
             and base._digest(act) == policy["activation_sha256"], "Root activation cannot change its original ledger, policy or risk.")
    for key in ("old_canonical_state_sha256", "old_state_sha256", "old_data_sha256", "initial_financials_sha256", "legacy_prefix_sha256",
                "legacy_history_sha256", "legacy_cursor_sha256", "legacy_stress_policy_sha256", "legacy_last_daily_result_sha256", "baseline_sha256"):
        _require(base._hash(act[key]), "Root activation requires exact old evidence identities.")
    active, decided = _clock(act["activated_at"]), _clock(state["last_decided_at"])
    _require(_clock(state["started_at"]) <= _clock(state["portfolio_activation_at"]) <= active <= decided, "Root activation/decision clocks moved backwards.")
    initial = base._validate_migration(state, source_id=base.RULE["source_id"])
    base._validate_financial_header(state, initial, decided)
    financial = act["initial_financials"]
    _require(type(financial) is dict and set(financial) == set(_FINANCIAL) and base._digest(financial) == act["initial_financials_sha256"]
             and financial["started_at"] == state["started_at"] and type(financial["funding_count"]) is int
             and 0 <= financial["funding_count"] <= state["funding_count"] and type(financial["trade_count"]) is int and type(financial["closed_trade_count"]) is int
             and financial["trade_count"] == financial["closed_trade_count"] == 0
             and base._number(financial["monthly_committed"]) == base._number(financial["fees"]) == 0
             and base._number(financial["cash"]) == base._number(financial["contributions"]) == base._number(financial["monthly_start_equity"])
             and base._number(financial["contributions"]) == base._number(initial["contributions"]) + 500*financial["funding_count"]
             and base._month(initial["month"]) <= base._month(financial["month"]) <= state["month"]
             and financial["month"] <= base._day_at(active).strftime("%Y-%m"), "Original root financial witness does not reconcile.")
    legacy, count = act["legacy_processed_count"], state["root_processed_count"]
    _require(type(legacy) is int and 0 <= legacy <= 100000 and type(count) is int and 0 <= count <= 100000
             and state["processed_count"] == legacy + count, "Root activation cannot reset inherited counts or global quotas.")
    if legacy:
        base._day(act["legacy_last_source_date"]); _clock(act["legacy_last_received_at"])
        _require(base._hash(act["legacy_last_snapshot_sha256"]), "Old source cutoff lost its actual snapshot.")
    else:
        _require(all(act[k] is None for k in ("legacy_last_source_date", "legacy_last_received_at", "legacy_last_snapshot_sha256")), "Empty old cursor cannot claim observations.")
    _require(base._digest(act["baseline"]) == act["baseline_sha256"], "Root baseline identity changed.")
    base._references(act["baseline"], before=base._day_at(active), received_by=active)
    history = base._references(state["history"])
    _require(state["last_observed_at"] is None and len(history) == min(len(act["baseline"]) + count, 15)
             and all(_clock(r["received_at"]) <= decided for r in history), "Root-only indicator ring/cursor changed.")
    cursor = state["cursor_history"]
    _require(type(cursor) is list and len(cursor) == min(count, 15) and base._hash(state["prefix_sha256"]), "Use the bounded post5 accepted cursor.")
    previous = previous_day = None
    for ordinal, row in enumerate(cursor):
        _require(type(row) is dict and set(row) == {"source_date", "snapshot_sha256", "previous_sha256", "prefix_sha256"}
                 and all(base._hash(row[k]) for k in ("snapshot_sha256", "previous_sha256", "prefix_sha256")), "Malformed root accepted cursor.")
        day = base._day(row["source_date"])
        _require(row["prefix_sha256"] == base._digest({"previous_sha256":row["previous_sha256"], "snapshot_sha256":row["snapshot_sha256"]})
                 and (previous is None or row["previous_sha256"] == previous and len(base._sessions(previous_day+timedelta(days=1), day)) == 1), "Root prefix is discontinuous.")
        if ordinal == 0 and count <= 15:
            _require(row["previous_sha256"] == act["legacy_prefix_sha256"] and (act["legacy_last_source_date"] is None or
                     len(base._sessions(base._day(act["legacy_last_source_date"])+timedelta(days=1), day)) == 1), "Root prefix lost its old4 cutoff.")
        previous, previous_day = row["prefix_sha256"], day
    if count:
        _require(history and cursor[-1]["source_date"] == state["last_source_date"] == history[-1]["source_date"]
                 and cursor[-1]["snapshot_sha256"] == state["last_snapshot_sha256"] and cursor[-1]["prefix_sha256"] == state["prefix_sha256"]
                 and history[-1]["received_at"] == state["last_received_at"], "Root current source/history/cursor disagree.")
    else:
        _require(history == act["baseline"] and state["prefix_sha256"] == act["legacy_prefix_sha256"]
                 and all(state[k] == act["legacy_"+k] for k in ("last_source_date", "last_received_at", "last_snapshot_sha256"))
                 and all(state[k] == financial[k] for k in _FINANCIAL)
                 and base._digest(state["last_daily_result"]) == act["legacy_last_daily_result_sha256"], "Unobserved root state cannot alter old financial/source evidence.")
    base._validate_financial_tail(state, active=active, decided=decided, identity=_identity)
    for identifier, value in state["strategy_stats"].items():
        if identifier != KERNEL_STRATEGY_ID:
            _require(value["entry_count"] == value["closed_count"] == 0 and all(base._number(value[k], signed=k == "realized_pnl") == 0 for k in ("fees", "realized_pnl", "monthly_committed"))
                     and not any(value["milestone_counts"].values()), "Root stress policy cannot create call exposure.")
    for exposure in (state["position"], state["pending"]):
        _require(exposure is None or exposure["strategy_id"] == KERNEL_STRATEGY_ID and exposure["type"] == "put", "Only the registered long-put exposure is allowed.")
    gates, n = policy["context_history"], policy["context_count"]
    _require(type(n) is int and 2*state["trade_count"] <= n <= 2*count and type(gates) is list and len(gates) == min(n, MAX_GATE_HISTORY)
             and base._hash(policy["context_prefix_sha256"]), "Root gate count cannot adopt old F permissions.")
    prior = prior_clock = None
    for ordinal, record in enumerate(gates):
        _require(type(record) is dict and set(record) == _GATE and type(record["sequence"]) is int and record["sequence"] == n-len(gates)+ordinal+1
                 and record["stage"] in ("decision", "fill") and type(record["context_input_rejected"]) is bool
                 and base._hash(record["previous_sha256"]) and base._hash(record["prefix_sha256"]), "Malformed root gate cursor.")
        _bounded(record, MAX_GATE_RECORD_BYTES)
        stamp = _clock(record["recorded_at"]); base._day(record["source_date"])
        gate = _validate_gate(record["gate"]); benchmark = gate["benchmark_gate"]
        _require(active <= stamp <= decided and record["source_date"] <= state["last_source_date"] and
                 (prior_clock is None or stamp >= prior_clock) and benchmark["stage"] == record["stage"] and _clock(benchmark["decision_at"]) == stamp
                 and benchmark["source_date"] == (record["source_date"] if record["stage"] == "fill" else None)
                 and (not record["context_input_rejected"] or not gate["allowed"]), "Root gate used unavailable or mismatched data.")
        _require(record["prefix_sha256"] == base._digest({k:v for k,v in record.items() if k != "prefix_sha256"})
                 and (prior is None or record["previous_sha256"] == prior), "Root gate prefix changed.")
        if ordinal == 0 and n <= MAX_GATE_HISTORY:
            _require(record["previous_sha256"] == _gate_start(policy["activation_sha256"]), "Root gates lost their new activation anchor.")
        prior, prior_clock = record["prefix_sha256"], stamp
    _require(policy["context_prefix_sha256"] == (prior or _gate_start(policy["activation_sha256"])), "Root context cursor changed.")
    for exposure, stage, clock_key, source_key in ((state["pending"], "decision", "decided_at", "signal_source_date"),
                                                  (state["position"], "fill", "entry_recorded_at", "entry_source_date")):
        if exposure is not None and (stage == "fill" or exposure["action"] == "BUY"):
            _require(any(r["stage"] == stage and r["gate"]["allowed"] and r["recorded_at"] == exposure[clock_key] and r["source_date"] == exposure[source_key] for r in gates), "Root exposure lacks its fresh root-bound permission.")
    _verify()
    return deepcopy(state)


@base._precision
def summarize(state, now=None):
    checked = validate_state(state); result = base._summarize_ledger(checked, now=now)
    policy = checked["root_policy"]; history = policy["context_history"]
    result.update(strategy_id=STRATEGY_ID, kernel_strategy_id=KERNEL_STRATEGY_ID, enabled_strategies=[STRATEGY_ID], next_strategy=STRATEGY_ID,
        policy_stats=deepcopy(checked["strategy_stats"][KERNEL_STRATEGY_ID]), stress_policy_activated_at=policy["activation"]["activated_at"],
        root_policy_activated_at=policy["activation"]["activated_at"], root_processed_count=checked["root_processed_count"],
        legacy_processed_count=policy["activation"]["legacy_processed_count"], context_count=policy["context_count"],
        legacy_last_source_date=policy["activation"]["legacy_last_source_date"],
        last_entry_gate=deepcopy(history[-1]["gate"]) if history else None, context_input_rejected=history[-1]["context_input_rejected"] if history else False,
        policy="Experimental KO-only stress-filtered put on the same shared account; not a proven winner or execution-qualified price.")
    if not checked["root_processed_count"]:
        result.update(last_source_date=None, last_daily_result=None, legacy_last_daily_result=deepcopy(checked["last_daily_result"]))
    return result


def _result(state, status, events):
    checked = validate_state(state)
    return {"state":checked, "status":status, "events":events, "summary":summarize(checked)}


def _hold(state, code, message, clock):
    value = base._ledger_hold(state, code, message, clock)
    return _result(value["state"], value["status"], value["events"])


@base._precision
def advance_paper(snapshot, state, *, decided_at, contexts=()):
    checked = validate_state(state); clock = _clock(decided_at)
    _require(clock >= _clock(checked["last_decided_at"]), "Actual root decisions cannot move backwards.")
    if checked["hold"]: return _result(checked, "held", [])
    row = validate_snapshot(snapshot); day = base._day(row["source_date"]); received = _clock(row["received_at"])
    _require(received <= clock, "Future-received root evidence is unavailable.")
    from .qqq_data import last_closed_session
    _require(day <= last_closed_session(clock), "Uncompleted root source session.")
    digest = base._digest(row)
    if checked["last_source_date"] == row["source_date"]:
        if not checked["root_processed_count"]: return _hold(checked, "legacy_source_session_already_consumed", "Adding a root cannot consume the old account source day again.", clock)
        if digest != checked["last_snapshot_sha256"]: return _hold(checked, "accepted_source_changed", "Accepted KO source changed; no revision or replay.", clock)
        return _result(checked, "unchanged", [])
    if day != last_closed_session(clock): return _hold(checked, "stale_source", "Root source is not the latest finalized session; no historical fill adoption.", clock)
    previous = checked["last_source_date"]
    if previous is not None and (day <= base._day(previous) or len(base._sessions(base._day(previous)+timedelta(days=1), day)) != 1):
        return _hold(checked, "missing_source_session", "Root financial source session missing; a stock window cannot repair option frames.", clock)
    if checked["history"]:
        last = base._day(checked["history"][-1]["source_date"])
        if day <= last or len(base._sessions(last+timedelta(days=1), day)) != 1:
            return _hold(checked, "missing_root_stock_session", "Use consecutive actual KO stock references; never splice old F prices.", clock)
    _require(checked["last_received_at"] is None or received >= _clock(checked["last_received_at"]), "Actual root receipt clocks moved backwards.")
    policy = deepcopy(checked["root_policy"]); evaluations = []
    manage_existing = checked["position"] is not None or checked["pending"] is not None
    def gate(identifier, data, stamp, *, stage):
        if identifier != KERNEL_STRATEGY_ID or stage == "decision" and manage_existing: return False
        _require(_clock(stamp) == clock, "Root kernel changed its actual gate clock.")
        query = {"decision_at":clock, "source_date":data["source_date"] if stage == "fill" else None}; rejected = False
        try:
            benchmark = context.validate_gate_result(context.select_gate(contexts, **query))
            if len(base._canonical(benchmark)) > MAX_GATE_RECORD_BYTES-1536: raise context.StressContextError("Root context too large.")
        except context.StressContextError:
            rejected = True; benchmark = context.validate_gate_result(context.select_gate((), **query))
        value = {"schema":1, "kind":"root_bound_stress_context_gate", "policy_id":POLICY_ID, "rule_sha256":RULE_SHA256,
                 "underlying_symbol":"KO", "benchmark_gate":benchmark, "allowed":benchmark["allowed"], "research_only":True, "execution_qualified":False}
        value["gate_sha256"] = base._digest(value); _validate_gate(value)
        record = {"sequence":policy["context_count"]+len(evaluations)+1, "stage":stage, "source_date":data["source_date"],
                  "recorded_at":clock.isoformat(), "context_input_rejected":rejected, "gate":value,
                  "previous_sha256":evaluations[-1]["prefix_sha256"] if evaluations else policy["context_prefix_sha256"]}
        record["prefix_sha256"] = base._digest(record); _bounded(record, MAX_GATE_RECORD_BYTES); evaluations.append(record)
        return value["allowed"]
    transition = base._step_eod_ledger(row, checked, decided_at=clock.isoformat(),
        decision_gate=lambda i,r,t:gate(i,r,t,stage="decision"), fill_gate=lambda i,r,t:gate(i,r,t,stage="fill"))
    updated, events = transition["state"], transition["events"]
    if not transition["accepted"]: return _result(updated, transition["status"], events)
    prefix = base._digest({"previous_sha256":updated["prefix_sha256"], "snapshot_sha256":digest})
    updated["cursor_history"] = (updated["cursor_history"]+[{"source_date":row["source_date"], "snapshot_sha256":digest,
        "previous_sha256":updated["prefix_sha256"], "prefix_sha256":prefix}])[-15:]
    updated.update(processed_count=updated["processed_count"]+1, root_processed_count=updated["root_processed_count"]+1,
                   prefix_sha256=prefix, last_source_date=row["source_date"], last_received_at=row["received_at"], last_snapshot_sha256=digest, last_decided_at=clock.isoformat())
    policy["context_history"] = (policy["context_history"]+evaluations)[-MAX_GATE_HISTORY:]; policy["context_count"] += len(evaluations)
    if evaluations: policy["context_prefix_sha256"] = evaluations[-1]["prefix_sha256"]
    updated["root_policy"] = policy
    events.append({"type":"paper_daily_result", **deepcopy(updated["last_daily_result"])})
    for event in events: event["underlying_symbol"] = None if event["type"] == "contribution" else "KO"
    return _result(updated, "completed", events)
