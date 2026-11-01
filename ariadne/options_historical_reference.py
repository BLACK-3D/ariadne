"""Historical KO observations with unknown side sizes retained as null.

This separate research schema never changes the live date-only parser or money
rules. Null is unknown liquidity, never zero, volume, OI, or permission to fill.
"""
from datetime import date
from decimal import Decimal
import hashlib
import re

from . import options_data
from . import options_reference_projection as projection
from . import options_reference_history as v1
from . import options_root_eod_data as source
from . import research_window as scope

ROOT, START, END, DAYS, CLOCK_PROFILES = projection.ROOT, projection.START, projection.END, projection.DAYS, projection.CLOCK_PROFILES
expiry_for, canonical = projection.expiry_for, projection.canonical
RULE = {"schema": 1, "id": "KO125-historical-nullable-side-size-reference-v1",
    "projection_rule_sha256": projection.RULE_SHA256, "reference_precision_rule_sha256": v1.RULE_SHA256,
    "symbol": ROOT, "start": START, "end": END, "sessions": 125,
    "quote_schema": "Uniform actual historical date-only KO observations. Required bid_size and ask_size values may be actual bounded integers or null. Missing optional volume/open_interest remains null. Never coerce unknown to zero or infer side sizes from volume/OI.",
    "identity": "Exact fixed source date, predeclared strictly later monthly expiry, actual KO root/OSI economic terms, allowed fields and finite exact monetary conversion. Reject duplicate IDs, crossed quotes, unsupported fields, bool/float/string sizes and malformed partial warnings; no quote quarantine.",
    "canonical": "Every quote retained and sorted only for canonical representation. Final/per-body canonical validation owns full reference-economic bounds. Null side sizes remain nonexecutable under the UNCHANGED research/owner liquidity gates.",
    "reference": "Reuse unchanged versioned parent-scope projection and unchangedV1 eight-decimalHALFEVEN reported-close conversion; global calendar failure, unknown basis and failed cross-provider reconciliation remain explicit.",
    "clocks": "Same125dates and two fixed counterfactual availability profiles. Actual receipts preserved; no backdating or false historical event/publication clocks. Entire paired plan passes existing date/future guards.",
    "source_binding": "Actual-source hash binds historical-parser policy, full original parent metadata/hash, derived scope projection/hash and every original quote URL/body hash/receipt. Profile-independent actual-source hash; hypothetical timing remains separate.",
    "authority": "Observation import only. Does not qualify paper trading, replace an owner source, add funding, alter risk/fees, resume an uncertain attempt, or authorize any execution.",
    "research_only": True, "execution_qualified": False, "deployable": False,
    "automatic_promotion": False, "network_calls": 0, "model_calls": 0, "owner_operations": 0}
RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class HistoricalReferenceError(ValueError):
    pass


def _require(value, message):
    if not value: raise HistoricalReferenceError(message)


def _verify():
    projection._verify()
    _require(hashlib.sha256(canonical(RULE)).hexdigest() == RULE_SHA256
        and projection.RULE_SHA256 == RULE["projection_rule_sha256"]
        and v1.RULE_SHA256 == RULE["reference_precision_rule_sha256"]
        and (ROOT, START, END, len(DAYS)) == (RULE["symbol"], RULE["start"], RULE["end"], RULE["sessions"]),
        "The additive historical observation or frozen reference contract changed.")


def validate_plan(window):
    _verify()
    return {**projection.validate_plan(window), "kind": "historical_nullable_sizes_plan_preflight",
        "rule_sha256": RULE_SHA256, "projection_rule_sha256": projection.RULE_SHA256}


def validate_reference(stock_raw, stock_metadata, window):
    _verify()
    return {**projection.validate_reference(stock_raw, stock_metadata, window),
        "kind": "historical_nullable_sizes_reference_preflight", "rule_sha256": RULE_SHA256,
        "projection_rule_sha256": projection.RULE_SHA256}


def parse_chain(raw, day, expiry):
    """Return FULL canonical quote dictionaries, original warning, counts.

    The caller must also run the canonical dataset's economic-upper-bound
    check with its actual reference before admitting a response or next GET.
    """
    _verify()
    day, expiry = v1._day(day), v1._day(expiry)
    _require(day in DAYS and expiry == expiry_for(day), "Use the exact fixed historical source day and monthly expiry.")
    _require(type(raw) is bytes and 0 < len(raw) <= v1.MAX_RAW, "Actual historical response requires1byte..2MiB.")
    rows = source._json(raw)
    _require(type(rows) is list and 1 <= len(rows) <= source.MAX_QUOTES+1, "Bounded original historical chain rows required.")
    warning, counts = None, None
    if type(rows[0]) is dict and set(rows[0]) == {"warning"}:
        warning = rows[0]["warning"]
        match = source.base._WARNING.fullmatch(warning) if type(warning) is str and len(warning) <= 512 else None
        _require(match is not None and 0 < int(match[1]) < int(match[2]) <= source.MAX_QUOTES,
            "Unknown or inconsistent actual partial-chain warning.")
        counts = {"returned_strikes": int(match[1]), "total_strikes": int(match[2])}
        rows = rows[1:]
    quotes, seen = [], set()
    for row in rows:
        _require(type(row) is dict and source.base._ROW_REQUIRED <= set(row) <= source.base._ROW_ALLOWED
            and row["ticker"] == ROOT, "Unsupported raw historical fields or KO root.")
        label = row["date"]
        if type(label) is str and re.fullmatch(r"[0-9]{8}", label): label = label[:4]+"-"+label[4:6]+"-"+label[6:]
        _require(source._day(label) == day and source._day(row["expiration"]) == expiry, "Actual historical source date/expiry mismatch.")
        match = source._OSI.fullmatch(row["osi-key"]) if type(row["osi-key"]) is str else None
        _require(match is not None, "Adjusted/unknown raw KO OSI root rejected.")
        identifier = ROOT+"".join(match.groups())
        parsed_expiry, kind, strike = source._contract(identifier)
        _require(parsed_expiry == expiry and row["type"] == kind
            and source._number(row["strike"], positive=True, maximum="99999.999") == strike,
            "Raw historical OSI economic terms mismatch.")
        _require(identifier not in seen, "Duplicate actual historical contract rejected, never dropped.")
        seen.add(identifier)
        bid, ask = source._number(row["bid"]), source._number(row["ask"])
        _require(Decimal(bid) <= Decimal(ask), "Crossed actual quote rejected, never repaired.")
        values = {"bid_size": row["bid_size"], "ask_size": row["ask_size"],
            "volume": row.get("volume"), "open_interest": row.get("open_interest")}
        for value in values.values(): options_data._integer(value)
        quotes.append({"contract_id": identifier, "expiry": expiry.isoformat(), "type": kind, "strike": strike,
            "bid": bid, "ask": ask, "multiplier": 100, **values})
    _require(quotes and {row["type"] for row in quotes} == {"call", "put"}, "Both actual option types are required; no chain narrowing or replacement.")
    quotes.sort(key=lambda row: row["contract_id"])
    return quotes, warning, counts


def normalize(records, *, stock_raw, stock_metadata, window, clock_profile):
    validate_plan(window)
    _require(type(clock_profile) is str and clock_profile in CLOCK_PROFILES, "Choose an explicit fixed historical timing profile.")
    registered = v1._clock(window.registered_at, "Actual historical import registration")
    derived, derived_metadata, projection_audit = projection._project(stock_raw, stock_metadata, window)
    stock, precision, stock_received = v1._stock(derived, derived_metadata, registered)
    _require(type(records) is list and len(records) == len(DAYS), "All125 fixed historical source records required once in order.")
    rows, manifests, receipts, used, total, unknown_bid, unknown_ask = [], [], [stock_received], 0, 0, 0, 0
    for day, record in zip(DAYS, records):
        _require(type(record) is dict and set(record) == v1.RECORD_KEYS and record["source_date"] == day.isoformat(),
            "Exact ordered fixed historical source records required; no sorting or clipping.")
        expiry = expiry_for(day)
        _require(record["chain_url"] == source.chain_url(day, expiry), "Fixed historical quote URL/date/expiry mismatch.")
        received = v1._clock(record["received_at"], "Actual historical quote receipt")
        _require(received <= registered, "Actual quote receipt cannot follow registration.")
        source._historical(day, received)
        body = record["chain_raw"]
        quotes, warning, counts = parse_chain(body, day, expiry)
        used += len(body); total += len(quotes)
        _require(used <= v1.MAX_REFERENCES and total <= options_data.MAX_QUOTES, "Historical aggregate byte/quote budget exceeded.")
        bid_null = sum(row["bid_size"] is None for row in quotes)
        ask_null = sum(row["ask_size"] is None for row in quotes)
        unknown_bid += bid_null; unknown_ask += ask_null
        observed, available = v1._availability(day, clock_profile)
        rows.append({"observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "underlying_symbol": ROOT, "underlying_price": stock[day], "quotes": quotes})
        manifests.append({"source_date": day.isoformat(), "fixed_expiry": expiry.isoformat(),
            "chain_url": record["chain_url"], "chain_sha256": hashlib.sha256(body).hexdigest(), "chain_bytes": len(body),
            "chain_received_at": record["received_at"], "quote_rows": len(quotes), "partial_warning": warning,
            "partial_counts": counts, "unknown_bid_size_quotes": bid_null, "unknown_ask_size_quotes": ask_null})
        receipts.append(received)
    actual_manifest = {"historical_parser_rule_sha256": RULE_SHA256, "projection": projection_audit,
        "chain_records": manifests, "derived_stock_metadata": derived_metadata}
    source_sha = hashlib.sha256(canonical(actual_manifest)).hexdigest()
    dates = ",".join(row["source_date"] for row in projection_audit["global_calendar_anomalies"][:8])
    dataset = {"schema": 1, "evidence_kind": "historical_quotes", "as_of": window.registered_at, "currency": "USD",
        "source": {"provider": "KO historical nullable-size observations / explicit independent reference scope projection",
            "url": source.ORIGIN+"/options/", "sha256": source_sha, "retrieved_at": max(receipts).isoformat(),
            "usage": "Private historical SCOPE PROJECTION counterfactual only. Actual null side sizes remain UNKNOWN, never executable liquidity or zero; unchanged strict money gates skip entries/hold exits. Parent calendar quality="
                +projection_audit["global_source_calendar_quality"]+"; unusable outside anomalies="+str(projection_audit["global_calendar_anomaly_count"])
                +"; dates="+(dates or "none observed")+". Derived125-row CSV is NOT an HTTP response. Unknown stock basis/failed reconciliation, original decimal text then frozenV1 eight-decimalHALFEVEN reference conversion. Actual quote prices/sizes unchanged; event/publication clocks assumed, standard100terms unverified. No paper adoption.",
            "quote_time_basis": "assumed_eod", "independently_verified": False}, "snapshots": rows}
    quality = options_data.validate_dataset(dataset, registered, research_symbols=(ROOT,))
    scope.require_option_dataset(dataset, window)
    audit = {"schema": 1, "kind": "KO_historical_nullable_sizes_reference_counterfactual", "rule_sha256": RULE_SHA256,
        "projection_rule_sha256": projection.RULE_SHA256, "reference_precision_rule_sha256": v1.RULE_SHA256,
        "clock_profile": clock_profile, "window": window.as_dict(), "source_date_start": START, "source_date_end": END,
        "sessions": len(rows), "quotes": total, "raw_chain_reference_bytes": used,
        "source_manifest": actual_manifest, "source_manifest_sha256": source_sha,
        "source_manifest_hash_kind": "historical_parser_policy_parent_raw_metadata_projection_derivedSHA_and_actual_quote_receipts",
        "projection": projection_audit, "parent_stock_metadata": dict(stock_metadata),
        "parent_raw_sha256": stock_metadata["sha256"], "derived_stock_sha256": derived_metadata["sha256"],
        "global_source_calendar_quality": projection_audit["global_source_calendar_quality"],
        "global_calendar_anomaly_count": projection_audit["global_calendar_anomaly_count"],
        "global_calendar_anomalies": projection_audit["global_calendar_anomalies"],
        "stock_precision_rows": precision, "stock_precision_sha256": hashlib.sha256(canonical(precision)).hexdigest(),
        "stock_precision_profile": "unchangedV1_plain_decimal_to8places_ROUND_HALF_EVEN_context80_max_abs_error0.000000005",
        "stock_price_basis": "unknown_reported_daily_close", "cross_provider_reconciliation_passed": False,
        "unknown_bid_size_quotes": unknown_bid, "unknown_ask_size_quotes": unknown_ask,
        "unknown_side_sizes_preserved_as_null": True, "side_size_values_coerced_or_inferred": False,
        "option_prices_or_sizes_rounded": False, "owner_source_replaced": False,
        "dataset_sha256": hashlib.sha256(options_data.canonical(dataset)).hexdigest(), "quality": quality,
        "actual_quote_event_clock": None, "historical_publication_clock_verified": False,
        "observed_clock_assumed": True, "availability_clock_assumed": True, "actual_receipts_remapped": False,
        "research_only": True, "execution_qualified": False, "deployable": False, "automatic_promotion": False,
        "owner_operations": 0, "network_calls": 0, "model_calls": 0,
        "limitations": ["125May4..Oct29 source sessions, not12months of captured option history; both profiles use identical predeclared dates.",
            "Historical null side sizes are missing execution evidence, never permission to fill or infer liquidity from volume/OI.",
            "Original parent calendar failure and failed cross-provider reconciliation remain; no out-of-cohort source row enters financial/context data.",
            "Partial fixed-monthly-expiry observations and assumed historical availability cannot establish a profitable or execution-qualified strategy."]}
    _verify()
    return dataset, audit
