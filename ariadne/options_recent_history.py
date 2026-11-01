"""One fixed recent KO research cohort, with explicit hypothetical clocks.

No data acquisition, files, accounts, paper transitions or source-clock repair.
Original dated quotes and receipts remain separate from TWO counterfactual
availability profiles. Neither profile authenticates historical publication or
replicates the owner's forward date-only execution/permission contract.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import urlencode

from . import options_root_eod_data as source
from . import options_data
from . import research_window as scope
from .qqq_data import session_dates, session_close_utc

ROOT = "KO"
START, END, EXPIRY = "2026-09-11", "2026-10-16", "2026-10-16"
STOCK_WINDOWS = (("2026-09-11", "2026-09-30"), ("2026-10-01", "2026-10-16"))
CLOCK_PROFILES = ("assumed_eod_plus_15m", "assumed_next_session_0935")
DAYS = tuple(session_dates(date.fromisoformat(START), date.fromisoformat(END)))
MAX_RECORDS, MAX_BYTES = 36, 64*1024**2
RECORD_KEYS = {"source_date", "received_at", "chain_raw", "chain_url",
               "stock_raw", "stock_received_at", "stock_url"}
RULE = {"schema": 1, "id": "recent-ko-fixed-expiry-research-normalization-v1",
    "symbol": ROOT, "start": START, "end": END, "sessions": 26, "fixed_expiry": EXPIRY,
    "stock_windows": [list(value) for value in STOCK_WINDOWS], "clock_profiles": list(CLOCK_PROFILES),
    "window_policy_sha256": scope.RULE_SHA256,
    "source_session_grid_policy_sha256": source.SESSION_GRID_POLICY_SHA256,
    "source": "Actual anonymous KO date-only chain bodies at the fixed existing API; one predeclared expiry, every supplied call/put/strike retained. Partial warnings and actual sizes retained; no future continuity screen.",
    "clocks": "Research observed_at is ASSUMED session close. Availability is explicitly ASSUMED close+15minutes OR next regular session09:35NewYork. Actual chain and stock receipt strings/hashes remain unchanged in the audit. Actual event/publication clocks UNKNOWN.",
    "capture": "All26source sessions required once in ascending source-date order. Actual receipt clocks must be historical and <=frozen study registration. Source capture/asof is never remapped to hypothetical availability.",
    "validation": "Existing root._chain and base._stock_sessions parsers unchanged; minute bodies at most15sessions, exact per-day or two declared window URLs, full grids validated before selecting actual closes. Canonical quote and last12month date checks mandatory. No rounding, missing rows, synthetic sizes, zero replacements or invalid-quote quarantine.",
    "optional_fields": "Preserve source volume/open_interest values if present; missing or null staysNone. Other source fields remain in original raw bytes, never invented for canonical output.",
    "selection": "Canonical contract-ID sorting is representation only; all supplied quotes retained. Source-date records are never reordered, clipped or repaired.",
    "scope": "Development counterfactuals only. Fixed expiry and partial chains do not represent every possible option. Delayed historical profile is a sensitivity, not a production-clock replica. No automatic promotion or owner operation.",
    "research_only": True, "deployable": False, "execution_qualified": False,
    "owner_operations": 0, "model_calls": 0, "network_calls": 0}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class RecentHistoryError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise RecentHistoryError(message)


def _verify():
    _require(hashlib.sha256(canonical(RULE)).hexdigest() == RULE_SHA256 and source.ROOT == ROOT
             and scope.RULE_SHA256 == RULE["window_policy_sha256"]
             and source.SESSION_GRID_POLICY_SHA256 == RULE["source_session_grid_policy_sha256"]
             and (ROOT, START, END, EXPIRY, len(DAYS)) == (RULE["symbol"], RULE["start"], RULE["end"], RULE["fixed_expiry"], RULE["sessions"])
             and [list(value) for value in STOCK_WINDOWS] == RULE["stock_windows"]
             and list(CLOCK_PROFILES) == RULE["clock_profiles"], "The fixed recent-source research contract changed.")


def stock_window_url(start, end):
    _require((start, end) in STOCK_WINDOWS, "Only the two fixed research stock windows are supported.")
    return source.ORIGIN+"/stock-data/v2/?"+urlencode({"ticker": ROOT, "from": start+"-09-30",
        "to": end+"-16-00", "extended": "false", "bar_size": "1m"})


def _stock_urls():
    result = {source.stock_url(day): (day,) for day in DAYS}
    for start, end in STOCK_WINDOWS:
        result[stock_window_url(start, end)] = tuple(session_dates(date.fromisoformat(start), date.fromisoformat(end)))
    return result


def _clock(value, name):
    _require(type(value) is str and 19 <= len(value) <= 64, name+" needs its original aware ISO string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, name+" must be aware.")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise RecentHistoryError(name+" requires a valid aware ISO clock.") from None


def _availability(day, profile):
    observed = session_close_utc(day)
    if profile == "assumed_eod_plus_15m":
        return observed, observed+timedelta(minutes=15)
    from .briefing import _tz
    following = session_dates(day+timedelta(days=1), day+timedelta(days=8))[0]
    # Reviewed US-equity sessions share Chicago/NY DST rules;08:35CT=09:35ET.
    available = datetime(following.year, following.month, following.day, 8, 35,
                         tzinfo=_tz("America/Chicago")).astimezone(timezone.utc)
    return observed, available


def normalize(records, *, window, clock_profile):
    """Return a canonical dataset and audit, leaving every input unchanged.

    Derive ``window`` at actual STUDY registration after data acquisition.
    Acquisition intent clocks must not substitute for later receipt clocks.
    """
    _verify()
    _require(type(clock_profile) is str and clock_profile in CLOCK_PROFILES, "Choose one explicit fixed research clock profile.")
    metadata = window.as_dict()
    scope.require_range(START, END, window)
    registered = _clock(window.registered_at, "Study registration")
    _require(type(records) is list and len(records) <= MAX_RECORDS and len(records) == len(DAYS),
             "The complete fixed26-session cohort is required; no partial or repeated dates.")
    allowed_stock, stock_cache, stock_bindings = _stock_urls(), {}, {}
    rows, evidence, receipts, used, quote_count = [], [], [], 0, 0
    for day, record in zip(DAYS, records):
        _require(type(record) is dict and set(record) == RECORD_KEYS and record["source_date"] == day.isoformat(),
                 "Use exact source records in the fixed consecutive source-date order; no sorting or repair.")
        _require(record["chain_url"] == source.chain_url(day, EXPIRY) and type(record["stock_url"]) is str
                 and record["stock_url"] in allowed_stock and day in allowed_stock[record["stock_url"]],
                 "Record URLs must bind the actual KO day, fixed expiry and allowed stock window.")
        for name in ("chain_raw", "stock_raw"):
            body = record[name]
            _require(type(body) is bytes and 0 < len(body) <= source.MAX_RAW, "Raw bodies require1byte..2MiB original bytes.")
            used += len(body)
        _require(used <= MAX_BYTES, "The complete raw input references exceed64MiB.")
        received, stock_received = _clock(record["received_at"], "Chain receipt"), _clock(record["stock_received_at"], "Stock receipt")
        _require(received <= registered and stock_received <= registered, "Actual source receipts cannot be future of study registration.")
        source._historical(day, received)
        source._historical(day, stock_received)
        chain_sha, stock_sha = hashlib.sha256(record["chain_raw"]).hexdigest(), hashlib.sha256(record["stock_raw"]).hexdigest()
        binding = (stock_sha, record["stock_received_at"])
        previous = stock_bindings.setdefault(record["stock_url"], binding)
        _require(previous == binding, "One original stock response URL must retain one exact body and receipt.")
        if stock_sha not in stock_cache:
            stock_cache[stock_sha] = source.base._stock_sessions(record["stock_raw"], details=True)
        stock = stock_cache[stock_sha]
        _require(tuple(stock) == allowed_stock[record["stock_url"]], "The whole original stock body must match its exact declared session window.")
        for actual_day in stock:
            source._historical(actual_day, stock_received)
        parsed, warning, partial = source._chain(record["chain_raw"], day, date.fromisoformat(EXPIRY))
        _require({quote["type"] for quote in parsed} == {"call", "put"},
                 "Both supplied option types are required in the fixed cohort; no replacement or narrowing.")
        original = source._json(record["chain_raw"])
        if warning is not None: original = original[1:]
        _require(len(original) == len(parsed), "Every actual quote must survive the trusted parser exactly once.")
        quotes = []
        for quote, supplied in zip(parsed, original):
            volume, interest = supplied.get("volume"), supplied.get("open_interest")
            options_data._integer(volume)
            options_data._integer(interest)
            quotes.append({**quote, "multiplier": 100, "volume": volume, "open_interest": interest})
        quotes.sort(key=lambda quote: quote["contract_id"])
        quote_count += len(quotes)
        _require(quote_count <= options_data.MAX_QUOTES, "The cohort exceeds the canonical quote-count bound.")
        observed, available = _availability(day, clock_profile)
        rows.append({"observed_at": observed.isoformat(), "available_at": available.isoformat(),
                     "underlying_symbol": ROOT, "underlying_price": stock[day]["underlying_close"], "quotes": quotes})
        evidence.append({"source_date": day.isoformat(), "chain_url": record["chain_url"],
                         "chain_sha256": chain_sha, "chain_bytes": len(record["chain_raw"]),
                         "chain_received_at": record["received_at"], "stock_url": record["stock_url"],
                         "stock_sha256": stock_sha, "stock_bytes": len(record["stock_raw"]),
                         "stock_received_at": record["stock_received_at"], "quote_rows": len(quotes),
                         "partial_warning": warning, "partial_counts": partial,
                         "actual_event_clock": None, "historical_publication_clock_verified": False,
                         "assumed_observed_at": observed.isoformat(), "assumed_available_at": available.isoformat(),
                         "stock_session_details": dict(stock[day])})
        receipts.extend((received, stock_received))
    raw_manifest = [{key: value for key, value in row.items() if not key.startswith("assumed_")}
                    for row in evidence]
    source_sha = hashlib.sha256(canonical(raw_manifest)).hexdigest()
    dataset = {"schema": 1, "evidence_kind": "historical_quotes", "as_of": window.registered_at, "currency": "USD",
        "source": {"provider": "OnClick actual KO date-only quotes; explicit research availability assumptions",
            "url": source.ORIGIN+"/options/", "sha256": source_sha,
            "retrieved_at": max(receipts).isoformat(),
            "usage": "Private educational counterfactual research. Actual capture clocks retained separately; event/publication clocks, rights, raw price basis and standard deliverables unverified. Partial fixed-expiry data; no promotion or raw redistribution.",
            "quote_time_basis": "assumed_eod", "independently_verified": False}, "snapshots": rows}
    quality = options_data.validate_dataset(dataset, registered, research_symbols=(ROOT,))
    scope.require_option_dataset(dataset, window)
    audit = {"schema": 1, "kind": "explicit_recent_ko_research_clock_normalization", "rule_sha256": RULE_SHA256,
        "clock_profile": clock_profile, "window": metadata, "source_date_start": START, "source_date_end": END,
        "fixed_expiry": EXPIRY, "sessions": len(rows), "quotes": quote_count, "raw_reference_bytes": used,
        "unique_stock_bodies_parsed": len(stock_cache), "record_sources": evidence,
        "source_manifest_sha256": source_sha, "source_manifest_hash_kind": "ordered_raw_receipts_and_parser_details_without_assumed_clocks",
        "profile_evidence_sha256": hashlib.sha256(canonical(evidence)).hexdigest(),
        "dataset_sha256": hashlib.sha256(options_data.canonical(dataset)).hexdigest(), "quality": quality,
        "actual_quote_event_clock": None, "historical_publication_clock_verified": False,
        "observed_clock_assumed": True, "availability_clock_assumed": True,
        "actual_receipts_remapped": False, "source_prices_rounded_or_repaired": False,
        "research_only": True, "execution_qualified": False, "deployable": False,
        "automatic_promotion": False, "owner_operations": 0, "network_calls": 0, "model_calls": 0,
        "limitations": ["Real supplied date-only prices are not known historical event/publication clocks or guaranteed fills.",
            "One fixed expiry and actual partial strikes cannot establish a complete option universe; vanished contracts hold studies.",
            "The next-session09:35 profile is sensitivity analysis, not the production source-day/receipt/permission envelope.",
            "Both profiles use unchanged raw prices/sizes but retain hypothetical availability; neither authorizes paper adoption."]}
    _verify()
    return dataset, audit
