"""Versioned, explicit125-session projection of one retained parent CSV.

The original generation and its calendar anomalies remain evidence. This does
not repair the original file, certify its basis, or replace an owner's source.
Only the declared125 actual source labels enter the unchanged V1 normalizer.
"""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
from urllib.parse import urlsplit

from . import options_reference_history as v1
from . import options_data
from . import research_window as scope
from .qqq_data import exchange_day, is_session

ROOT, START, END, DAYS, CLOCK_PROFILES = v1.ROOT, v1.START, v1.END, v1.DAYS, v1.CLOCK_PROFILES
expiry_for, canonical = v1.expiry_for, v1.canonical
RULE = {"schema": 1, "id": "KO125-explicit-parent-source-scope-projection-v1",
    "v1_normalization_rule_sha256": v1.RULE_SHA256,
    "symbol": ROOT, "start": START, "end": END, "sessions": 125,
    "parent": "Whole original CSV bytes/SHA/metadata retained separately, never relabeled as an HTTP response for the derived CSV. Validate all original rows for strict UTF8, bounded columns, exact parseable ascending unique dates, positive finite bounded plain-decimal closes and actual historical receipt chronology.",
    "calendar": "Any non-session source label inside inclusive START..END rejects the entire projection. Non-session rows strictly outside that range remain explicitly unusable anomalies, never option or context prices. Malformed, duplicate, future or invalid-price rows anywhere still reject; no quarantine of those failures.",
    "projection": "Predeclared125DAYS only, each required exactly once. Derived CSV has date,close header and LF lines, original YYYYMMDD labels and original close decimal TEXT unchanged. Every other parent row is excluded from financial/context data; full parent hash remains bound.",
    "precision": "Only unchanged V1 may perform its declared8decimalHALFEVEN conversion on the projected125rows. No earlier rounding, repair, cross-provider equality or source substitution.",
    "source_binding": "Final actual-source manifest binds parent raw metadata/SHA, projection policy, derived CSV SHA, and V1 actual quote/derived-reference manifest. Same actual-source hash for both hypothetical timing profiles.",
    "quality": "Parent global source calendar is FAILED when any outside non-session row exists, with every such date/raw price/unusable role exposed. Projection does not make the parent globally valid or authenticate prices/basis/publication.",
    "research_only": True, "execution_qualified": False, "deployable": False,
    "paper_promotion": False, "network_calls": 0, "owner_operations": 0}
RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()


class ReferenceProjectionError(ValueError):
    pass


def _require(value, message):
    if not value: raise ReferenceProjectionError(message)


def _verify():
    v1._verify()
    _require(hashlib.sha256(canonical(RULE)).hexdigest() == RULE_SHA256
        and v1.RULE_SHA256 == RULE["v1_normalization_rule_sha256"]
        and (ROOT, START, END, len(DAYS)) == (RULE["symbol"], RULE["start"], RULE["end"], RULE["sessions"]),
        "The new projection or unchanged V1 contract changed.")


def validate_plan(window):
    _verify()
    result = v1.validate_plan(window)
    return {**result, "kind": "fixed_scope_projection_plan_preflight", "rule_sha256": RULE_SHA256,
            "v1_normalization_rule_sha256": v1.RULE_SHA256}


def _project(stock_raw, stock_metadata, window):
    validate_plan(window)
    registered = v1._clock(window.registered_at, "Actual projection registration")
    _require(type(stock_raw) is bytes and 0 < len(stock_raw) <= v1.MAX_RAW, "Retain whole original parent CSV within2MiB.")
    _require(type(stock_metadata) is dict and set(stock_metadata) == v1.STOCK_KEYS, "Exact original parent provenance fields required.")
    for key, maximum in (("provider", 128), ("url", 2048), ("usage", 2000)):
        _require(type(stock_metadata[key]) is str and 1 <= len(stock_metadata[key]) <= maximum and stock_metadata[key].isprintable(), "Invalid original parent "+key+" provenance.")
    _require(stock_metadata["basis"] == "unknown_reported_daily_close", "Parent price basis remains unknown.")
    for key in ("sha256", "reconciliation_sha256"):
        _require(type(stock_metadata[key]) is str and v1._HASH.fullmatch(stock_metadata[key]), "Pin exact original parent SHA256 fields.")
    _require(hashlib.sha256(stock_raw).hexdigest() == stock_metadata["sha256"], "Parent whole raw SHA256 changed.")
    try:
        url = urlsplit(stock_metadata["url"])
        _require(url.scheme == "https" and url.hostname and not url.username and not url.password and not url.fragment,
            "Original parent URL requires credential-free HTTPS provenance.")
        url.port
    except ValueError: raise ReferenceProjectionError("Invalid parent HTTPS provenance.") from None
    received = v1._clock(stock_metadata["received_at"], "Actual original parent receipt")
    _require(received <= registered, "Parent actual receipt cannot follow registration.")
    first, last = date.fromisoformat(START), date.fromisoformat(END)
    selected, anomalies, previous, count, parent_first = {}, [], None, 0, None
    try:
        text = stock_raw.decode("utf-8-sig")
        _require("\x00" not in text, "Original CSV contains NUL bytes.")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fields = reader.fieldnames
        _require(fields is not None and 2 <= len(fields) <= 100 and len(set(fields)) == len(fields)
            and {"date", "close"} <= set(fields)
            and all(type(field) is str and 1 <= len(field) <= 64 and field.isascii() and field.isprintable() for field in fields),
            "Whole parent CSV requires strict unique date/close headers and bounded columns.")
        for index, row in enumerate(reader):
            _require(index < v1.MAX_STOCK_ROWS and None not in row
                and all(type(value) is str and len(value) <= 4096 for value in row.values()), "Invalid original parent row/column bounds.")
            label, price = row["date"], row["close"]
            _require(bool(v1._DATE.fullmatch(label)), "Parent source date requires exact YYYYMMDD text.")
            try: day = date(int(label[:4]), int(label[4:6]), int(label[6:]))
            except ValueError: raise ReferenceProjectionError("Malformed parent source calendar date.") from None
            _require(previous is None or previous < day, "All original parent dates must be unique and ascending.")
            _require(day < exchange_day(received) and day < exchange_day(registered), "Future or nonhistorical parent rows cannot be excluded into acceptance.")
            _require(bool(v1._PRICE.fullmatch(price)), "Every parent close, even unused, requires original bounded plain-decimal text.")
            with localcontext() as context:
                context.prec = 80; context.clear_traps()
                value = Decimal(price)
                _require(value.is_finite() and 0 < value <= Decimal("1000000"), "Every parent close must be finite positive and bounded.")
            regular = is_session(day)
            if first <= day <= last:
                _require(regular, "Non-session source row inside the declared option range rejects the entire projection.")
                _require(day in DAYS, "In-range source row is outside the exact predeclared session list.")
                selected[day] = (label, price)
            elif not regular:
                anomalies.append({"source_date": day.isoformat(), "raw_date": label, "raw_close": price,
                    "role": "unusable_out_of_scope_source_anomaly", "reason": "not_regular_session",
                    "used_as_option_price": False, "used_as_context_price": False})
            parent_first = day if parent_first is None else parent_first
            previous, count = day, index+1
        _require(count > 0 and len(selected) == len(DAYS) and all(day in selected for day in DAYS),
            "All125 fixed source days need actual parent rows; no filling or range clipping.")
    except (UnicodeError, csv.Error, InvalidOperation, OverflowError):
        raise ReferenceProjectionError("Malformed whole original parent CSV.") from None
    derived = ("date,close\n"+"".join(selected[day][0]+","+selected[day][1]+"\n" for day in DAYS)).encode("ascii")
    derived_sha = hashlib.sha256(derived).hexdigest()
    quality = "FAILED" if anomalies else "NO_NONSESSION_ROWS_OBSERVED_NOT_AUTHENTICATED"
    projection = {"schema": 1, "kind": "explicit125session_original_text_scope_projection",
        "rule_sha256": RULE_SHA256, "parent_raw_sha256": stock_metadata["sha256"], "parent_raw_bytes": len(stock_raw),
        "parent_stock_metadata": dict(stock_metadata), "parent_rows": count,
        "parent_first_date": parent_first.isoformat(), "parent_last_date": previous.isoformat(),
        "projected_rows": len(DAYS), "derived_csv_sha256": derived_sha, "derived_csv_bytes": len(derived),
        "derived_csv_is_http_response": False, "original_parent_bytes_changed": False,
        "global_source_calendar_quality": quality, "global_calendar_anomaly_count": len(anomalies),
        "global_calendar_anomalies": anomalies, "excluded_outside_cohort_rows": count-len(DAYS),
        "excluded_rows_used_as_context": False, "original_close_text_preserved_in_projection": True,
        "price_basis": "unknown_reported_daily_close", "cross_provider_reconciliation_passed": False,
        "research_only": True, "execution_qualified": False, "deployable": False, "owner_operations": 0}
    derived_metadata = {**stock_metadata, "sha256": derived_sha,
        "provider": "DERIVED125-row scope projection of retained original KO CSV",
        "usage": "DERIVED CSV, not an HTTP response. Exact original decimal text on125 predeclared source sessions only. Original whole parent CSV and unknown basis retained; outside anomalies unusable; no paper adoption."}
    return derived, derived_metadata, projection


def validate_reference(stock_raw, stock_metadata, window):
    """Validate whole parent, explicit scope projection, then unchanged V1."""
    derived, metadata, projection = _project(stock_raw, stock_metadata, window)
    original_v1 = v1.validate_reference(derived, metadata, window)
    return {**original_v1, "kind": "scope_projection_reference_preflight", "rule_sha256": RULE_SHA256,
        "v1_normalization_rule_sha256": v1.RULE_SHA256, "stock_sha256": stock_metadata["sha256"],
        "stock_rows": projection["parent_rows"], "stock_first_date": projection["parent_first_date"],
        "stock_last_date": projection["parent_last_date"], "derived_stock_sha256": metadata["sha256"],
        "projection": projection, "global_source_calendar_quality": projection["global_source_calendar_quality"],
        "global_calendar_anomaly_count": projection["global_calendar_anomaly_count"],
        "global_calendar_anomalies": projection["global_calendar_anomalies"]}


def normalize(records, *, stock_raw, stock_metadata, window, clock_profile):
    derived, metadata, projection = _project(stock_raw, stock_metadata, window)
    dataset, original_v1 = v1.normalize(records, stock_raw=derived, stock_metadata=metadata, window=window, clock_profile=clock_profile)
    actual_manifest = {"projection": projection, "v1_actual_source_manifest": original_v1["source_manifest"]}
    source_sha = hashlib.sha256(canonical(actual_manifest)).hexdigest()
    dates = ",".join(row["source_date"] for row in projection["global_calendar_anomalies"][:8])
    dataset["source"]["provider"] = "KO actual date-only options / explicit independent125session scope projection"
    dataset["source"]["sha256"] = source_sha
    dataset["source"]["usage"] = ("Private SCOPE PROJECTION counterfactual research, not owner-source replacement. Original parent calendar quality="
        +projection["global_source_calendar_quality"]+"; unusable out-of-scope calendar anomalies="+str(projection["global_calendar_anomaly_count"])
        +"; dates="+(dates or "none observed")+" (full anomaly audit retained). Derived125-row CSV is NOT an HTTP response: original decimal text preserved then unchangedV1 eight-decimalHALFEVEN conversion. Unknown stock basis and failed cross-provider reconciliation remain. Actual option prices/sizes unchanged; historical event/publication clocks assumed; no paper promotion.")
    quality = options_data.validate_dataset(dataset, v1._clock(window.registered_at, "Actual registration"), research_symbols=(ROOT,))
    scope.require_option_dataset(dataset, window)
    audit = {**original_v1, "kind": "explicit_scope_projection_KO_counterfactual_normalization",
        "rule_sha256": RULE_SHA256, "v1_normalization_rule_sha256": v1.RULE_SHA256,
        "source_manifest": actual_manifest, "source_manifest_sha256": source_sha,
        "source_manifest_hash_kind": "original_parent_raw_metadata_projection_policy_derivedSHA_and_V1_actual_quote_manifest",
        "projection": projection, "parent_stock_metadata": dict(stock_metadata),
        "parent_raw_sha256": stock_metadata["sha256"], "derived_stock_sha256": metadata["sha256"],
        "global_source_calendar_quality": projection["global_source_calendar_quality"],
        "global_calendar_anomaly_count": projection["global_calendar_anomaly_count"],
        "global_calendar_anomalies": projection["global_calendar_anomalies"],
        "dataset_sha256": hashlib.sha256(options_data.canonical(dataset)).hexdigest(), "quality": quality,
        "v1_dataset_sha256_before_provenance_wrapper": original_v1["dataset_sha256"],
        "limitations": original_v1["limitations"]+["This is a versioned source-scope projection. Whole parent global-calendar failure remains visible; no out-of-cohort row enters financial or context data."]}
    _verify()
    return dataset, audit
