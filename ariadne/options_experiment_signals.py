"""Fixed causal closing-price hypotheses; no execution or account authority.

The eight new definitions are the unchanged preregistered development-pattern
diagnostics. Oracle exits and historical profits do not enter these functions.
"""
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json

CANDIDATES = ("cash", "stress_breakdown_put", "raw_breakdown_put", "growth_recovery_call",
    "three_day_breakdown_put", "ma5_reclaim_call", "five_day_momentum_call",
    "uptrend_pullback_call", "failed_bounce_put", "tight_range_breakout_call",
    "oversold_rebound_call", "volatile_downside_put")
PATTERNS = {
 "three_day_breakdown_put": {"kind": "put", "rule": "current<each preceding3 closes AND current<mean latest10 closes"},
 "ma5_reclaim_call": {"kind": "call", "rule": "previous<=mean preceding5 closes AND current>mean latest5 closes AND current>previous"},
 "five_day_momentum_call": {"kind": "call", "rule": "current/close5sessionsago-1>=3%"},
 "uptrend_pullback_call": {"kind": "call", "rule": "current>latestMA10>previousMA10 AND -3%<=current/close3sessionsago-1<=-1%"},
 "failed_bounce_put": {"kind": "put", "rule": "current<latestMA10 AND previous/close3sessionsago-1>=1% AND current<previous"},
 "tight_range_breakout_call": {"kind": "call", "rule": "(max preceding5 closes-min preceding5 closes)/previous<=2% AND current>max preceding5 closes"},
 "oversold_rebound_call": {"kind": "call", "rule": "previous/close6sessionsago-1<=-3% AND current/previous-1>=0.5%"},
 "volatile_downside_put": {"kind": "put", "rule": "(max preceding10 closes-min preceding10 closes)/previous>=6% AND current/previous-1<=-1% AND current<latestMA5"},
}
RULE = {"schema": 1, "id": "fixed-causal-options-experiment-signals-v1",
    "candidates": list(CANDIDATES), "new_patterns": PATTERNS,
    "preregistration": {"private_script_sha256": "b20967afdc85a3fd61212a938d77c8bcf38220df076292668b86d06e4540e70f",
        "intent_sha256": "217ad791fa841554bc7bcc29dba52a357aa93b9a44451fd02b826fb7a55b8db6",
        "definitions_changed_using_pnl": False},
    "warmup": "All noncash definitions require at least14 completed positive finite Decimal closes. New patterns use exactly latest14, matching original diagnostic. No future prices, quote exits, profits or labels are inputs.",
    "legacy": {
        "raw_breakdown_put": "current below each preceding10 closes and their mean; latest15 closes reproduce frozen research history truncation",
        "stress_breakdown_put": "Same raw breakdown, with separate required stress_unwind regime label",
        "growth_recovery_call": "current strictly above previous2 closes; at least3% decline versus preceding10-close high in current/prior4 observations with full10-close lookback; latest15 closes, matching frozen research history truncation; separate recovery_growth label",
        "source_file_sha256": {"ariadne/options_campaign.py": "96cbacbca6137a9fbef9520b5fb23992f81c09e25780e60336fb39b68a542cfd",
            "ariadne/options_recovery.py": "4eaabf98774d0a9d4db96a6c438f3a8268c479442763d6afe16aa7ab673f1bf3"}},
    "regimes": "Only stress_breakdown_put requires stress_unwind; only growth_recovery_call requires recovery_growth. Eight new hypotheses and raw comparator are ungated. This module returns the required label; it never creates or verifies a regime.",
    "arithmetic": "Explicit isolated Decimal Context60 HALF_EVEN for original patterns, Context40 HALF_EVEN for legacy parity. No float conversion. Input list bounded10000 values; exact Decimal values positive<=1000000, at most50 digits and exponent-40..6.",
    "cash": "No option kind, no required regime and condition always false.",
    "scope": "Signal predicates only. No profitability claim, ranking, execution, financial-rule edit, paper promotion, account or source operations.",
    "research_only": True, "deployable": False, "execution_qualified": False,
    "network_calls": 0, "model_calls": 0, "owner_operations": 0}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class ExperimentSignalError(ValueError):
    pass


def _verify():
    if (hashlib.sha256(canonical(RULE)).hexdigest() != RULE_SHA256
        or list(CANDIDATES) != RULE["candidates"] or PATTERNS != RULE["new_patterns"]):
        raise ExperimentSignalError("The fixed experimental signal definitions changed.")


def _identifier(identifier):
    _verify()
    if type(identifier) is not str or identifier not in CANDIDATES:
        raise ExperimentSignalError("Choose one explicit fixed experimental signal identifier.")
    return identifier


def kind(identifier):
    identifier = _identifier(identifier)
    if identifier == "cash": return None
    if identifier in PATTERNS: return PATTERNS[identifier]["kind"]
    return "call" if identifier == "growth_recovery_call" else "put"


def regime_label(identifier):
    identifier = _identifier(identifier)
    return {"stress_breakdown_put": "stress_unwind", "growth_recovery_call": "recovery_growth"}.get(identifier)


def _history(values):
    if type(values) is not list or len(values) > 10000:
        raise ExperimentSignalError("Use a bounded list of completed Decimal closes.")
    for value in values:
        if (type(value) is not Decimal or not value.is_finite()
            or not 0 < value <= Decimal("1000000") or len(value.as_tuple().digits) > 50
            or not -40 <= value.as_tuple().exponent <= 6):
            raise ExperimentSignalError("Every close must be an exact bounded positive finite Decimal; no floats/bools.")


def condition(identifier, history_closes):
    """Evaluate only the supplied completed prefix, without mutation/state."""
    identifier = _identifier(identifier)
    _history(history_closes)
    if identifier == "cash" or len(history_closes) < 14: return False
    if identifier not in PATTERNS:
        with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
            p = history_closes[-15:]
            if identifier == "growth_recovery_call":
                if p[-1] <= max(p[-3:-1]): return False
                return any(p[index] <= max(p[index-10:index])*Decimal(".97")
                    for index in range(max(10, len(p)-5), len(p)))
            prior = p[-11:-1]
            return p[-1] < min(prior) and p[-1] < sum(prior, Decimal(0))/len(prior)
    with localcontext(Context(prec=60, rounding=ROUND_HALF_EVEN)):
        p = history_closes[-14:]
        current, previous = p[-1], p[-2]
        ma5, prior5 = sum(p[-5:])/5, sum(p[-6:-1])/5
        ma10, prior10 = sum(p[-10:])/10, sum(p[-11:-1])/10
        if identifier == "three_day_breakdown_put":
            return current < min(p[-4:-1]) and current < ma10
        if identifier == "ma5_reclaim_call":
            return previous <= prior5 and current > ma5 and current > previous
        if identifier == "five_day_momentum_call":
            return current/p[-6]-1 >= Decimal(".03")
        if identifier == "uptrend_pullback_call":
            return current > ma10 > prior10 and Decimal("-.03") <= current/p[-4]-1 <= Decimal("-.01")
        if identifier == "failed_bounce_put":
            return current < ma10 and previous/p[-4]-1 >= Decimal(".01") and current < previous
        if identifier == "tight_range_breakout_call":
            prior = p[-6:-1]
            return (max(prior)-min(prior))/previous <= Decimal(".02") and current > max(prior)
        if identifier == "oversold_rebound_call":
            return previous/p[-7]-1 <= Decimal("-.03") and current/previous-1 >= Decimal(".005")
        prior = p[-11:-1]
        return (max(prior)-min(prior))/previous >= Decimal(".06") and current/previous-1 <= Decimal("-.01") and current < ma5
