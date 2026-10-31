"""Finite, stream-once historical option research; no owner-account authority.

QQQ is an explicit research-universe exception; KO uses an under-$100 research
profile. Neither qualifies the owner's F account. Saved quote and availability
clocks are counterfactual inputs, not records of decisions made in the past.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from functools import wraps
from fractions import Fraction
import hashlib

from .options_data import RESEARCH_SYMBOLS
from .options_recovery import (OptionsRecoveryError, _canonical, _clock, _number,
    _text, _debit, _credit, _day_at, _sessions, _deadline, _recovery, _rows)

MAX_CANDIDATES = 16
MAX_ROWS = 10000
MAX_CHUNKS = 96
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
CONVENTIONAL_SIGNALS = ("recovery_call", "breakout_call", "breakdown_put")
ORIGINAL_SIGNALS = ("rsi_rebound_call", "rsi_exhaustion_put")
LEGACY_SIGNALS = CONVENTIONAL_SIGNALS + ORIGINAL_SIGNALS
FOLLOWUP_SIGNALS = ("compression_breakout_call", "trend_pullback_call", "downside_shock_put")
SIGNALS = LEGACY_SIGNALS + FOLLOWUP_SIGNALS
FOLLOWUP_WARMUP = {"compression_breakout_call": 41, "trend_pullback_call": 65, "downside_shock_put": 23}
SIGNAL_KINDS = {"recovery_call": "call", "breakout_call": "call", "breakdown_put": "put",
    "rsi_rebound_call": "call", "rsi_exhaustion_put": "put",
    "compression_breakout_call": "call", "trend_pullback_call": "call", "downside_shock_put": "put"}
CANDIDATES = {
    "cash": {"kind": "control", "signals": []},
    "recovery_call": {"kind": "conventional", "signals": ["recovery_call"]},
    "breakout_call": {"kind": "conventional", "signals": ["breakout_call"]},
    "breakdown_put": {"kind": "conventional", "signals": ["breakdown_put"]},
    "rotating_ensemble": {"kind": "predeclared_combination", "signals": list(CONVENTIONAL_SIGNALS)},
    "rsi_rebound_call": {"kind": "ariadne_original", "signals": ["rsi_rebound_call"]},
    "rsi_exhaustion_put": {"kind": "ariadne_original", "signals": ["rsi_exhaustion_put"]},
    "diversified_ensemble": {"kind": "predeclared_combination", "signals": list(LEGACY_SIGNALS)},
}
DEFAULT_CANDIDATE_IDS = tuple(CANDIDATES)
CANDIDATES.update({
    identifier: {"kind": "ariadne_followup", "signals": [identifier]} for identifier in FOLLOWUP_SIGNALS})
CANDIDATES["followup_ensemble"] = {"kind": "predeclared_combination", "signals": list(FOLLOWUP_SIGNALS)}
CANDIDATE_IDS = tuple(CANDIDATES)
FOLLOWUP_CANDIDATE_IDS = ("cash", *CONVENTIONAL_SIGNALS, *FOLLOWUP_SIGNALS, "followup_ensemble")
LIQUIDITY_PROFILES = ("strict_sizes", "unknown_size_price_proxy")
RULE = {
    "id": "historical-options-campaign-v3", "schema": 1,
    "universe": list(RESEARCH_SYMBOLS), "research_only": True, "deployable": False,
    "signals": {
        "recovery_call": "14 closes required; current exceeds prior two; current/prior four contain a 3% decline from its preceding ten-close high",
        "breakout_call": "14 closes required; current exceeds preceding ten-close maximum and arithmetic mean",
        "breakdown_put": "14 closes required; current is below preceding ten-close minimum and arithmetic mean",
        "rsi_rebound_call": "15 closes required; simple arithmetic RSI14<=30 and current close strictly exceeds both preceding closes; call",
        "rsi_exhaustion_put": "15 closes required; simple arithmetic RSI14>=70 and current close strictly below both preceding closes; put",
        "compression_breakout_call": "41 closes;20-close population bands at2 deviations; normalized variance at t-2 is a20-bandwidth minimum; t-2 not above its upper band; t-1 and t strictly above their own upper bands; normalized variance rises from t-1 to t; call",
        "trend_pullback_call": "65 closes; close>SMA60; SMA60 exceeds its five-session lag; previous close<=0.98 times the close six sessions before current; current strictly above both preceding closes; call",
        "downside_shock_put": "23 closes; prior20 returns exclude shock and confirmation; positive population variance; prior return negative and at least2 deviations below its prior mean; current return negative and close<SMA20; put",
    },
    "signal_kinds": SIGNAL_KINDS,
    "original_rsi_definition": "Last15 closes yield14 changes; arithmetic mean positive changes and arithmetic mean absolute negative changes, without Wilder smoothing; RSI=100-100/(1+meanGain/meanLoss), equivalently100*sumGain/(sumGain+sumLoss) with the common14 denominator cancelled before division; flat=50, gains/no losses=100, losses/no gains=0. Fixed Ariadne hypotheses, not creator replications or published profitable-option findings.",
    "followup_intent_sha256": "1dce791c090233c4f938a0bd00b7666b56bc7e26a3e193e5b73a707eacaaabdf",
    "followup_definition": "Exact rational indicators from validated raw decimal closes; population means/variances, no square roots or normality probabilities.20-close Bollinger-style compression window deliberately differs from the original125-period/ADX method.60-close trend/pullback and downside-shock rules are fixed Ariadne hypotheses, not profitable published-option findings. At most65 closes retained, legacy signals receive their prior15-close inputs. No new indicator is computed when no followup candidate is selected.",
    "KO_contract_terms": "KO is research-only and requires each saved underlying price below100. A normalized100-share multiplier and EOD availability are unverified simulation assumptions unless independently established by source terms/clocks; actual side sizes alone do not qualify execution.",
    "candidates": CANDIDATES,
    "contract": "One fully paid standard100-share long call OR put total per counterfactual; no stock, short leg, spread, exercise or real order",
    "entry_maximum_debit": "100.00", "monthly_maximum_debit": "500.00",
    "arithmetic": "Fixed40-digit Decimal ROUND_HALF_EVEN context, independent of caller precision/rounding/traps; monetary debits round upward and credits downward to cents",
    "fee_per_side": "0.65", "funding": "Initial500 and one500 increment only upon an observed later month when flat; no missing-month catch-up or profit recycling",
    "selection": "7-21 calendarDTE at decision and fill; expiry in decision NY month; strike0-5%OTM; nearest14DTE, closest spot, contractID; current-month disposal remains future",
    "fill": "Decision at saved available_at; buy later-session ask after decision+60s; sell later eligible bid after decision+60s; mandatory disposal is precommitted",
    "exit": "Observe bid>=5x entry ask or bid<=0.5x entry ask, then delayed sale; mandatory earliest tenth following regular session, preceding expiry session, penultimate entry-month session",
    "liquidity": {"strict_sizes": "Positive uncrossed bid/ask; spread<=25% midpoint; actual executable-side size>=1; missing size rejects/holds",
        "unknown_size_price_proxy": "Same price/spread gates; supplied side size must>=1, but None remains explicitly unknown and permitted only for nondeployable price-proxy research; zero size rejects/holds"},
    "ensemble": "One shared500 ledger; fixed recovery-call/breakout-call/breakdown-put priority rotates after entry; standalone counterfactual capital/returns never added",
    "diversified_ensemble": "One shared500 ledger; fixed recovery_call/breakout_call/breakdown_put/rsi_rebound_call/rsi_exhaustion_put priority rotates after entry; original three-signal rotating_ensemble membership and rotation remain unchanged; standalone capital/returns never added",
    "followup_ensemble": "One separate500 ledger shared by compression_breakout_call/trend_pullback_call/downside_shock_put; listed priority rotates after entry; never add standalone or other ensemble capital/returns",
    "default_candidates": list(DEFAULT_CANDIDATE_IDS), "followup_candidates": list(FOLLOWUP_CANDIDATE_IDS),
    "scope": "Fixed QQQ/KO historical research only; QQQ is an explicit below100-universe exception. Neither source/clock/terms profile is the owner's F account; no adoption or funding is permitted",
    "sources": ["https://www.optionseducation.org/strategies/all-strategies/long-call",
        "https://www.optionseducation.org/strategies/all-strategies/long-put",
        "https://www.optionseducation.org/referencelibrary/faq/option-price-behavior",
        "https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf",
        "https://www.bollingerbands.com/_files/ugd/58be43_d09c50b6e8ea4afd9af0523ef94de876.pdf",
        "https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data",
        "https://www.optionseducation.org/referencelibrary/faq/technical-information"],
}
RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()
REJECTIONS = ("wrong_side", "dte_outside_7_21", "expiry_outside_decision_month",
    "outside_0_5_percent_otm", "entry_over_100_including_fees", "no_future_disposal_session",
    "nonpositive_or_crossed_bid_ask", "spread_above_25_percent_midpoint", "ask_size_unavailable_or_zero")


def _precision(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)
    return guarded


def profile_rule(liquidity_profile):
    if type(liquidity_profile) is not str or liquidity_profile not in LIQUIDITY_PROFILES:
        raise OptionsRecoveryError("Choose one fixed research liquidity profile.")
    if hashlib.sha256(_canonical(RULE)).hexdigest() != RULE_SHA256:
        raise OptionsRecoveryError("The frozen campaign registry changed.")
    result = deepcopy(RULE)
    result["liquidity_profile"] = liquidity_profile
    return result


def profile_rule_sha256(liquidity_profile):
    return hashlib.sha256(_canonical(profile_rule(liquidity_profile))).hexdigest()


def _candidates(values):
    values = list(DEFAULT_CANDIDATE_IDS) if values is None else values
    if (type(values) is not list or not 1 <= len(values) <= MAX_CANDIDATES
            or any(type(value) is not str or value not in CANDIDATES for value in values)
            or len(values) != len(set(values))):
        raise OptionsRecoveryError("Choose unique fixed campaign candidate IDs; arbitrary rules/code are not accepted.")
    return [value for value in CANDIDATE_IDS if value in values]


def _rsi14(prices):
    changes = [right-left for left, right in zip(prices, prices[1:])]
    gains = sum((max(change, Decimal(0)) for change in changes), Decimal(0))
    losses = sum((max(-change, Decimal(0)) for change in changes), Decimal(0))
    # Both arithmetic means divide by14. Cancelling that common denominator
    # avoids introducing intermediate rounding at the exact30/70 boundaries.
    return Decimal(50) if gains == losses == 0 else 100*gains/(gains+losses)


def _moments(values):
    mean = sum(values, Fraction()) / len(values)
    return mean, sum(((value-mean)**2 for value in values), Fraction()) / len(values)


def _above_band(value, mean, variance):
    difference = value-mean
    return difference > 0 and difference*difference > 4*variance


def _followup_signal(identifier, history):
    count = FOLLOWUP_WARMUP[identifier]
    if len(history) < count:
        return False
    prices = [Fraction(_number(value["underlying_price"])) for value in history[-count:]]
    if identifier == "compression_breakout_call":
        bands = [_moments(prices[index-19:index+1]) for index in range(19, 41)]
        mean, variance = bands[-3]
        compressed = all(variance*other_mean**2 <= other_variance*mean**2
                         for other_mean, other_variance in bands[:-2])
        return (compressed and not _above_band(prices[-3], *bands[-3])
            and _above_band(prices[-2], *bands[-2]) and _above_band(prices[-1], *bands[-1])
            and bands[-1][1]*bands[-2][0]**2 > bands[-2][1]*bands[-1][0]**2)
    if identifier == "trend_pullback_call":
        current = sum(prices[-60:], Fraction())/60
        previous = sum(prices[:60], Fraction())/60
        return (prices[-1] > current > previous and prices[-2] <= Fraction(98, 100)*prices[-7]
                and prices[-1] > max(prices[-3:-1]))
    returns = [right/left-1 for left, right in zip(prices, prices[1:])]
    mean, variance = _moments(returns[:-2])
    shock, confirmation = returns[-2:]
    return (variance > 0 and shock < 0 and shock < mean and (shock-mean)**2 >= 4*variance
            and confirmation < 0 and prices[-1] < sum(prices[-20:], Fraction())/20)


@_precision
def signal(identifier, history):
    if identifier not in SIGNALS:
        raise OptionsRecoveryError("Unknown frozen campaign signal.")
    if len(history) < 14:
        return False
    if identifier in FOLLOWUP_SIGNALS:
        return _followup_signal(identifier, history)
    if identifier in ORIGINAL_SIGNALS:
        if len(history) < 15:
            return False
        prices = [_number(value["underlying_price"]) for value in history[-15:]]
        rsi = _rsi14(prices)
        if identifier == "rsi_rebound_call":
            return rsi <= 30 and prices[-1] > max(prices[-3:-1])
        return rsi >= 70 and prices[-1] < min(prices[-3:-1])
    if identifier == "recovery_call":
        return _recovery(history)
    prices = [_number(value["underlying_price"]) for value in history]
    prior = prices[-11:-1]
    mean = sum(prior, Decimal(0)) / len(prior)
    return prices[-1] > max(prior) and prices[-1] > mean if identifier == "breakout_call" else prices[-1] < min(prior) and prices[-1] < mean


def _liquid(quote, side, profile):
    bid, ask = _number(quote["bid"]), _number(quote["ask"])
    if bid <= 0 or ask <= 0 or bid > ask or ask-bid > (bid+ask)/2*Decimal("0.25"):
        return False
    size = quote["ask_size" if side == "BUY" else "bid_size"]
    return size is None and profile == "unknown_size_price_proxy" or type(size) is int and size >= 1


def _reject(quote, row, kind, clock, profile, disposal=None, row_context=None):
    # Trusted host-internal values shared by this already validated source row.
    # The uncached path remains available for the infrequent pending-entry check.
    if row_context is None:
        day, decision_day = _day_at(_clock(row["observed_at"])), _day_at(clock)
    else:
        day, decision_day, _ = row_context
    expiry = date.fromisoformat(quote["expiry"])
    spot = _number(row["underlying_price"]) if row_context is None else row_context[2]
    strike = _number(quote["strike"])
    bid, ask = _number(quote["bid"]), _number(quote["ask"])
    if quote["type"] != kind: return REJECTIONS[0]
    if not 7 <= (expiry-day).days <= 21: return REJECTIONS[1]
    if expiry.strftime("%Y-%m") != decision_day.strftime("%Y-%m"): return REJECTIONS[2]
    if not (spot <= strike <= spot*Decimal("1.05") if kind == "call" else spot*Decimal("0.95") <= strike <= spot): return REJECTIONS[3]
    if _debit(ask*100)+Decimal("1.30") > 100: return REJECTIONS[4]
    if disposal is None:
        deadline = _deadline(day, expiry)
    else:
        if expiry not in disposal: disposal[expiry] = _deadline(day, expiry)
        deadline = disposal[expiry]
    if deadline <= decision_day: return REJECTIONS[5]
    if bid <= 0 or ask <= 0 or bid > ask: return REJECTIONS[6]
    if ask-bid > (bid+ask)/2*Decimal("0.25"): return REJECTIONS[7]
    if not (quote["ask_size"] is None and profile == "unknown_size_price_proxy" or type(quote["ask_size"]) is int and quote["ask_size"] >= 1): return REJECTIONS[8]
    return None


def _rotation(identifier):
    if identifier in (*FOLLOWUP_SIGNALS, "followup_ensemble"):
        return FOLLOWUP_SIGNALS
    return LEGACY_SIGNALS if identifier in (*ORIGINAL_SIGNALS, "diversified_ensemble") else CONVENTIONAL_SIGNALS


def _initial(identifier, row, profile):
    return {"candidate_id": identifier, "liquidity_profile": profile, "month": _day_at(_clock(row["available_at"])).strftime("%Y-%m"),
        "cash": "500", "contributions": "500", "monthly_start_equity": "500", "monthly_committed": "0",
        "fees": "0", "entries": 0, "closed_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
        "position": None, "pending": None, "hold": None, "next_priority": 0,
        "processed_rows": 0, "last_source_date": None, "last_available_at": None,
        "milestones": {"2x": 0, "5x": 0, "10x": 0}, "realized_pnl": "0",
        "diagnostics": {"warmup_rows": 0, "flat_rows": 0, "busy_rows": 0,
            "cancellations": {}, "signals": {key: 0 for key in _rotation(identifier)},
            "inspected_quotes": 0, "eligible_quotes": 0, "budget_blocked": 0,
            "rejections": {key: 0 for key in REJECTIONS}}}


def _equity(state):
    value = _number(state["cash"])
    if state["position"]:
        value += max(Decimal(0), _credit(_number(state["position"]["marked_bid"])*100)-Decimal("0.65"))
    return value


def _hold(state, code, message, row, events):
    state["hold"] = {"code": code, "message": message, "source_date": _day_at(_clock(row["observed_at"])).isoformat(),
        "available_at": row["available_at"]}
    events.append({"type": "held", **state["hold"]})


def _cancel(state, reason, events):
    state["diagnostics"]["cancellations"][reason] = state["diagnostics"]["cancellations"].get(reason, 0)+1
    events.append({"type": "cancelled", "reason": reason, "decision": deepcopy(state["pending"])})
    state["pending"] = None


def _pending(action, quote, row, identifier, reasons):
    return {"action": action, "strategy_id": identifier, "contract_id": quote["contract_id"], "type": quote["type"],
        "expiry": quote["expiry"], "strike": quote["strike"], "decided_at": row["available_at"],
        "source_date": _day_at(_clock(row["observed_at"])).isoformat(), "reasons": list(reasons)}


def _sell(state, quote, row, reason, events):
    position = state["position"]
    gross = _credit(_number(quote["bid"])*100)
    net = gross-Decimal("0.65")
    entry = _number(position["premium"])+Decimal("0.65")
    pnl, ratio = net-entry, net/entry
    state["cash"] = _text(_number(state["cash"])+net)
    state["fees"] = _text(_number(state["fees"])+Decimal("0.65"))
    state["closed_trades"] += 1
    state["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven"] += 1
    state["realized_pnl"] = _text(_number(state["realized_pnl"], signed=True)+pnl)
    for multiple in (2, 5, 10):
        if ratio >= multiple: state["milestones"][str(multiple)+"x"] += 1
    events.append({"type": "fill", "action": "SELL", "strategy_id": position["strategy_id"], "option_type": quote["type"],
        "contract_id": quote["contract_id"], "source_date": _day_at(_clock(row["observed_at"])).isoformat(),
        "observed_at": row["observed_at"], "available_at": row["available_at"], "price": quote["bid"],
        "gross_credit": _text(gross), "fee": "0.65", "net_credit": _text(net), "trade_pnl": _text(pnl),
        "net_payout_multiple": _text(ratio), "reasons": list(reason), "simulated": True})
    state["position"], state["pending"] = None, None


def _step(state, row, history, cached, signals, events, curve):
    observed, clock = _clock(row["observed_at"]), _clock(row["available_at"])
    day, decision_day = _day_at(observed), _day_at(clock)
    month = decision_day.strftime("%Y-%m")
    rotation = _rotation(state["candidate_id"])
    if month < state["month"]:
        _hold(state, "backwards_month", "Availability moved before the funded research month.", row, events); return
    if month != state["month"]:
        if state["position"]:
            _hold(state, "unresolved_month_boundary", "Unresolved exposure prevents later funding; no invented exit.", row, events); return
        if state["pending"]: _cancel(state, "month_boundary", events)
        state.update(month=month, cash=_text(_number(state["cash"])+500), contributions=_text(_number(state["contributions"])+500), monthly_committed="0")
        state["monthly_start_equity"] = state["cash"]
        events.append({"type": "contribution", "month": month, "amount": "500", "simulated": True})
    position = state["position"]
    if position:
        deadline = date.fromisoformat(position["forced_exit_session"])
        if day > deadline or decision_day > deadline:
            _hold(state, "missed_disposal_deadline", "No supported sale before mandatory disposal; no backfill or settlement.", row, events); return
        quote = cached["by_id"].get(position["contract_id"])
        if quote is None or not _liquid(quote, "SELL", state["liquidity_profile"]):
            _hold(state, "missing_or_unexecutable_position_quote", "An exact qualified held-contract bid/size is absent; retain the previous dated mark.", row, events); return
        position["marked_bid"] = quote["bid"]
        pending = state["pending"]
        due = day == deadline
        decision = _clock(position["decision_at"]) if due else _clock(pending["decided_at"]) if pending else None
        if (due or pending) and observed > decision+timedelta(seconds=60):
            _sell(state, quote, row, ["precommitted_disposal_deadline"] if due else pending["reasons"], events)
        if state["position"] and not state["pending"]:
            bid, entry = _number(quote["bid"]), _number(position["entry_ask"])
            reason = ["bid_reached_5x_entry_ask"] if bid >= entry*5 else ["bid_at_or_below_half_entry_ask"] if bid <= entry/2 else []
            if reason:
                state["pending"] = _pending("SELL", quote, row, position["strategy_id"], reason)
                events.append({**deepcopy(state["pending"]), "type": "decision", "option_type": quote["type"]})
    elif state["pending"]:
        pending = state["pending"]
        quote = cached["by_id"].get(pending["contract_id"])
        following = _sessions(date.fromisoformat(pending["source_date"])+timedelta(days=1), day)
        if len(following) != 1 or observed <= _clock(pending["decided_at"])+timedelta(seconds=60):
            _cancel(state, "late_or_unavailable_entry", events)
        elif quote is None or _reject(quote, row, pending["type"], clock, state["liquidity_profile"]):
            _cancel(state, "entry_quote_no_longer_eligible", events)
        else:
            premium = _debit(_number(quote["ask"])*100)
            reserve = premium+Decimal("1.30")
            if reserve+_number(state["monthly_committed"]) > 500 or reserve > _number(state["cash"]):
                _cancel(state, "shared_monthly_or_cash_budget", events)
            else:
                state["cash"] = _text(_number(state["cash"])-premium-Decimal("0.65"))
                state["monthly_committed"] = _text(_number(state["monthly_committed"])+reserve)
                state["fees"] = _text(_number(state["fees"])+Decimal("0.65")); state["entries"] += 1
                identifier = pending["strategy_id"]
                state["next_priority"] = (rotation.index(identifier)+1) % len(rotation)
                state["position"] = {"strategy_id": identifier, "contract_id": quote["contract_id"], "type": quote["type"],
                    "expiry": quote["expiry"], "strike": quote["strike"], "quantity": 1, "multiplier": 100,
                    "entry_at": row["observed_at"], "decision_at": pending["decided_at"], "entry_ask": quote["ask"],
                    "premium": _text(premium), "entry_fee": "0.65", "reserved_exit_fee": "0.65", "budget_debit": _text(reserve),
                    "marked_bid": quote["bid"], "forced_exit_session": _deadline(day, date.fromisoformat(quote["expiry"])).isoformat()}
                events.append({"type": "fill", "action": "BUY", "strategy_id": identifier, "option_type": quote["type"], "contract_id": quote["contract_id"],
                    "source_date": day.isoformat(), "observed_at": row["observed_at"], "available_at": row["available_at"],
                    "decided_at": pending["decided_at"], "price": quote["ask"], "premium": _text(premium), "fee": "0.65",
                    "reserved_exit_fee": "0.65", "budget_committed": _text(reserve), "simulated": True})
                state["pending"] = None
    enabled = CANDIDATES[state["candidate_id"]]["signals"]
    diagnostics = state["diagnostics"]
    identifier = state["candidate_id"]
    required_closes = FOLLOWUP_WARMUP.get(identifier, 23 if identifier == "followup_ensemble"
                                         else 15 if identifier in ORIGINAL_SIGNALS else 14)
    if len(history) < required_closes: diagnostics["warmup_rows"] += 1
    elif state["position"] is None and state["pending"] is None:
        diagnostics["flat_rows"] += 1
        order = rotation[state["next_priority"]:]+rotation[:state["next_priority"]]
        for identifier in order:
            if identifier not in enabled or not signals[identifier]: continue
            diagnostics["signals"][identifier] += 1
            kind = SIGNAL_KINDS[identifier]
            candidates, reasons = cached[state["liquidity_profile"]][kind]
            diagnostics["inspected_quotes"] += len(row["quotes"])
            diagnostics["eligible_quotes"] += len(candidates)
            for reason, count in reasons.items(): diagnostics["rejections"][reason] += count
            if not candidates: continue
            quote = candidates[0]
            reserve = _debit(_number(quote["ask"])*100)+Decimal("1.30")
            if reserve+_number(state["monthly_committed"]) > 500 or reserve > _number(state["cash"]):
                diagnostics["budget_blocked"] += 1; continue
            state["pending"] = _pending("BUY", quote, row, identifier, [identifier+"_registered_signal"])
            events.append({**deepcopy(state["pending"]), "type": "decision", "option_type": quote["type"]}); break
    else: diagnostics["busy_rows"] += 1
    equity = _equity(state)
    previous = curve[-1] if curve and curve[-1]["month"] == month else None
    prior = _number(previous["equity"], signed=True) if previous else _number(state["monthly_start_equity"])
    curve.append({"source_date": day.isoformat(), "available_at": row["available_at"], "month": month,
        "cash": state["cash"], "equity": _text(equity), "contributions": state["contributions"],
        "trading_pnl": _text(equity-_number(state["contributions"])), "daily_pnl": _text(equity-prior),
        "monthly_pnl": _text(equity-_number(state["monthly_start_equity"])), "monthly_committed": state["monthly_committed"], "fees": state["fees"]})
    if (not 0 <= _number(state["monthly_committed"]) <= 500 or _number(state["cash"]) < 0
            or state["entries"]-state["closed_trades"] != int(state["position"] is not None)
            or equity < _number(state["monthly_start_equity"])-500):
        raise OptionsRecoveryError("Campaign risk/cash/one-position invariant failed.")
    state["processed_rows"] += 1
    state["last_source_date"], state["last_available_at"] = day.isoformat(), row["available_at"]


def _report(state, events, curve, coverage):
    peak, drop = Decimal(0), Decimal(0)
    for row in curve:
        pnl = _number(row["trading_pnl"], signed=True)
        peak = max(peak, pnl); drop = max(drop, peak-pnl)
    complete = not state["hold"] and state["processed_rows"] == coverage["source_rows"]
    flat = state["position"] is None and state["pending"] is None
    closed = state["closed_trades"]
    months = {}
    for row in curve: months[row["month"]] = deepcopy(row)
    metrics = {"trading_pnl": _text(_equity(state)-_number(state["contributions"])), "realized_pnl": state["realized_pnl"],
        "ending_equity": _text(_equity(state)), "contributions": state["contributions"], "fees": state["fees"],
        **{key: state[key] for key in ("entries", "closed_trades", "wins", "losses", "breakeven")},
        "observed_win_rate_pct": None if not closed else _text(Decimal(state["wins"])*100/closed),
        "max_trading_pnl_drop": _text(drop), "max_monthly_loss": _text(max((max(Decimal(0), -_number(row["monthly_pnl"], signed=True)) for row in curve), default=Decimal(0))),
        "payout_milestones": deepcopy(state["milestones"]), "sample_sufficient_for_edge": False,
        "monthly_budget_used": state["monthly_committed"], "monthly_budget_remaining": _text(500-_number(state["monthly_committed"]))}
    gate = "held_incomplete_evidence" if state["hold"] else "open_at_input_end" if not flat else "cash_control" if state["candidate_id"] == "cash" else "no_completed_trades" if not closed else "exploratory_only"
    if coverage["evidence_kind"] == "synthetic_fixture": gate = "synthetic_fixture_only"
    return {"candidate_id": state["candidate_id"], "liquidity_profile": state["liquidity_profile"], "research_gate": gate,
        "coverage": {**coverage, "processed_rows": state["processed_rows"], "last_processed_date": state["last_source_date"],
            "all_input_processed": complete, "finished_flat": flat, "complete": complete and flat},
        "metrics": metrics, "diagnostics": deepcopy(state["diagnostics"]), "hold": deepcopy(state["hold"]),
        "state": deepcopy(state), "events": events, "curve": curve, "months": list(months.values()),
        "research_only": True, "execution_qualified": False, "deployable": False}


@_precision
def _campaign_run(dataset_or_iterable, *, symbol, candidates, profiles, checkpoint):
    """Validate once per chunk and fan out one checked stream to fixed ledgers.

    ``checkpoint`` is a trusted caller callback, never manifest code. It runs
    before every row and before return so the host can revoke publication.
    The entire iterator is consumed even after a candidate enters a hold.
    """
    if symbol not in RESEARCH_SYMBOLS:
        raise OptionsRecoveryError("Choose a fixed QQQ/KO research symbol; this cannot run the F paper account.")
    selected = _candidates(candidates)
    followup_enabled = any(key in (*FOLLOWUP_SIGNALS, "followup_ensemble") for key in selected)
    history_limit = 65 if followup_enabled else 15
    for profile in profiles: profile_rule(profile)
    if len(selected)*len(profiles) > MAX_CANDIDATES: raise OptionsRecoveryError("Campaign exceeds16 total counterfactual ledgers across profiles.")
    if checkpoint is not None and not callable(checkpoint): raise OptionsRecoveryError("checkpoint must be a trusted host callback.")
    datasets = [dataset_or_iterable] if type(dataset_or_iterable) is dict else dataset_or_iterable
    states = {}
    events = {profile: {key: [] for key in selected} for profile in profiles}
    curves = {profile: {key: [] for key in selected} for profile in profiles}
    history, qualities = [], []
    previous, previous_available, basis, first, last, source_rows, source_quotes = None, None, None, None, None, 0, 0
    prefix = hashlib.sha256(b"[")
    for chunk_count, dataset in enumerate(datasets, 1):
        if chunk_count > MAX_CHUNKS: raise OptionsRecoveryError("Campaign exceeds the inherited96-chunk archive limit.")
        rows, quality = _rows(dataset, symbol, allow_research_ko=True)
        current_basis = (dataset["evidence_kind"], dataset["source"]["quote_time_basis"], dataset["source"]["provider"])
        if basis is not None and current_basis != basis: raise OptionsRecoveryError("Campaign evidence/provider/clock basis changed across chunks.")
        basis = current_basis
        qualities.append({**quality, "dataset_sha256": hashlib.sha256(_canonical(dataset)).hexdigest(), "source_sha256": dataset["source"]["sha256"]})
        for row in rows:
            if checkpoint is not None: checkpoint()
            day, available = _day_at(_clock(row["observed_at"])), _clock(row["available_at"])
            if previous is not None and (day <= previous or available <= previous_available): raise OptionsRecoveryError("Campaign rows overlap or move backwards across chunks.")
            source_rows += 1; source_quotes += len(row["quotes"])
            if source_rows > MAX_ROWS: raise OptionsRecoveryError("Campaign exceeds10000 inherited daily observations.")
            first, last = first or day.isoformat(), day.isoformat()
            if not states: states = {profile: {key: _initial(key, row, profile) for key in selected} for profile in profiles}
            gap = previous is not None and len(_sessions(previous+timedelta(days=1), day)) != 1
            history = (history+[{"underlying_price": row["underlying_price"], "observed_at": row["observed_at"]}])[-history_limit:]
            signals = {key: signal(key, history[-15:]) for key in LEGACY_SIGNALS}
            if followup_enabled:
                signals.update({key: signal(key, history) for key in FOLLOWUP_SIGNALS})
            cached = {"by_id": {quote["contract_id"]: quote for quote in row["quotes"]}}
            disposal = {}  # Same source day/expiry across all strikes/sides/profiles.
            row_context = (day, _day_at(available), _number(row["underlying_price"]))
            for profile in profiles:
                cached[profile] = {}
                for kind in ("call", "put"):
                    eligible, counts = [], dict.fromkeys(REJECTIONS, 0)
                    for quote in row["quotes"]:
                        reason = _reject(quote, row, kind, available, profile, disposal, row_context)
                        if reason is None: eligible.append(quote)
                        else: counts[reason] += 1
                    spot = row_context[2]
                    eligible.sort(key=lambda quote: (abs((date.fromisoformat(quote["expiry"])-day).days-14), abs(_number(quote["strike"])-spot), quote["contract_id"]))
                    cached[profile][kind] = eligible, counts
            if source_rows > 1: prefix.update(b",")
            prefix.update(_canonical(row))
            for profile, ledgers in states.items():
                for key, state in ledgers.items():
                    if state["hold"]: continue
                    if gap: _hold(state, "historical_session_gap", "A selected exchange session is absent, including chunk boundaries; no repair or fill.", row, events[profile][key])
                    else: _step(state, row, history, cached, signals, events[profile][key], curves[profile][key])
            previous, previous_available = day, available
    if not states: raise OptionsRecoveryError("No supported historical option observations supplied.")
    prefix.update(b"]")
    coverage = {"symbol": symbol, "source_rows": source_rows, "source_quotes": source_quotes, "chunks": len(qualities),
        "first_date": first, "last_source_date": last, "quote_time_basis": basis[1], "evidence_kind": basis[0],
        "source_prefix_sha256": prefix.hexdigest(), "source_stream_exhausted": True}
    outputs = {profile: _profile_report(profile, selected,
        {key: _report(state, events[profile][key], curves[profile][key], coverage) for key, state in ledgers.items()},
        coverage, qualities) for profile, ledgers in states.items()}
    result = {"schema": 1, "kind": "finite_historical_options_campaign_profiles", "profiles": outputs,
        "coverage": coverage, "registry_sha256": RULE_SHA256, "research_only": True,
        "execution_qualified": False, "deployable": False, "model_calls": 0, "network_calls": 0,
        "comparison_scope": "Different liquidity profiles are separate assumptions, never a pooled ranking, combined capital or permission to trade."}
    if len(_canonical(result)) > MAX_OUTPUT_BYTES: raise OptionsRecoveryError("Campaign output exceeds its fixed128MiB bound.")
    if checkpoint is not None: checkpoint()
    return result


def _profile_report(profile, selected, reports, coverage, qualities):
    complete = all(value["coverage"]["complete"] for value in reports.values())
    traded = [key for key, value in reports.items() if key != "cash" and value["metrics"]["closed_trades"]]
    rankable = coverage["evidence_kind"] == "historical_quotes" and complete and bool(traded)
    order = sorted(traded, key=lambda key: (-_number(reports[key]["metrics"]["trading_pnl"], signed=True), _number(reports[key]["metrics"]["max_trading_pnl_drop"]), CANDIDATE_IDS.index(key))) if rankable else []
    result = {"schema": 1, "kind": "finite_historical_options_campaign", "rule": profile_rule(profile), "rule_sha256": profile_rule_sha256(profile),
        "registry_sha256": RULE_SHA256, "liquidity_profile": profile, "liquidity_verified": False,
        "symbol": coverage["symbol"], "candidate_ids": selected, "candidate_results": reports, "coverage": coverage, "data_quality": qualities,
        "comparison": {"ranked_ids": order, "ranking_scope": "Exploratory same-window net P/L ordering only; not best overall, out-of-sample proof or deployment selection.",
            "reason": "common_complete_flat_historical_window" if rankable else "synthetic_incomplete_open_or_zero_trade_evidence",
            "excluded_zero_trade_ids": [key for key in selected if key != "cash" and not reports[key]["metrics"]["closed_trades"]],
            "all_ledgers_complete": complete, "cash_control_pnl": reports.get("cash", {}).get("metrics", {}).get("trading_pnl"),
            "holdout_qualified": False, "automatic_adoption": False},
        "limits": {"candidates": MAX_CANDIDATES, "rows": MAX_ROWS, "chunks": MAX_CHUNKS, "output_bytes": MAX_OUTPUT_BYTES},
        "research_only": True, "execution_qualified": False, "deployable": False, "model_calls": 0, "network_calls": 0,
        "limitations": ["QQQ/KO research sources, clocks and contract terms differ from the owner's F account. KO stays below100 but its normalized100-share terms/EOD clock may remain assumptions. No deployment or funding follows.",
            "Every candidate is a separate counterfactual500 budget. Each ensemble shares one500 allowance across only its own fixed signals; standalone or ensemble returns/capital are never summed.",
            "Unknown quote event clocks remain assumed-EOD price proxies; supplied sizes/provenance are not independently authenticated.",
            "Contributions are excluded from trading P/L. P/L peak declines are not the500 monthly gross commitment cap.",
            "All failed/incomplete/zero-trade candidates are retained. No absent exit, clock, size, settlement or price is supplied; unknown-size proxy explicitly permits missing sizes as unknown, never executable liquidity.",
            "These fixed signal heuristics are not published profitable-option findings. Repeated trials, selective reporting and revisited holdouts can overfit."]}
    return result


def campaign(dataset_or_iterable, *, symbol="QQQ", candidates=None,
             liquidity_profile="strict_sizes", checkpoint=None):
    """Run one fixed profile, defaulting to actual-side-size requirements."""
    profile_rule(liquidity_profile)
    return _campaign_run(dataset_or_iterable, symbol=symbol, candidates=candidates,
        profiles=(liquidity_profile,), checkpoint=checkpoint)["profiles"][liquidity_profile]


def campaign_profiles(dataset_or_iterable, *, symbol="QQQ", candidates=None, checkpoint=None):
    """Fan out both explicitly distinct research profiles from one source read."""
    return _campaign_run(dataset_or_iterable, symbol=symbol, candidates=candidates,
        profiles=LIQUIDITY_PROFILES, checkpoint=checkpoint)
