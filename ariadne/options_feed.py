"""Documented anonymous AAPL historical snapshots; no accounts or orders.

The provider opens a prior session's options after the next session opens.
Its recorded EOD snapshot is distinct from our actual receipt/availability time.
The adapter is pure; only an explicitly authorized bounded child may fetch.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import http.client
import json
import os
import re
import socket
import time
from urllib.parse import urlencode, urlsplit

from .options_data import (OptionsDataError, canonical, clock, contract_id,
                           decimal_string, number, validate_dataset)
from .options_recovery import _precision
from .qqq_data import exchange_day, is_session, last_closed_session, session_close_utc

SOURCE_URL = "https://api.marketdata.app/v1/options/chain/AAPL/"
SOURCE_HOST = "api.marketdata.app"
MAX_RAW = 8 * 1024 * 1024
MAX_OUTPUT = 12 * 1024 * 1024
MAX_ROWS = 12000
MAX_DTE = 35
WORKER_SECONDS = 15
UTC = timezone.utc
NORMALIZATION_POLICY = {
    "id": "marketdata-aapl-eod-v1", "schema": 1,
    "raw": "Retain every supplied provider row unchanged in private source evidence.",
    "validation": "Reject malformed identity, economic terms, timestamps, underlying, decimals and counts. No repair or substitution.",
    "crossed": "Only non-required bid-greater-than-ask rows are explicitly quarantined with their exact ID/bid/ask/reason; never canonical or executable.",
    "required": "A funded or pending contract that is crossed or absent makes the whole capture unavailable; retain exposure and do not replace or settle it.",
    "scope": "All supplied zero quotes/sizes retained inside 0-35 calendar DTE and plus/minus5percent strikes, plus required contracts outside strike band.",
    "timestamp": "Only the provider's exact requested EOD snapshot; actual receipt establishes availability. No request/last-trade timestamp substitution.",
}
NORMALIZATION_SHA256 = hashlib.sha256(canonical(NORMALIZATION_POLICY)).hexdigest()
_ID = re.compile(r"AAPL([0-9]{6})([CP])([0-9]{8})\Z")
_REQUIRED = {"optionSymbol", "underlying", "expiration", "side", "strike", "updated",
             "bid", "bidSize", "ask", "askSize", "underlyingPrice", "volume", "openInterest"}
_OPTIONAL = {"firstTraded", "dte", "mid", "last", "inTheMoney", "intrinsicValue", "extrinsicValue",
             "iv", "delta", "gamma", "theta", "vega"}


class OptionsFeedError(OptionsDataError):
    """Malformed source evidence is never repaired or used as a fill."""


class OptionsFeedUnavailable(OptionsFeedError):
    """A source restriction or absent observation; never permission to retry."""


def _session(value):
    if type(value) is not date or not is_session(value):
        raise OptionsFeedError("Choose a reviewed exchange-session calendar date.")
    return value


def _identifier(value):
    match = _ID.fullmatch(value) if type(value) is str else None
    if not match:
        raise OptionsFeedError("Only standard AAPL OCC contract IDs are supported by this feed.")
    try:
        expiry = datetime.strptime(match[1], "%y%m%d").date()
        strike = decimal_string(Decimal(int(match[3])) / 1000)
        if value != contract_id("AAPL", expiry.isoformat(), "call" if match[2] == "C" else "put", strike):
            raise ValueError
    except (ValueError, OverflowError):
        raise OptionsFeedError("The required AAPL contract has inconsistent terms.") from None
    return value


@_precision
def required_ids(values=()):
    if type(values) not in (tuple, list) or len(values) > 2:
        raise OptionsFeedError("At most the funded and pending AAPL contracts may be required.")
    result = [_identifier(value) for value in values]
    if len(set(result)) != len(result):
        raise OptionsFeedError("Required contracts must be unique.")
    return sorted(result)


def source_url(session):
    session = _session(session)
    return SOURCE_URL + "?" + urlencode({"date": session.isoformat(), "expiration": "all",
        "from": session.isoformat(), "to": (session + timedelta(days=MAX_DTE)).isoformat(), "nonstandard": "false"})


def _json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RAW:
        raise OptionsFeedError("Options feed responses require 1 byte-8 MiB of JSON.")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise OptionsFeedError("Duplicate provider keys are rejected.")
            value[key] = item
        return value
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=Decimal,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise OptionsFeedError("Use bounded ordinary provider JSON with unique finite fields.") from None


def _amount(value, *, positive=False):
    if type(value) is int:
        value = Decimal(value)
    if type(value) is not Decimal:
        raise OptionsFeedError("Provider prices must be JSON numbers, not strings, booleans or null.")
    # Reject exponent-sized allocations before rendering an untrusted Decimal.
    # Removing trailing coefficient zeroes changes no economic value.
    if not value.is_finite() or value < 0 or value > Decimal("1000000") or positive and value <= 0:
        raise OptionsFeedError("Provider prices are outside the supported finite range.")
    if value == 0:
        return "0"
    parts = value.as_tuple()
    exponent = parts.exponent
    index = len(parts.digits) - 1
    while index >= 0 and parts.digits[index] == 0:
        exponent += 1
        index -= 1
    if exponent < -8:
        raise OptionsFeedError("Provider prices exceed eight exact decimal places.")
    text = decimal_string(value)
    number(text, positive=positive)
    return text


def _count(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000_000):
        raise OptionsFeedError("Provider sizes and counts must be bounded integers or null.")
    return value


def _timestamp(value):
    if type(value) is not int or not 915148800 <= value < 2082758400:
        raise OptionsFeedError("Provider timestamps must be whole Unix seconds in the reviewed calendar.")
    return datetime.fromtimestamp(value, UTC)


@_precision
def normalize_chain(raw, *, retrieved_at, expected_session=None, required_contracts=()):
    """Normalize exact supplied rows; source times are never invented.

    Explicit expected_session supports a bounded owner-approved historical study.
    Network collection always requests the actual latest completed session only.
    Critical fields are validated before deterministic DTE/strike scope.
    Crossed non-required rows remain explicitly quarantined in provenance/raw,
    never repaired or admitted to the canonical dataset.
    """
    received = clock(retrieved_at)
    expected = _session(last_closed_session(received) if expected_session is None else expected_session)
    if session_close_utc(expected) > received:
        raise OptionsFeedError("A provider session cannot be in the future.")
    required = required_ids(required_contracts)
    value = _json(raw)
    if type(value) is not dict or value.get("s") != "ok":
        raise OptionsFeedUnavailable("The provider supplied no successful AAPL quote snapshot.")
    if set(value) - (_REQUIRED | _OPTIONAL | {"s"}) or not _REQUIRED <= set(value):
        raise OptionsFeedError("Provider quote columns changed or required economic evidence is missing.")
    symbols = value["optionSymbol"]
    if type(symbols) is not list or not 1 <= len(symbols) <= MAX_ROWS:
        raise OptionsFeedError("AAPL chains require 1-12000 bounded rows.")
    size = len(symbols)
    if any(type(column) is not list or len(column) != size for key, column in value.items() if key != "s"):
        raise OptionsFeedError("Every provider column must align with the contract rows.")
    seen, quotes, quarantined, observed, spot = set(), [], [], None, None
    for index, identifier in enumerate(symbols):
        _identifier(identifier)
        if identifier in seen:
            raise OptionsFeedError("Provider contracts repeat; no duplicate price is selected.")
        seen.add(identifier)
        if value["underlying"][index] != "AAPL" or type(value["side"][index]) is not str or value["side"][index] not in {"call", "put"}:
            raise OptionsFeedError("The anonymous source only supports ordinary AAPL calls and puts.")
        stamp = _timestamp(value["updated"][index])
        if stamp != session_close_utc(expected):
            raise OptionsFeedError("Only the exact requested EOD snapshot is accepted; no request or trade timestamp substitution.")
        if observed is not None and stamp != observed:
            raise OptionsFeedError("Mixed quote snapshots cannot form a simultaneous chain.")
        observed = stamp
        underlying = _amount(value["underlyingPrice"][index], positive=True)
        if spot is not None and underlying != spot:
            raise OptionsFeedError("Underlying observations differ across the source chain.")
        spot = underlying
        expiry_at = _timestamp(value["expiration"][index])
        expiry = exchange_day(expiry_at)
        # Ordinary AAPL equity options expire at the session's regular close.
        if not is_session(expiry) or expiry_at != session_close_utc(expiry):
            raise OptionsFeedError("Only standard PM-settled equity expiry terms are supported.")
        if not expected <= expiry <= expected + timedelta(days=1096):
            raise OptionsFeedError("Provider expirations must be current/future and within three years.")
        strike = _amount(value["strike"][index], positive=True)
        kind = value["side"][index]
        if identifier != contract_id("AAPL", expiry.isoformat(), kind, strike):
            raise OptionsFeedError("Provider OCC identity disagrees with strike, expiry or option type.")
        quote = {"contract_id": identifier, "type": kind, "expiry": expiry.isoformat(), "strike": strike,
            "multiplier": 100, "bid": _amount(value["bid"][index]), "ask": _amount(value["ask"][index]),
            **{target: _count(value[source][index]) for source, target in (("bidSize", "bid_size"),
                ("askSize", "ask_size"), ("volume", "volume"), ("openInterest", "open_interest"))}}
        upper = Decimal(spot) if kind == "call" else Decimal(strike)
        if Decimal(quote["bid"]) > upper:
            raise OptionsFeedError("An option bid exceeds its supplied standard-contract economic upper bound.")
        if Decimal(quote["bid"]) > Decimal(quote["ask"]):
            if identifier in required:
                raise OptionsFeedUnavailable("A funded or pending contract has a crossed quote; no replacement or repaired fill is permitted.")
            quarantined.append({"contract_id": identifier, "bid": quote["bid"], "ask": quote["ask"], "reason": "crossed_bid_greater_than_ask"})
            continue
        # No other malformed row can be hidden by the later research scope.
        quotes.append(quote)
    if not quotes:
        raise OptionsFeedUnavailable("Every supplied contract was crossed; no canonical quote snapshot is available.")
    dataset = {"schema": 1, "evidence_kind": "historical_quotes", "currency": "USD", "as_of": received.isoformat(),
        "source": {"provider": "MarketData.app anonymous AAPL historical snapshots", "url": SOURCE_URL,
            "sha256": hashlib.sha256(raw).hexdigest(), "retrieved_at": received.isoformat(),
            "usage": "Private personal nonprofessional API research only; no redistribution or whole-database copying. Documented historical EOD quote snapshots, not last-trade timestamps; source price accuracy/chain completeness not independently verified. Anonymous demo is revocable with no established SLA or unlimited quota. Actual receipt is availability, never backdated. Explicit crossed non-required quote quarantine policy " + NORMALIZATION_POLICY["id"] + " SHA-256 " + NORMALIZATION_SHA256 + "; all raw rows retained, required crossed/missing contracts block the whole capture.",
            "quote_time_basis": "recorded", "independently_verified": False},
        "snapshots": [{"observed_at": observed.isoformat(), "available_at": received.isoformat(),
            "underlying_symbol": "AAPL", "underlying_price": spot, "quotes": sorted(quotes, key=lambda row: row["contract_id"])}]}
    validate_dataset(dataset, received)
    excluded = {"outside_dte": 0, "outside_price_band": 0}
    selected = []
    for quote in dataset["snapshots"][0]["quotes"]:
        dte = (date.fromisoformat(quote["expiry"]) - expected).days
        if quote["contract_id"] not in required:
            if not 0 <= dte <= MAX_DTE:
                excluded["outside_dte"] += 1
                continue
            if not Decimal(spot) * Decimal("0.95") <= Decimal(quote["strike"]) <= Decimal(spot) * Decimal("1.05"):
                excluded["outside_price_band"] += 1
                continue
        selected.append(quote)
    if not set(required) <= {row["contract_id"] for row in selected}:
        raise OptionsFeedUnavailable("A funded or pending contract is absent; no replacement quote or settlement is invented.")
    if not selected:
        raise OptionsFeedUnavailable("No contracts remain inside the fixed AAPL capture scope.")
    dataset["snapshots"][0]["quotes"] = selected
    quality = validate_dataset(dataset, received)
    return {"dataset": dataset, "provenance": {"provider": dataset["source"]["provider"], "symbol": "AAPL",
        "session": expected.isoformat(), "observed_at": observed.isoformat(), "available_at": received.isoformat(),
        "source_sha256": dataset["source"]["sha256"], "provider_rows": size, "selected_rows": len(selected),
        "normalization_policy": NORMALIZATION_POLICY.copy(), "normalization_policy_sha256": NORMALIZATION_SHA256,
        "quarantined_rows": len(quarantined), "quarantined": quarantined,
        "excluded": excluded, "required_contracts": required, "maximum_dte": MAX_DTE,
        "strike_band": ["0.95", "1.05"], "zero_bid_quotes_retained": sum(row["bid"] == "0" for row in selected),
        "quote_time_basis": "recorded", "timestamp_semantics": "Vendor EOD quote-snapshot as-of, not retrieval or last trade. Actual receipt controls availability.",
        "source_independently_verified": False, "quality": quality}}


def _fetch(url, *, deadline, check):
    """One fixed-host numeric-pinned TLS GET; never ambient proxies or redirects."""
    from .internet import _global_address, _tls_context, public_url
    if public_url(url) != url or urlsplit(url).hostname != SOURCE_HOST or not url.startswith(SOURCE_URL + "?"):
        raise OptionsFeedError("Only the fixed anonymous AAPL chain endpoint is allowed.")
    if time.monotonic() >= deadline:
        raise OptionsFeedUnavailable("The options source deadline passed.")
    check()
    addresses = socket.getaddrinfo(SOURCE_HOST, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not _global_address(row[4][0]) for row in addresses):
        raise OptionsFeedUnavailable("Options source DNS must contain only global addresses.")
    family, socktype, protocol, _, address = addresses[0]
    raw, wrapped, connection, response = socket.socket(family, socktype, protocol), None, None, None
    try:
        raw.settimeout(max(.1, min(10, deadline - time.monotonic())))
        raw.connect(address)
        wrapped = _tls_context().wrap_socket(raw, server_hostname=SOURCE_HOST)
        connection = http.client.HTTPSConnection(SOURCE_HOST, timeout=10)
        connection.sock = wrapped
        parsed = urlsplit(url)
        connection.request("GET", parsed.path + "?" + parsed.query, headers={
            "User-Agent": "Ariadne-Local/0.24 (bounded personal AAPL research)", "Accept": "application/json",
            "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        if response.status not in {200, 203}:
            raise OptionsFeedUnavailable("Options provider returned HTTP " + str(response.status) + "; no retry, account or bypass attempted.")
        content_type = response.getheader("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise OptionsFeedUnavailable("The fixed options source no longer returns JSON.")
        if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise OptionsFeedUnavailable("Compressed options responses are not accepted.")
        length = response.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > MAX_RAW):
            raise OptionsFeedUnavailable("Options response length exceeds its bound or is invalid.")
        payload = bytearray()
        while len(payload) <= MAX_RAW:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OptionsFeedUnavailable("Options response exceeded its wall-clock deadline.")
            # HTTPConnection can close its socket object for Connection: close
            # while HTTPResponse's file retains the fd. Keep the original
            # bounded timeout rather than touching that closed socket on Windows.
            block = response.read1(min(65536, MAX_RAW + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        if len(payload) > MAX_RAW:
            raise OptionsFeedUnavailable("Options response exceeded 8 MiB.")
        if time.monotonic() >= deadline:
            raise OptionsFeedUnavailable("Options response completed after its deadline.")
        check()
        return {"payload": bytes(payload), "status": response.status, "url": url, "content_type": content_type}
    finally:
        if response is not None: response.close()
        if connection is not None: connection.close()
        if wrapped is not None: wrapped.close()
        raw.close()


def collect_bundle(now=None, *, fetch=None, check=None, required_contracts=()):
    """One daily source response; parent owns session deduplication/publication."""
    fixed = now is not None
    started = clock(now)
    expected = last_closed_session(started)
    required = required_ids(required_contracts)
    check = check or (lambda: None)
    url = source_url(expected)
    if fetch is None:
        if os.environ.get("ARIADNE_OPTIONS_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
            raise OptionsFeedUnavailable("A real options request requires the explicitly authorized online child.")
        deadline = time.monotonic() + WORKER_SECONDS
        fetch = lambda address: _fetch(address, deadline=deadline, check=check)
    check()
    response = fetch(url)
    check()
    if type(response) is not dict or set(response) != {"payload", "status", "url", "content_type"} or type(response["status"]) is not int or response["status"] not in {200, 203} or response["url"] != url or response["content_type"].split(";", 1)[0].strip().lower() != "application/json":
        raise OptionsFeedUnavailable("Options transport did not return the exact successful fixed-source receipt.")
    received = clock(started if fixed else None)
    evidence = {"url": url, "content_base64": base64.b64encode(response["payload"]).decode("ascii"),
        "sha256": hashlib.sha256(response["payload"]).hexdigest(), "bytes": len(response["payload"]),
        "retrieved_at": received.isoformat(), "http_status": response["status"]}
    try:
        result = normalize_chain(response["payload"], retrieved_at=received, expected_session=expected, required_contracts=required)
    except OptionsDataError as error:
        # A successful HTTP response can still fail economic evidence checks.
        # Preserve its bounded exact bytes for the parent's failure receipt,
        # without returning any canonical dataset or invented paper fill.
        unavailable = OptionsFeedUnavailable("Options source evidence rejected: " + str(error))
        unavailable.source_evidence = evidence
        raise unavailable from None
    result.update(schema=1, network_requests=1, raw=evidence)
    check()
    if len(canonical(result)) > MAX_OUTPUT:
        raise OptionsFeedError("The complete options worker response exceeds 12 MiB.")
    return result
