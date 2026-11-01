"""Normalized benchmark witnesses for forward-only F stress-put research.

No Store, filesystem, network, model or financial-account operation is exposed.
Only existing bundled calendar helpers read their timezone rules. The host must
verify the accepted source receipt and publish witnesses under the supplied real
clock; neither a source claim nor a digest authenticates vendor prices.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from . import options_regime as regime
from .qqq_data import is_session, last_closed_session, session_dates
from .qqq_recovery import next_session_open, validate_dataset

UTC=timezone.utc
POLICY_ID="f_stress_breakdown_put_v1"
MAX_DATA_BYTES, MAX_SOURCE_ROWS = 8*1024*1024, 8000
MAX_RECEIPT_BYTES, MAX_WITNESS_BYTES, MAX_GATE_BYTES, MAX_WITNESSES = 16*1024, 10*1024, 11*1024, 16
RECEIPT_FIELDS=("request_id","action","status","outcome","created_at","data_sha256","source_sha256","last_sessions")
VALIDATION_SCOPE="Full accepted dataset hash bound; final60 consumed bars revalidated/classified. Earlier bars were accepted by the QQQ workflow, not independently revalidated here."
CALENDAR = {"id":"bundled-us-equity-1999-2035", "qqq_data_sha256":"0e911302fccfa5de7285b994dbf2e771712ae4aa2f07e37b18d736ceb4066dcc",
    "qqq_recovery_sha256":"c5e45e68c1b5d6619d7ec27530e5856392ce0bdd779fd3a483c77af8d9ef935b",
    "chicago_tzif_sha256":"c27b739ff46a7df0594e120d725b439217e11e44ea9a50cdc49130383b5482e7"}
RULE={"schema":1,"id":"forward-normalized-stress-context-v1","policy_id":POLICY_ID,
    "regime_rule_sha256":regime.RULE_SHA256,"calendar":CALENDAR,"sessions":60,"witness_ring":16,
    "decision":"Latest finalized common QQQ/SPY source session, with a newly published normalized witness available by actual decision time; stress_unwind alone permits a new put decision.",
    "fill":"The immediately preceding regular-session benchmark witness must have been newly published no later than the F source session open. Unknown F quote-event clocks remain unknown; never use later current-day context to permit a modeled earlier fill.",
    "publication":"New actual witness publication time, not QQQ receipt.created_at or dataset.as_of, is availability. Previously captured prices are not backdated as previously durable context.",
    "financial_scope":"Entry gate only; missing/malformed/stale/nonstress context cannot disable marks, exits or mandatory disposal. One shared500/100 ledger remains external and unchanged.",
    "evidence":"Last60 normalized accepted closes and source identities retained. Critical receipt projection and original full-receipt SHA are separate; host retains and verifies the full receipt beside the witness. No raw-chart reconstruction or independent price/publication-vintage authentication.",
    "bounds":"Source URLs<=512characters; full receipt<=16KiB, witness<=10KiB and gate<=11KiB to fit the separate12KiB policy record.",
    "validation_scope":VALIDATION_SCOPE}
RULE["normalized_closes"]="Exact decimal TEXT in retained witnesses/account state; conversion to classifier numeric values must preserve their decimal rational value."


class StressContextError(ValueError):
    """Invalid context never grants entry authority."""


def _require(condition,message):
    if not condition:raise StressContextError(message)


def _json(value,maximum):
    nodes=0; pending=[(value,0)]
    while pending:
        item,depth=pending.pop();nodes+=1
        _require(nodes<=250000 and depth<=16,"Context metadata exceeds its structural bound.")
        if type(item) is dict:
            _require(len(item)<=128 and all(type(k) is str and len(k)<=128 for k in item),"Invalid bounded context object.")
            pending.extend((v,depth+1) for v in item.values())
        elif type(item) is list:
            _require(len(item)<=MAX_SOURCE_ROWS,"Context list exceeds8000 records.")
            pending.extend((v,depth+1) for v in item)
        elif type(item) is str:_require(len(item)<=4096,"Context text exceeds its bound.")
        elif type(item) is int:_require(abs(item)<=10**14,"Context integer exceeds its bound.")
        elif type(item) is float:_require(math.isfinite(item) and abs(item)<=10**14,"Context number is not bounded finite data.")
        else:_require(item is None or type(item) is bool,"Only bounded JSON context data is accepted.")
    result=bytearray()
    try:
        for text in json.JSONEncoder(sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).iterencode(value):
            block=text.encode();_require(len(result)+len(block)<=maximum,"Context encoded bytes exceed their bound.");result.extend(block)
    except (TypeError,ValueError,OverflowError,RecursionError) as exc:
        raise StressContextError(str(exc)) from None
    return bytes(result)


def _digest(value,maximum=MAX_WITNESS_BYTES):return hashlib.sha256(_json(value,maximum)).hexdigest()
RULE_SHA256=_digest(RULE)
CALENDAR_SHA256=_digest(CALENDAR)


def _verify_rule():
    _require(_digest(RULE)==RULE_SHA256 and RULE["calendar"]==CALENDAR and RULE["regime_rule_sha256"]==regime.RULE_SHA256
        and hashlib.sha256(regime.canonical(regime.RULE)).hexdigest()==regime.RULE_SHA256,"Fixed context/calendar/regime rule changed.")


def _clock(value):
    if type(value) is str:
        _require(10<=len(value)<=40,"Use a bounded aware context clock.")
        try:value=datetime.fromisoformat(value.replace("Z","+00:00"))
        except ValueError:raise StressContextError("Use an aware context clock.") from None
    _require(isinstance(value,datetime) and value.tzinfo is not None and value.utcoffset() is not None,"Use an aware context clock.")
    return value.astimezone(UTC)


def _day(value):
    try:
        _require(type(value) is str and len(value)==10,"Use an ISO regular-session date.")
        result=date.fromisoformat(value)
        _require(result.isoformat()==value and is_session(result),"Use a known regular-session date.")
        return result
    except ValueError as exc:raise StressContextError(str(exc)) from None


def _hash(value):return type(value) is str and re.fullmatch("[a-f0-9]{64}",value) is not None


def _close(value):
    _require(type(value) in (str,int,float) and len(str(value))<=64,"Use bounded normalized closes.")
    try:
        exact=Fraction(str(value));normal=float(value)
        _require(math.isfinite(normal) and 0.00000001<=normal<=10000000 and Fraction(str(normal))==exact,
                 "A normalized close must preserve its decimal value in the fixed classifier.")
        return normal
    except (ValueError,OverflowError,ZeroDivisionError):raise StressContextError("Invalid normalized close.") from None


def _sources(value,as_of):
    _require(type(value) is dict and set(value)=={"QQQ","SPY"},"Both benchmark sources are required.")
    for source in value.values():
        _require(type(source) is dict and set(source)=={"url","sha256","retrieved_at"} and _hash(source["sha256"]),"Invalid source identity.")
        url=source["url"]
        try:
            parsed=urlsplit(url)
            _require(type(url) is str and len(url)<=512 and parsed.scheme=="https" and parsed.hostname and
                parsed.username is None and parsed.password is None and parsed.fragment=="" and parsed.port in (None,443) and
                not any(ord(c)<33 for c in url),"Use a bounded HTTPS benchmark source claim.")
        except (ValueError,TypeError,AttributeError):raise StressContextError("Invalid HTTPS source claim.") from None
        _require(_clock(source["retrieved_at"])<=as_of,"Benchmark retrieval follows dataset analysis.")


def _receipt(receipt,data_sha,sources,last_session,published):
    _require(type(receipt) is dict,"A completed accepted-source receipt is required.")
    _json(receipt,MAX_RECEIPT_BYTES)
    outcomes={"refresh":"updated","resolve_history":"diagnostic_history_resolved"}
    _require(receipt.get("status")=="completed" and receipt.get("action") in outcomes and
        receipt.get("outcome")==outcomes[receipt["action"]] and receipt.get("data_sha256")==data_sha and _hash(data_sha),
        "Receipt does not bind the accepted updated dataset.")
    _require(type(receipt.get("request_id")) is str and 1<=len(receipt["request_id"])<=128 and
             _clock(receipt.get("created_at"))<=published,"Source receipt clock/identity is unavailable.")
    if receipt["action"]=="refresh":
        _require(receipt.get("source_sha256")=={s:v["sha256"] for s,v in sources.items()} and
                 receipt.get("last_sessions")=={s:last_session for s in sources},"Refresh source hashes/sessions disagree.")
    if "source_sha256" in receipt:_require(receipt["source_sha256"]=={s:v["sha256"] for s,v in sources.items()},"Receipt source hashes disagree.")
    if "last_sessions" in receipt:_require(receipt["last_sessions"]=={s:last_session for s in sources},"Receipt source dates disagree.")


def receipt_projection(receipt):
    """Copy critical fields only; callers retain/hash the separate full receipt."""
    _require(type(receipt) is dict,"Use a bounded source receipt.")
    _json(receipt,MAX_RECEIPT_BYTES)
    return deepcopy({k:receipt[k] for k in RECEIPT_FIELDS if k in receipt})


def _point(rows):
    _require(type(rows) is list and len(rows)==60,"Exactly60 common normalized sessions are required.")
    dates=[];q=[];s=[]
    for row in rows:
        _require(type(row) is dict and set(row)=={"date","QQQ","SPY"},"Invalid normalized session fields.")
        _require(type(row["QQQ"]) is str and type(row["SPY"]) is str,"Retained normalized closes must use exact decimal text.")
        dates.append(_day(row["date"]));q.append(_close(row["QQQ"]));s.append(_close(row["SPY"]))
    _require(dates==session_dates(dates[0],dates[-1]),"Benchmark sessions must be consecutive, paired and ordered.")
    return regime.classify(q,s)


def build_witness(accepted_dataset,*,source_receipt,recorded_at):
    """Build NEW normalized context; the trusted host supplies its actual publication clock."""
    _verify_rule();published=_clock(recorded_at)
    _require(type(accepted_dataset) is dict and type(accepted_dataset.get("symbols")) is dict and
             set(accepted_dataset["symbols"])=={"QQQ","SPY"},"Use the actual paired accepted dataset.")
    for entry in accepted_dataset["symbols"].values():
        _require(type(entry) is dict and type(entry.get("bars")) is list and 60<=len(entry["bars"])<=MAX_SOURCE_ROWS,
                 "Accepted benchmark histories require60–8000 rows per symbol.")
    raw=_json(accepted_dataset,MAX_DATA_BYTES)
    _require(len(accepted_dataset["symbols"]["QQQ"]["bars"])==len(accepted_dataset["symbols"]["SPY"]["bars"]),"Accepted benchmark history lengths disagree.")
    policy_dataset={**accepted_dataset,"symbols":{s:{**entry,"source":deepcopy(entry["source"]),"bars":deepcopy(entry["bars"][-60:])}
        for s,entry in accepted_dataset["symbols"].items()}}
    try:quality=validate_dataset(policy_dataset)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:raise StressContextError(str(exc)) from None
    _require(not any(quality["excluded_incomplete_or_future_bars"].values()),"No unfinished source bars may be ignored.")
    as_of=_clock(accepted_dataset["as_of"]);sources={s:deepcopy(v["source"]) for s,v in accepted_dataset["symbols"].items()}
    _sources(sources,as_of)
    _require(as_of<=published and quality["last_date"]==last_closed_session(as_of).isoformat(),"Captured history is future or lacks its latest finalized session.")
    data_sha=hashlib.sha256(raw).hexdigest()
    _receipt(source_receipt,data_sha,sources,quality["last_date"],published)
    projection=receipt_projection(source_receipt)
    q,s=(accepted_dataset["symbols"][key]["bars"][-60:] for key in ("QQQ","SPY"))
    rows=[{"date":a["date"],"QQQ":str(_close(a["close"])),"SPY":str(_close(b["close"]))} for a,b in zip(q,s)]
    value={"schema":1,"kind":"normalized_accepted_stress_context","policy_id":POLICY_ID,"rule_sha256":RULE_SHA256,
        "regime_rule_sha256":regime.RULE_SHA256,"calendar_sha256":CALENDAR_SHA256,"source_session":quality["last_date"],
        "dataset_as_of":as_of.isoformat(),"published_at":published.isoformat(),"data_available_at":published.isoformat(),
        "data_sha256":data_sha,"source_receipt":projection,"source_receipt_sha256":_digest(projection,MAX_RECEIPT_BYTES),
        "source_receipt_original_sha256":_digest(source_receipt,MAX_RECEIPT_BYTES),
        "sources":sources,"sessions":rows,"classification":_point(rows),"raw_chart_reconstruction_available":False,
        "source_authentication_established":False,"historical_publication_verified":False,"research_only":True,"execution_qualified":False,
        "validation_scope":VALIDATION_SCOPE}
    value["witness_sha256"]=_digest(value)
    _json(value,MAX_WITNESS_BYTES);_verify_rule()
    return value


def validate_witness(witness):
    """Recheck retained normalized arithmetic/identity; raw vendor authenticity stays unverified."""
    _verify_rule();_json(witness,MAX_WITNESS_BYTES)
    keys={"schema","kind","policy_id","rule_sha256","regime_rule_sha256","calendar_sha256","source_session",
        "dataset_as_of","published_at","data_available_at","data_sha256","source_receipt","source_receipt_sha256","source_receipt_original_sha256","sources",
        "sessions","classification","raw_chart_reconstruction_available","source_authentication_established",
        "historical_publication_verified","research_only","execution_qualified","witness_sha256","validation_scope"}
    _require(type(witness) is dict and set(witness)==keys and type(witness["schema"]) is int and witness["schema"]==1 and
        witness["kind"]=="normalized_accepted_stress_context" and witness["policy_id"]==POLICY_ID and witness["rule_sha256"]==RULE_SHA256 and
        witness["regime_rule_sha256"]==regime.RULE_SHA256 and witness["calendar_sha256"]==CALENDAR_SHA256,"Context witness schema/rule changed.")
    for field in ("raw_chart_reconstruction_available","source_authentication_established","historical_publication_verified","execution_qualified"):
        _require(witness[field] is False,"Context cannot certify source/execution evidence.")
    _require(witness["research_only"] is True and witness["validation_scope"]==VALIDATION_SCOPE,"Context must retain its research/validation scope.")
    _require(_hash(witness["witness_sha256"]) and witness["witness_sha256"]==_digest({k:v for k,v in witness.items() if k!="witness_sha256"}),"Context witness digest changed.")
    as_of,published=_clock(witness["dataset_as_of"]),_clock(witness["published_at"])
    _require(as_of<=published and witness["data_available_at"]==witness["published_at"],"New publication, not an old capture clock, determines availability.")
    _sources(witness["sources"],as_of)
    classification=_point(witness["sessions"])
    _require(witness["source_session"]==witness["sessions"][-1]["date"]==last_closed_session(as_of).isoformat(),"Context source session is not finalized/current at capture.")
    _require(witness["classification"]==classification,"Retained normalized classifier result changed.")
    _receipt(witness["source_receipt"],witness["data_sha256"],witness["sources"],witness["source_session"],published)
    _require(witness["source_receipt"]==receipt_projection(witness["source_receipt"]) and
             _hash(witness["source_receipt_original_sha256"]),"Retained receipt projection/original identity changed.")
    _require(witness["source_receipt_sha256"]==_digest(witness["source_receipt"],MAX_RECEIPT_BYTES),"Source receipt digest changed.")
    _verify_rule();return deepcopy(witness)


def select_gate(witnesses,*,decision_at,source_date=None):
    """Gate new exposure only. An unavailable/nonstress benchmark returns allowed=False."""
    _verify_rule();now=_clock(decision_at)
    _require(type(witnesses) in (list,tuple) and len(witnesses)<=MAX_WITNESSES,"Use at most16 retained context witnesses.")
    stage="decision" if source_date is None else "fill"
    if source_date is None:required=last_closed_session(now);cutoff=now
    else:
        day=_day(source_date)
        _require(day<=last_closed_session(now),"A modeled fill source session must already be finalized.")
        prior=session_dates(day-timedelta(days=16),day-timedelta(days=1))
        _require(bool(prior),"No preceding benchmark session is available.")
        required=prior[-1];cutoff=next_session_open(required)
    seen=set();eligible=[]
    for item in witnesses:
        witness=validate_witness(item)
        _require(witness["witness_sha256"] not in seen,"A context ring must not duplicate a witness.");seen.add(witness["witness_sha256"])
        available=_clock(witness["data_available_at"])
        if witness["source_session"]==required.isoformat() and available<=cutoff and available<=now:eligible.append(witness)
    eligible.sort(key=lambda w:(_clock(w["data_available_at"]),_clock(w["dataset_as_of"]),w["witness_sha256"]))
    if len(eligible)>1:
        _require(eligible[-1]["data_available_at"]!=eligible[-2]["data_available_at"],"Ambiguous simultaneous context publications.")
    witness=eligible[-1] if eligible else None
    label=witness["classification"]["label"] if witness else None
    allowed=bool(witness and label=="stress_unwind")
    result={"schema":1,"kind":"forward_stress_context_gate","policy_id":POLICY_ID,"rule_sha256":RULE_SHA256,
        "stage":stage,"decision_at":now.isoformat(),"source_date":source_date,"required_source_session":required.isoformat(),
        "cutoff_at":cutoff.isoformat(),"allowed":allowed,"label":label,
        "reason":"ready_stress_unwind" if allowed else "benchmark_not_stress" if witness else "no_available_required_session_witness",
        "witness":witness,"witness_sha256":witness["witness_sha256"] if witness else None,
        "research_only":True,"execution_qualified":False,"marks_exits_disposal_authorized_by_this_gate":False}
    result["gate_sha256"]=_digest(result,MAX_GATE_BYTES);_verify_rule()
    return result


def validate_gate_result(result):
    """Validate a stored gate independently of a later, rotated witness ring."""
    _json(result,MAX_GATE_BYTES)
    _require(type(result) is dict and {"decision_at","source_date","witness","gate_sha256"}<=set(result),"Invalid retained gate result.")
    expected=select_gate(() if result["witness"] is None else (result["witness"],),
        decision_at=result["decision_at"],source_date=result["source_date"])
    _require(result==expected,"Retained entry gate result changed.")
    return deepcopy(result)
