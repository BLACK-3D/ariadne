"""Finite matched historical counterfactuals with one $25k bankroll each.

Historical observation/publication assumptions never become a forward source
receipt or a schema6 activation. This separate research envelope shares the
capital proposal's exact sizing calculation, not the owner's state/authority.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib

from . import options_bankroll_plan as capital
from . import options_campaign as signals
from .options_data import research_symbol_set
from .options_regime import RegimeTimeline, RULE_SHA256 as REGIME_SHA256
from .options_universe import _stock_rows

MAX_GROUPS, MAX_SYMBOLS, MAX_BYTES, MAX_OUTPUT = 10000, 64, 512*1024**2, 128*1024**2
CANDIDATES = ("cash", "stress_breakdown_put", "raw_breakdown_put", "growth_recovery_call")
RULE = {"schema": 1, "id": "total-bankroll-matched-options-research-v1",
        "capital_policy_sha256": capital.POLICY_SHA256,
        "signal_rule_sha256": signals.RULE_SHA256, "regime_rule_sha256": REGIME_SHA256,
        "candidates": list(CANDIDATES), "initial_total_capital": "25000.00",
        "additional_contributions": "0.00", "entry_ceiling": "1000.00",
        "equity_fraction": "0.04", "shared_monthly_gross_ceiling": "25000.00",
        "liquidity_profile": "strict_sizes", "fee_per_contract_per_side": "0.65",
        "selection": "Lexical explicit roots, round robin advanced only after filled BUY; first affordable eligible quote ranked nearest14DTE/spot/contractID; no outcome selection.",
        "clock": "At least14 completed closes. Decisions at saved available_at. Fill only next regular observed session after decision+60s. Fill context uses observed_at, never current available_at. Historical clocks remain unverified research assumptions.",
        "month": "Only verified-flat, unheld state resets commitment at an observed new month with NO cash credit. Position or pending decision crossing month, or missed disposal, holds. No profits replenish within-month gross allowance.",
        "contract": "US-stock/ETF status and standard100-share deliverables remain source assumptions; explicit roots, saved raw price<100 at decision and BUY fill,7-21DTE,decision NY month expiry,0-5%OTM,positive uncrossed bid/ask,spread<=25%midpoint,actual supplied sidesizes.",
        "quantity": "Same options_bankroll_plan.size() as schema6: premium rounded upward per contract,0.65 entry+0.65 reserved exit per contract,integer q clipped by current supplied ask AND bid sizes/cash/equity/monthly budget. This supports the whole-position entry mark without future liquidity screening. Never rescale prior P&L.",
        "exit": "Exact held contract requires actual bid_size>=entire q. Delayed bid>=5xask or bid<=halfask exit, or precommitted earliest tenth following session/pre-expiry session/penultimate entry-month session. Missing exact quote, insufficient size, gap or missed deadline holds; no partial/zero/fabricated liquidation.",
        "comparison": "Four separate same-window total25k counterfactuals, each with ONE shared position across all supplied roots. Capital and returns never added. Held/open/zero cases retained. When complete/ready historical evidence permits ranking, include cash at0PnL; noncash zero-trade cases remain unranked. Development research only, no independent holdout qualification, automatic winner adoption or profit target.",
        "research_only": True, "execution_qualified": False, "deployable": False,
        "owner_operations": 0, "model_calls": 0, "network_calls": 0}
RULE_SHA256 = hashlib.sha256(signals._canonical(RULE)).hexdigest()


class BankrollResearchError(signals.OptionsRecoveryError):
    pass


def _verify():
    if (hashlib.sha256(signals._canonical(RULE)).hexdigest() != RULE_SHA256
            or capital.POLICY_SHA256 != RULE["capital_policy_sha256"]
            or signals.RULE_SHA256 != RULE["signal_rule_sha256"]
            or REGIME_SHA256 != RULE["regime_rule_sha256"]):
        raise BankrollResearchError("The fixed bankroll research contract changed.")
    capital.plan()
    signals.profile_rule("strict_sizes")


def profile_rule():
    _verify()
    return deepcopy(RULE)


def _initial(row):
    return {"cash": "25000", "contributions": "25000", "fees": "0", "monthly_committed": "0",
            "month": signals._day_at(signals._clock(row["available_at"])).strftime("%Y-%m"),
            "position": None, "pending": None, "hold": None, "entries": 0, "closed_trades": 0,
            "wins": 0, "losses": 0, "breakeven": 0, "realized_pnl": "0", "processed_rows": 0,
            "last_source_date": None, "last_available_at": None, "monthly_start_equity": "25000"}


def _equity(state):
    result = signals._number(state["cash"])
    if state["position"]:
        position = state["position"]
        result += (signals._credit(signals._number(position["marked_bid"])*100)*position["quantity"]
                   - Decimal("0.65")*position["quantity"])
    return result


def _size(state, quote):
    # Only called while verified flat; input cash equals net equity here.
    return capital.size(ask=quote["ask"], ask_size=min(quote["ask_size"], quote["bid_size"]), cash=state["cash"],
                        equity=signals._text(_equity(state)), monthly_committed=state["monthly_committed"])


def _liquid(quote, *, side, quantity=1):
    bid, ask = signals._number(quote["bid"]), signals._number(quote["ask"])
    size = quote["ask_size" if side == "BUY" else "bid_size"]
    return (bid > 0 and ask > 0 and bid <= ask and ask-bid <= (bid+ask)/2*Decimal("0.25")
            and type(size) is int and size >= quantity)


def _eligible(quote, row, kind, state):
    day = signals._day_at(signals._clock(row["observed_at"]))
    decision_day = signals._day_at(signals._clock(row["available_at"]))
    expiry, spot = date.fromisoformat(quote["expiry"]), signals._number(row["underlying_price"])
    strike = signals._number(quote["strike"])
    ordinary = (quote["type"] == kind and quote["multiplier"] == 100 and 0 < spot < 100
            and 7 <= (expiry-day).days <= 21 and expiry.strftime("%Y-%m") == decision_day.strftime("%Y-%m")
            and (spot <= strike <= spot*Decimal("1.05") if kind == "call" else spot*Decimal("0.95") <= strike <= spot)
            and signals._deadline(day, expiry) > decision_day and _liquid(quote, side="BUY"))
    if not ordinary or type(quote["bid_size"]) is not int: return False
    quantity = _size(state, quote)["quantity"]
    return quantity > 0


def _kind(identifier):
    return "call" if identifier == "growth_recovery_call" else "put"


def _gate(identifier, context):
    if identifier == "raw_breakdown_put":
        return True
    expected = "recovery_growth" if identifier == "growth_recovery_call" else "stress_unwind"
    return context["status"] == "ready" and context["label"] == expected


def _pending(action, quote, row, reason):
    return {"action": action, "contract_id": quote["contract_id"], "type": quote["type"],
            "expiry": quote["expiry"], "strike": quote["strike"], "underlying_symbol": row["underlying_symbol"],
            "source_date": signals._day_at(signals._clock(row["observed_at"])).isoformat(),
            "decided_at": row["available_at"], "reason": reason}


def _hold(state, row, reason, events):
    state["hold"] = {"code": reason, "source_date": signals._day_at(signals._clock(row["observed_at"])).isoformat(),
                     "available_at": row["available_at"], "retained_previous_dated_mark": True}
    events.append({"type": "held", **state["hold"]})


def _cancel(state, reason, events):
    events.append({"type": "cancelled", "reason": reason, "decision": deepcopy(state["pending"])})
    state["pending"] = None


def _buy(state, quote, row, events):
    decision = state["pending"]
    sized = _size(state, quote)
    q = sized["quantity"]
    if q == 0:
        _cancel(state, "shared_capital_or_size_budget", events)
        return
    premium, fee = signals._number(sized["premium"]), signals._number(sized["entry_fee"])
    reserve = signals._number(sized["all_in_commitment"])
    state["cash"] = signals._text(signals._number(state["cash"])-premium-fee)
    state["monthly_committed"] = signals._text(signals._number(state["monthly_committed"])+reserve)
    state["fees"] = signals._text(signals._number(state["fees"])+fee)
    state["entries"] += 1
    day = signals._day_at(signals._clock(row["observed_at"]))
    state["position"] = {"contract_id": quote["contract_id"], "type": quote["type"], "strike": quote["strike"],
        "expiry": quote["expiry"], "quantity": q, "multiplier": 100, "underlying_symbol": row["underlying_symbol"],
        "entry_ask": quote["ask"], "premium": sized["premium"], "entry_fee": sized["entry_fee"],
        "reserved_exit_fee": sized["reserved_exit_fee"], "budget_debit": sized["all_in_commitment"],
        "entry_at": row["observed_at"], "decision_at": decision["decided_at"], "marked_bid": quote["bid"],
        "entry_ask_size": quote["ask_size"], "entry_bid_size": quote["bid_size"], "last_marked_bid_size": quote["bid_size"],
        "forced_exit_session": signals._deadline(day, date.fromisoformat(quote["expiry"])).isoformat()}
    state["pending"] = None
    events.append({"type": "fill", "action": "BUY", "quantity": q, "contract_id": quote["contract_id"],
        "underlying_symbol": row["underlying_symbol"], "source_date": day.isoformat(),
        "observed_at": row["observed_at"], "available_at": row["available_at"], "decided_at": decision["decided_at"],
        "price": quote["ask"], "premium": sized["premium"], "fee": sized["entry_fee"],
        "reserved_exit_fee": sized["reserved_exit_fee"], "budget_committed": sized["all_in_commitment"],
        "actual_ask_size": quote["ask_size"], "actual_bid_size": quote["bid_size"],
        "entry_risk_ceiling": sized["entry_risk_ceiling"], "simulated": True})


def _sell(state, quote, row, reason, events):
    position = state["position"]
    q, fee = position["quantity"], Decimal("0.65")*position["quantity"]
    gross = signals._credit(signals._number(quote["bid"])*100)*q
    net = gross-fee
    pnl = net-signals._number(position["premium"])-signals._number(position["entry_fee"])
    state["cash"] = signals._text(signals._number(state["cash"])+net)
    state["fees"] = signals._text(signals._number(state["fees"])+fee)
    state["realized_pnl"] = signals._text(signals._number(state["realized_pnl"], signed=True)+pnl)
    state["closed_trades"] += 1
    state["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven"] += 1
    events.append({"type": "fill", "action": "SELL", "quantity": q, "contract_id": quote["contract_id"],
        "underlying_symbol": row["underlying_symbol"], "source_date": signals._day_at(signals._clock(row["observed_at"])).isoformat(),
        "observed_at": row["observed_at"], "available_at": row["available_at"], "price": quote["bid"],
        "gross_credit": signals._text(gross), "fee": signals._text(fee), "net_credit": signals._text(net),
        "trade_pnl": signals._text(pnl), "reason": reason, "simulated": True})
    state["position"], state["pending"] = None, None


def _step(state, row, identifier, *, allowed, selected_quote, events, curve):
    observed, clock = signals._clock(row["observed_at"]), signals._clock(row["available_at"])
    day, decision_day = signals._day_at(observed), signals._day_at(clock)
    month = decision_day.strftime("%Y-%m")
    busy = state["position"] is not None or state["pending"] is not None
    if month < state["month"]:
        raise BankrollResearchError("Research month moved backwards.")
    if month != state["month"]:
        if state["position"] or state["pending"]:
            _hold(state, row, "unresolved_month_boundary", events)
            return
        state.update(month=month, monthly_committed="0", monthly_start_equity=state["cash"])
        events.append({"type": "month_budget_reset", "month": month, "funding_added": "0", "simulated": True})
    quotes = {quote["contract_id"]: quote for quote in row["quotes"]}
    if state["position"]:
        position = state["position"]
        deadline = date.fromisoformat(position["forced_exit_session"])
        quote = quotes.get(position["contract_id"])
        if day > deadline or decision_day > deadline:
            _hold(state, row, "missed_disposal_deadline", events)
            return
        if quote is None or not _liquid(quote, side="SELL", quantity=position["quantity"]):
            _hold(state, row, "missing_or_insufficient_full_quantity_bid", events)
            return
        position["marked_bid"] = quote["bid"]
        position["last_marked_bid_size"] = quote["bid_size"]
        pending, due = state["pending"], day == deadline
        decision = signals._clock(position["decision_at"]) if due else signals._clock(pending["decided_at"]) if pending else None
        if (due or pending) and observed > decision+timedelta(seconds=60):
            _sell(state, quote, row, "precommitted_disposal_deadline" if due else pending["reason"], events)
        elif state["pending"] is None:
            bid, entry = signals._number(quote["bid"]), signals._number(position["entry_ask"])
            reason = "bid_reached_5x_entry_ask" if bid >= entry*5 else "bid_at_or_below_half_entry_ask" if bid <= entry/2 else None
            if reason:
                state["pending"] = _pending("SELL", quote, row, reason)
                events.append({**deepcopy(state["pending"]), "type": "decision", "option_type": quote["type"]})
    elif state["pending"]:
        pending = state["pending"]
        quote = quotes.get(pending["contract_id"])
        following = signals._sessions(date.fromisoformat(pending["source_date"])+timedelta(days=1), day)
        if not allowed:
            _cancel(state, "entry_gate_blocked", events)
        elif len(following) != 1 or observed <= signals._clock(pending["decided_at"])+timedelta(seconds=60):
            _cancel(state, "late_or_unavailable_entry", events)
        elif quote is None or not _eligible(quote, row, _kind(identifier), state):
            _cancel(state, "entry_quote_no_longer_eligible", events)
        else:
            _buy(state, quote, row, events)
    elif not busy and identifier != "cash" and allowed and selected_quote is not None:
        state["pending"] = _pending("BUY", selected_quote, row, identifier+"_registered_signal")
        events.append({**deepcopy(state["pending"]), "type": "decision", "option_type": selected_quote["type"]})
    equity = _equity(state)
    prior = signals._number(curve[-1]["equity"]) if curve else Decimal(25000)
    curve.append({"source_date": day.isoformat(), "available_at": row["available_at"], "month": month,
        "cash": state["cash"], "equity": signals._text(equity), "contributions": "25000",
        "trading_pnl": signals._text(equity-25000), "daily_pnl": signals._text(equity-prior),
        "monthly_pnl": signals._text(equity-signals._number(state["monthly_start_equity"])),
        "monthly_committed": state["monthly_committed"], "fees": state["fees"]})
    if (signals._number(state["cash"]) < 0 or not 0 <= signals._number(state["monthly_committed"]) <= 25000
            or state["contributions"] != "25000"
            or state["entries"]-state["closed_trades"] != int(state["position"] is not None)):
        raise BankrollResearchError("Total-capital cash/commitment/one-position invariant failed.")
    state["processed_rows"] += 1
    state["last_source_date"], state["last_available_at"] = day.isoformat(), row["available_at"]


def _report(identifier, state, events, curve, coverage):
    peak, drop = Decimal(25000), Decimal(0)
    for row in curve:
        equity = signals._number(row["equity"])
        peak = max(peak, equity)
        drop = max(drop, peak-equity)
    complete = state["hold"] is None and state["processed_rows"] == coverage["source_rows"]
    flat = state["position"] is None and state["pending"] is None
    equity = _equity(state)
    return {"candidate_id": identifier, "coverage": {**coverage, "processed_rows": state["processed_rows"],
                "last_processed_date": state["last_source_date"], "all_input_processed": complete,
                "finished_flat": flat, "complete": complete and flat},
            "metrics": {"contributions": "25000", "additional_contributions": "0", "ending_equity": signals._text(equity),
                "trading_pnl": signals._text(equity-25000), "realized_pnl": state["realized_pnl"], "fees": state["fees"],
                "entries": state["entries"], "closed_trades": state["closed_trades"], "wins": state["wins"],
                "losses": state["losses"], "breakeven": state["breakeven"], "max_equity_drawdown": signals._text(drop),
                "monthly_gross_used": state["monthly_committed"], "sample_sufficient_for_edge": False},
            "state": deepcopy(state), "hold": deepcopy(state["hold"]), "events": events, "curve": curve,
            "research_gate": "held_incomplete_evidence" if state["hold"] else "open_at_input_end" if not flat
                else "cash_control" if identifier == "cash" else "no_completed_trades" if state["closed_trades"] == 0 else "exploratory_only",
            "research_only": True, "execution_qualified": False, "deployable": False}


@signals._precision
def backtest(datasets, *, symbols, benchmark=None, checkpoint=None):
    """Exhaust exact aligned streams; never merge missing dates or adopt results."""
    _verify()
    if type(symbols) is not tuple:
        raise BankrollResearchError("Use an explicit immutable tuple of research roots.")
    symbols = tuple(sorted(research_symbol_set(symbols)))
    if not 1 <= len(symbols) <= MAX_SYMBOLS or type(datasets) is not dict or set(datasets) != set(symbols):
        raise BankrollResearchError("Register exactly one producer for each of1-64 explicit roots.")
    if benchmark is not None and (not isinstance(benchmark, RegimeTimeline) or benchmark.rule_sha256 != REGIME_SHA256):
        raise BankrollResearchError("Use the reviewed immutable regime timeline or explicit unavailable context.")
    if checkpoint is not None and not callable(checkpoint):
        raise BankrollResearchError("Checkpoint is a trusted host callback, never input code.")
    budget, quality = {"bytes": 0, "basis": None}, {symbol: [] for symbol in symbols}
    iterators = {symbol: iter(_stock_rows(datasets[symbol], symbol, symbols, budget, checkpoint, quality)) for symbol in symbols}
    histories = {symbol: [] for symbol in symbols}
    states, events, curves, pointers = {}, {}, {}, dict.fromkeys(CANDIDATES, 0)
    groups, quote_count, previous, first, last = 0, 0, None, None, None
    contexts, prefix = [], hashlib.sha256(b"[")
    while True:
        if checkpoint: checkpoint()
        frame, ended = {}, []
        for symbol in symbols:
            try: frame[symbol] = next(iterators[symbol])
            except StopIteration: ended.append(symbol)
        if len(ended) == len(symbols): break
        if ended: raise BankrollResearchError("Registered stock streams ended at different sessions.")
        reference = frame[symbols[0]]
        if any((row["observed_at"], row["available_at"]) != (reference["observed_at"], reference["available_at"]) for row in frame.values()):
            raise BankrollResearchError("Registered observation/publication frames must match exactly.")
        groups += 1
        if groups > MAX_GROUPS or budget["bytes"] > MAX_BYTES:
            raise BankrollResearchError("Finite source/session budget exceeded.")
        day = signals._day_at(signals._clock(reference["observed_at"]))
        gap = previous is not None and len(signals._sessions(previous+timedelta(days=1), day)) != 1
        first, last = first or day.isoformat(), day.isoformat()
        if groups > 1: prefix.update(b",")
        prefix.update(signals._canonical(frame))
        daily_quotes = sum(len(row["quotes"]) for row in frame.values())
        if daily_quotes > 200000: raise BankrollResearchError("A combined source frame exceeds200000 quotes.")
        quote_count += daily_quotes
        unavailable = {"status": "unavailable", "label": None, "source_session": None, "reason": "benchmark_not_supplied"}
        context = benchmark.at(reference["available_at"]) if benchmark else unavailable
        fill_context = benchmark.at(reference["observed_at"]) if benchmark else unavailable
        keys = ("status", "label", "source_session", "available_at", "reason")
        contexts.append({"decision_at": reference["available_at"], "decision": {k: context.get(k) for k in keys},
                         "fill_at": reference["observed_at"], "fill": {k: fill_context.get(k) for k in keys}})
        raw = {}
        for symbol, row in frame.items():
            histories[symbol] = (histories[symbol]+[{"underlying_price": row["underlying_price"], "observed_at": row["observed_at"]}])[-15:]
            raw[symbol] = {kind: signals.signal(kind, histories[symbol]) for kind in ("breakdown_put", "recovery_call")}
        if not states:
            for identifier in CANDIDATES:
                states[identifier], events[identifier], curves[identifier] = _initial(reference), [], []
        for identifier in CANDIDATES:
            state, log, curve = states[identifier], events[identifier], curves[identifier]
            if state["hold"]: continue
            active = state["position"] or state["pending"]
            chosen, selected = active["underlying_symbol"] if active else None, None
            used_context = fill_context if state["pending"] and state["pending"]["action"] == "BUY" else context
            if chosen is None and identifier != "cash" and _gate(identifier, context):
                projected = state
                if signals._day_at(signals._clock(reference["available_at"])).strftime("%Y-%m") > state["month"]:
                    # Read-only new-month budget projection; _step books no cash.
                    projected = {**state, "monthly_committed": "0"}
                start = pointers[identifier]
                for symbol in symbols[start:]+symbols[:start]:
                    if len(histories[symbol]) < 14: continue
                    signal = "recovery_call" if identifier == "growth_recovery_call" else "breakdown_put"
                    if not raw[symbol][signal]: continue
                    eligible = [q for q in frame[symbol]["quotes"] if _eligible(q, frame[symbol], _kind(identifier), projected)]
                    if eligible:
                        eligible.sort(key=lambda q: (abs((date.fromisoformat(q["expiry"])-day).days-14),
                            abs(signals._number(q["strike"])-signals._number(frame[symbol]["underlying_price"])), q["contract_id"]))
                        chosen, selected = symbol, eligible[0]
                        break
            chosen = chosen or symbols[0]
            row = frame[chosen]
            allowed = _gate(identifier, used_context) and 0 < signals._number(row["underlying_price"]) < 100
            offset, prior_position = len(log), deepcopy(state["position"])
            if gap:
                _hold(state, row, "historical_session_gap", log)
            else:
                _step(state, row, identifier, allowed=allowed, selected_quote=selected, events=log, curve=curve)
            for event in log[offset:]:
                event["regime_status"], event["regime_label"] = used_context["status"], used_context["label"]
            if prior_position is None and state["position"] is not None:
                pointers[identifier] = (symbols.index(chosen)+1) % len(symbols)
        previous = day
    if not groups: raise BankrollResearchError("At least one aligned source session is required.")
    prefix.update(b"]")
    coverage = {"symbols": list(symbols), "source_rows": groups, "source_quotes": quote_count,
                "first_date": first, "last_source_date": last, "evidence_kind": budget["basis"][0],
                "quote_time_basis": budget["basis"][1], "source_prefix_sha256": prefix.hexdigest()}
    reports = {identifier: _report(identifier, states[identifier], events[identifier], curves[identifier], coverage) for identifier in CANDIDATES}
    _verify()
    complete = all(report["coverage"]["complete"] for report in reports.values())
    context_complete = all(value["decision"]["status"] == value["fill"]["status"] == "ready" for value in contexts)
    ranked = sorted((key for key in CANDIDATES if key == "cash" or reports[key]["metrics"]["closed_trades"]),
                    key=lambda key: (-signals._number(reports[key]["metrics"]["trading_pnl"], signed=True),
                                     signals._number(reports[key]["metrics"]["max_equity_drawdown"]), CANDIDATES.index(key))) if complete and context_complete and budget["basis"][0] == "historical_quotes" else []
    result = {"schema": 1, "kind": "finite_total_bankroll_options_research", "classification": "development_research", "rule": profile_rule(),
              "rule_sha256": RULE_SHA256, "candidate_results": reports, "coverage": coverage,
              "data_quality": quality, "source_streams_exhausted": True, "source_canonical_bytes": budget["bytes"],
              "benchmark": benchmark.describe() if benchmark else None, "context_observations": contexts,
              "comparison": {"ranked_ids": ranked, "all_ledgers_complete": complete, "context_complete": context_complete,
                "holdout_qualified": False, "automatic_adoption": False, "capital_or_returns_may_be_summed": False},
              "research_only": True, "execution_qualified": False, "deployable": False,
              "owner_operations": 0, "network_calls": 0, "model_calls": 0,
              "limitations": ["This research ledger is not the owner's schema6 ledger or a forward execution proof.",
                "Each counterfactual receives25000 once; no additional contributions or pooled capital/returns.",
                "Retrospective clocks, raw price basis, deliverables, source authenticity and rights remain unverified unless separately established.",
                "Actual side sizes do not establish simultaneous executable liquidity or a fill. Full-quantity missing bids hold the ledger.",
                "Previously examined windows are development data, not independent holdouts; no universal best strategy or profit guarantee."]}
    if len(signals._canonical(result)) > MAX_OUTPUT:
        raise BankrollResearchError("Finite research report exceeds128MiB; no truncation.")
    if checkpoint: checkpoint()
    return result
