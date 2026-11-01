"""Fixed KO quote research against one independent, unknown-basis close series.

No acquisition or account operation. The whole reported-close generation stays
separate from the owner's source; a failed reconciliation is never a repair or
paper-feed qualification. All historical event/publication clocks are assumed.
"""
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import io
import json
import re
from urllib.parse import urlsplit

from . import options_data
from . import options_root_eod_data as source
from . import research_window as scope
from .qqq_data import exchange_day, is_session, session_close_utc, session_dates

ROOT, START, END = "KO", "2026-05-04", "2026-10-29"
DAYS = tuple(session_dates(date.fromisoformat(START), date.fromisoformat(END)))
CLOCK_PROFILES = ("assumed_eod_plus_15m", "assumed_next_session_0935")
RECORD_KEYS = {"source_date", "received_at", "chain_raw", "chain_url"}
STOCK_KEYS = {"provider", "url", "sha256", "received_at", "usage", "basis", "reconciliation_sha256"}
MAX_RAW, MAX_REFERENCES, MAX_STOCK_ROWS = 2*1024**2, 64*1024**2, 2000
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"[0-9]{8}\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,40})?\Z")
RULE = {"schema": 1, "id": "KO-independent-reported-close-reference-research-v1",
    "symbol": ROOT, "start": START, "end": END, "sessions": 125,
    "clock_profiles": list(CLOCK_PROFILES), "window_policy_sha256": scope.RULE_SHA256,
    "expiry": "Next standard third-Friday monthly expiry adjusted backward to a regular session; strictly after source day, roll next month on adjusted expiry day. Source rows must prove exact returned date/root/expiry.",
    "cohort": "All125 declared source days exactly once, in ascending order. October30 excluded BEFORE prices for BOTH profiles because its next-session availability is future at actual registration. Every supplied call/put/strike retained, both types required. No future continuity, return, liquidity or outcome filtering.",
    "stock_generation": "Whole independent reported-daily-close CSV generation. Its unknown price basis and failed cross-provider reconciliation remain explicit. Never substitute into the owner's source, combine with minute-source closes, or claim verified equality.",
    "precision": "Original positive finite plain-decimal close string retained. Isolated Decimal precision80; ROUND_HALF_EVEN to0.00000001. Per-row signed delta and <=0.000000005 absolute error audited. No cents rounding, synthetic stock price, corporate-action adjustment or repair. Raw>=100 cannot become entry-eligible via quantization.",
    "clocks": "Observed_at ASSUMED regular close; available_at ASSUMED close+15minutes OR next regular09:35NewYork. Both profiles' latest availability must precede actual frozen registration in paired-plan preflight before CSV validation or any acquisition. Actual receipts stay unchanged. Future assumed availability rejects the entire plan, not clipped or backdated.",
    "source": "Actual source quote prices/sizes pass unchanged frozen root._chain, canonical contract/economic checks and last12calendar-month admission; source hashes/receipts and partial warnings retained.",
    "limits": {"stock_bytes": MAX_RAW, "stock_rows": MAX_STOCK_ROWS, "chain_references_bytes": MAX_REFERENCES},
    "research_only": True, "execution_qualified": False, "deployable": False,
    "paper_promotion": False, "network_calls": 0, "model_calls": 0, "owner_operations": 0}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class ReferenceHistoryError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise ReferenceHistoryError(message)


def _verify():
    _require(hashlib.sha256(canonical(RULE)).hexdigest() == RULE_SHA256
        and (ROOT, START, END, len(DAYS)) == (RULE["symbol"], RULE["start"], RULE["end"], RULE["sessions"])
        and list(CLOCK_PROFILES) == RULE["clock_profiles"] and scope.RULE_SHA256 == RULE["window_policy_sha256"]
        and source.ROOT == ROOT, "The fixed independent-reference research policy changed.")


def _day(value):
    if type(value) is str:
        _require(bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)), "Use a strict ISO source day.")
        try: value = date.fromisoformat(value)
        except ValueError: raise ReferenceHistoryError("Invalid source date.") from None
    _require(type(value) is date and is_session(value), "Source day must be an exact regular-session date.")
    return value


def expiry_for(day):
    """Fixed monthly selection before prices; no metadata or outcome fallback."""
    day = _day(day)
    year, month = day.year, day.month
    for _ in range(2):
        first = date(year, month, 1)
        expiry = first+timedelta(days=(4-first.weekday()) % 7+14)
        while not is_session(expiry): expiry -= timedelta(days=1)
        if expiry > day: return expiry
        year, month = (year+1, 1) if month == 12 else (year, month+1)
    raise ReferenceHistoryError("No strictly later fixed monthly expiry.")


def _clock(value, name):
    _require(type(value) is str and 19 <= len(value) <= 64, name+" needs its original aware ISO string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, name+" must be aware.")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ReferenceHistoryError(name+" requires a valid aware ISO clock.") from None


def _availability(day, profile):
    observed = session_close_utc(day)
    if profile == CLOCK_PROFILES[0]: return observed, observed+timedelta(minutes=15)
    from .briefing import _tz
    following = session_dates(day+timedelta(days=1), day+timedelta(days=8))[0]
    return observed, datetime(following.year, following.month, following.day, 8, 35,
        tzinfo=_tz("America/Chicago")).astimezone(timezone.utc)


def _stock(raw, metadata, registered):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, "Whole stock CSV requires1byte..2MiB original bytes.")
    _require(type(metadata) is dict and set(metadata) == STOCK_KEYS, "Use exact independent stock provenance fields.")
    for name, maximum in (("provider", 128), ("usage", 2000), ("url", 2048)):
        _require(type(metadata[name]) is str and 1 <= len(metadata[name]) <= maximum and metadata[name].isprintable(), "Invalid stock "+name+" provenance.")
    _require(metadata["basis"] == "unknown_reported_daily_close", "No verified/unadjusted basis is inferred for this generation.")
    _require(type(metadata["sha256"]) is str and _HASH.fullmatch(metadata["sha256"])
        and hashlib.sha256(raw).hexdigest() == metadata["sha256"], "Whole stock bytes do not match their actual SHA256.")
    _require(type(metadata["reconciliation_sha256"]) is str and _HASH.fullmatch(metadata["reconciliation_sha256"]), "Pin the separate failed-reconciliation evidence SHA256.")
    try:
        parsed = urlsplit(metadata["url"])
        _require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.fragment, "Stock provenance requires credential-free HTTPS.")
        parsed.port
    except ValueError: raise ReferenceHistoryError("Invalid stock HTTPS provenance.") from None
    received = _clock(metadata["received_at"], "Stock receipt")
    _require(received <= registered, "Actual stock receipt is future of study registration.")
    try:
        text = raw.decode("utf-8-sig")
        _require("\x00" not in text, "Stock CSV contains NUL bytes.")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fields = reader.fieldnames
        _require(fields is not None and 2 <= len(fields) <= 100 and len(set(fields)) == len(fields)
            and {"date", "close"} <= set(fields)
            and all(type(field) is str and 1 <= len(field) <= 64 and field.isascii() and field.isprintable() for field in fields),
            "Stock CSV needs unique date/close headers; additional original fields remain whole-hash bound.")
        result, audit, previous = {}, [], None
        for index, row in enumerate(reader):
            _require(index < MAX_STOCK_ROWS and None not in row and all(type(value) is str and len(value) <= 4096 for value in row.values()),
                "Stock CSV row/column or bounded-value contract failed.")
            label, supplied = row["date"], row["close"]
            _require(bool(_DATE.fullmatch(label)), "Stock dates require exact YYYYMMDD labels.")
            try: day = date(int(label[:4]), int(label[4:6]), int(label[6:]))
            except ValueError: raise ReferenceHistoryError("Invalid stock date.") from None
            _require(is_session(day) and (previous is None or previous < day), "Stock rows must be unique ascending regular sessions.")
            _require(day < exchange_day(received) and day < exchange_day(registered), "Actual stock rows must be historical at receipt and registration.")
            _require(bool(_PRICE.fullmatch(supplied)), "Stock close needs its original bounded positive plain decimal string.")
            with localcontext() as context:
                context.prec = 80
                context.rounding = ROUND_HALF_EVEN
                context.clear_traps()
                original = Decimal(supplied)
                _require(original.is_finite() and 0 < original <= Decimal("1000000"), "Stock close is outside positive supported bounds.")
                quantized = original.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
                delta = quantized-original
                _require(quantized.is_finite() and quantized > 0 and abs(delta) <= Decimal("0.000000005"), "Declared stock precision conversion exceeded its fixed tolerance.")
                rendered = options_data.decimal_string(quantized)
                _require(not (original >= Decimal(100) and quantized < Decimal(100)), "Stock quantization cannot create under100 eligibility.")
                signed_delta = format(delta, "f")
                raw_under100, canonical_under100 = original < Decimal(100), quantized < Decimal(100)
            options_data.number(rendered, positive=True)
            result[day] = rendered
            audit.append({"source_date": day.isoformat(), "raw_date": label, "raw_close": supplied,
                "canonical_close": rendered, "signed_delta": signed_delta,
                "raw_under100": raw_under100, "canonical_under100": canonical_under100,
                "role": ("option_reference" if day in DAYS else "causal_context_only_not_option_metrics" if day < DAYS[0]
                         else "outside_option_cohort_not_used")})
            previous = day
        _require(result and all(day in result for day in DAYS), "Every fixed quote source day needs its actual stock reference; no filling missing prices.")
    except (UnicodeError, csv.Error, InvalidOperation, OverflowError):
        raise ReferenceHistoryError("Malformed whole stock CSV or precision conversion.") from None
    return result, audit, received


def validate_plan(window):
    """Admit both fixed timing profiles before any CSV parsing or acquisition."""
    _verify()
    scope.require_range(START, END, window)
    registered = _clock(window.registered_at, "Actual paired-plan registration")
    availability = {profile: _availability(DAYS[-1], profile)[1] for profile in CLOCK_PROFILES}
    _require(all(value <= registered for value in availability.values()),
        "Both paired research profiles must already be available at actual registration; no future acquisition plan or clipping.")
    return {"schema": 1, "kind": "fixed_paired_reference_plan_preflight", "rule_sha256": RULE_SHA256,
        "source_date_start": START, "source_date_end": END, "sessions": len(DAYS),
        "latest_assumed_availability": {profile: value.isoformat() for profile, value in availability.items()},
        "window": window.as_dict(), "scope_only": True, "network_calls": 0, "owner_operations": 0}


def validate_reference(stock_raw, stock_metadata, window):
    """Fully validate the fixed local reference before any quote acquisition."""
    validate_plan(window)
    registered = _clock(window.registered_at, "Actual study registration")
    stock, precision, received = _stock(stock_raw, stock_metadata, registered)
    return {"schema": 1, "kind": "independent_reference_preflight", "rule_sha256": RULE_SHA256,
        "source_date_start": START, "source_date_end": END, "option_sessions": len(DAYS),
        "stock_rows": len(stock), "stock_first_date": min(stock).isoformat(), "stock_last_date": max(stock).isoformat(),
        "stock_sha256": stock_metadata["sha256"], "stock_received_at": stock_metadata["received_at"],
        "stock_precision_sha256": hashlib.sha256(canonical(precision)).hexdigest(),
        "canonical_closes": {day.isoformat(): stock[day] for day in DAYS},
        "cross_provider_reconciliation_passed": False, "research_only": True,
        "execution_qualified": False, "deployable": False, "owner_operations": 0, "network_calls": 0}


def normalize(records, *, stock_raw, stock_metadata, window, clock_profile):
    """Return a NEW independent counterfactual dataset/audit; mutate nothing."""
    validate_plan(window)
    registered = _clock(window.registered_at, "Actual study registration")
    _require(type(clock_profile) is str and clock_profile in CLOCK_PROFILES, "Choose an explicit fixed clock profile.")
    _require(type(records) is list and len(records) == len(DAYS), "The complete fixed125-session cohort is required once, in order.")
    stock, precision, stock_received = _stock(stock_raw, stock_metadata, registered)
    source_records, rows, receipts, used, quote_count = [], [], [stock_received], 0, 0
    for day, record in zip(DAYS, records):
        _require(type(record) is dict and set(record) == RECORD_KEYS and record["source_date"] == day.isoformat(), "Exact consecutive fixed source records required; no sorting or repair.")
        expiry = expiry_for(day)
        _require(record["chain_url"] == source.chain_url(day, expiry), "Chain URL must prove actual KO day and the predeclared monthly expiry.")
        body = record["chain_raw"]
        _require(type(body) is bytes and 0 < len(body) <= MAX_RAW, "Each original chain requires1byte..2MiB.")
        used += len(body)
        _require(used <= MAX_REFERENCES, "Aggregate chain-reference budget exceeded.")
        received = _clock(record["received_at"], "Actual chain receipt")
        _require(received <= registered, "Actual chain receipt is future of registration.")
        source._historical(day, received)
        parsed, warning, partial = source._chain(body, day, expiry)
        _require({quote["type"] for quote in parsed} == {"call", "put"}, "Both preregistered option types must actually be supplied.")
        original = source._json(body)[1 if warning is not None else 0:]
        _require(len(parsed) == len(original), "All actual quote rows must survive exactly once.")
        quotes = []
        for quote, supplied in zip(parsed, original):
            volume, interest = supplied.get("volume"), supplied.get("open_interest")
            options_data._integer(volume); options_data._integer(interest)
            quotes.append({**quote, "multiplier": 100, "volume": volume, "open_interest": interest})
        quotes.sort(key=lambda row: row["contract_id"])
        quote_count += len(quotes)
        _require(quote_count <= options_data.MAX_QUOTES, "Canonical quote-count bound exceeded.")
        observed, available = _availability(day, clock_profile)
        rows.append({"observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "underlying_symbol": ROOT, "underlying_price": stock[day], "quotes": quotes})
        source_records.append({"source_date": day.isoformat(), "fixed_expiry": expiry.isoformat(),
            "chain_url": record["chain_url"], "chain_sha256": hashlib.sha256(body).hexdigest(),
            "chain_bytes": len(body), "chain_received_at": record["received_at"], "quote_rows": len(quotes),
            "partial_warning": warning, "partial_counts": partial})
        receipts.append(received)
    actual_manifest = {"chain_records": source_records, "stock_metadata": dict(stock_metadata)}
    source_sha = hashlib.sha256(canonical(actual_manifest)).hexdigest()
    dataset = {"schema": 1, "evidence_kind": "historical_quotes", "as_of": window.registered_at, "currency": "USD",
        "source": {"provider": "OnClick KO date-only options / independent unknown-basis reported-close research",
            "url": source.ORIGIN+"/options/", "sha256": source_sha, "retrieved_at": max(receipts).isoformat(),
            "usage": "Private counterfactual research only. Independent reported daily close generation; price basis unknown and cross-provider reconciliation FAILED. Explicit8decimalHALFEVEN reference conversion; actual option prices/sizes unchanged. Historical event/publication clocks and standard100terms unverified. No owner-source replacement or paper adoption.",
            "quote_time_basis": "assumed_eod", "independently_verified": False}, "snapshots": rows}
    quality = options_data.validate_dataset(dataset, registered, research_symbols=(ROOT,))
    scope.require_option_dataset(dataset, window)
    audit = {"schema": 1, "kind": "independent_reported_close_KO_counterfactual_normalization", "rule_sha256": RULE_SHA256,
        "clock_profile": clock_profile, "window": window.as_dict(), "source_date_start": START, "source_date_end": END,
        "sessions": len(rows), "quotes": quote_count, "raw_chain_reference_bytes": used,
        "source_manifest": actual_manifest, "source_manifest_sha256": source_sha,
        "source_manifest_hash_kind": "original_chain_receipts_and_whole_independent_stock_metadata_without_assumed_clocks",
        "stock_precision_profile": "plain_decimal_to8places_ROUND_HALF_EVEN_context80_max_abs_error0.000000005",
        "stock_precision_rows": precision, "stock_precision_sha256": hashlib.sha256(canonical(precision)).hexdigest(),
        "stock_price_basis": "unknown_reported_daily_close", "cross_provider_reconciliation_passed": False,
        "owner_source_replaced": False, "option_prices_or_sizes_rounded": False,
        "dataset_sha256": hashlib.sha256(options_data.canonical(dataset)).hexdigest(), "quality": quality,
        "actual_quote_event_clock": None, "historical_publication_clock_verified": False,
        "observed_clock_assumed": True, "availability_clock_assumed": True, "actual_receipts_remapped": False,
        "research_only": True, "execution_qualified": False, "deployable": False, "automatic_promotion": False,
        "owner_operations": 0, "network_calls": 0, "model_calls": 0,
        "limitations": ["125sessions fromMay4throughOct29, not12months of captured option history. Same dates for both profiles, withOct30 excluded before quote acquisition for future availability.",
            "Whole independent stock series has unknown price basis and failed cross-provider reconciliation; not owner-source evidence.",
            "One predeclared monthly expiry per day and actual partial strikes cannot establish a complete options universe.",
            "Real date-only prices are not verified historical publication clocks or guaranteed fills; next-session0935 is sensitivity, not production replication.",
            "Neither profile qualifies live paper operation, model promotion, source replacement or a profitable strategy."]}
    _verify()
    return dataset, audit
