"""Pure descriptive stress-put comparisons over a caller-bound CSV.

This reads completed counterfactual observations; it never runs a strategy,
changes a paper account, fits parameters or authenticates the original feed.
"""
from __future__ import annotations

from collections import Counter
from decimal import localcontext
import hashlib
import json

from . import options_paired_review as base

COLUMNS = base.COLUMNS
REFERENCES = {"conventional_regime": "stress_breakdown_put", "volatility_regime": "stress_shock_put"}
MAX_ROWS, MAX_OUTPUT_BYTES = base.MAX_ROWS, base.MAX_OUTPUT_BYTES
StressReviewError = base.PairedReviewError
_WARMUPS = {
    "conventional_regime": {name: 0 if name == "cash" else 14 for name in base.PRESETS["conventional_regime"]},
    "volatility_regime": {name: (0 if name == "cash" else 41 if "compression" in name else
        65 if "pullback" in name else 23) for name in base.PRESETS["volatility_regime"]},
}
RULE = {"schema": 1, "id": "stress-put-paired-options-export-review-v1",
    "base_comparison_rule_sha256": base.RULE_SHA256, "references": REFERENCES,
    "presets": base.PRESETS, "stratify": base.STRATUM, "columns": COLUMNS,
    "source_row_warmups": _WARMUPS, "row_limit": MAX_ROWS, "output_byte_limit": MAX_OUTPUT_BYTES,
    "paired_delta": "Peer net P/L minus the fixed stress-filtered put net P/L within the exact same portfolio/window/profile/preset.",
    "eligibility": "Both legs must be complete, flat, context-complete and sufficiently warmed. Held, open, partial or context/warmup-censored observations remain counted without imputed P/L.",
    "zero_and_activity": "Complete no-trade zeros remain. Both-traded comparisons and the four paired trading-activity counts are separate.",
    "capital": "No summed capital/PnL, cross-window/preset ranking, win probability, fitting, activation or risk-limit change."}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _verify_rule():
    base._verify_rule()
    try:
        digest = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise StressReviewError("The fixed stress comparison rule changed.") from None
    base._require(digest == RULE_SHA256 and RULE["base_comparison_rule_sha256"] == base.RULE_SHA256
        and RULE["references"] == REFERENCES and RULE["columns"] == COLUMNS
        and RULE["presets"] == base.PRESETS and RULE["stratify"] == base.STRATUM
        and all(base._warmup(preset, name) == count for preset, values in _WARMUPS.items() for name, count in values.items()),
        "The fixed stress comparison or its validation dependency changed.")


def _family_result(value):
    return {"status_counts": {s: value["status"][s] for s in base.STATUSES},
        "context_incomplete": value["context_incomplete"],
        "source_rows_below_registered_warmup": value["warmup_insufficient"],
        "volatility_rotation_not_all_components_warmed": value["partial_component_warmup"],
        "reported_entries": value["entries"], "reported_closed_trades": value["closed"],
        "maximum_reported_monthly_commitment": base._text(value["maximum_commitment"]),
        "eligible_net_pnl": base._distribution(value["eligible"]),
        "eligible_traded_net_pnl": base._distribution(value["traded"]),
        "eligible_fees": base._distribution(value["fees"])}


def _new_pair():
    return {**base._pair(), "activity": Counter()}


def _record_pair(pair, reference, other):
    matrix = ("both_eligible" if reference["eligible"] and other["eligible"] else
        "reference_only_eligible" if reference["eligible"] else
        "competitor_only_eligible" if other["eligible"] else "neither_eligible")
    pair["matrix"][matrix] += 1
    for side, value in (("reference", reference), ("competitor", other)):
        for reason, excluded in (("held", bool(value["hold"])), ("open", not value["finished_flat"]),
            ("incomplete", not value["complete"]), ("context_incomplete", not value["context_complete"]),
            ("warmup_insufficient", value["warmup_insufficient"])):
            if excluded:
                pair["excluded"][side + "_" + reason] += 1
    if matrix != "both_eligible":
        return
    delta = other["pnl"] - reference["pnl"]
    pair["all"].append(delta)
    pair["reference_fees"].append(reference["fees"])
    pair["competitor_fees"].append(other["fees"])
    activity = ("both_traded" if reference["closed"] and other["closed"] else
        "reference_only_traded" if reference["closed"] else
        "competitor_only_traded" if other["closed"] else "neither_traded")
    pair["activity"][activity] += 1
    if activity == "both_traded":
        pair["traded"].append(delta)


def _pair_result(value, cohorts):
    return {"matched_cohorts": cohorts, "eligibility_matrix": {k: value["matrix"][k] for k in
            ("both_eligible", "reference_only_eligible", "competitor_only_eligible", "neither_eligible")},
        "exclusion_counts_may_overlap": dict(sorted(value["excluded"].items())),
        "eligible_pair_activity_counts": {k: value["activity"][k] for k in
            ("both_traded", "reference_only_traded", "competitor_only_traded", "neither_traded")},
        "all_eligible_pairs": base._pair_metrics(value["all"]),
        "both_traded_pairs": base._pair_metrics(value["traded"]),
        "matched_reference_fees": base._distribution(value["reference_fees"]),
        "matched_competitor_fees": base._distribution(value["competitor_fees"])}


def compare_rows(rows):
    """Review the two fixed stress-put references; no I/O or financial replay."""
    with localcontext(base._CONTEXT):
        return _compare(rows)


def _compare(rows):
    _verify_rule()
    strata, pending, completed = {}, {}, set()
    count = 0
    for raw in rows:
        count += 1
        base._require(count <= MAX_ROWS, "Stress comparison exceeds150000 rows; no truncation.")
        row = base._row(raw)
        key = tuple(row[k] for k in base.STRATUM)
        cohort = (key, row["portfolio"])
        base._require(cohort not in completed, "Duplicate completed portfolio/window/profile cohort.")
        group = pending.setdefault(cohort, {})
        base._require(row["candidate"] not in group, "Duplicate candidate in one matched cohort.")
        if group:
            prior = next(iter(group.values()))
            base._require(row["source_rows"] == prior["source_rows"] and row["case_id"] == prior["case_id"],
                "Cohort source rows/case identity disagree.")
        group[row["candidate"]] = row
        expected = base.PRESETS[row["preset"]]
        if len(group) != len(expected):
            continue
        base._require(set(group) == set(expected), "Cohort lacks the fixed eight candidates and cash control.")
        completed.add(cohort)
        del pending[cohort]
        reference_id = REFERENCES[row["preset"]]
        stat = strata.setdefault(key, {"cohorts": 0, "families": {c: base._family() for c in expected},
            "pairs": {c: _new_pair() for c in expected if c != reference_id}})
        stat["cohorts"] += 1
        for candidate, value in group.items():
            family = stat["families"][candidate]
            family["status"][value["status"]] += 1
            family["context_incomplete"] += not value["context_complete"]
            family["warmup_insufficient"] += value["warmup_insufficient"]
            family["partial_component_warmup"] += value["partial_component_warmup"]
            family["entries"] += value["entries"]
            family["closed"] += value["closed"]
            family["maximum_commitment"] = max(family["maximum_commitment"], value["max_commitment"])
            if value["eligible"]:
                family["eligible"].append(value["pnl"])
                family["fees"].append(value["fees"])
                if value["closed"]:
                    family["traded"].append(value["pnl"])
        for candidate, pair in stat["pairs"].items():
            _record_pair(pair, group[reference_id], group[candidate])
    base._require(count > 0 and not pending, "Empty input or incomplete fixed eight-candidate cohort, including stress put/cash.")
    output = {"schema": 1, "kind": "descriptive_stress_put_options_review", "rule_sha256": RULE_SHA256,
        "base_comparison_rule_sha256": base.RULE_SHA256, "columns": list(COLUMNS), "input_rows": count,
        "cohort_count": len(completed), "reference_candidates": dict(REFERENCES), "strata": [],
        "source_authentication_established": False, "individual_entry_cap_verified_from_csv": False,
        "research_only": True, "execution_qualified": False, "automatic_adoption": False,
        "capital_or_pnl_summed": False, "independent_win_probability_estimated": False,
        "cross_preset_or_window_ranking": False, "backtests_run": 0, "owner_operations": 0,
        "network_calls": 0, "model_calls": 0,
        "limitations": [
            "Caller must bind this exact26-column CSV to its completed publication. It does not independently authenticate archive/registry identities, quote clocks, raw stock prices, terms, funding or each fill/debit.",
            "Only same-portfolio, same-full-window, same-preset and same-liquidity-profile legs are paired. Accepted prefixes can differ because holds are censored rather than rejected or repaired.",
            "Held/open/incomplete/context-incomplete/short-warmup observations remain in status and exclusion counts, never completed net-P/L comparisons. Exclusion reasons can overlap.",
            "Complete no-trade zeros remain. All-eligible and both-traded statistics and paired activity counts are distinct; cash never supplies a traded observation.",
            "Reported distributions/counts describe dependent counterfactual cases, not additive capital, independent trials, a win probability or a global winner. All-zero cases establish no edge.",
            "Both presets retain their original signals and warmups; short volatility rotations can trade a warmed23-session component while41/65-session peers remain censored.",
            "No parameter search, new backtest, forward adoption, broker/order, account migration, spending or change of the100entry/500shared-monthly rules occurs."]}
    for key, stat in sorted(strata.items()):
        stratum = dict(zip(base.STRATUM, key))
        output["strata"].append({"stratum": stratum, "cohorts": stat["cohorts"],
            "reference_candidate": REFERENCES[stratum["preset"]],
            "families": {candidate: _family_result(value) for candidate, value in stat["families"].items()},
            "stress_pairs": {candidate: _pair_result(value, stat["cohorts"]) for candidate, value in stat["pairs"].items()}})
    base._require(len(json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) <= MAX_OUTPUT_BYTES,
        "Stress comparison output exceeds1MiB; no truncation.")
    _verify_rule()
    return output

