"""Bounded, offline imports of timestamped standard-contract option quotes.

Schema checks establish internal consistency, never quote authenticity or a
profitable strategy. Source retrieval time is distinct from historical quote
and publication times. This module neither opens files nor accesses a network.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
from urllib.parse import urlsplit

UTC = timezone.utc
SYMBOLS = ("QQQ", "SPY", "AAPL", "KO")
# KO is an explicit offline campaign addition. Existing forward engines keep
# their own frozen universes; parsing a symbol never grants account authority.
RESEARCH_SYMBOLS = ("QQQ", "KO")
MAX_DATA = 32 * 1024 * 1024
MAX_SNAPSHOTS = 4000
MAX_QUOTES = 200000
MAX_CSV_ROWS = 300000
DELAY_MINUTES = 15
TOP_KEYS = {"schema", "evidence_kind", "as_of", "currency", "source", "snapshots"}
SOURCE_KEYS = {"provider", "url", "sha256", "retrieved_at", "usage", "quote_time_basis", "independently_verified"}
SNAPSHOT_KEYS = {"observed_at", "available_at", "underlying_symbol", "underlying_price", "quotes"}
QUOTE_KEYS = {"contract_id", "type", "expiry", "strike", "multiplier", "bid", "ask", "bid_size", "ask_size", "volume", "open_interest"}
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class OptionsDataError(ValueError):
    """Rejected data is never repaired into executable evidence."""


def canonical(value):
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise OptionsDataError("Options data must be bounded ordinary JSON.") from None


def clock(value=None):
    value = datetime.now(UTC) if value is None else value
    if type(value) is str:
        if len(value) > 40:
            raise OptionsDataError("Use an aware ISO timestamp.")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise OptionsDataError("Use an aware ISO timestamp.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionsDataError("Use an aware ISO timestamp.")
    return value.astimezone(UTC)


def number(value, *, positive=False, limit="1000000"):
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        raise OptionsDataError("Use a nonnegative plain decimal string, at most eight decimal places.")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise OptionsDataError("An options decimal is invalid.") from None
    if parsed > Decimal(limit) or positive and parsed <= 0:
        raise OptionsDataError("An options decimal is outside the supported range.")
    return parsed


def decimal_string(value):
    """Only for already parsed finite Decimals, never a permissive import parser."""
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise OptionsDataError("Use a finite nonnegative Decimal.")
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def contract_id(symbol, expiry, option_type, strike):
    if type(symbol) is not str or symbol not in SYMBOLS or type(option_type) is not str or option_type not in {"call", "put"}:
        raise OptionsDataError("Only standard QQQ, SPY, AAPL and research KO calls/puts are supported.")
    day = _date(expiry)
    amount = number(strike, positive=True, limit="99999.999") * 1000
    if amount != amount.to_integral_value():
        raise OptionsDataError("Standard OCC strikes require exact thousandths.")
    return symbol + day.strftime("%y%m%d") + ("C" if option_type == "call" else "P") + f"{int(amount):08d}"


def _date(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise OptionsDataError("Use an ISO calendar date.")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise OptionsDataError("An options calendar date is invalid.") from None
    if not 1999 <= day.year <= 2035:
        raise OptionsDataError("The reviewed market calendar supports 1999-2035.")
    return day


def _text(value, name, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.isprintable():
        raise OptionsDataError(f"Invalid {name} metadata.")
    return value


def _integer(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000_000):
        raise OptionsDataError("Quote sizes, volume and open interest must be bounded nonnegative integers or null.")


def _source(source, now, as_of):
    if type(source) is not dict or set(source) != SOURCE_KEYS:
        raise OptionsDataError("The exact options provenance contract is required.")
    _text(source["provider"], "provider", 128)
    _text(source["usage"], "usage", 2000)
    url = _text(source["url"], "source URL", 2048)
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        parsed.port
    except ValueError:
        raise OptionsDataError("A source URL is HTTPS provenance only, without credentials or fragment.") from None
    if type(source["sha256"]) is not str or not _HASH.fullmatch(source["sha256"]):
        raise OptionsDataError("Source SHA-256 must be lowercase hexadecimal.")
    retrieved = clock(source["retrieved_at"])
    if retrieved > now or retrieved > as_of:
        raise OptionsDataError("Retrieval cannot be after the import clock or dataset as_of.")
    if type(source["quote_time_basis"]) is not str or source["quote_time_basis"] not in {"recorded", "assumed_eod"} or source["independently_verified"] is not False:
        raise OptionsDataError("Imported quotes are not independently verified; declare recorded or assumed_eod timestamps.")
    return retrieved


def validate_dataset(dataset, now=None):
    """Validate without mutating or filling fields; return compact coverage facts."""
    from .qqq_data import exchange_day, is_session, session_close_utc
    current = clock(now)
    if type(dataset) is not dict or set(dataset) != TOP_KEYS or type(dataset["schema"]) is not int or dataset["schema"] != 1:
        raise OptionsDataError("Use the exact schema-1 options dataset contract.")
    if type(dataset["evidence_kind"]) is not str or dataset["evidence_kind"] not in {"historical_quotes", "synthetic_fixture"} or dataset["currency"] != "USD":
        raise OptionsDataError("Declare historical_quotes or synthetic_fixture in USD.")
    as_of = clock(dataset["as_of"])
    if as_of > current:
        raise OptionsDataError("A dataset cannot be dated in the future.")
    retrieved = _source(dataset["source"], current, as_of)
    snapshots = dataset["snapshots"]
    if type(snapshots) is not list or not 1 <= len(snapshots) <= MAX_SNAPSHOTS:
        raise OptionsDataError("A dataset requires 1-4000 bounded quote snapshots.")
    previous, seen_days, symbols, quote_count, times, last_available = None, set(), set(), 0, [], {}
    for snapshot in snapshots:
        if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_KEYS:
            raise OptionsDataError("A snapshot does not match the exact timestamped contract.")
        symbol = snapshot["underlying_symbol"]
        if type(symbol) is not str or symbol not in SYMBOLS:
            raise OptionsDataError("Only QQQ, SPY, AAPL and research KO quote snapshots are supported.")
        observed, available = clock(snapshot["observed_at"]), clock(snapshot["available_at"])
        if available < observed + timedelta(minutes=DELAY_MINUTES) or available > retrieved or observed > current:
            raise OptionsDataError("Quotes need an explicit publication lag of at least 15 minutes, available before retrieval.")
        day = exchange_day(observed)
        if not is_session(day):
            raise OptionsDataError("A quote must be on a reviewed exchange session.")
        close = session_close_utc(day)
        from .briefing import _tz
        opening = datetime(day.year, day.month, day.day, 8, 30, tzinfo=_tz("America/Chicago")).astimezone(UTC)
        if not opening <= observed <= close:
            raise OptionsDataError("Only regular equity-session quote timestamps are supported.")
        key = (observed, symbol)
        if previous is not None and key <= previous:
            raise OptionsDataError("Snapshots must be unique and sorted by actual UTC observation then symbol.")
        previous = key
        if symbol in last_available and available <= last_available[symbol]:
            raise OptionsDataError("Per-symbol publication timestamps must strictly advance.")
        last_available[symbol] = available
        # One snapshot per symbol/session is intentional: this daily workflow
        # cannot manufacture intraday fill/stop precision from mixed intervals.
        if (day, symbol) in seen_days:
            raise OptionsDataError("The daily research contract permits one snapshot per symbol/session.")
        seen_days.add((day, symbol))
        underlying_price = number(snapshot["underlying_price"], positive=True)
        quotes = snapshot["quotes"]
        if type(quotes) is not list or not 1 <= len(quotes) <= 25000:
            raise OptionsDataError("A snapshot requires 1-25000 bounded contracts.")
        previous_contract = None
        for quote in quotes:
            if type(quote) is not dict or set(quote) != QUOTE_KEYS:
                raise OptionsDataError("A quote does not match the exact standard-contract contract.")
            if type(quote["multiplier"]) is not int or quote["multiplier"] != 100:
                raise OptionsDataError("Only unadjusted standard 100-share contracts are supported.")
            expected = contract_id(symbol, quote["expiry"], quote["type"], quote["strike"])
            if quote["contract_id"] != expected or previous_contract is not None and expected <= previous_contract:
                raise OptionsDataError("Contract IDs must match economic terms and be unique/sorted; adjusted aliases are rejected.")
            previous_contract = expected
            expiry = _date(quote["expiry"])
            if not day <= expiry <= day + timedelta(days=1096):
                raise OptionsDataError("A quote's expiry must be current/future and within three years.")
            bid, ask = number(quote["bid"]), number(quote["ask"])
            if bid > ask:
                raise OptionsDataError("Crossed quotes are rejected, never repaired.")
            # A positive executable bid above the instrument's economic upper
            # bound can turn a corrupt row into a fictitious home-run exit.
            # No asynchronous-price tolerance is inferred from daily snapshots.
            upper = underlying_price if quote["type"] == "call" else number(quote["strike"], positive=True)
            if bid > upper:
                raise OptionsDataError("An option bid exceeds its supplied standard-contract economic upper bound.")
            for name in ("bid_size", "ask_size", "volume", "open_interest"):
                _integer(quote[name])
            quote_count += 1
            if quote_count > MAX_QUOTES:
                raise OptionsDataError("Options history exceeds 200000 contract quotes.")
        symbols.add(symbol)
        times.append(observed.isoformat())
    if len(canonical(dataset)) > MAX_DATA:
        raise OptionsDataError("Canonical options history exceeds 32 MiB.")
    return {"snapshots":len(snapshots), "quotes":quote_count, "symbols":sorted(symbols),
            "first_observed_at":times[0], "last_observed_at":times[-1],
            "quote_time_basis":dataset["source"]["quote_time_basis"],
            "evidence_kind":dataset["evidence_kind"], "source_independently_verified":False,
            "limitations":["Schema and hashes do not authenticate market prices or completeness.",
                           "Daily snapshots cannot establish intraday fills, stops or executable liquidity.",
                           "Assumed EOD timestamps are retrospective research only; synthetic fixtures are not historical evidence."]}


def load_dataset(raw, now=None):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DATA:
        raise OptionsDataError("Options imports require 1 byte-32 MiB of ordinary JSON.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OptionsDataError("Duplicate JSON keys are rejected.")
            result[key] = value
        return result
    try:
        dataset = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(OptionsDataError("Nonfinite JSON is rejected.")))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise OptionsDataError("Options import must be bounded ordinary JSON with unique keys.") from None
    validate_dataset(dataset, now)
    return dataset


def parse_cboe_csv(raw, *, source, as_of, symbols=SYMBOLS):
    """Convert Cboe's documented interval CSV into daily actual-time snapshots.

    Select the last supported regular-session interval per symbol/day. Other
    intraday intervals are not combined into a fictitious simultaneous chain.
    Unsupported underlyings are excluded; malformed selected rows are rejected.
    Raw sample/paid files stay private and are never bundled in source releases.
    """
    from .briefing import _tz
    from .qqq_data import session_close_utc, is_session
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DATA:
        raise OptionsDataError("Cboe CSV must be within 32 MiB.")
    if (type(symbols) not in (list, tuple) or not symbols or any(type(s) is not str or s not in SYMBOLS for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise OptionsDataError("Select unique supported symbols.")
    if type(source) is not dict or set(source) != SOURCE_KEYS or source["quote_time_basis"] != "recorded":
        raise OptionsDataError("Cboe interval imports require exact recorded-time provenance.")
    if source["sha256"] != hashlib.sha256(raw).hexdigest():
        raise OptionsDataError("Cboe CSV source hash must match the actual CSV bytes.")
    required = {"underlying_symbol", "quote_datetime", "root", "expiration", "strike", "option_type", "trade_volume", "bid_size", "bid", "ask_size", "ask", "underlying_bid", "underlying_ask"}
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not required.issubset(reader.fieldnames):
            raise OptionsDataError("Cboe CSV headers do not match the documented interval layout.")
        groups = {}
        for count, row in enumerate(reader, 1):
            if count > MAX_CSV_ROWS or None in row or any(value is None for value in row.values()):
                raise OptionsDataError("Cboe CSV row count or shape exceeds its contract.")
            symbol = row["underlying_symbol"]
            if symbol not in symbols:
                continue
            if row["root"] != symbol:
                raise OptionsDataError("Adjusted or aliased selected option roots are not supported.")
            observed = _csv_time(row["quote_datetime"]).replace(tzinfo=_tz("America/Chicago")) + timedelta(hours=-1)
            observed = observed.astimezone(UTC)
            day = observed.date()  # all regular US sessions share their UTC date
            if not is_session(day):
                raise OptionsDataError("Cboe row is not on a supported market session.")
            if observed > session_close_utc(day):
                continue  # QQQ/SPY options' post-equity-close 16:15 snapshots are outside this daily engine's scope.
            ubid, uask = number(_csv_decimal(row["underlying_bid"]), positive=True), number(_csv_decimal(row["underlying_ask"]), positive=True)
            if ubid > uask:
                raise OptionsDataError("Underlying quotes cannot be crossed.")
            underlying = decimal_string((ubid + uask) / 2)
            expiry = _csv_day(row["expiration"]).isoformat()
            option_type = {"C":"call", "P":"put"}.get(row["option_type"])
            strike = _csv_decimal(row["strike"])
            quote = {"contract_id":contract_id(symbol, expiry, option_type, strike), "type":option_type,
                     "expiry":expiry, "strike":strike, "multiplier":100,
                     "bid":_csv_decimal(row["bid"]), "ask":_csv_decimal(row["ask"]),
                     "bid_size":_csv_integer(row["bid_size"]), "ask_size":_csv_integer(row["ask_size"]),
                     "volume":_csv_integer(row["trade_volume"]), "open_interest":_csv_integer(row["open_interest"]) if "open_interest" in row else None}
            key = (day, symbol, observed)
            snapshot = groups.setdefault(key, {"observed_at":observed.isoformat(),
                "available_at":(observed + timedelta(minutes=DELAY_MINUTES)).isoformat(),
                "underlying_symbol":symbol, "underlying_price":underlying, "quotes":[]})
            if snapshot["underlying_price"] != underlying:
                raise OptionsDataError("Underlying quotes disagree inside a supposedly simultaneous snapshot.")
            snapshot["quotes"].append(quote)
        latest = {}
        for (day, symbol, observed), snapshot in groups.items():
            if (day, symbol) not in latest or clock(latest[(day, symbol)]["observed_at"]) < observed:
                latest[(day, symbol)] = snapshot
        snapshots = sorted(latest.values(), key=lambda item:(clock(item["observed_at"]), item["underlying_symbol"]))
        for snapshot in snapshots:
            snapshot["quotes"].sort(key=lambda item:item["contract_id"])
        dataset = {"schema":1, "evidence_kind":"historical_quotes", "as_of":clock(as_of).isoformat(),
                   "currency":"USD", "source":dict(source), "snapshots":snapshots}
        validate_dataset(dataset, as_of)
        return dataset
    except (UnicodeError, csv.Error, KeyError, TypeError, InvalidOperation, OverflowError):
        raise OptionsDataError("Cboe CSV does not match the supported documented contract.") from None


def _csv_decimal(value):
    number(value)
    return decimal_string(Decimal(value))


def _csv_integer(value):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value):
        raise OptionsDataError("Cboe sizes and volume must be ordinary integers.")
    result = int(value)
    _integer(result)
    return result


def _csv_time(value):
    for form in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, form)
        except ValueError:
            pass
    raise OptionsDataError("Cboe quote_datetime must be a documented US Eastern timestamp.")


def _csv_day(value):
    for form in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, form).date()
        except ValueError:
            pass
    raise OptionsDataError("Cboe expiration must be an ordinary calendar date.")
