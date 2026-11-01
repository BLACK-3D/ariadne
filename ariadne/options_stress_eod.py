"""Versioned forward stress-put policy over the unchanged F EOD risk ledger.

Only entry decisions and BUY fills have a policy gate. Existing exact quotes,
funding, fees, exits, deadlines and evidence holds remain the base model's work.
This experimental paper hypothesis is not a historical winner promotion.
"""
from copy import deepcopy
from datetime import datetime

from . import options_eod as base
from . import options_stress_context as context

OptionsStressEODError = base.OptionsEODError
STRATEGY_ID = "f_stress_breakdown_put_v1"
KERNEL_STRATEGY_ID = "eod_breakdown_put_v1"
MAX_STATE_BYTES = 256 * 1024
MAX_LEGACY_BYTES = 48 * 1024
MAX_GATE_RECORD_BYTES = 12 * 1024
MAX_GATE_HISTORY = 16
RULE = {
    "schema": 4, "id": "f-stress-breakdown-eod-policy-v1", "symbol": "F",
    "base_rule_sha256": base.RULE_SHA256, "context_rule_sha256": context.RULE_SHA256,
    "strategy_id": STRATEGY_ID, "kernel_strategy_id": KERNEL_STRATEGY_ID,
    "entry": "Only the existing registered breakdown-put signal, with fresh stress_unwind context at the actual decision clock.",
    "fill": "Only a pending breakdown-put BUY; context must actually have been published before that modeled source session's open and cover its preceding regular session.",
    "frames": "A source frame beginning with a position or pending decision manages only that existing exposure. New selection resumes on the next source frame; never decide a new BUY after an exit or cancelled/filled entry in the same frame.",
    "missing_context": "Deny/cancel BUY only. Invalid newly provided context becomes an explicitly rejected empty-ring gate. Stored policy/evidence corruption is fatal.",
    "risk": "One existing shared500 gross monthly commitment and100 all-in entry limit; fully paid standard100 long put only; no extra account, funding, margin, profit recycling, exercise or changed exits.",
    "migration": "Healthy schema3 zero-trade, zero-fee, unspent, flat account only. Preserve all financial fields, source prefix, old migration and accounting buckets; record immutable original state/data hashes and actual activation.",
    "evidence": "Experimental date-only partial-chain paper proxy, unknown option event clock and unverified execution; no established best strategy or future profitability.",
    "gate_history": "Last16 validated gate evaluations with canonical hash-chain; they record policy checks, not guaranteed trade decisions or fills.",
    "bounds": "Legacy projection<=48KiB; each retained gate record<=12KiB; complete schema4 state<=256KiB. Oversized new context denies entry without blocking exits.",
    "research_only": True, "execution_qualified": False,
}
RULE_SHA256 = base._digest(RULE)
STATE_KEYS = frozenset(base._STATE | {"stress_policy"})
_FINANCIAL = (*base._FINANCIAL, "funding_count")
_ACTIVATION = {"from_schema", "from_rule_sha256", "old_canonical_state_sha256", "old_state_sha256",
    "old_data_sha256", "activated_at", "initial_financials", "initial_financials_sha256",
    "initial_processed_count", "new_funding", "risk_limits_changed"}
_POLICY = {"schema", "rule_sha256", "activation", "activation_sha256", "context_count", "context_prefix_sha256", "context_history"}
_GATE = {"sequence", "stage", "source_date", "recorded_at", "context_input_rejected", "gate", "previous_sha256", "prefix_sha256"}


def _require(condition, message):
    if not condition:
        raise OptionsStressEODError(message)


def _clock(value):
    return base._clock(value.isoformat() if isinstance(value, datetime) else value)


def _verify():
    _require(base._digest(RULE) == RULE_SHA256 and RULE["base_rule_sha256"] == base.RULE_SHA256
        and base._digest(base.RULE) == base.RULE_SHA256 and RULE["context_rule_sha256"] == context.RULE_SHA256
        and RULE["strategy_id"] == STRATEGY_ID and RULE["kernel_strategy_id"] == KERNEL_STRATEGY_ID,
        "The fixed stress policy or its base/context rule changed.")


def _projection(state):
    result = {key: deepcopy(value) for key, value in state.items() if key != "stress_policy"}
    result.update(schema=3, rule_sha256=base.RULE_SHA256)
    return result


def _initial_prefix(activation_sha256):
    return base._digest({"rule_sha256": RULE_SHA256, "activation_sha256": activation_sha256})


def _gate_payload(record):
    return {key: value for key, value in record.items() if key != "prefix_sha256"}


@base._precision
def migrate_flat(old_schema3, *, activated_at, old_state_sha256, old_data_sha256):
    """Version the existing pristine ledger without resetting or adding capital."""
    _verify()
    old = base.validate_state(old_schema3)
    clock = _clock(activated_at)
    _require(base._hash(old_state_sha256) and base._hash(old_data_sha256), "Migration needs exact original state/data hashes.")
    _require(clock >= _clock(old["last_decided_at"]), "Stress activation cannot precede the last durable decision.")
    _require(old["position"] is None and old["pending"] is None and old["hold"] is None and old["cap_qualified"]
        and old["trade_count"] == old["closed_trade_count"] == 0 and base._number(old["fees"]) == 0
        and base._number(old["monthly_committed"]) == 0
        and base._number(old["cash"]) == base._number(old["contributions"]) == base._number(old["monthly_start_equity"])
        and all(not any(value["milestone_counts"].values()) and value["entry_count"] == value["closed_count"] == 0
            and all(base._number(value[key], signed=key == "realized_pnl") == 0
                    for key in ("fees", "monthly_committed", "realized_pnl")) for value in old["strategy_stats"].values()),
        "Only a healthy zero-trade, zero-fee, unspent flat schema3 account may activate the stress policy.")
    financials = {key: deepcopy(old[key]) for key in _FINANCIAL}
    activation = {"from_schema": 3, "from_rule_sha256": base.RULE_SHA256,
        "old_canonical_state_sha256": base._digest(old), "old_state_sha256": old_state_sha256,
        "old_data_sha256": old_data_sha256, "activated_at": clock.isoformat(), "initial_financials": financials,
        "initial_financials_sha256": base._digest(financials), "initial_processed_count": old["processed_count"],
        "new_funding": "0", "risk_limits_changed": False}
    activation_sha256 = base._digest(activation)
    result = deepcopy(old)
    result.update(schema=4, rule_sha256=RULE_SHA256, last_decided_at=clock.isoformat(), stress_policy={
        "schema": 1, "rule_sha256": RULE_SHA256, "activation": activation, "activation_sha256": activation_sha256,
        "context_count": 0, "context_prefix_sha256": _initial_prefix(activation_sha256), "context_history": []})
    return validate_state(result)


@base._precision
def validate_state(state):
    """Validate schema4 lineage and policy, then all original ledger invariants."""
    _verify()
    _require(type(state) is dict and set(state) == STATE_KEYS and type(state["schema"]) is int
        and state["schema"] == 4 and state["rule_sha256"] == RULE_SHA256, "Use the exact registered schema4 stress state.")
    _require(len(base._canonical(state)) <= MAX_STATE_BYTES, "Stress state exceeds256KiB.")
    projection = _projection(state)
    _require(len(base._canonical(projection)) <= MAX_LEGACY_BYTES, "Stress legacy projection exceeds48KiB.")
    legacy = base.validate_state(projection)
    policy = state["stress_policy"]
    _require(type(policy) is dict and set(policy) == _POLICY and type(policy["schema"]) is int
        and policy["schema"] == 1 and policy["rule_sha256"] == RULE_SHA256, "Malformed fixed stress policy witness.")
    activation = policy["activation"]
    _require(type(activation) is dict and set(activation) == _ACTIVATION and type(activation["from_schema"]) is int
        and activation["from_schema"] == 3 and activation["from_rule_sha256"] == base.RULE_SHA256
        and activation["new_funding"] == "0" and activation["risk_limits_changed"] is False,
        "Stress activation cannot rewrite capital, risk limits or prior engine identity.")
    for key in ("old_canonical_state_sha256", "old_state_sha256", "old_data_sha256", "initial_financials_sha256"):
        _require(base._hash(activation[key]), "Original stress activation evidence lacks a valid digest.")
    _require(base._hash(policy["activation_sha256"]) and base._digest(activation) == policy["activation_sha256"],
        "Stress activation witness was changed.")
    active = _clock(activation["activated_at"])
    _require(_clock(legacy["portfolio_activation_at"]) <= active <= _clock(legacy["last_decided_at"]), "Stress activation clocks disagree.")
    initial = activation["initial_financials"]
    _require(type(initial) is dict and set(initial) == set(_FINANCIAL)
        and base._digest(initial) == activation["initial_financials_sha256"], "Original financial witness was changed.")
    _require(initial["started_at"] == legacy["started_at"] and base._month(initial["month"]) <= legacy["month"]
        and base._month(initial["month"]) <= base._day_at(active).strftime("%Y-%m")
        and type(initial["funding_count"]) is int and 0 <= initial["funding_count"] <= legacy["funding_count"]
        and type(initial["trade_count"]) is int and type(initial["closed_trade_count"]) is int
        and initial["trade_count"] == initial["closed_trade_count"] == 0
        and base._number(initial["fees"]) == base._number(initial["monthly_committed"]) == 0
        and base._number(initial["cash"]) == base._number(initial["contributions"]) == base._number(initial["monthly_start_equity"])
        and base._number(initial["contributions"]) >= 500
        and base._number(initial["contributions"]) == base._number(legacy["migration"]["initial_financials"]["contributions"]) + 500*initial["funding_count"],
        "The zero-risk original financial witness does not reconcile with retained funding.")
    for identifier, stats in legacy["strategy_stats"].items():
        if identifier == KERNEL_STRATEGY_ID:
            continue
        _require(stats["entry_count"] == stats["closed_count"] == 0
            and all(base._number(stats[key], signed=key == "realized_pnl") == 0
                    for key in ("fees", "monthly_committed", "realized_pnl"))
            and not any(stats["milestone_counts"].values()), "The stress policy cannot attribute risk to a call strategy.")
    for value, key in ((legacy["position"], "decision_at"), (legacy["pending"], "decided_at")):
        if value is not None:
            _require(value["strategy_id"] == KERNEL_STRATEGY_ID and value["type"] == "put"
                and _clock(value[key]) >= active, "Stress exposure must be a new policy-gated long put.")
    initial_count = activation["initial_processed_count"]
    _require(type(initial_count) is int and 0 <= initial_count <= legacy["processed_count"], "Stress activation source cursor changed.")
    count, history = policy["context_count"], policy["context_history"]
    _require(type(count) is int and 0 <= count <= 2*(legacy["processed_count"]-initial_count)
        and type(history) is list and len(history) == min(count, MAX_GATE_HISTORY)
        and base._hash(policy["context_prefix_sha256"]), "Stress context history lost its bounded cursor.")
    _require(count >= 2*legacy["trade_count"], "Stress fills lack their entry-policy evaluation history.")
    previous, previous_clock = None, None
    for index, row in enumerate(history):
        _require(type(row) is dict and set(row) == _GATE and type(row["sequence"]) is int
            and row["sequence"] == count-len(history)+index+1 and row["stage"] in ("decision", "fill")
            and type(row["context_input_rejected"]) is bool and base._hash(row["previous_sha256"])
            and base._hash(row["prefix_sha256"]), "Malformed stress gate history record.")
        _require(len(base._canonical(row)) <= MAX_GATE_RECORD_BYTES, "Retained stress gate exceeds12KiB.")
        stamp = _clock(row["recorded_at"])
        base._day(row["source_date"])
        _require(active <= stamp <= _clock(legacy["last_decided_at"])
            and legacy["last_source_date"] is not None and row["source_date"] <= legacy["last_source_date"]
            and (previous_clock is None or stamp >= previous_clock), "Stress gate history used unavailable clocks.")
        gate = context.validate_gate_result(row["gate"])
        _require(gate == row["gate"] and type(gate["allowed"]) is bool and gate["stage"] == row["stage"]
            and _clock(gate["decision_at"]) == stamp
            and gate["source_date"] == (row["source_date"] if row["stage"] == "fill" else None)
            and (not row["context_input_rejected"] or not gate["allowed"]), "Stress gate evidence disagrees with its actual query.")
        _require(base._digest(_gate_payload(row)) == row["prefix_sha256"]
            and (previous is None or row["previous_sha256"] == previous), "Stress gate hash-chain changed.")
        if index == 0 and count <= MAX_GATE_HISTORY:
            _require(row["previous_sha256"] == _initial_prefix(policy["activation_sha256"]), "Stress gate history lost its activation anchor.")
        previous, previous_clock = row["prefix_sha256"], stamp
    _require(policy["context_prefix_sha256"] == (previous or _initial_prefix(policy["activation_sha256"])), "Stress gate cursor digest changed.")
    if legacy["pending"] is not None and legacy["pending"]["action"] == "BUY":
        pending = legacy["pending"]
        _require(any(row["stage"] == "decision" and row["gate"]["allowed"]
            and row["recorded_at"] == pending["decided_at"] and row["source_date"] == pending["signal_source_date"]
            for row in history), "Pending stress BUY lacks its actual allowed decision gate.")
    if legacy["position"] is not None:
        position = legacy["position"]
        _require(any(row["stage"] == "fill" and row["gate"]["allowed"]
            and row["recorded_at"] == position["entry_recorded_at"] and row["source_date"] == position["entry_source_date"]
            for row in history), "Open stress put lacks its actual allowed fill gate.")
    return deepcopy(state)


def legacy_state(state):
    """Return the validated original schema3 projection, never mutate its source."""
    return _projection(validate_state(state))


def summarize(state, now=None):
    checked = validate_state(state)
    result = base.summarize(_projection(checked), now=now)
    history = checked["stress_policy"]["context_history"]
    result.update(schema=4, rule_sha256=RULE_SHA256, strategy_id=STRATEGY_ID,
        kernel_strategy_id=KERNEL_STRATEGY_ID, enabled_strategies=[STRATEGY_ID], next_strategy=STRATEGY_ID,
        stress_policy_activated_at=checked["stress_policy"]["activation"]["activated_at"],
        policy_stats=deepcopy(checked["strategy_stats"][KERNEL_STRATEGY_ID]),
        context_count=checked["stress_policy"]["context_count"],
        last_entry_gate=deepcopy(history[-1]["gate"]) if history else None,
        context_input_rejected=history[-1]["context_input_rejected"] if history else False,
        policy="Forward experimental F stress-filtered breakdown put; candidate testing, not a proven strongest strategy.")
    return result


@base._precision
def advance_paper(snapshot, state, *, decided_at, contexts=()):
    """Use trusted local context data for entry gates; exits stay independent."""
    checked = validate_state(state)
    clock = _clock(decided_at)
    policy = deepcopy(checked["stress_policy"])
    evaluations = []
    manage_existing = checked["position"] is not None or checked["pending"] is not None

    def check_entry(identifier, row, decision_clock, *, stage):
        if stage == "decision" and manage_existing:
            return False
        if identifier != KERNEL_STRATEGY_ID:
            return False
        _require(_clock(decision_clock) == clock, "The base entry hook changed its actual decision clock.")
        query = {"decision_at": clock, "source_date": row["source_date"] if stage == "fill" else None}
        rejected = False
        try:
            gate = context.select_gate(contexts, **query)
            gate = context.validate_gate_result(gate)
            if len(base._canonical(gate)) > MAX_GATE_RECORD_BYTES-1024:
                raise context.StressContextError("New context is too large for its retained policy gate.")
        except context.StressContextError:
            rejected = True
            gate = context.validate_gate_result(context.select_gate((), **query))
        _require(type(gate["allowed"]) is bool and gate["stage"] == stage, "Entry gates require an explicit boolean and matching stage.")
        record = {"sequence": policy["context_count"]+len(evaluations)+1, "stage": stage,
            "source_date": row["source_date"], "recorded_at": clock.isoformat(),
            "context_input_rejected": rejected, "gate": deepcopy(gate),
            "previous_sha256": evaluations[-1]["prefix_sha256"] if evaluations else policy["context_prefix_sha256"]}
        record["prefix_sha256"] = base._digest(record)
        evaluations.append(record)
        return gate["allowed"]

    result = base._advance_paper(snapshot, _projection(checked), decided_at=clock.isoformat(),
        decision_gate=lambda identifier, row, stamp: check_entry(identifier, row, stamp, stage="decision"),
        fill_gate=lambda identifier, row, stamp: check_entry(identifier, row, stamp, stage="fill"))
    policy["context_history"] = (policy["context_history"]+evaluations)[-MAX_GATE_HISTORY:]
    policy["context_count"] += len(evaluations)
    if evaluations:
        policy["context_prefix_sha256"] = evaluations[-1]["prefix_sha256"]
    updated = deepcopy(result["state"])
    updated.update(schema=4, rule_sha256=RULE_SHA256, stress_policy=policy)
    updated = validate_state(updated)
    return {**result, "state": updated, "summary": summarize(updated)}
