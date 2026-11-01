"""Pure metadata inventory; never grants source, entry or account authority.

The host supplies a projection of an already verified schema4 account. Catalog
identities and provenance flags remain declarations, not independently checked
quotes, file existence, current eligibility or support for another paper root.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
import hashlib
import json
import re

MAX_ROOTS, MAX_WINDOWS = 16, 32
MAX_INDEX_BYTES, MAX_OUTPUT_BYTES = 64 * 1024, 32 * 1024
ROOT_KEYS = {"symbol", "windows", "dated_spot"}
WINDOW_KEYS = {"catalog_path", "catalog_sha256", "completion_sha256", "start", "end", "sessions",
               "side_sizes_verified", "quote_clock_verified", "raw_price_basis_verified", "standard_terms_verified"}
PROVENANCE_FIELDS = ("side_sizes_verified", "quote_clock_verified", "raw_price_basis_verified", "standard_terms_verified")
SPOT_KEYS = {"price", "source_date", "received_at", "source_sha256"}
PAPER_KEYS = {"account_id", "schema", "symbol", "source_id", "source_enabled", "policy_id", "cash",
              "monthly_committed", "position_present", "pending_present", "hold_present", "context"}
CONTEXT_KEYS = {"status", "source_session", "published_at", "label"}
CONTEXT_STATUSES = ("inactive", "unavailable", "missing", "stale", "held", "stress_ready", "not_stress")
LABELS = ("stress_unwind", "recovery_growth", "mixed_neutral", "insufficient_history")
RULE = {"schema": 1, "id": "offline-options-paper-readiness-inventory-v1",
        "limits": {"roots": 16, "windows": 32, "index_bytes": 65536, "output_bytes": 32768},
        "scope": "Declared metadata only; no file/provider verification, selection, funding, activation or entry permission.",
        "configured_paper": "Only the existing schema4 F onclick_f_eod_v1 stress policy may be described as configured. Runtime permissions and full causal entry gates remain external.",
        "risk": "One existing shared500 monthly gross commitment,100 all-in entry and one fully paid long put; no added funding, reset or new per-root books.",
        "research": "Windows are separate declared coverage, possibly overlapping; envelope dates never prove continuous coverage, executable sizes, raw basis, terms or current feed access.",
        "spot": "Absent means current_price_unmeasured. A supplied dated spot remains a saved observation, not live price or established current eligibility."}


class ReadinessError(ValueError):
    """Invalid optional inventory input; the host must not turn this into a financial hold."""


def _require(condition, message):
    if not condition:
        raise ReadinessError(message)


def _encoded(value, maximum):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        _require(nodes <= 4096 and depth <= 8, "Readiness metadata exceeds its structural bound.")
        if type(item) is dict:
            _require(len(item) <= 64 and all(type(k) is str and len(k) <= 64 for k in item), "Use bounded string metadata keys.")
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            _require(len(item) <= 128, "Readiness metadata list is unbounded.")
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            _require(len(item) <= 1024, "Readiness metadata text is unbounded.")
        elif type(item) is int:
            _require(abs(item) <= 10**9, "Readiness metadata integer is unbounded.")
        else:
            _require(item is None or type(item) is bool, "Use ordinary JSON metadata without floats or custom objects.")
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ReadinessError("Readiness metadata must be bounded ordinary JSON.") from None
    _require(len(raw) <= maximum, "Readiness metadata exceeds its encoded byte bound.")
    return raw


RULE_SHA256 = hashlib.sha256(_encoded(RULE, MAX_OUTPUT_BYTES)).hexdigest()


def _verify_rule():
    _require(hashlib.sha256(_encoded(RULE, MAX_OUTPUT_BYTES)).hexdigest() == RULE_SHA256,
             "The fixed readiness inventory rule changed.")


def _clock(value):
    if type(value) is str:
        _require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})", value) is not None,
                 "Use a bounded aware ISO clock with at most six fractional digits.")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ReadinessError("Use an aware ISO clock.") from None
    _require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None,
             "Use an aware ISO clock.")
    return value.astimezone(timezone.utc)


def _day(value):
    _require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None, "Use an ISO source date.")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ReadinessError("Use a valid source date.") from None
    _require(1999 <= result.year <= 2035, "Source date is outside the reviewed calendar range.")
    return result


def _money(value, maximum="1000000000"):
    _require(type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,8})?", value) is not None,
             "Use a bounded plain nonnegative decimal string.")
    result = Decimal(value)
    _require(result <= Decimal(maximum), "Readiness amount exceeds its bound.")
    return result


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _path(value):
    _require(type(value) is str and 1 <= len(value) <= 128 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) is not None
             and p not in (".", "..") for p in value.split("/")), "Use a bounded canonical relative catalog descriptor.")


def validate_index(index, *, checked_at):
    """Validate declarations only; no catalog, source or financial state is opened."""
    _verify_rule()
    now = _clock(checked_at)
    _encoded(index, MAX_INDEX_BYTES)
    _require(type(index) is dict and set(index) == {"schema", "roots"} and type(index["schema"]) is int and index["schema"] == 1,
             "Use the exact schema1 readiness index.")
    _require(type(index["roots"]) is list and len(index["roots"]) <= MAX_ROOTS, "Readiness index supports at most16 roots.")
    seen, descriptors, windows = set(), {}, 0
    for root in index["roots"]:
        _require(type(root) is dict and set(root) == ROOT_KEYS and type(root["symbol"]) is str and
                 re.fullmatch(r"[A-Z]{1,6}", root["symbol"]) is not None and root["symbol"] not in seen,
                 "Use unique explicit standard ASCII root descriptors.")
        seen.add(root["symbol"])
        _require(type(root["windows"]) is list and len(root["windows"]) <= MAX_WINDOWS, "Use bounded source windows.")
        root_windows = set()
        for window in root["windows"]:
            windows += 1
            _require(windows <= MAX_WINDOWS and type(window) is dict and set(window) == WINDOW_KEYS, "Readiness index supports at most32 exact window descriptors.")
            _path(window["catalog_path"])
            _require(_hash(window["catalog_sha256"]) and _hash(window["completion_sha256"]), "Source descriptors require SHA256 identities.")
            start, end = _day(window["start"]), _day(window["end"])
            _require(start <= end <= now.date() and type(window["sessions"]) is int and
                     1 <= window["sessions"] <= min(10000, (end - start).days + 1), "Source window dates/session count are inconsistent.")
            _require(all(type(window[k]) is bool for k in PROVENANCE_FIELDS), "Provenance fields must be exact declared booleans.")
            identity = (window["catalog_sha256"], window["completion_sha256"])
            _require(window["catalog_path"] not in descriptors or descriptors[window["catalog_path"]] == identity,
                     "One catalog descriptor cannot claim conflicting identities.")
            descriptors[window["catalog_path"]] = identity
            key = (window["catalog_path"], window["start"], window["end"])
            _require(key not in root_windows, "Duplicate root/window descriptors are not additional coverage.")
            root_windows.add(key)
        spot = root["dated_spot"]
        if spot is not None:
            _require(type(spot) is dict and set(spot) == SPOT_KEYS and _hash(spot["source_sha256"]), "Use the exact dated spot descriptor.")
            _require(_money(spot["price"], "1000000") > 0, "Dated spot must be positive.")
            received = _clock(spot["received_at"])
            _require(_day(spot["source_date"]) <= received.date() and received <= now, "Dated spot is future or received before its source date.")
    _verify_rule()
    return deepcopy(index)


def load_index(raw, *, checked_at):
    """Decode a bounded optional file already read by the host's path guard."""
    _require(type(raw) in (bytes, str), "Readiness index requires ordinary JSON bytes or text.")
    try:
        encoded = raw.encode() if type(raw) is str else raw
        _require(0 < len(encoded) <= MAX_INDEX_BYTES, "Readiness index exceeds64KiB.")
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, "Duplicate readiness JSON fields are rejected.")
                result[key] = value
            return result
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ReadinessError("Nonfinite readiness JSON is rejected.")))
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as error:
        if isinstance(error, ReadinessError):
            raise
        raise ReadinessError("Readiness index must be bounded strict JSON.") from None
    return validate_index(value, checked_at=checked_at)


def _paper(paper, now):
    if paper is None:
        return None
    _encoded(paper, 8192)
    _require(type(paper) is dict and set(paper) == PAPER_KEYS and type(paper["schema"]) is int and paper["schema"] == 4 and
             paper["symbol"] == "F" and paper["source_id"] == "onclick_f_eod_v1" and paper["policy_id"] == "f_stress_breakdown_put_v1",
             "Supply only the verified existing schema4 F account projection, or None.")
    _require(type(paper["account_id"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", paper["account_id"]) is not None,
             "Use a bounded existing account ID.")
    _require(all(type(paper[k]) is bool for k in ("source_enabled", "position_present", "pending_present", "hold_present")),
             "Paper flags must be exact booleans.")
    _money(paper["cash"])
    _money(paper["monthly_committed"], "500")
    context = paper["context"]
    _require(type(context) is dict and set(context) == CONTEXT_KEYS and context["status"] in CONTEXT_STATUSES,
             "Use the exact bounded F context projection.")
    _require(context["label"] is None or context["label"] in LABELS, "Unknown context label.")
    if context["source_session"] is None:
        _require(context["published_at"] is None and context["label"] is None, "An absent context cannot claim a label or publication.")
    else:
        published = _clock(context["published_at"])
        _require(_day(context["source_session"]) <= published.date() and published <= now, "Context projection has a future source/publication.")
    if context["status"] == "stress_ready":
        _require(context["source_session"] is not None and context["label"] == "stress_unwind", "Stress context projection is inconsistent.")
    return deepcopy(paper)


def build_readiness(index=None, *, paper=None, checked_at):
    """Describe declared research and the existing F source; authorize nothing."""
    _verify_rule()
    now = _clock(checked_at)
    supplied = index is not None
    index = {"schema": 1, "roots": []} if index is None else validate_index(index, checked_at=now)
    account = _paper(paper, now)
    configured = account is not None
    roots = []
    for root in sorted(index["roots"], key=lambda value: value["symbol"]):
        windows = sorted(root["windows"], key=lambda value: (value["start"], value["end"], value["catalog_path"]))
        active = configured and root["symbol"] == "F"
        blockers = ["full_runtime_and_causal_entry_checks_remain_required"] if active else ["unsupported_active_paper_root", "no_qualified_current_options_source"]
        if active and not account["source_enabled"]:
            blockers.append("configured_source_disabled")
        if active and account["hold_present"]:
            blockers.append("existing_account_hold")
        spot = root["dated_spot"]
        roots.append({"symbol": root["symbol"], "declared_window_count": len(windows), "declared_windows": deepcopy(windows),
                      "coverage_envelope_start": min((w["start"] for w in windows), default=None),
                      "coverage_envelope_end": max((w["end"] for w in windows), default=None),
                      "continuous_history_established": False, "catalogs_verified_by_this_module": False,
                      "provenance_flags_are_declarations": True,
                      "current_price_status": "dated_spot_supplied" if spot else "current_price_unmeasured",
                      "dated_spot": deepcopy(spot), "below_100_at_saved_spot": _money(spot["price"]) < 100 if spot else None,
                      "current_eligibility_established": False, "asset_type_inferred_from_symbol": False,
                      "configured_active_paper_root": active,
                      "current_options_source": "configured_experimental_date_only_F" if active else "not_qualified_for_this_owner_account",
                      "blockers": blockers, "entry_authorized": False, "execution_qualified": False})
    with localcontext(Context(prec=40)):
        remaining = format(Decimal("500") - _money(account["monthly_committed"]), "f") if account else None
    result = {"schema": 1, "kind": "offline_options_paper_readiness", "rule_sha256": RULE_SHA256,
              "checked_at": now.isoformat(), "inventory_status": "configured" if supplied else "unconfigured",
              "declared_root_count": len(roots), "declared_window_count": sum(len(r["declared_windows"]) for r in roots),
              "configured_active_paper_roots": ["F"] if configured else [],
              "active_paper": {"account_id": account["account_id"] if account else None,
                               "schema": 4 if configured else None, "source_id": account["source_id"] if account else None,
                               "policy_id": account["policy_id"] if account else None,
                               "source_enabled": account["source_enabled"] if account else False,
                               "context": deepcopy(account["context"]) if account else None,
                               "quote_scope": "experimental_date_only_partial_chain" if account else "not_supplied",
                               "runtime_permissions_and_full_entry_gates_required": True,
                               "entry_authorized": False, "execution_qualified": False},
              "shared_risk": {"monthly_gross_commitment_cap": "500", "all_in_entry_cap": "100", "maximum_positions": 1,
                              "instrument": "one_fully_paid_standard_100_long_put", "cash": account["cash"] if account else None,
                              "monthly_committed": account["monthly_committed"] if account else None,
                              "monthly_remaining": remaining, "position_present": account["position_present"] if account else False,
                              "pending_present": account["pending_present"] if account else False,
                              "hold_present": account["hold_present"] if account else False,
                              "risk_view_only_not_accounting_qualification": True,
                              "new_funding": "0", "monthly_budget_reset": False, "additional_accounts_created": 0},
              "roots": roots,
              "limitations": ["Catalog pins, window coverage and provenance flags are declared metadata, not source verification or additional paper-feed support.",
                              "Window envelopes may contain gaps or overlap; no continuous history or summed independent capital/PnL is established.",
                              "A dated spot is saved evidence only; current share-price eligibility, asset type and standard deliverables are not inferred.",
                              "Only the existing F source can be described as configured, and remains an experimental date-only partial-chain proxy.",
                              "This optional inventory never affects existing position management, exits, source holds, pauses or financial authority."],
              "financial_authority": False, "entry_authorized": False, "source_activation_authorized": False,
              "additional_active_paper_roots_supported": False, "selection_performed": False,
              "model_calls": 0, "network_calls": 0, "paper_operations": 0}
    _encoded(result, MAX_OUTPUT_BYTES)
    _verify_rule()
    return result
