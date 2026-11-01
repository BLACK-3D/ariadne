"""Anonymous F historical EOD inputs for a separate experimental paper model.

Dates are source labels, not exchange quote-event clocks. A standard100-share
contract is a MODEL assumption, not verified OCC deliverable evidence. Original
provider rights remain unverified; personal educational research only. This
module never changes the frozen execution-qualified data contract or accounts.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import http.client
import json
import os
import queue
import re
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from .qqq_data import exchange_day, is_session, session_close_utc

SOURCE_ID = "onclick_f_eod_v1"
HOST = "api.onclickmedia.com"
ORIGIN = "https://" + HOST
MAX_RAW = 2 * 1024 * 1024
MAX_WIRE = 8 * 1024 * 1024
MAX_OUTPUT = 12 * 1024 * 1024
MAX_QUOTES = 10000
WORKER_SECONDS = 15
UTC = timezone.utc
SESSION_GRID_POLICY = {
    "id":"onclick_regular_source_labels_v2",
    "primary_sources":["https://www.onclickmedia.com/doc-stock-data",
        "https://www.nyse.com/markets/hours-calendars"],
    "source_scope":"Unadjusted v2 minute data requested with extended=false; the provider documents9:30AMto4PM but does not establish individual interval/event-label semantics.",
    "labels":"Require every source minute label from09:30through the minute preceding the reviewed session close:390regular labels or210early-close labels. Permit one additional exact16:00or13:00 boundary label, respectively. No other missing, extra, duplicate or shifted label is accepted.",
    "price":"Use the close of the last actually supplied positive-volume source bar. Preserve every supplied row and original byte hash, including zero-volume and optional boundary rows. Never create, remove or relabel source rows.",
    "limitations":"This is an explicit source-label acceptance policy for date-only educational paper research. It does not verify interval-start semantics, an official closing/auction price, UTC event clocks, complete trade coverage or executable prices.",
}
CANDIDATE_EXPIRY_POLICY = {
    "id":"onclick_actual_receipt_month_candidate_v2",
    "universe":"Use only expiries present in both actual snapshot call/put metadata lists and7-21calendar days after the requested historical source session.",
    "selection_clock":"The actual metadata receipt's America/New_York calendar month, not the source-session month, UTC month, a backdated decision or a later wall clock.",
    "selection":"Prefer candidates in that receipt month; nearest14DTE then earlier ISO expiry. If none exists, retain the global nearest14DTE candidate for indicator observations only; the paper engine still applies all entry gates.",
    "required_contracts":"Always union the exact required held/pending contract expiries, even outside the candidate month/window. Missing required metadata or actual quotes remains a substantive stop.",
    "limits":"At most one candidate plus two required expiries; unchanged five GETs,15second worker and8MiB shared wire bound. No extra strike/type requests, retries, accounts or paid fields.",
    "qualification":"Frozen engine rules, source identity, snapshot fields, shared capital and gross monthly cap are unchanged. The actual durable decision month, delayed7-21DTE fill gate and disposal deadline can still reject every returned candidate. No trade, return, execution quality or complete universe is promised.",
}
CHAIN_FIELDS = "date,contractID,symbol,expiration,strike,type,bid,bid_size,ask,ask_size,volume,open_interest"
SNAPSHOT_KEYS = {"source_date", "received_at", "underlying_close", "quote_observed_at",
                 "underlying_basis", "quotes", "partial_chain", "source_sha256", "stock_sha256"}
QUOTE_KEYS = {"contract_id", "expiry", "type", "strike", "bid", "ask", "bid_size", "ask_size"}
EVIDENCE_KEYS = {"url", "received_at", "sha256", "bytes", "content_base64", "http_status"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"F([0-9]{6})([CP])([0-9]{8})\Z")
_OSI = re.compile(r"F(?:-----|     )([0-9]{6})([CP])([0-9]{8})\Z")
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,8})?\Z")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} (?:AM|PM)\Z")
_WARNING = re.compile(r"only ([0-9]{1,5}) strikes out of ([0-9]{1,5}) strikes were returned\. Upgrade to level 2 to get full chain\.", re.I)
_ROW_REQUIRED = {"osi-key", "ticker", "date", "expiration", "strike", "type", "bid", "ask", "bid_size", "ask_size"}
_ROW_ALLOWED = _ROW_REQUIRED | {"last", "mark", "volume", "open_interest", "implied_volatility", "delta", "gamma", "theta", "vega", "rho", "contractID", "symbol", "change", "percent_change", "underlying_price", "underlying_dividend_yield", "risk_free_rate"}


class OptionsEODDataError(ValueError):
    """Unusable inputs are never repaired into paper evidence."""


class OptionsEODUnavailable(OptionsEODDataError):
    """Unavailable/restricted sources are a stop, not permission to retry."""


def canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise OptionsEODDataError("Use bounded ordinary JSON.") from None
    if len(raw) > MAX_OUTPUT:
        raise OptionsEODDataError("Canonical EOD evidence exceeds12MiB.")
    return raw


SESSION_GRID_POLICY_SHA256 = hashlib.sha256(canonical(SESSION_GRID_POLICY)).hexdigest()
CANDIDATE_EXPIRY_POLICY_SHA256 = hashlib.sha256(canonical(CANDIDATE_EXPIRY_POLICY)).hexdigest()


def _clock(value):
    if type(value) is str and len(value) <= 40:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise OptionsEODDataError("An aware actual receipt clock is required.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionsEODDataError("An aware actual receipt clock is required.")
    return value.astimezone(UTC)


def _day(value, *, session=True):
    if type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise OptionsEODDataError("Invalid EOD calendar date.") from None
    if type(value) is not date or not 1999 <= value.year <= 2035 or session and not is_session(value):
        raise OptionsEODDataError("Use a reviewed US-equity session in1999-2035.")
    return value


def _historical(day, received):
    # Current/live scopes are denied for anonymous access, even after close.
    if day >= exchange_day(received) or session_close_utc(day) >= received:
        raise OptionsEODDataError("Only an earlier completed exchange session is historical here.")


def _number(value, *, positive=False, strings=False, maximum="1000000"):
    if strings:
        if type(value) is not str or not _MONEY.fullmatch(value):
            raise OptionsEODDataError("Canonical money requires exact plain decimal strings.")
        value = Decimal(value)
    elif type(value) is int:
        value = Decimal(value)
    if type(value) is not Decimal or not value.is_finite() or value < 0 or value > Decimal(maximum) or positive and value <= 0:
        raise OptionsEODDataError("Prices require finite nonnegative exact numeric values.")
    if value == 0:
        return "0"
    parts = value.as_tuple()
    exponent, index = parts.exponent, len(parts.digits)-1
    while index >= 0 and parts.digits[index] == 0:
        exponent += 1
        index -= 1
    if exponent < -8:
        raise OptionsEODDataError("Prices exceed eight exact decimal places.")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _integer(value, maximum=1000000000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise OptionsEODDataError("Actual side sizes and volume must be bounded integers.")
    return value


def _json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RAW:
        raise OptionsEODDataError("Each source body must contain1byte-2MiB.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    def integer(text):
        if len(text) > 16:
            raise ValueError("integer bound")
        return int(text)
    def decimal(text):
        if len(text) > 80:
            raise ValueError("decimal bound")
        value = Decimal(text)
        if abs(value.as_tuple().exponent) > 100:
            raise ValueError("exponent bound")
        return value
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_int=integer,
                           parse_float=decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 12 or count > 250000:
                raise ValueError("source structure bound")
            if type(item) is dict:
                if any(len(key) > 128 for key in item):
                    raise ValueError("source key bound")
                pending.extend((entry, depth+1) for entry in item.values())
            elif type(item) is list:
                pending.extend((entry, depth+1) for entry in item)
            elif type(item) is str:
                if len(item) > 2048:
                    raise ValueError("source string bound")
                item.encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError, InvalidOperation):
        raise OptionsEODDataError("Use bounded unique-key finite source JSON.") from None


def _contract(value):
    match = _ID.fullmatch(value) if type(value) is str else None
    if not match:
        raise OptionsEODDataError("Only canonical unadjusted-root F model IDs are allowed.")
    expiry = _day("20"+match[1][:2]+"-"+match[1][2:4]+"-"+match[1][4:6])
    with localcontext() as context:
        context.prec = 40
        strike = _number(Decimal(int(match[3])) / Decimal(1000), positive=True, maximum="99999.999")
    return expiry, "call" if match[2] == "C" else "put", strike


def required_ids(values=()):
    if type(values) not in (list, tuple) or len(values) > 2:
        raise OptionsEODDataError("At most two funded/pending F contracts may be required.")
    for value in values:
        _contract(value)
    if len(set(values)) != len(values):
        raise OptionsEODDataError("Required contracts must be unique.")
    return sorted(values)


def expiration_url(session):
    return ORIGIN + "/options/?" + urlencode({"ticker":"F", "date":_day(session).isoformat(), "list":"expiration"})


def chain_url(session, expiry):
    return ORIGIN + "/options/?" + urlencode({"ticker":"F", "date":_day(session).isoformat(),
        "expiration":_day(expiry).isoformat(), "data":CHAIN_FIELDS, "output":"json-v1"})


def _close_minutes(day):
    from .briefing import _tz
    close = session_close_utc(day).astimezone(_tz("America/Chicago"))
    return (close.hour+1)*60 + close.minute


def stock_url(session):
    day = _day(session)
    close = _close_minutes(day)
    return ORIGIN + "/stock-data/v2/?" + urlencode({"ticker":"F", "from":day.isoformat()+"-09-30",
        "to":day.isoformat()+f"-{close//60:02d}-{close%60:02d}", "extended":"false", "bar_size":"1m"})


def _expirations(raw, day, *, received_at):
    value = _json(raw)
    if type(value) is not dict or set(value) != {"call", "put"}:
        raise OptionsEODDataError("Expiration metadata needs exact call/put lists.")
    for kind, dates in value.items():
        if type(dates) is not list or not 1 <= len(dates) <= 200:
            raise OptionsEODDataError("Expiration metadata is empty or unbounded.")
        parsed = [_day(item) for item in dates]
        if any(expiry < day or (expiry-day).days > 1096 for expiry in parsed) or parsed != sorted(set(parsed)):
            raise OptionsEODDataError("Expirations must be unique ordered current/future sessions.")
    choices = sorted((item for item in set(value["call"]) & set(value["put"])
                      if 7 <= (_day(item)-day).days <= 21),
                     key=lambda item:(abs((_day(item)-day).days-14), item))
    month = exchange_day(_clock(received_at)).strftime("%Y-%m")
    current_month = [item for item in choices if item[:7] == month]
    candidates = current_month or choices
    return value, candidates[0] if candidates else None


def _wanted_expiries(metadata, candidate, required, day):
    wanted = {candidate} if candidate else set()
    for identifier in required:
        expiry, kind, _ = _contract(identifier)
        if expiry < day or expiry.isoformat() not in metadata[kind]:
            raise OptionsEODUnavailable("Required contract expiry absent from snapshot metadata.")
        wanted.add(expiry.isoformat())
    if not wanted:
        raise OptionsEODUnavailable("No eligible historical expiry or required disposal contract.")
    return sorted(wanted)


def _stock_sessions(raw, *, details=False):
    rows = _json(raw)
    if type(rows) is not list or not 1 <= len(rows) <= 15*391:
        raise OptionsEODDataError("Stock evidence needs at most15full regular-session grids.")
    groups = {}
    previous = None
    for row in rows:
        if type(row) is not dict or set(row) != {"timestamp", "open", "high", "low", "close", "volume"}:
            raise OptionsEODDataError("Unexpected unadjusted minute-stock fields.")
        stamp = row["timestamp"]
        try:
            if type(stamp) is not str or not _STAMP.fullmatch(stamp):
                raise ValueError
            parsed = datetime.strptime(stamp, "%Y-%m-%d %I:%M %p")
        except ValueError:
            raise OptionsEODDataError("Stock labels require their actual calendar date/minute, without inferred UTC.") from None
        if previous is not None and parsed <= previous:
            raise OptionsEODDataError("Stock minutes must be unique and strictly increasing.")
        previous = parsed
        day = _day(parsed.date())
        prices = {name:Decimal(_number(row[name], positive=True)) for name in ("open", "high", "low", "close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            raise OptionsEODDataError("Inconsistent stock OHLC; no repair.")
        _integer(row["volume"], 10000000000000)
        groups.setdefault(day, []).append((parsed, row))
    days = sorted(groups)
    if not 1 <= len(days) <= 15:
        raise OptionsEODDataError("At most15stock sessions are allowed.")
    expected_days = []
    cursor = days[0]
    while cursor <= days[-1]:
        if is_session(cursor):
            expected_days.append(cursor)
        cursor += timedelta(days=1)
        if len(expected_days) > 15 or (cursor-days[0]).days > 40:
            raise OptionsEODDataError("Stock baseline must be bounded consecutive sessions.")
    if days != expected_days:
        raise OptionsEODDataError("Stock baseline has missing exchange sessions.")
    accepted = {}
    for day, entries in groups.items():
        count = _close_minutes(day)-570
        expected = [datetime(day.year, day.month, day.day, 9, 30)+timedelta(minutes=n) for n in range(count)]
        boundary = expected[-1]+timedelta(minutes=1)
        actual = [item[0] for item in entries]
        positive = [(stamp, row) for stamp, row in entries if row["volume"] > 0]
        if actual not in (expected, expected+[boundary]) or not positive:
            raise OptionsEODDataError("Stock evidence needs every regular source label, at most one exact close-boundary label, and an actual positive-volume bar.")
        stamp, last = positive[-1]
        accepted[day] = {"underlying_close":_number(last["close"], positive=True),
            "last_positive_volume_label":last["timestamp"], "observed_rows":len(entries),
            "closing_boundary_present":actual[-1] == boundary,
            "zero_volume_rows":sum(row["volume"] == 0 for _, row in entries)}
    return accepted if details else {day:row["underlying_close"] for day, row in accepted.items()}


def load_baseline(raw_stock, *, received_at, through_date):
    """Original unadjusted1m bytes only; receipts are availability, not quote clocks."""
    through = _day(through_date)
    received = _clock(received_at)
    _historical(through, received)
    if raw_stock is None:
        return []
    if _json(raw_stock) == []:
        return []
    sessions = _stock_sessions(raw_stock)
    if max(sessions) != through:
        raise OptionsEODDataError("Baseline must end on the requested actual source session.")
    digest = hashlib.sha256(raw_stock).hexdigest()
    return [{"source_date":day.isoformat(), "underlying_close":close,
             "received_at":received.isoformat(), "stock_sha256":digest} for day, close in sessions.items()]


def _evidence(value, expected_url, day):
    if type(value) is not dict or set(value) != EVIDENCE_KEYS or value["url"] != expected_url or type(value["http_status"]) is not int or value["http_status"] != 200:
        raise OptionsEODDataError("Exact fixed-source successful evidence receipt required.")
    if type(value["bytes"]) is not int or not 1 <= value["bytes"] <= MAX_RAW or type(value["content_base64"]) is not str or len(value["content_base64"]) > (MAX_RAW+2)//3*4:
        raise OptionsEODDataError("Raw source byte bound exceeded.")
    try:
        raw = base64.b64decode(value["content_base64"], validate=True)
    except (ValueError, UnicodeError):
        raise OptionsEODDataError("Source bytes require exact base64.") from None
    if len(raw) != value["bytes"] or type(value["sha256"]) is not str or not _HASH.fullmatch(value["sha256"]) or hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise OptionsEODDataError("Retained raw source hash/length mismatch.")
    received = _clock(value["received_at"])
    _historical(day, received)
    metadata = {key:value[key] for key in ("url", "received_at", "sha256", "bytes")}
    return raw, received, metadata


def _chain(raw, day, expiry):
    rows = _json(raw)
    if type(rows) is not list or not 1 <= len(rows) <= MAX_QUOTES+1:
        raise OptionsEODDataError("Option chains require bounded actual rows.")
    warning, counts = None, None
    if type(rows[0]) is dict and set(rows[0]) == {"warning"}:
        warning = rows[0]["warning"]
        match = _WARNING.fullmatch(warning) if type(warning) is str and len(warning) <= 512 else None
        if not match or not 0 < int(match[1]) < int(match[2]) <= MAX_QUOTES:
            raise OptionsEODDataError("Unknown or inconsistent partial-chain warning.")
        counts = {"returned_strikes":int(match[1]), "total_strikes":int(match[2])}
        rows = rows[1:]
    quotes = []
    for row in rows:
        if type(row) is not dict or not _ROW_REQUIRED <= set(row) <= _ROW_ALLOWED or row["ticker"] != "F":
            raise OptionsEODDataError("Unsupported option fields or underlying root.")
        source_day = row["date"]
        if type(source_day) is str and re.fullmatch(r"[0-9]{8}", source_day):
            source_day = source_day[:4]+"-"+source_day[4:6]+"-"+source_day[6:]
        if _day(source_day) != day or _day(row["expiration"]) != expiry:
            raise OptionsEODDataError("Option source session/expiry mismatch.")
        match = _OSI.fullmatch(row["osi-key"]) if type(row["osi-key"]) is str else None
        if not match:
            raise OptionsEODDataError("Adjusted roots and unknown OSI identities are rejected.")
        identifier = "F"+"".join(match.groups())
        parsed_expiry, kind, strike = _contract(identifier)
        if parsed_expiry != expiry or row["type"] != kind or _number(row["strike"], positive=True, maximum="99999.999") != strike:
            raise OptionsEODDataError("OSI economic terms do not match actual quote fields.")
        quotes.append({"contract_id":identifier, "expiry":expiry.isoformat(), "type":kind, "strike":strike,
                       "bid":_number(row["bid"]), "ask":_number(row["ask"]),
                       "bid_size":_integer(row["bid_size"]), "ask_size":_integer(row["ask_size"])})
    if not quotes:
        raise OptionsEODUnavailable("Empty actual option chain; no synthetic replacement.")
    return quotes, warning, counts


def validate_snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_KEYS:
        raise OptionsEODDataError("Exact experimental date-only EOD snapshot keys required.")
    day, received = _day(snapshot["source_date"]), _clock(snapshot["received_at"])
    _historical(day, received)
    underlying = _number(snapshot["underlying_close"], positive=True, strings=True)
    if underlying != snapshot["underlying_close"] or snapshot["quote_observed_at"] is not None or snapshot["underlying_basis"] != "unadjusted_regular_last_bar" or type(snapshot["partial_chain"]) is not bool:
        raise OptionsEODDataError("No inferred event clock, adjusted stock or repaired monetary strings.")
    for field in ("source_sha256", "stock_sha256"):
        if type(snapshot[field]) is not str or not _HASH.fullmatch(snapshot[field]):
            raise OptionsEODDataError("Source hashes require lowercase SHA256.")
    quotes = snapshot["quotes"]
    if type(quotes) is not list or not 1 <= len(quotes) <= MAX_QUOTES:
        raise OptionsEODDataError("A bounded nonempty quote list is required.")
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
        raise OptionsEODDataError("Exact historical source bundle required.")
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
    stock_details = _stock_sessions(stock_raw, details=True)
    if list(stock_details) != [day]:
        raise OptionsEODDataError("Underlying must be the exact requested single source session.")
    stock_source.update(stock_details[day], session_grid_policy_sha256=SESSION_GRID_POLICY_SHA256)
    clocks.append(stock_received)
    if sum(item["bytes"] for item in [exp_source, stock_source]+sources) > MAX_WIRE:
        raise OptionsEODDataError("Combined raw source evidence exceeds8MiB.")
    quotes.sort(key=lambda row:row["contract_id"])
    if set(required)-{row["contract_id"] for row in quotes}:
        raise OptionsEODUnavailable("Required funded/pending contract is absent; no replacement.")
    source_hash = hashlib.sha256(canonical({"expirations":exp_source["sha256"],
        "chains":[{"url":item["url"], "sha256":item["sha256"]} for item in sources]})).hexdigest()
    snapshot = {"source_date":day.isoformat(), "received_at":max(clocks).isoformat(),
        "underlying_close":stock_details[day]["underlying_close"], "quote_observed_at":None,
        "underlying_basis":"unadjusted_regular_last_bar", "quotes":quotes,
        "partial_chain":bool(warnings), "source_sha256":source_hash, "stock_sha256":stock_source["sha256"]}
    validate_snapshot(snapshot)
    provenance = {"source_id":SOURCE_ID, "profile":"experimental_eod_date_only_paper",
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


def _now():
    return datetime.now(UTC)


def _approved_url(url):
    if type(url) is not str or len(url) > 2048:
        raise OptionsEODDataError("Only a bounded reviewed source URL is allowed.")
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if parsed.scheme != "https" or parsed.netloc != HOST or parsed.fragment or len(query) != len(pairs) or query.get("ticker") != "F":
        raise OptionsEODDataError("Only exact anonymous fixed-host F requests are allowed.")
    if parsed.path == "/options/" and set(query) == {"ticker", "date", "list"}:
        expected = expiration_url(query["date"])
    elif parsed.path == "/options/" and set(query) == {"ticker", "date", "expiration", "data", "output"}:
        expected = chain_url(query["date"], query["expiration"])
    elif parsed.path == "/stock-data/v2/" and set(query) == {"ticker", "from", "to", "extended", "bar_size"}:
        expected = stock_url(query["from"][:10])
    else:
        raise OptionsEODDataError("Accounts, keys, type/strike queries and unreviewed paths are prohibited.")
    if url != expected:
        raise OptionsEODDataError("Only canonical historical source parameters are allowed.")
    return parsed


class _WireFile:
    """Count HTTP framing/headers as well as dechunked JSON body bytes."""
    def __init__(self, stream, consume, budget):
        self.stream, self.consume, self.budget = stream, consume, budget

    def _read(self, method, count=-1):
        count = min(count if count >= 0 else MAX_WIRE+1, max(1, self.budget["remaining"]+1))
        value = getattr(self.stream, method)(count)
        self.consume(len(value))
        return value

    def read(self, count=-1):
        return self._read("read", count)

    def read1(self, count=-1):
        return self._read("read1", count)

    def readline(self, count=-1):
        return self._read("readline", count)

    def readinto(self, buffer):
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _WireSocket:
    def __init__(self, channel, *, budget, deadline, check):
        self.channel, self.budget, self.deadline, self.check = channel, budget, deadline, check

    def consume(self, amount):
        self.budget["remaining"] -= amount
        self.check()
        if self.budget["remaining"] < 0 or time.monotonic() >= self.deadline:
            raise OptionsEODUnavailable("Historical HTTP wire/time budget exceeded.")

    def sendall(self, value):
        self.consume(len(value))
        return self.channel.sendall(value)

    def makefile(self, *args, **kwargs):
        return _WireFile(self.channel.makefile(*args, **kwargs), self.consume, self.budget)

    def __getattr__(self, name):
        return getattr(self.channel, name)


def _fetch_common(url, *, deadline, check, budget, approved_url):
    """Private transport; callers supply a fixed code-owned URL validator.

    A JSON request never selects this callback. Fixed-root adapters retain
    separate validators; the host, TLS, wire/request and deadline bounds remain
    the existing transport contract.
    """
    from .internet import _global_address, _tls_context
    parsed = approved_url(url)
    check()
    remaining = deadline-time.monotonic()
    if remaining <= 0 or budget["requests"] >= 5 or budget["remaining"] <= 0:
        raise OptionsEODUnavailable("Historical source wall-time/request/byte budget exhausted.")
    # getaddrinfo has no portable timeout; daemon resolution is bounded by this
    # worker's remaining deadline, without leaving a non-daemon executor alive.
    resolved = queue.Queue(maxsize=1)
    def lookup():
        try:
            resolved.put((socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM), None))
        except Exception as error:
            resolved.put((None, error))
    threading.Thread(target=lookup, daemon=True).start()
    while True:
        check()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise OptionsEODUnavailable("Historical source DNS deadline passed.")
        try:
            addresses, error = resolved.get(timeout=min(.1, remaining))
            break
        except queue.Empty:
            pass
    if error or not addresses or any(not _global_address(row[4][0]) for row in addresses):
        raise OptionsEODUnavailable("Source DNS must resolve exclusively to public global addresses.")
    family, socktype, protocol, _, address = addresses[0]
    raw = socket.socket(family, socktype, protocol)
    wrapped = connection = response = None
    def interrupt():
        for channel in (wrapped, raw):
            if channel is not None:
                try:
                    channel.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    timer = threading.Timer(max(.001, deadline-time.monotonic()), interrupt)
    timer.daemon = True
    timer.start()
    try:
        def timeout(channel):
            check()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise OptionsEODUnavailable("Historical source deadline passed.")
            channel.settimeout(remaining)
        timeout(raw)
        raw.connect(address)
        timeout(raw)
        wrapped = _tls_context().wrap_socket(raw, server_hostname=HOST)
        timeout(wrapped)
        connection = http.client.HTTPSConnection(HOST)
        connection.sock = _WireSocket(wrapped, budget=budget, deadline=deadline, check=check)
        budget["requests"] += 1
        connection.request("GET", parsed.path+"?"+parsed.query, headers={"Accept":"application/json", "Accept-Encoding":"identity", "Connection":"close"})
        timeout(wrapped)
        response = connection.getresponse()
        if budget["remaining"] <= 0 or response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise OptionsEODUnavailable("Compressed or oversized source headers are rejected.")
        limit = min(MAX_RAW, budget["remaining"])
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or len(length) > 10 or int(length) > limit):
            raise OptionsEODUnavailable("Source body length exceeds fixed budget.")
        payload = bytearray()
        while len(payload) <= limit:
            check()
            if time.monotonic() >= deadline:
                raise OptionsEODUnavailable("Historical body exceeded wall-clock deadline.")
            block = response.read1(min(65536, limit+1-len(payload)))
            if not block:
                break
            payload.extend(block)
        check()
        if len(payload) > limit or time.monotonic() >= deadline:
            raise OptionsEODUnavailable("Historical source byte/time bound exceeded.")
        body = bytes(payload)
        evidence = {"url":url, "received_at":_now().isoformat(), "sha256":hashlib.sha256(body).hexdigest(),
                    "bytes":len(body), "content_base64":base64.b64encode(body).decode("ascii"), "http_status":response.status}
        if response.status != 200 or response.getheader("Content-Type", "").split(";",1)[0].strip().lower() != "application/json":
            error = OptionsEODUnavailable("Historical source returned HTTP"+str(response.status)+" or non-JSON; no retry/access change.")
            error.source_evidence = evidence
            raise error
        return evidence
    except (OSError, http.client.HTTPException) as error:
        raise OptionsEODUnavailable("Historical source network failure: "+type(error).__name__+"; no retry.") from None
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
        if wrapped is not None:
            wrapped.close()
        raw.close()


def _fetch(url, *, deadline, check, budget):
    """Unchanged F-only entry point over the shared private transport."""
    return _fetch_common(url, deadline=deadline, check=check, budget=budget,
                         approved_url=_approved_url)


def fetch_bundle(session, required_contracts=(), check=lambda:None):
    """Worker-only <=5GETs, one15second/8MiB budget; returns retained raw bundle."""
    if os.environ.get("ARIADNE_OPTIONS_EOD_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
        raise OptionsEODUnavailable("Network requires the explicitly authorized online EOD child.")
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
        # The owner retains completed exact raw responses even on substantive
        # rejection. This is not a valid success bundle and cannot be adopted.
        failure = OptionsEODUnavailable(str(error))
        failure.source_evidence = {"partial_bundle":bundle, "failed_response":getattr(error, "source_evidence", None)}
        raise failure from None
