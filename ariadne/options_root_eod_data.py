"""Fixed KO anonymous historical inputs; no owner or trading authority.

The legacy F API remains separate. Root identity is checked on URLs, raw OSI
terms and normalized snapshots without coercing data through another ticker.
Date-only observations and standard100 terms remain explicit model assumptions.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import options_eod_data as base
from .qqq_data import exchange_day

ROOT = "KO"
SOURCE_ID = "onclick_ko_eod_v1"
HOST, ORIGIN = base.HOST, base.ORIGIN
MAX_RAW, MAX_WIRE, MAX_OUTPUT = base.MAX_RAW, base.MAX_WIRE, base.MAX_OUTPUT
MAX_QUOTES, WORKER_SECONDS = base.MAX_QUOTES, base.WORKER_SECONDS
OptionsEODDataError = base.OptionsEODDataError
OptionsEODUnavailable = base.OptionsEODUnavailable
SNAPSHOT_KEYS = base.SNAPSHOT_KEYS | {"underlying_symbol"}
QUOTE_KEYS = base.QUOTE_KEYS
SESSION_GRID_POLICY = base.SESSION_GRID_POLICY
SESSION_GRID_POLICY_SHA256 = base.SESSION_GRID_POLICY_SHA256
CANDIDATE_EXPIRY_POLICY = base.CANDIDATE_EXPIRY_POLICY
CANDIDATE_EXPIRY_POLICY_SHA256 = base.CANDIDATE_EXPIRY_POLICY_SHA256
canonical = base.canonical
_clock, _day, _historical = base._clock, base._day, base._historical
_json, _number, _integer = base._json, base._number, base._integer
_evidence, _expirations = base._evidence, base._expirations
_now = base._now
_ID = re.compile(r"KO([0-9]{6})([CP])([0-9]{8})\Z")
_OSI = re.compile(r"KO(?:----|    )([0-9]{6})([CP])([0-9]{8})\Z")


def _contract(value):
    match = _ID.fullmatch(value) if type(value) is str else None
    if not match:
        raise OptionsEODDataError("Only canonical unadjusted-root KO model IDs are allowed.")
    expiry = _day("20"+match[1][:2]+"-"+match[1][2:4]+"-"+match[1][4:6])
    with localcontext() as context:
        context.prec = 40
        strike = _number(Decimal(int(match[3])) / Decimal(1000), positive=True, maximum="99999.999")
    return expiry, "call" if match[2] == "C" else "put", strike


def required_ids(values=()):
    if type(values) not in (list, tuple) or len(values) > 2:
        raise OptionsEODDataError("At most two funded/pending KO contracts may be required.")
    for value in values:
        _contract(value)
    if len(set(values)) != len(values):
        raise OptionsEODDataError("Required KO contracts must be unique.")
    return sorted(values)


def expiration_url(session):
    return ORIGIN + "/options/?" + urlencode({"ticker":ROOT, "date":_day(session).isoformat(), "list":"expiration"})


def chain_url(session, expiry):
    return ORIGIN + "/options/?" + urlencode({"ticker":ROOT, "date":_day(session).isoformat(),
        "expiration":_day(expiry).isoformat(), "data":base.CHAIN_FIELDS, "output":"json-v1"})


def stock_url(session):
    day = _day(session)
    close = base._close_minutes(day)
    return ORIGIN + "/stock-data/v2/?" + urlencode({"ticker":ROOT, "from":day.isoformat()+"-09-30",
        "to":day.isoformat()+f"-{close//60:02d}-{close%60:02d}", "extended":"false", "bar_size":"1m"})


def _wanted_expiries(metadata, candidate, required, day):
    wanted = {candidate} if candidate else set()
    for identifier in required:
        expiry, kind, _ = _contract(identifier)
        if expiry < day or expiry.isoformat() not in metadata[kind]:
            raise OptionsEODUnavailable("Required KO expiry is absent; no replacement or metadata bypass.")
        wanted.add(expiry.isoformat())
    # Valid metadata may contain no candidate in the fixed DTE window. With
    # no required contract, retain the stock observation without inventing a
    # chain. Every actual required expiry above remains mandatory.
    return sorted(wanted)


def load_baseline(raw_stock, *, received_at, through_date):
    """Validate the whole original window; return an explicit actual prefix.

    Every row keeps the hash of the unchanged full body. A later raw date is
    still validated and must be historical at receipt even when the caller
    requests an earlier prefix. Nothing grants backdated financial decisions.
    """
    through, received = _day(through_date), _clock(received_at)
    _historical(through, received)
    if raw_stock is None or _json(raw_stock) == []:
        return []
    sessions = base._stock_sessions(raw_stock)
    for day in sessions:
        _historical(day, received)
    if through not in sessions:
        raise OptionsEODDataError("Baseline prefix end must be present in the fully validated raw window.")
    digest = hashlib.sha256(raw_stock).hexdigest()
    return [{"source_date":day.isoformat(), "underlying_close":close,
             "received_at":received.isoformat(), "stock_sha256":digest}
            for day, close in sessions.items() if day <= through]


def _chain(raw, day, expiry):
    rows = _json(raw)
    if type(rows) is not list or not 1 <= len(rows) <= MAX_QUOTES+1:
        raise OptionsEODDataError("Option chains require bounded actual rows.")
    warning, counts = None, None
    if type(rows[0]) is dict and set(rows[0]) == {"warning"}:
        warning = rows[0]["warning"]
        match = base._WARNING.fullmatch(warning) if type(warning) is str and len(warning) <= 512 else None
        if not match or not 0 < int(match[1]) < int(match[2]) <= MAX_QUOTES:
            raise OptionsEODDataError("Unknown or inconsistent partial-chain warning.")
        counts = {"returned_strikes":int(match[1]), "total_strikes":int(match[2])}
        rows = rows[1:]
    quotes = []
    for row in rows:
        if type(row) is not dict or not base._ROW_REQUIRED <= set(row) <= base._ROW_ALLOWED or row["ticker"] != ROOT:
            raise OptionsEODDataError("Unsupported option fields or actual KO underlying root.")
        source_day = row["date"]
        if type(source_day) is str and re.fullmatch(r"[0-9]{8}", source_day):
            source_day = source_day[:4]+"-"+source_day[4:6]+"-"+source_day[6:]
        if _day(source_day) != day or _day(row["expiration"]) != expiry:
            raise OptionsEODDataError("Option source session/expiry mismatch.")
        match = _OSI.fullmatch(row["osi-key"]) if type(row["osi-key"]) is str else None
        if not match:
            raise OptionsEODDataError("Adjusted roots and unknown KO OSI identities are rejected.")
        identifier = ROOT+"".join(match.groups())
        parsed_expiry, kind, strike = _contract(identifier)
        if parsed_expiry != expiry or row["type"] != kind or _number(row["strike"], positive=True, maximum="99999.999") != strike:
            raise OptionsEODDataError("KO OSI economic terms do not match actual quote fields.")
        quotes.append({"contract_id":identifier, "expiry":expiry.isoformat(), "type":kind, "strike":strike,
                       "bid":_number(row["bid"]), "ask":_number(row["ask"]),
                       "bid_size":_integer(row["bid_size"]), "ask_size":_integer(row["ask_size"])})
    if not quotes:
        raise OptionsEODUnavailable("Empty actual option chain; no synthetic replacement.")
    return quotes, warning, counts


def validate_snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_KEYS or snapshot["underlying_symbol"] != ROOT:
        raise OptionsEODDataError("Exact KO snapshot fields and explicit KO underlying identity required.")
    day, received = _day(snapshot["source_date"]), _clock(snapshot["received_at"])
    _historical(day, received)
    underlying = _number(snapshot["underlying_close"], positive=True, strings=True)
    if underlying != snapshot["underlying_close"] or snapshot["quote_observed_at"] is not None or snapshot["underlying_basis"] != "unadjusted_regular_last_bar" or type(snapshot["partial_chain"]) is not bool:
        raise OptionsEODDataError("No inferred event clock, adjusted stock or repaired monetary strings.")
    for field in ("source_sha256", "stock_sha256"):
        if type(snapshot[field]) is not str or not base._HASH.fullmatch(snapshot[field]):
            raise OptionsEODDataError("Source hashes require lowercase SHA256.")
    quotes = snapshot["quotes"]
    if type(quotes) is not list or not 0 <= len(quotes) <= MAX_QUOTES:
        raise OptionsEODDataError("A bounded quote list is required; an empty observation grants no entry.")
    previous = None
    for row in quotes:
        if type(row) is not dict or set(row) != QUOTE_KEYS:
            raise OptionsEODDataError("Exact date-only quote keys required.")
        expiry, kind, strike = _contract(row["contract_id"])
        if expiry < day or (expiry-day).days > 1096 or row["expiry"] != expiry.isoformat() or row["type"] != kind or row["strike"] != strike:
            raise OptionsEODDataError("Contract economic terms or expiry mismatch.")
        if previous is not None and row["contract_id"] <= previous:
            raise OptionsEODDataError("Contracts must be unique/sorted.")
        previous = row["contract_id"]
        amounts = {key:_number(row[key], strings=True, positive=key == "strike") for key in ("bid", "ask", "strike")}
        if any(row[key] != value for key, value in amounts.items()):
            raise OptionsEODDataError("Monetary strings must be canonical, without rounding.")
        if Decimal(row["bid"]) > Decimal(row["ask"]) or Decimal(row["bid"]) > Decimal(underlying if kind == "call" else strike):
            raise OptionsEODDataError("Crossed or economically impossible bid rejected, never repaired.")
        _integer(row["bid_size"])
        _integer(row["ask_size"])
    canonical(snapshot)
    return snapshot


def normalize_bundle(bundle, *, expected_session, required_contracts=()):
    day, required = _day(expected_session), required_ids(required_contracts)
    if type(bundle) is not dict or set(bundle) != {"session", "expirations", "chains", "stock"} or bundle["session"] != day.isoformat():
        raise OptionsEODDataError("Exact historical KO source bundle required.")
    exp_raw, exp_received, exp_source = _evidence(bundle["expirations"], expiration_url(day), day)
    metadata, candidate = _expirations(exp_raw, day, received_at=exp_received)
    wanted = _wanted_expiries(metadata, candidate, required, day)
    chains = bundle["chains"]
    if type(chains) is not list or len(chains) != len(wanted):
        raise OptionsEODDataError("Exactly candidate plus required expiries must be captured once.")
    quotes, sources, warnings, clocks = [], [], [], [exp_received]
    for evidence, expiry in zip(chains, wanted):
        raw, received, source = _evidence(evidence, chain_url(day, expiry), day)
        rows, warning, counts = _chain(raw, day, _day(expiry))
        quotes.extend(rows)
        source.update(expiry=expiry, partial_counts=counts, observed_quote_rows=len(rows))
        sources.append(source)
        clocks.append(received)
        if warning:
            warnings.append({"expiry":expiry, "text":warning, "counts":counts})
    stock_raw, stock_received, stock_source = _evidence(bundle["stock"], stock_url(day), day)
    stock_details = base._stock_sessions(stock_raw, details=True)
    if list(stock_details) != [day]:
        raise OptionsEODDataError("Underlying must be the exact requested single source session.")
    stock_source.update(stock_details[day], session_grid_policy_sha256=SESSION_GRID_POLICY_SHA256)
    clocks.append(stock_received)
    if sum(item["bytes"] for item in [exp_source, stock_source]+sources) > MAX_WIRE:
        raise OptionsEODDataError("Combined raw source evidence exceeds8MiB.")
    quotes.sort(key=lambda row:row["contract_id"])
    if set(required)-{row["contract_id"] for row in quotes}:
        raise OptionsEODUnavailable("Required funded/pending KO contract is absent; no replacement.")
    source_hash = hashlib.sha256(canonical({"expirations":exp_source["sha256"],
        "chains":[{"url":item["url"], "sha256":item["sha256"]} for item in sources]})).hexdigest()
    snapshot = {"source_date":day.isoformat(), "received_at":max(clocks).isoformat(), "underlying_symbol":ROOT,
        "underlying_close":stock_details[day]["underlying_close"], "quote_observed_at":None,
        "underlying_basis":"unadjusted_regular_last_bar", "quotes":quotes,
        "partial_chain":bool(warnings), "source_sha256":source_hash, "stock_sha256":stock_source["sha256"]}
    validate_snapshot(snapshot)
    provenance = {"source_id":SOURCE_ID, "symbol":ROOT, "profile":"experimental_eod_date_only_paper",
        "expiration_source":exp_source, "quote_sources":sources, "stock_source":stock_source,
        "candidate_expiry":candidate, "warnings":warnings, "quote_observed_at":None,
        "session_grid_policy":deepcopy(SESSION_GRID_POLICY), "session_grid_policy_sha256":SESSION_GRID_POLICY_SHA256,
        "candidate_expiry_policy":deepcopy(CANDIDATE_EXPIRY_POLICY),
        "candidate_expiry_policy_sha256":CANDIDATE_EXPIRY_POLICY_SHA256,
        "candidate_selection_receipt_at":exp_received.isoformat(),
        "candidate_selection_month":exchange_day(exp_received).strftime("%Y-%m"),
        "candidate_selection_scope":("actual_receipt_month" if candidate[:7] == exchange_day(exp_received).strftime("%Y-%m")
                                     else "global_warmup_fallback") if candidate else "no_eligible_common_expiry",
        "limitations":["Source dates do not establish exchange quote-event times or executable liquidity.",
            "100share standard contract is a model assumption; OCC deliverables not independently verified.",
            "Personal educational research only; original-provider entitlements remain unverified.",
            "Partial free chains constrain the candidate universe; no missing strike is reconstructed."]}
    canonical(provenance)
    return {"snapshot":snapshot, "provenance":provenance}


def _approved_url(url):
    if type(url) is not str or len(url) > 2048:
        raise OptionsEODDataError("Only a bounded reviewed source URL is allowed.")
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if parsed.scheme != "https" or parsed.netloc != HOST or parsed.fragment or len(query) != len(pairs) or query.get("ticker") != ROOT:
        raise OptionsEODDataError("Only exact anonymous fixed-host KO requests are allowed.")
    if parsed.path == "/options/" and set(query) == {"ticker", "date", "list"}:
        expected = expiration_url(query["date"])
    elif parsed.path == "/options/" and set(query) == {"ticker", "date", "expiration", "data", "output"}:
        expected = chain_url(query["date"], query["expiration"])
    elif parsed.path == "/stock-data/v2/" and set(query) == {"ticker", "from", "to", "extended", "bar_size"}:
        expected = stock_url(query["from"][:10])
    else:
        raise OptionsEODDataError("Accounts, keys, type/strike queries and unreviewed paths are prohibited.")
    if url != expected:
        raise OptionsEODDataError("Only canonical single-session historical KO parameters are allowed.")
    return parsed


def _fetch(url, *, deadline, check, budget):
    return base._fetch_common(url, deadline=deadline, check=check, budget=budget,
                              approved_url=_approved_url)


def fetch_bundle(session, required_contracts=(), check=lambda:None):
    """Daily3GETs, up to5 for required expiries;2 for valid no-expiry observation."""
    if os.environ.get("ARIADNE_OPTIONS_ROOT_EOD_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
        raise OptionsEODUnavailable("Network requires the explicitly authorized online fixed-root EOD child.")
    day, required = _day(session), required_ids(required_contracts)
    now = _clock(_now())
    _historical(day, now)
    if (exchange_day(now)-day).days > 366:
        raise OptionsEODDataError("Anonymous source scope is limited to one year of EOD history.")
    deadline, budget = time.monotonic()+WORKER_SECONDS, {"requests":0, "remaining":MAX_WIRE}
    bundle = {"session":day.isoformat(), "expirations":None, "chains":[], "stock":None}
    try:
        bundle["expirations"] = _fetch(expiration_url(day), deadline=deadline, check=check, budget=budget)
        raw, received, _ = _evidence(bundle["expirations"], expiration_url(day), day)
        metadata, candidate = _expirations(raw, day, received_at=received)
        for expiry in _wanted_expiries(metadata, candidate, required, day):
            bundle["chains"].append(_fetch(chain_url(day, expiry), deadline=deadline, check=check, budget=budget))
        bundle["stock"] = _fetch(stock_url(day), deadline=deadline, check=check, budget=budget)
        check()
        normalize_bundle(bundle, expected_session=day, required_contracts=required)
        canonical(bundle)
        return bundle
    except Exception as error:
        failure = OptionsEODUnavailable(str(error))
        failure.source_evidence = {"partial_bundle":bundle, "failed_response":getattr(error, "source_evidence", None)}
        raise failure from None
