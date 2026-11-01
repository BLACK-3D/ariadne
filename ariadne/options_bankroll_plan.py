"""A versioned, offline capital proposal; no account or execution authority.

The $25,000 amount is a proposed TOTAL initial bankroll, not recurring funding.
This module never migrates a ledger, changes a live limit or ranks old P&L by
rescaling it. All sizing inputs are hypothetical caller inputs, not quotes.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import hashlib
import json
import re


POLICY = {
    "schema": 1,
    "id": "total-bankroll-stress-put-proposal-v1",
    "status": "research_proposal_not_activated",
    "currency": "USD",
    "funding_basis": "one_time_total_bankroll",
    "initial_bankroll": "25000.00",
    "additional_contributions": "0.00",
    "monthly_shared_gross_commitment_ceiling": "25000.00",
    "entry_all_in_ceiling": "1000.00",
    "entry_equity_fraction": "0.04",
    "contract_multiplier": 100,
    "fee_per_contract_per_side": "0.65",
    "simultaneous_positions": 1,
    "instrument": "fully_paid_standard_long_put_or_comparison_long_call",
    "funding": "Exactly 25000 initially, including capital already credited in any future migration. No monthly deposits, leverage, extra accounts or automatic topups.",
    "entry_risk": "At most min(1000, 4% of current net-liquidation equity, cash, unused shared monthly gross commitment). Premium plus entry and reserved exit fees; integer quantity limited by actual supplied ask size.",
    "month": "The 25000 gross ceiling is shared by every symbol/strategy. Only a verified-flat, unheld new month may reset gross commitment; it adds no cash. No within-month recycling of sales or profits into this ceiling.",
    "primary": "stress_breakdown_put",
    "comparators": ["raw_breakdown_put", "growth_recovery_call", "cash"],
    "regime": "Use existing causal QQQ/SPY context: ready stress_unwind only for primary puts; ready recovery_growth only for filtered recovery calls; unavailable/stale/neutral blocks new gated entries, never exits.",
    "signal": "At least 14 completed stock closes; latest close below each of preceding 10 closes and their mean. Recovery-call comparator uses the existing frozen 14-close recovery signal. Never infer an AI-bubble forecast.",
    "universe": "US stocks/ETFs only; saved raw underlying strictly below 100 at decision and fill; positive real supplied executable-side sizes. No new symbol is enabled by this plan.",
    "contract": "Existing 7-21 calendar DTE, current decision NY month expiry, 0-5% OTM, positive uncrossed bid/ask, spread<=25% midpoint; nearest 14 DTE, nearest spot, contractID. No shorts/spreads/exercise/rolls.",
    "fills": "Decision from a completed available observation; fill only at next eligible later-session ask, rerun all gates and sizing. No retrospective adoption or stock-price substitutes. Quantity must fit supplied ask size; no assumed repeat liquidity.",
    "exits": "Existing bid>=5x entry ask or bid<=0.5x entry ask creates a delayed sell decision; forced deadline remains earliest tenth following regular session, preceding expiry session, penultimate entry-month session. Actual later bid and supplied bid size covering entire quantity required; otherwise hold. A 50% trigger is not a guaranteed 50% realized loss.",
    "evaluation": "Preregister the fixed rule and exact hashes before new outcomes. Re-run each candidate on the same sources, dates, clocks, costs and total capital. Preserve held/incomplete/zero-trade cases; no linear rescaling, annualizing tiny samples, portfolio sums or automatic winner adoption.",
    "validation": "Inspect completed net P&L, full path drawdown, trade count, coverage, source holds and costs. Require positive matched holdout evidence and prospective paper observations before proposing promotion; no proven winner or profit target.",
    "scope": "Proposal/sizing only; current owner ledger, legacy500/100 rules and funding remain unchanged. Funding basis must be confirmed and a separately reviewed ledger version implemented before activation.",
    "research_only": True,
    "deployable": False,
    "automatic_adoption": False,
    "model_calls": 0,
    "network_calls": 0,
    "real_orders": False,
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


POLICY_SHA256 = hashlib.sha256(canonical(POLICY)).hexdigest()
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,8})?\Z")


def _verify():
    if hashlib.sha256(canonical(POLICY)).hexdigest() != POLICY_SHA256:
        raise ValueError("The registered capital proposal changed.")


def _decimal(value, pattern, name):
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(name + " requires a bounded nonnegative plain decimal string.")
    return Decimal(value)


def _cash(value, name):
    return _decimal(value, _MONEY, name)


def _money(value):
    return format(value.quantize(Decimal("0.01")), ".2f")


def plan():
    """Return a detached proposed rule, never a current-account description."""
    _verify()
    return {"policy": deepcopy(POLICY), "policy_sha256": POLICY_SHA256,
            "confirmation_required": "Confirm 25000 total bankroll versus recurring monthly loss allowance; then implement/review a versioned ledger transition. This planner performs neither.",
            "sizing_is_hypothetical": True,
            "risk": "The entire 25000 bankroll can be lost over time. A larger limit does not establish positive expectancy; option premium and fees may be lost in full.",
            "recurring_cost": "No model/API calls, network, dependencies, subscription or background work in this planner; machine time/energy have not been measured."}


def size(*, ask, ask_size, cash="25000.00", equity="25000.00", monthly_committed="0.00"):
    """Size a hypothetical long option. This is NOT an actionable order.

    No caller-supplied state is persisted or trusted as the owner's account.
    Full production gates (root, terms, clocks, regime, bid/ask and deadlines)
    are intentionally not replaced by an affordability calculation.
    """
    _verify()
    with localcontext(Context(prec=40)):
        price = _decimal(ask, _PRICE, "Ask")
        if price <= 0:
            raise ValueError("Ask must be positive.")
        if type(ask_size) is not int or not 0 <= ask_size <= 1_000_000_000:
            raise ValueError("Ask size must be an actual supplied nonnegative integer; null/bool are rejected.")
        available, nav = _cash(cash, "Cash"), _cash(equity, "Equity")
        committed = _cash(monthly_committed, "Monthly commitment")
        if available > nav:
            raise ValueError("A flat long-only sizing proposal cannot have cash above net equity.")
        monthly_max = Decimal(POLICY["monthly_shared_gross_commitment_ceiling"])
        if committed > monthly_max:
            raise ValueError("The shared monthly commitment exceeds this proposed ceiling.")
        ceiling = min(Decimal(POLICY["entry_all_in_ceiling"]),
                      nav * Decimal(POLICY["entry_equity_fraction"]),
                      available, monthly_max - committed).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        premium_unit = (price * 100).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        fee = Decimal(POLICY["fee_per_contract_per_side"])
        reserve_unit = premium_unit + fee * 2
        quantity = min(int(ceiling // reserve_unit), ask_size)
        reserve, premium = reserve_unit * quantity, premium_unit * quantity
        entry_fee, exit_fee = fee * quantity, fee * quantity
        return {
            "policy_sha256": POLICY_SHA256,
            "status": "hypothetical_size" if quantity else "no_affordable_supplied_quantity",
            "quantity": quantity,
            "entry_risk_ceiling": _money(ceiling),
            "premium": _money(premium),
            "entry_fee": _money(entry_fee),
            "reserved_exit_fee": _money(exit_fee),
            "all_in_commitment": _money(reserve),
            "cash_after_entry_and_exit_reserve": _money(available - reserve),
            "shared_monthly_commitment_after": _money(committed + reserve),
            "hypothetical_inputs": {"ask": ask, "ask_size": ask_size, "cash": cash,
                                    "equity": equity, "monthly_committed": monthly_committed},
            "assumes_verified_flat_unheld_account": True,
            "requires_full_production_gates": True,
            "executable_quote_or_order": False,
            "account_mutation": False,
            "research_only": True,
        }


def losing_streaks():
    """Deterministic stress illustration, not a probability or backtest.

    Fractional idealized losses ignore indivisible contracts; they demonstrate
    compounded exposure only. No stop fill or positive-return assumption.
    """
    _verify()
    rows = []
    with localcontext(Context(prec=40)):
        initial = Decimal(POLICY["initial_bankroll"])
        fraction = Decimal(POLICY["entry_equity_fraction"])
        for count in (5, 10, 20):
            balance = initial * (1 - fraction) ** count
            rows.append({"consecutive_full_allocation_losses": count,
                         "idealized_remaining_bankroll": _money(balance),
                         "idealized_loss": _money(initial - balance),
                         "probability_estimated": False})
    return rows
