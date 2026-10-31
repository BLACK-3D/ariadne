"""Finite multi-stock research using one existing trade ledger per portfolio.

No Store, owner state, data acquisition, broker, model or activation capability.
Eight portfolios are separate counterfactuals, never capital to be pooled.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib

from . import options_campaign as core
from .options_data import research_symbol_set, validate_dataset
from .options_regime import RegimeTimeline, RULE_SHA256 as REGIME_RULE_SHA256
from .qqq_data import is_session, session_close_utc

INTENT_SHA256 = "f6c0929e7269e38baa2a02095112d9ea20244bb4331540312fd3505aa6baea57"
CAUSAL_CLARIFICATIONS_SHA256 = "146ebe97bff05cd93bf987e7a63b565d8fb0e01f4254e588dde48962e784ba51"
KERNEL_RULE_SHA256 = "332efb2157162bb165f0617aa1911f52707aa3e95ab92cc776d685b663445431"
LIQUIDITY_PROFILES = core.LIQUIDITY_PROFILES
MAX_SYMBOLS, MAX_GROUPS, MAX_CHUNKS = 64, 10000, 96
MAX_SOURCE_BYTES, MAX_DAILY_QUOTES = 512*1024*1024, 200000
MAX_LEDGERS, MAX_OUTPUT_BYTES = 16, 128*1024*1024

def _preset(raw, gated, combo):
    return {"cash": {"kernel": "cash", "gated": False},
        **{name: {"kernel": kernel, "gated": False} for name, kernel in raw},
        **{name: {"kernel": kernel, "gated": True} for name, kernel in gated},
        "regime_rotating": {"kernel": combo, "gated": True}}

PRESETS = {
    "conventional_regime": _preset(
        (("raw_recovery_call", "recovery_call"), ("raw_breakout_call", "breakout_call"), ("raw_breakdown_put", "breakdown_put")),
        (("growth_recovery_call", "recovery_call"), ("growth_breakout_call", "breakout_call"), ("stress_breakdown_put", "breakdown_put")), "rotating_ensemble"),
    "volatility_regime": _preset(
        (("raw_compression_call", "compression_breakout_call"), ("raw_pullback_call", "trend_pullback_call"), ("raw_shock_put", "downside_shock_put")),
        (("growth_compression_call", "compression_breakout_call"), ("growth_pullback_call", "trend_pullback_call"), ("stress_shock_put", "downside_shock_put")), "followup_ensemble"),
}
PRESET_IDS = tuple(PRESETS)
RULE = {"schema": 1, "id": "shared-options-universe-research-v1", "intent_sha256": INTENT_SHA256,
    "causal_clarifications_sha256": CAUSAL_CLARIFICATIONS_SHA256,
    "kernel_rule_sha256": KERNEL_RULE_SHA256, "regime_rule_sha256": REGIME_RULE_SHA256,
    "presets": PRESETS, "entry_cap": "100.00", "shared_monthly_gross_cap": "500.00",
    "capital": "One initial500 and one later-month500 only when verified flat per PORTFOLIO, never per stock. Counterfactuals/presets/profiles are not pooled.",
    "selector": "Frozen lexical symbols, round-robin starting at a persistent pointer advanced only after filled entry; first current enabled signal with affordable kernel-eligible quote. Frames starting with position/pending manage only that exposure; global selection resumes the next flat frame. Kernel strategy rotation and contract order are unchanged. Never select by historical return.",
    "entry_gate": "Saved underlying spot strictly below100 at decision AND BUY fill, plus directional regime for gated portfolios. Over100 warmup/held exit rows stay valid; no future-max or current-price universe filter.",
    "regimes": "Ready recovery_growth permits new calls; ready stress_unwind permits new puts. New decisions use benchmark availability at row.available_at; pendingBUY fill gates use ONLY context already published by row.observed_at, never the later current close. All other context blocks only new gated exposure. Marks/SELL/deadlines are never disabled.",
    "frames": "Every registered symbol at the exact same observed_at/available_at regular-session frame; no inner join, forward fill, missing or unequal EOF. All streams exhausted before report.",
    "continuity": "Reuse strict kernel position-quote/size/deadline holds. No replacement contract, future-continuity screen, zero liquidation, exercise or invented settlement.",
    "terms": "Source/symbol syntax does not authenticate US-stock/ETF status, raw historical share-price basis, optionability, standard deliverables or event clocks. Registered assumptions remain nondeployable; multiplier100 alone is not100-share proof.",
    "limits": {"symbols": MAX_SYMBOLS, "groups": MAX_GROUPS, "chunks_per_symbol": MAX_CHUNKS,
        "source_bytes": MAX_SOURCE_BYTES, "quotes_per_group": MAX_DAILY_QUOTES, "ledgers": MAX_LEDGERS, "output_bytes": MAX_OUTPUT_BYTES},
    "research_only": True, "deployable": False, "automatic_adoption": False, "model_calls": 0, "network_calls": 0}
RULE_SHA256 = hashlib.sha256(core._canonical(RULE)).hexdigest()

class UniverseError(core.OptionsRecoveryError):
    pass

def _verify():
    if hashlib.sha256(core._canonical(RULE)).hexdigest() != RULE_SHA256:
        raise UniverseError("The registered universe rule changed.")
    if core.RULE_SHA256 != KERNEL_RULE_SHA256:
        raise UniverseError("The reviewed base trade-ledger rule changed.")
    core.profile_rule("strict_sizes")

def profile_rule(preset, liquidity_profile):
    _verify()
    if type(preset) is not str or preset not in PRESETS or type(liquidity_profile) is not str or liquidity_profile not in LIQUIDITY_PROFILES:
        raise UniverseError("Choose one fixed universe preset and liquidity assumption.")
    return {**deepcopy(RULE), "preset": preset, "liquidity_profile": liquidity_profile,
        "candidate_ids": list(PRESETS[preset])}

def profile_rule_sha256(preset, liquidity_profile):
    return hashlib.sha256(core._canonical(profile_rule(preset, liquidity_profile))).hexdigest()

def _stock_rows(values, symbol, symbols, budget, checkpoint, quality):
    chunks = [values] if type(values) is dict else values
    previous_day = previous_available = basis = None
    for count, dataset in enumerate(chunks, 1):
        if checkpoint: checkpoint()
        if count > MAX_CHUNKS: raise UniverseError("A universe stock exceeds96 chunks.")
        raw = core._canonical(dataset)
        budget["bytes"] += len(raw)
        if budget["bytes"] > MAX_SOURCE_BYTES: raise UniverseError("Universe source chunks exceed512MiB in aggregate.")
        checked = validate_dataset(dataset, research_symbols=symbols)
        if set(checked["symbols"]) != {symbol}: raise UniverseError("Each stock producer must contain exactly its declared symbol.")
        current = (dataset["evidence_kind"], dataset["source"]["quote_time_basis"], dataset["currency"], dataset["source"]["provider"])
        if basis is not None and current != basis: raise UniverseError("A stock evidence/clock basis changed across chunks.")
        if budget["basis"] is not None and current[:3] != budget["basis"]: raise UniverseError("Universe evidence/clock bases differ.")
        budget["basis"], basis = current[:3], current
        quality[symbol].append({**checked, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "source_sha256": dataset["source"]["sha256"], "canonical_bytes": len(raw)})
        for supplied in dataset["snapshots"]:
            if checkpoint: checkpoint()
            row = deepcopy(supplied)
            observed, available = core._clock(row["observed_at"]), core._clock(row["available_at"])
            day = core._day_at(observed)
            close = session_close_utc(day)
            if (not is_session(day) or not close-timedelta(minutes=30) <= observed <= close or available < observed
                    or previous_day is not None and (day <= previous_day or available <= previous_available)):
                raise UniverseError("Use ordered regular-session stock rows with causal availability.")
            previous_day, previous_available = day, available
            yield row

def _cache(row, profiles, disposal):
    day, available = core._day_at(core._clock(row["observed_at"])), core._clock(row["available_at"])
    spot = core._number(row["underlying_price"])
    context = (day, core._day_at(available), spot)
    result = {"by_id": {quote["contract_id"]: quote for quote in row["quotes"]}}
    for profile in profiles:
        result[profile] = {}
        for kind in ("call", "put"):
            eligible, reasons = [], dict.fromkeys(core.REJECTIONS, 0)
            for quote in row["quotes"]:
                reason = core._reject(quote, row, kind, available, profile, disposal, context)
                if reason is None: eligible.append(quote)
                else: reasons[reason] += 1
            eligible.sort(key=lambda quote: (abs((date.fromisoformat(quote["expiry"])-day).days-14),
                abs(core._number(quote["strike"])-spot), quote["contract_id"]))
            result[profile][kind] = eligible, reasons
    return result

def _signals(spec, raw, context):
    if not spec["gated"]: return raw
    allowed = "call" if context["status"] == "ready" and context["label"] == "recovery_growth" else "put" if context["status"] == "ready" and context["label"] == "stress_unwind" else None
    return {key: value and core.SIGNAL_KINDS[key] == allowed for key, value in raw.items()}

def _entry_allowed(spec, row, context, pending):
    if not 0 < core._number(row["underlying_price"]) < 100: return False
    if not spec["gated"]: return True
    if context["status"] != "ready": return False
    allowed = "call" if context["label"] == "recovery_growth" else "put" if context["label"] == "stress_unwind" else None
    return allowed is not None and (not pending or pending["action"] != "BUY" or pending["type"] == allowed)

def _affordable(state, row, cached, signals):
    cash, committed = core._number(state["cash"]), core._number(state["monthly_committed"])
    month = core._day_at(core._clock(row["available_at"])).strftime("%Y-%m")
    if month > state["month"] and state["position"] is None and state["pending"] is None:
        # Read-only selector projection. Only _step actually books this one deposit.
        cash, committed = cash+500, Decimal(0)
    enabled, rotation = core.CANDIDATES[state["candidate_id"]]["signals"], core._rotation(state["candidate_id"])
    order = rotation[state["next_priority"]:]+rotation[:state["next_priority"]]
    for identifier in order:
        if identifier not in enabled or not signals[identifier]: continue
        quotes = cached[state["liquidity_profile"]][core.SIGNAL_KINDS[identifier]][0]
        if quotes:
            # Selection hint only. _step independently checks/bookkeeps every fill.
            reserve = core._debit(core._number(quotes[0]["ask"])*100)+2*core._number(core.RULE["fee_per_side"])
            if reserve+committed <= 500 and reserve <= cash: return True
    return False

@core._precision
def universe_campaign(datasets, *, symbols, benchmark=None, preset="conventional_regime",
        liquidity_profiles=LIQUIDITY_PROFILES, checkpoint=None):
    """Zip bounded stock streams; call the unchanged ledger once per portfolio/day."""
    _verify()
    if type(symbols) is not tuple: raise UniverseError("Use an explicit immutable research-symbol tuple.")
    symbols = tuple(sorted(research_symbol_set(symbols)))
    if type(datasets) is not dict or set(datasets) != set(symbols): raise UniverseError("Stock producers must exactly match registered symbols.")
    if type(preset) is not str or preset not in PRESETS: raise UniverseError("Unknown fixed universe preset.")
    if (type(liquidity_profiles) not in (tuple, list) or not liquidity_profiles
            or any(type(value) is not str or value not in LIQUIDITY_PROFILES for value in liquidity_profiles)
            or len(set(liquidity_profiles)) != len(liquidity_profiles)):
        raise UniverseError("Choose unique fixed liquidity assumptions.")
    profiles = tuple(value for value in LIQUIDITY_PROFILES if value in liquidity_profiles)
    if len(profiles)*len(PRESETS[preset]) > MAX_LEDGERS: raise UniverseError("Universe campaign exceeds16 portfolio ledgers.")
    if benchmark is not None and (not isinstance(benchmark, RegimeTimeline) or benchmark.rule_sha256 != REGIME_RULE_SHA256):
        raise UniverseError("Use the fixed immutable benchmark timeline or explicit unavailable context.")
    if checkpoint is not None and not callable(checkpoint): raise UniverseError("Checkpoint must be a trusted host callback.")
    spec = PRESETS[preset]
    families = core.CONVENTIONAL_SIGNALS if preset == "conventional_regime" else core.FOLLOWUP_SIGNALS
    history_limit = 15 if preset == "conventional_regime" else 65
    budget, quality = {"bytes": 0, "basis": None}, {symbol: [] for symbol in symbols}
    iterators = {symbol: iter(_stock_rows(datasets[symbol], symbol, symbols, budget, checkpoint, quality)) for symbol in symbols}
    states, events, curves, pointers = {}, {}, {}, {}
    histories = {symbol: [] for symbol in symbols}
    contexts, first, last, previous, groups, quotes = [], None, None, None, 0, 0
    prefix = hashlib.sha256(b"[")
    while True:
        if checkpoint: checkpoint()
        frame, ended = {}, []
        for symbol in symbols:
            try: frame[symbol] = next(iterators[symbol])
            except StopIteration: ended.append(symbol)
        if len(ended) == len(symbols): break
        if ended: raise UniverseError("Universe stock streams ended at different sessions.")
        reference = frame[symbols[0]]
        if any((row["observed_at"], row["available_at"]) != (reference["observed_at"], reference["available_at"]) for row in frame.values()):
            raise UniverseError("Universe stocks have mismatched observation/availability frames.")
        groups += 1
        if groups > MAX_GROUPS: raise UniverseError("Universe exceeds10000 daily groups.")
        count = sum(len(row["quotes"]) for row in frame.values())
        if count > MAX_DAILY_QUOTES: raise UniverseError("Universe daily quote count exceeds200000.")
        quotes += count
        day = core._day_at(core._clock(reference["observed_at"]))
        gap = previous is not None and len(core._sessions(previous+timedelta(days=1), day)) != 1
        first, last = first or day.isoformat(), day.isoformat()
        if groups > 1: prefix.update(b",")
        prefix.update(core._canonical(frame))
        unavailable = {"status": "unavailable", "label": None, "source_session": None, "reason": "benchmark_not_supplied"}
        context = benchmark.at(reference["available_at"]) if benchmark else unavailable
        fill_context = benchmark.at(reference["observed_at"]) if benchmark else unavailable
        keys = ("status", "label", "source_session", "available_at", "reason")
        contexts.append({"decision_at": reference["available_at"], "decision": {key: context.get(key) for key in keys},
            "fill_at": reference["observed_at"], "fill": {key: fill_context.get(key) for key in keys}})
        raw_signals, caches, disposal = {}, {}, {}
        for symbol, row in frame.items():
            histories[symbol] = (histories[symbol]+[{"underlying_price": row["underlying_price"], "observed_at": row["observed_at"]}])[-history_limit:]
            raw_signals[symbol] = {key: core.signal(key, histories[symbol]) for key in families}
            caches[symbol] = _cache(row, profiles, disposal)
        if not states:
            for profile in profiles:
                states[profile], events[profile], curves[profile], pointers[profile] = {}, {}, {}, {}
                for identifier, item in spec.items():
                    states[profile][identifier] = core._initial(item["kernel"], reference, profile)
                    events[profile][identifier], curves[profile][identifier], pointers[profile][identifier] = [], [], 0
        for profile in profiles:
            for identifier, item in spec.items():
                state, log, curve = states[profile][identifier], events[profile][identifier], curves[profile][identifier]
                if state["hold"]: continue
                active = state["position"] or state["pending"]
                chosen = active["underlying_symbol"] if active else None
                if chosen is None:
                    start = pointers[profile][identifier]
                    order = symbols[start:]+symbols[:start]
                    for symbol in order:
                        signals = _signals(item, raw_signals[symbol], context)
                        if (_entry_allowed(item, frame[symbol], context, None)
                                and _affordable(state, frame[symbol], caches[symbol], signals)):
                            chosen = symbol; break
                chosen = chosen or symbols[0]
                row, before_position, before_pending = frame[chosen], deepcopy(state["position"]), deepcopy(state["pending"])
                gate_context = fill_context if before_pending and before_pending["action"] == "BUY" else context
                allowed = _entry_allowed(item, row, gate_context, state["pending"])
                # No same-symbol redecision after an exit/cancellation: next flat
                # frame re-enters through the global round-robin selector.
                signals = {key: False for key in families} if active else _signals(item, raw_signals[chosen], context)
                offset = len(log)
                if gap:
                    core._hold(state, "historical_session_gap", "A universe regular session is absent; no repair or fill.", row, log)
                else:
                    core._step(state, row, histories[chosen], caches[chosen], signals, log, curve, entry_allowed=allowed)
                for event in log[offset:]:
                    event["underlying_symbol"] = None if event["type"] == "contribution" else chosen
                    if event.get("action") == "BUY": event["underlying_spot"] = row["underlying_price"]
                    using_fill = bool(before_pending and before_pending["action"] == "BUY" and (event["type"] == "cancelled" or event.get("action") == "BUY"))
                    used = fill_context if using_fill else context
                    event["regime_status"], event["regime_label"] = used["status"], used["label"]
                    event["regime_checked_at"] = row["observed_at"] if using_fill else row["available_at"]
                if state["position"] is not None:
                    state["position"]["underlying_symbol"] = chosen
                    if before_position is None:
                        state["position"]["decision_underlying_spot"] = before_pending["decision_underlying_spot"]
                        state["position"]["fill_underlying_spot"] = row["underlying_price"]
                        pointers[profile][identifier] = (symbols.index(chosen)+1)%len(symbols)
                if state["pending"] is not None:
                    state["pending"]["underlying_symbol"] = chosen
                    if state["pending"]["action"] == "BUY" and (before_pending is None or before_pending["decided_at"] != state["pending"]["decided_at"]):
                        state["pending"]["decision_underlying_spot"] = row["underlying_price"]
        previous = day
    if not groups: raise UniverseError("No common universe session was supplied.")
    prefix.update(b"]")
    _verify()
    coverage = {"symbol": "registered_stock_universe", "symbols": list(symbols), "source_rows": groups,
        "source_symbol_rows": groups*len(symbols), "source_quotes": quotes, "chunks": sum(len(value) for value in quality.values()),
        "first_date": first, "last_source_date": last, "evidence_kind": budget["basis"][0],
        "quote_time_basis": budget["basis"][1], "source_prefix_sha256": prefix.hexdigest(), "source_stream_exhausted": True}
    output = {}
    context_complete = all(value["decision"]["status"] == value["fill"]["status"] == "ready" for value in contexts)
    for profile in profiles:
        reports = {}
        for identifier, state in states[profile].items():
            report = core._report(state, events[profile][identifier], curves[profile][identifier], coverage)
            attribution = {symbol: {"entries": 0, "closed_trades": 0, "fees": Decimal(0), "realized_pnl": Decimal(0)} for symbol in symbols}
            for event in events[profile][identifier]:
                if event["type"] != "fill": continue
                item = attribution[event["underlying_symbol"]]
                item["fees"] += core._number(event["fee"])
                if event["action"] == "BUY": item["entries"] += 1
                else:
                    item["closed_trades"] += 1
                    item["realized_pnl"] += core._number(event["trade_pnl"], signed=True)
            for item in attribution.values():
                item["fees"], item["realized_pnl"] = core._text(item["fees"]), core._text(item["realized_pnl"])
            report.update(candidate_id=identifier, kernel_candidate_id=state["candidate_id"], shared_across_symbols=True,
                context_required=spec[identifier]["gated"], context_complete=context_complete if spec[identifier]["gated"] else True,
                next_symbol_priority=symbols[pointers[profile][identifier]], symbol_attribution=attribution)
            reports[identifier] = report
        complete = all(value["coverage"]["complete"] and value["context_complete"] for value in reports.values())
        traded = [key for key, value in reports.items() if key != "cash" and value["metrics"]["closed_trades"]]
        ranked = sorted(traded, key=lambda key: (-core._number(reports[key]["metrics"]["trading_pnl"], signed=True),
            core._number(reports[key]["metrics"]["max_trading_pnl_drop"]), list(spec).index(key))) if complete and budget["basis"][0] == "historical_quotes" else []
        output[profile] = {"schema": 1, "rule": profile_rule(preset, profile), "rule_sha256": profile_rule_sha256(preset, profile),
            "candidate_ids": list(spec), "candidate_results": reports, "coverage": coverage,
            "comparison": {"ranked_ids": ranked, "all_ledgers_complete": complete, "cash_control_pnl": reports["cash"]["metrics"]["trading_pnl"],
                "holdout_qualified": False, "automatic_adoption": False, "scope": "Separate same-window portfolios, each with one shared500 ledger; no pooled returns or universal best-strategy claim."},
            "research_only": True, "deployable": False, "execution_qualified": False}
    result = {"schema": 1, "kind": "finite_shared_options_universe", "preset": preset, "symbols": list(symbols),
        "registry_sha256": RULE_SHA256, "profiles": output, "coverage": coverage, "data_quality": quality,
        "source_canonical_bytes": budget["bytes"], "source_streams_exhausted": True, "benchmark": benchmark.describe() if benchmark else None,
        "context_observations": contexts, "research_only": True, "deployable": False, "automatic_adoption": False,
        "model_calls": 0, "network_calls": 0, "owner_operations": 0,
        "limitations": ["At most64 registered symbols; observed coverage is not an exhaustive US market or survivorship-free historical universe.",
            "Prices, event/publication clocks, asset type and standard deliverables remain unverified unless separately established. No synthetic option history is promoted to actual quotes.",
            "Every portfolio has one500 budget across all stocks; counterfactuals, presets and assumptions are never pooled.",
            "Missing exact position quotes preserve dated holds; no carry repair, zero liquidation or invented settlement.",
            "Causal price-context labels/analogs do not identify an AI bubble or establish future profitability. Incomplete and zero-trade outcomes remain visible."]}
    if len(core._canonical(result)) > MAX_OUTPUT_BYTES: raise UniverseError("Universe report exceeds128MiB; no truncation.")
    if checkpoint: checkpoint()
    return result
