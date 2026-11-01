"""Pure, bounded descriptive review of the versioned options CSV export.

CSV fields cannot authenticate their source or prove individual fills. The caller
must bind the export to its completed publication. No strategy or account runs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import re

COLUMNS = ("source_window", "mode", "portfolio", "preset", "profile", "candidate", "status", "pnl", "fees",
    "entries", "closed", "max_commitment", "start", "source_end", "accepted_end", "processed_rows", "source_rows",
    "complete", "finished_flat", "context_required", "context_complete", "hold", "research_gate", "evidence_kind",
    "quote_time_basis", "case_id")
PRESETS = {
    "conventional_regime": ("cash", "raw_recovery_call", "raw_breakout_call", "raw_breakdown_put",
        "growth_recovery_call", "growth_breakout_call", "stress_breakdown_put", "regime_rotating"),
    "volatility_regime": ("cash", "raw_compression_call", "raw_pullback_call", "raw_shock_put",
        "growth_compression_call", "growth_pullback_call", "stress_shock_put", "regime_rotating"),
}
STRATUM = ("source_window", "mode", "profile", "preset", "start", "source_end", "evidence_kind", "quote_time_basis")
STATUSES = ("gain", "loss", "zero", "held", "incomplete", "no_trade")
REFERENCE = "raw_recovery_call"
MAX_ROWS, MAX_OUTPUT_BYTES = 150000, 1024*1024
_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?\Z")
_COUNT = re.compile(r"(?:0|[1-9][0-9]{0,4})\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,15}\Z")
RULE = {"schema":1, "id":"paired-options-export-review-v1", "columns":COLUMNS, "presets":PRESETS,
    "reference":REFERENCE, "stratify":STRATUM, "row_limit":MAX_ROWS, "output_byte_limit":MAX_OUTPUT_BYTES,
    "paired_delta":"Competitor net P/L minus recovery net P/L within the same complete, flat, context-complete, sufficiently warmed source-window/portfolio/profile cohort.",
    "censoring":"Held, open, incomplete, context-incomplete and insufficient-warmup legs never supply an imputed P/L. Complete no-trade outcomes remain zero; both-traded comparisons are separate.",
    "capital":"Alternative counterfactual accounts are not combined; no summed P/L, capital, win probability, parameter fitting or automatic promotion."}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE,sort_keys=True,separators=(",",":")).encode()).hexdigest()


class PairedReviewError(ValueError):
    """An invalid, unfairly paired or unbounded result export."""


def _require(condition, message):
    if not condition: raise PairedReviewError(message)


def _verify_rule():
    try:
        digest=hashlib.sha256(json.dumps(RULE,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise PairedReviewError("The fixed comparison rule changed.") from None
    _require(digest==RULE_SHA256 and RULE["presets"]==PRESETS and RULE["columns"]==COLUMNS and
             RULE["reference"]==REFERENCE, "The fixed comparison rule changed.")


def _money(raw, *, signed=False):
    _require(_DECIMAL.fullmatch(raw) is not None, "Use bounded plain decimal strings, not floats/exponents.")
    result = Decimal(raw)
    _require(abs(result) <= Decimal("1000000000000") and (signed or result >= 0), "Amount is outside its finite bound.")
    return result


def _count(raw):
    _require(_COUNT.fullmatch(raw) is not None and int(raw) <= 10000, "Use bounded integer count strings.")
    return int(raw)


def _boolean(raw):
    _require(raw in ("True","False"), "CSV booleans must be exactly True or False.")
    return raw == "True"


def _day(raw):
    try:
        result = date.fromisoformat(raw)
        _require(result.isoformat() == raw, "Use canonical ISO dates.")
        return result
    except (TypeError, ValueError):
        raise PairedReviewError("Use canonical ISO dates.") from None


def _warmup(preset, candidate):
    if candidate == "cash": return 0
    if preset == "conventional_regime": return 14
    return 41 if "compression" in candidate else 65 if "pullback" in candidate else 23


def _row(raw):
    _require(type(raw) is dict and set(raw) == set(COLUMNS), "Use the exact26-column options export schema.")
    _require(all(type(v) is str and len(v) <= (1100 if k == "portfolio" else 128) for k,v in raw.items()),
             "Every CSV field must be a bounded string.")
    _require(raw["preset"] in PRESETS and raw["candidate"] in PRESETS[raw["preset"]], "Unknown fixed preset/candidate.")
    _require(raw["profile"] in ("strict_sizes","unknown_size_price_proxy") and raw["mode"] in ("individual","pooled"), "Unknown profile/mode.")
    _require(raw["evidence_kind"] in ("historical_quotes","synthetic_fixture") and
             raw["quote_time_basis"] in ("recorded","assumed_eod"), "Unknown quote evidence/clock basis.")
    _require(_TOKEN.fullmatch(raw["source_window"]) and _TOKEN.fullmatch(raw["case_id"]), "Invalid window/case identity.")
    symbols = raw["portfolio"].split(",")
    _require(1 <= len(symbols) <= (1 if raw["mode"] == "individual" else 64) and
             symbols == sorted(set(symbols)) and all(_SYMBOL.fullmatch(s) for s in symbols), "Invalid fixed portfolio membership.")
    row = dict(raw)
    for key in ("pnl","fees","max_commitment"): row[key] = _money(raw[key],signed=key=="pnl")
    for key in ("entries","closed","processed_rows","source_rows"): row[key] = _count(raw[key])
    for key in ("complete","finished_flat","context_required","context_complete"): row[key] = _boolean(raw[key])
    first,last = _day(raw["start"]),_day(raw["source_end"])
    _require(first <= last and 1 <= row["source_rows"] <= (last-first).days+1 and
             0 <= row["processed_rows"] <= row["source_rows"], "Invalid source window/row counts.")
    if row["processed_rows"]:
        _require(first <= _day(raw["accepted_end"]) <= last, "Accepted prefix leaves its full window.")
    else: _require(raw["accepted_end"] == "", "An empty prefix cannot claim a last accepted date.")
    _require(0 <= row["entries"]-row["closed"] <= 1 and row["entries"] <= row["source_rows"] and
             (not row["finished_flat"] or row["entries"]==row["closed"]), "Trade counts/open state disagree.")
    _require(row["fees"] == Decimal("0.65")*(row["entries"]+row["closed"]), "Fee totals do not reconcile.")
    _require(0 <= row["max_commitment"] <= min(Decimal(500),Decimal(100)*row["entries"]), "Reported gross commitment exceeds risk/count bounds.")
    _require(row["pnl"] >= -Decimal(100)*row["entries"], "Reported loss exceeds fully paid entry-count bounds.")
    if row["entries"] == 0:
        _require(row["pnl"] == row["fees"] == row["max_commitment"] == 0, "No-entry rows cannot claim funded profit/loss.")
    else: _require(row["max_commitment"] > Decimal("1.30"), "A funded entry must include premium and both fees.")
    expected_context = raw["candidate"].startswith(("growth_","stress_")) or raw["candidate"] == "regime_rotating"
    _require(row["context_required"] == expected_context, "Candidate context requirement changed.")
    _require(raw["status"] in STATUSES and (not raw["hold"] or re.fullmatch(r"[a-z][a-z0-9_]{0,119}",raw["hold"])), "Invalid status/hold code.")
    if row["complete"]:
        _require(row["finished_flat"] and not row["hold"] and row["processed_rows"] == row["source_rows"] and
                 raw["accepted_end"] == raw["source_end"], "Complete rows cannot conceal an open/held/partial prefix.")
    status = ("held" if row["hold"] else "incomplete" if not row["complete"] else "no_trade" if not row["entries"]
              else "gain" if row["pnl"]>0 else "loss" if row["pnl"]<0 else "zero")
    _require(raw["status"] == status and not (row["hold"] and row["complete"]), "Status disagrees with financial coverage.")
    gate = ("held_incomplete_evidence" if row["hold"] else "open_at_input_end" if not row["finished_flat"] else
            "cash_control" if raw["candidate"]=="cash" else "no_completed_trades" if not row["closed"] else "exploratory_only")
    if raw["evidence_kind"] == "synthetic_fixture": gate = "synthetic_fixture_only"
    _require(raw["research_gate"] == gate, "Research gate disagrees with saved ledger state.")
    if raw["candidate"] == "cash":
        _require(row["entries"] == 0 and row["complete"] and row["finished_flat"] and not row["context_required"], "Cash is not a complete zero-risk control.")
    row["warmup_insufficient"] = row["source_rows"] < _warmup(raw["preset"],raw["candidate"])
    row["partial_component_warmup"] = raw["preset"] == "volatility_regime" and raw["candidate"] == "regime_rotating" and 23 <= row["source_rows"] < 65
    row["eligible"] = bool(row["complete"] and row["finished_flat"] and not row["hold"] and
                           row["context_complete"] and not row["warmup_insufficient"])
    return row


def _text(value):
    if value == 0: return "0"
    raw = format(value,"f")
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


def _distribution(values):
    if not values: return {"count":0,"minimum":None,"median":None,"maximum":None}
    values = sorted(values); length=len(values); middle=length//2
    median = values[middle] if length%2 else (values[middle-1]+values[middle])/2
    return {"count":length,"minimum":_text(values[0]),"median":_text(median),"maximum":_text(values[-1])}


def _family():
    return {"status":Counter(),"context_incomplete":0,"warmup_insufficient":0,"partial_component_warmup":0,"eligible":[],"traded":[],
            "fees":[],"entries":0,"closed":0,"maximum_commitment":Decimal(0)}


def _pair():
    return {"matrix":Counter(),"excluded":Counter(),"all":[],"traded":[],"reference_fees":[],"competitor_fees":[]}


def _pair_metrics(values):
    return {"competitor_higher":sum(v>0 for v in values),"reference_higher":sum(v<0 for v in values),
            "equal":sum(v==0 for v in values),"competitor_minus_reference_net_pnl":_distribution(values)}


def compare_rows(rows):
    """Describe fixed peers against recovery, preserving censored and zero cases.

    Row dictionaries use the exact string-valued CSV schema. No I/O, account
    writes, strategy runs, parameter search, global winner or probability model.
    """
    with localcontext(_CONTEXT):
        return _compare(rows)


def _compare(rows):
    _verify_rule()
    strata, pending, completed = {}, {}, set()
    count = 0
    for raw in rows:
        count += 1
        _require(count <= MAX_ROWS, "Comparison exceeds150000 rows; no truncation.")
        row = _row(raw); key=tuple(row[k] for k in STRATUM); cohort=(key,row["portfolio"])
        _require(cohort not in completed, "Duplicate completed portfolio/window/profile cohort.")
        group=pending.setdefault(cohort,{})
        _require(row["candidate"] not in group, "Duplicate candidate in one matched cohort.")
        if group:
            prior=next(iter(group.values()))
            _require(row["source_rows"]==prior["source_rows"] and row["case_id"]==prior["case_id"], "Cohort source rows/case identity disagree.")
        group[row["candidate"]]=row
        expected=PRESETS[row["preset"]]
        if len(group) != len(expected): continue
        _require(set(group)==set(expected), "Cohort lacks the fixed eight candidates and cash control.")
        completed.add(cohort); del pending[cohort]
        stat=strata.setdefault(key,{"cohorts":0,"families":{c:_family() for c in expected},
            "pairs":{c:_pair() for c in expected if c != REFERENCE} if row["preset"]=="conventional_regime" else {}})
        stat["cohorts"] += 1
        for candidate,value in group.items():
            family=stat["families"][candidate]
            family["status"][value["status"]]+=1
            family["context_incomplete"]+=not value["context_complete"]
            family["warmup_insufficient"]+=value["warmup_insufficient"]
            family["partial_component_warmup"]+=value["partial_component_warmup"]
            family["entries"]+=value["entries"];family["closed"]+=value["closed"]
            family["maximum_commitment"]=max(family["maximum_commitment"],value["max_commitment"])
            if value["eligible"]:
                family["eligible"].append(value["pnl"]);family["fees"].append(value["fees"])
                if value["closed"]: family["traded"].append(value["pnl"])
        if not stat["pairs"]: continue
        reference=group[REFERENCE]
        for candidate,pair in stat["pairs"].items():
            other=group[candidate]
            matrix=("both_eligible" if reference["eligible"] and other["eligible"] else
                    "reference_only_eligible" if reference["eligible"] else
                    "competitor_only_eligible" if other["eligible"] else "neither_eligible")
            pair["matrix"][matrix]+=1
            for side,value in (("reference",reference),("competitor",other)):
                for reason,excluded in (("held",bool(value["hold"])),("open",not value["finished_flat"]),
                    ("incomplete",not value["complete"]),("context_incomplete",not value["context_complete"]),
                    ("warmup_insufficient",value["warmup_insufficient"])):
                    if excluded:pair["excluded"][side+"_"+reason]+=1
            if matrix=="both_eligible":
                delta=other["pnl"]-reference["pnl"]
                pair["all"].append(delta);pair["reference_fees"].append(reference["fees"]);pair["competitor_fees"].append(other["fees"])
                if reference["closed"] and other["closed"]:pair["traded"].append(delta)
    _require(count>0 and not pending, "Empty input or incomplete fixed eight-candidate cohort, including recovery/cash.")
    result=[]
    for key,stat in sorted(strata.items()):
        families={}
        for candidate,f in stat["families"].items():
            families[candidate]={"status_counts":{s:f["status"][s] for s in STATUSES},"context_incomplete":f["context_incomplete"],
                "source_rows_below_registered_warmup":f["warmup_insufficient"],"reported_entries":f["entries"],
                "volatility_rotation_not_all_components_warmed":f["partial_component_warmup"],
                "reported_closed_trades":f["closed"],"maximum_reported_monthly_commitment":_text(f["maximum_commitment"]),
                "eligible_net_pnl":_distribution(f["eligible"]),"eligible_traded_net_pnl":_distribution(f["traded"]),
                "eligible_fees":_distribution(f["fees"])}
        pairs={}
        for candidate,p in stat["pairs"].items():
            pairs[candidate]={"matched_cohorts":stat["cohorts"],"eligibility_matrix":{v:p["matrix"][v] for v in
                ("both_eligible","reference_only_eligible","competitor_only_eligible","neither_eligible")},
                "exclusion_counts_may_overlap":dict(sorted(p["excluded"].items())),"all_eligible_pairs":_pair_metrics(p["all"]),
                "both_traded_pairs":_pair_metrics(p["traded"]),"matched_reference_fees":_distribution(p["reference_fees"]),
                "matched_competitor_fees":_distribution(p["competitor_fees"])}
        result.append({"stratum":dict(zip(STRATUM,key)),"cohorts":stat["cohorts"],"families":families,"recovery_pairs":pairs})
    output={"schema":1,"kind":"descriptive_paired_options_review","rule_sha256":RULE_SHA256,"columns":list(COLUMNS),
        "input_rows":count,"cohort_count":len(completed),"strata":result,"reference_candidate":REFERENCE,
        "source_authentication_established":False,"individual_entry_cap_verified_from_csv":False,"research_only":True,
        "execution_qualified":False,"automatic_adoption":False,"capital_or_pnl_summed":False,
        "independent_win_probability_estimated":False,"backtests_run":0,"owner_operations":0,"network_calls":0,"model_calls":0,
        "limitations":["Caller must bind this exact26-column export to its completed publication; rows lack archive/registry hashes and cannot independently verify funding, underlying spots or every entry debit.",
            "Pair eligibility is conditional on both legs completing flat with full context and sufficient source-row warmup; excluded holds/open/partial prefixes are counted, never imputed zero.",
            "Complete no-trade zeros remain outcomes; both-traded statistics are separate. All-zero or unqualified-size cases establish no trading edge or winner.",
            "Distributions and higher/lower/tie counts describe dependent counterfactual cases, not pooled money, independent trials, a win probability or a prospective profitability guarantee.",
            "Volatility presets are summarized separately without cross-preset recovery pairing. No parameter search, strategy activation, account migration or change of100/500 risk rules occurs."]}
    _require(len(json.dumps(output,sort_keys=True,separators=(",",":"),allow_nan=False).encode())<=MAX_OUTPUT_BYTES,
             "Comparison output exceeds1MiB; no truncation.")
    _verify_rule()
    return output
