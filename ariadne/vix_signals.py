"""Offline descriptive VIX index studies, never option prices or paper fills.

An index archive downloaded today has unknown historical publication times. Its
dated closes support retrospective descriptions only. No network, filesystem,
model, optimization, trading-account or process-control action is performed.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import re

from .qqq_data import CALENDAR_START, CALENDAR_END, is_session, session_dates

MAX_PAYLOAD = 2_000_000
MAX_ROWS = 15000
MAX_REPORT = 8 * 1024 * 1024
HISTORY_PAGE = "https://www.cboe.com/tradable-products/vix/vix-historical-data"
PERMISSION_URL = "https://www.cboe.com/terms"
SOURCE_URLS = {symbol: "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/" + symbol + "_History.csv"
               for symbol in ("VIX", "VIX9D")}
OFFICIAL_VIX_URL = SOURCE_URLS["VIX"]
PREREGISTRATION_SHA256 = "01dc93f5799f58a6dc35c318ae5d5eecdfcc735be17ac0f7ec1671e58a943027"
RULE = {
    "id": "ariadne-vix-index-signal-study-v1", "schema": 1,
    "preregistration_sha256": PREREGISTRATION_SHA256,
    "preregistered_at": "2026-10-30T20:57:06Z",
    "strategy_order": ["vix_shock_breakout_call_v1", "vix_spike_fade_put_v1", "vix_short_term_stress_call_v1"],
    "conditions": {
        "vix_shock_breakout_call_v1": "VIX[t]>max(VIX[t-20:t]) AND VIX[t]>=1.10*VIX[t-1]",
        "vix_spike_fade_put_v1": "VIX[t-1]>=25 AND VIX[t]<=0.90*VIX[t-1] AND VIX[t]<mean(VIX[t-5:t])",
        "vix_short_term_stress_call_v1": "VIX9D[t]/VIX[t]>1.05 AND all(VIX9D[i]/VIX[i]<=1 for i in t-5:t)"},
    "lookbacks": "Exclude current observation; consecutive reviewed regular exchange sessions; no filling gaps",
    "outcomes": "Next1/5 regular-session VIX CLOSE changes in index points and percent; no option price/return or tradable P&L",
    "split": "Chronological first70%/final30% of reviewed source-span sessions; descriptive historical comparison, not untouched holdout",
    "calendar": {"reference": "ariadne.qqq_data reviewed regular US-equity session calendar", "start": CALENDAR_START, "end": CALENDAR_END},
    "availability": "Current historical download is retrospective; dated source rows do not establish historical provider availability",
    "selection": "All fixed hypotheses reported in registered order; no optimization, ranking or winner selection",
}


class VixSignalsError(ValueError):
    """Malformed or insufficiently identified index-study evidence."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


RULE_SHA256 = hashlib.sha256(canonical(RULE)).hexdigest()
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_VALUE = re.compile(r"(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,8})?\Z")
_ROW_KEYS = {"date", "open", "high", "low", "close"}
_SOURCE_KEYS = {"provider", "url", "raw_sha256", "bytes", "retrieved_at", "source_published_at", "permission_url", "permission_scope", "official_link_page", "historical_availability"}
_HISTORY_KEYS = {"schema", "kind", "evidence_kind", "symbol", "source", "rows", "history_sha256"}


def _clock(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if type(value) is str else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, AttributeError):
        raise VixSignalsError("Use an aware, valid source timestamp.") from None


def _day(value):
    try:
        if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
        if not 1990 <= result.year <= CALENDAR_END:
            raise ValueError
        return result
    except ValueError:
        raise VixSignalsError("Index dates must be valid ISO dates within 1990 through the reviewed calendar end.") from None


def _number(value):
    if type(value) is not str or len(value) > 16 or not _VALUE.fullmatch(value):
        raise VixSignalsError("Index observations require plain positive decimal strings.")
    number = Decimal(value)
    if not Decimal(0) < number <= Decimal(1000):
        raise VixSignalsError("Index observation exceeds its positive supported range.")
    return number


def _text(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _row(value, latest):
    if type(value) is not dict or set(value) != _ROW_KEYS:
        raise VixSignalsError("A daily index row has exactly date/open/high/low/close.")
    day = _day(value["date"])
    if day > latest:
        raise VixSignalsError("An index observation cannot follow its retrieval date.")
    numbers = {key: _number(value[key]) for key in ("open", "high", "low", "close")}
    # Cboe's archive contains historical OPEN values outside its HIGH/LOW.
    # Preserve and report them; this preregistration uses CLOSE only. Never
    # silently repair prices or use these OPEN values as executable fills.
    if not numbers["low"] <= numbers["close"] <= numbers["high"]:
        raise VixSignalsError("Index close/high/low bounds are inconsistent.")
    return day


def load_history(raw, *, source_url=None, retrieved_at, symbol="VIX", source_published_at=None,
                 permission_url=PERMISSION_URL, now=None):
    """Parse the fixed official OHLC CSV, preserving date-only retrospective provenance.

    ``source_published_at`` identifies this file snapshot only, never availability
    of its historical rows. Raw archives must remain private under source terms.
    """
    if type(symbol) is not str or symbol not in SOURCE_URLS:
        raise VixSignalsError("Only official VIX and VIX9D daily index histories are supported.")
    source_url = SOURCE_URLS[symbol] if source_url is None else source_url
    if source_url != SOURCE_URLS[symbol] or permission_url != PERMISSION_URL:
        raise VixSignalsError("Use the fixed officially linked index source and its reviewed permission URL.")
    fetched = _clock(retrieved_at)
    clock = datetime.now(timezone.utc) if now is None else _clock(now)
    published = None if source_published_at is None else _clock(source_published_at)
    if fetched > clock or published is not None and published > fetched:
        raise VixSignalsError("Publication must not follow retrieval, and retrieval must not be in the future.")
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PAYLOAD:
        raise VixSignalsError("Provide at most2 MB of original index CSV bytes.")
    try:
        decoded = raw.decode("utf-8-sig")
        if any(ord(char) < 32 and char not in "\n\r" for char in decoded):
            raise ValueError
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        if next(reader) != ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]:
            raise ValueError
        rows = []
        previous = None
        for values in reader:
            if len(values) != 5 or len(rows) >= MAX_ROWS:
                raise ValueError
            if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", values[0]):
                raise ValueError
            day = datetime.strptime(values[0], "%m/%d/%Y").date()
            row = dict(zip(("date", "open", "high", "low", "close"), (day.isoformat(), *values[1:])))
            _row(row, fetched.date())
            if previous is not None and day <= previous:
                raise ValueError
            rows.append(row)
            previous = day
        if not rows:
            raise ValueError
    except (UnicodeError, ValueError, csv.Error, OverflowError):
        raise VixSignalsError("Expected ordered unique dated official OHLC rows with positive decimals and consistent close/high/low.") from None
    source = {"provider": "Cboe", "url": source_url, "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "bytes": len(raw), "retrieved_at": fetched.isoformat(),
              "source_published_at": None if published is None else published.isoformat(),
              "permission_url": permission_url, "permission_scope": "personal_noncommercial_one_copy_no_redistribution",
              "official_link_page": HISTORY_PAGE, "historical_availability": "unknown"}
    value = {"schema": 1, "kind": "vix_index_history", "evidence_kind": "retrospective_index_history", "symbol": symbol,
             "source": source, "rows": rows}
    value["history_sha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return value


def _validate(history, symbol):
    if type(history) is not dict or set(history) != _HISTORY_KEYS or history.get("schema") != 1 or type(history.get("schema")) is not int:
        raise VixSignalsError("Use a complete normalized index-history object.")
    if history["kind"] != "vix_index_history" or history["evidence_kind"] != "retrospective_index_history" or history["symbol"] != symbol:
        raise VixSignalsError("Index history identity or evidence kind disagrees with the study.")
    source = history["source"]
    if type(source) is not dict or set(source) != _SOURCE_KEYS:
        raise VixSignalsError("Complete fixed source provenance is required.")
    if (source["provider"] != "Cboe" or source["url"] != SOURCE_URLS[symbol] or source["permission_url"] != PERMISSION_URL
            or source["official_link_page"] != HISTORY_PAGE or source["historical_availability"] != "unknown"
            or source["permission_scope"] != "personal_noncommercial_one_copy_no_redistribution"
            or type(source["raw_sha256"]) is not str or not _HEX.fullmatch(source["raw_sha256"])
            or type(source["bytes"]) is not int or not 1 <= source["bytes"] <= MAX_PAYLOAD):
        raise VixSignalsError("Unsupported source identity, permissions or raw evidence digest.")
    fetched = _clock(source["retrieved_at"])
    if source["source_published_at"] is not None and _clock(source["source_published_at"]) > fetched:
        raise VixSignalsError("File publication cannot follow retrieval.")
    rows = history["rows"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ROWS:
        raise VixSignalsError("Provide bounded nonempty index rows.")
    previous = None
    for row in rows:
        day = _row(row, fetched.date())
        if previous is not None and day <= previous:
            raise VixSignalsError("Index dates must increase strictly without duplicates.")
        previous = day
    if (type(history["history_sha256"]) is not str or not _HEX.fullmatch(history["history_sha256"])
            or hashlib.sha256(canonical({key: history[key] for key in history if key != "history_sha256"})).hexdigest() != history["history_sha256"]):
        raise VixSignalsError("Normalized history changed after evidence binding.")
    return fetched


def _outcome(values, sessions, offset, horizon):
    stop = offset + horizon
    if stop >= len(sessions):
        return {"status": "pending_future_sessions", "index_point_change": None, "index_percent_change": None}
    sequence = sessions[offset:stop + 1]
    if any(day not in values for day in sequence):
        return {"status": "missing_index_observation", "index_point_change": None, "index_percent_change": None}
    before, after = values[sequence[0]], values[sequence[-1]]
    return {"status": "observed", "session": sequence[-1].isoformat(),
            "index_point_change": _text((after-before).quantize(Decimal("0.000001"))),
            "index_percent_change": _text(((after/before-1)*100).quantize(Decimal("0.000001")))}


def _stats(events, horizon):
    outcomes = [event["next_"+str(horizon)+"_session"] for event in events]
    observed = [outcome for outcome in outcomes if outcome["status"] == "observed"]
    changes = [Decimal(item["index_percent_change"]) for item in observed]
    return {"observations": len(observed), "pending": sum(item["status"] == "pending_future_sessions" for item in outcomes),
            "missing": sum(item["status"] == "missing_index_observation" for item in outcomes),
            "index_increased": sum(value > 0 for value in changes), "index_decreased": sum(value < 0 for value in changes),
            "index_unchanged": sum(value == 0 for value in changes),
            "mean_index_percent_change": None if not changes else _text((sum(changes)/len(changes)).quantize(Decimal("0.000001")))}


def study(history, *, vix9d_history=None, as_of=None):
    """Describe all frozen signals and later INDEX changes; never trading returns.

    Date-only index observations have ``observed_at=None`` and explicit
    ``observed_session``. No historical publication or paper decision is invented.
    """
    with localcontext() as context:
        context.prec = 40
        return _study(history, vix9d_history=vix9d_history, as_of=as_of)


def _study(history, *, vix9d_history, as_of):
    fetched = _validate(history, "VIX")
    if vix9d_history is not None:
        fetched = max(fetched, _validate(vix9d_history, "VIX9D"))
    clock = fetched if as_of is None else _clock(as_of)
    if clock < fetched:
        raise VixSignalsError("A retrospective download cannot be presented as available before it was retrieved.")
    rows = history["rows"]
    reviewed = [row for row in rows if CALENDAR_START <= _day(row["date"]).year <= CALENDAR_END]
    regular = [row for row in reviewed if is_session(_day(row["date"]))]
    if not regular:
        raise VixSignalsError("No observed rows fall in the reviewed regular-session calendar.")
    sessions = session_dates(_day(regular[0]["date"]), _day(regular[-1]["date"]))
    values = {_day(row["date"]): _number(row["close"]) for row in regular}
    short_values = {} if vix9d_history is None else {_day(row["date"]): _number(row["close"]) for row in vix9d_history["rows"]
        if CALENDAR_START <= _day(row["date"]).year <= CALENDAR_END and is_session(_day(row["date"]))}
    split_at = len(sessions)*7//10
    output = {key: {"signal_count": 0, "evaluated_sessions": 0, "blocked_sessions": 0, "events": []}
              for key in RULE["strategy_order"]}
    latest_conditions = {key: None for key in output}
    for offset, day in enumerate(sessions):
        for position, identifier in enumerate(RULE["strategy_order"]):
            warmup = 21 if position == 0 else 6
            window = sessions[max(0, offset-warmup+1):offset+1]
            current = output[identifier]
            eligible = len(window) == warmup and all(item in values for item in window)
            if position == 2:
                eligible = eligible and all(item in short_values for item in window)
            if not eligible:
                current["blocked_sessions"] += 1
                continue
            current["evaluated_sessions"] += 1
            close, previous = values[day], values[window[-2]]
            if position == 0:
                active = close > max(values[item] for item in window[:-1]) and close >= Decimal("1.10")*previous
            elif position == 1:
                active = previous >= 25 and close <= Decimal("0.90")*previous and close < sum(values[item] for item in window[:-1])/5
            else:
                active = short_values[day] > Decimal("1.05")*close and all(short_values[item] <= values[item] for item in window[:-1])
            if offset == len(sessions)-1:
                latest_conditions[identifier] = active
            if not active:
                continue
            current["signal_count"] += 1
            event = {"observed_session": day.isoformat(), "observed_at": None, "historical_available_at": None,
                     "evidence_kind": "retrospective_index_signal", "index_close": _text(close),
                     "split": "first70" if offset < split_at else "final30",
                     "next_1_session": _outcome(values, sessions, offset, 1), "next_5_session": _outcome(values, sessions, offset, 5)}
            if position == 2:
                event["vix9d_close"] = _text(short_values[day])
            current["events"].append(event)
    for current in output.values():
        current["next_1_session"] = _stats(current["events"], 1)
        current["next_5_session"] = _stats(current["events"], 5)
        current["split_counts"] = {key: sum(event["split"] == key for event in current["events"]) for key in ("first70", "final30")}
    missing = [day.isoformat() for day in sessions if day not in values]
    overlap = [day for day in sessions if day in values and day in short_values]
    short_start, short_end = (min(short_values), max(short_values)) if short_values else (None, None)
    paired_missing = [day.isoformat() for day in sessions if short_start is not None and short_start <= day <= short_end and day not in short_values]
    latest_day = sessions[-1]
    coverage = {"raw_vix_rows": len(rows), "raw_start": rows[0]["date"], "raw_end": rows[-1]["date"],
                "reviewed_start": sessions[0].isoformat(), "reviewed_end": latest_day.isoformat(),
                "expected_regular_sessions": len(sessions), "observed_regular_sessions": len(values),
                "calendar_unreviewed_rows": len(rows)-len(reviewed), "nonregular_rows_excluded": len(reviewed)-len(regular),
                "open_outside_high_low_count": sum(not _number(row["low"]) <= _number(row["open"]) <= _number(row["high"]) for row in rows),
                "open_high_low_used_in_signals": False,
                "missing_vix_session_count": len(missing), "missing_vix_session_sample": missing[:20],
                "vix9d_supplied": vix9d_history is not None, "paired_observed_sessions": len(overlap),
                "paired_start": None if not overlap else overlap[0].isoformat(), "paired_end": None if not overlap else overlap[-1].isoformat(),
                "missing_vix9d_session_count_in_its_span": len(paired_missing), "missing_vix9d_session_sample": paired_missing[:20],
                "split_session": sessions[split_at].isoformat(), "split_basis": "all reviewed regular sessions in VIX source span",
                "no_gap_filling": True, "source_history_revisions_unknown": True}
    latest_signals = {key: None if not value["events"] else value["events"][-1]["observed_session"] for key, value in output.items()}
    blocked = "VIX9D history not supplied; no substitute/interpolation" if vix9d_history is None else (
        "Identical-session VIX/VIX9D current and prior five observations incomplete" if latest_conditions[RULE["strategy_order"][2]] is None else None)
    summary = {"kind": "retrospective_index_signal_study", "source_date": latest_day.isoformat(), "observed_session": latest_day.isoformat(),
               "observed_at": None, "index_close": _text(values[latest_day]),
               "vix9d_close": None if latest_day not in short_values else _text(short_values[latest_day]),
               "signal_counts": {key: value["signal_count"] for key, value in output.items()},
               "latest_signal_sessions": latest_signals, "latest_conditions": latest_conditions,
               "vix9d_blocked_reason": blocked, "option_backtest_status": "blocked_missing_qualified_option_quotes",
               "tradable_pnl": None, "historical_availability": "unknown", "forward_execution_qualified": False}
    report = {"schema": 1, "kind": "vix_index_signal_study", "research_only": True, "execution_qualified": False,
              "research_gate": "retrospective_index_only_not_option_backtest",
              "evidence_kind": "retrospective_index_history", "as_of": clock.isoformat(),
              "rule_sha256": RULE_SHA256, "preregistration_sha256": PREREGISTRATION_SHA256, "rules": deepcopy(RULE),
              "sources": {"VIX": deepcopy(history["source"]), "VIX9D": None if vix9d_history is None else deepcopy(vix9d_history["source"])},
              "input_history_sha256": {"VIX": history["history_sha256"], "VIX9D": None if vix9d_history is None else vix9d_history["history_sha256"]},
              "coverage": coverage, "summary": summary, "strategies": output,
              "limitations": ["Index changes are not tradable returns, option returns, strategy profit, probability of profit or execution qualification.",
                              "Historical file publication/availability per observation is unknown; retrieval today does not permit earlier paper decisions.",
                              "Importer-supplied source URL/retrieval declarations are preserved; a SHA binds the imported bytes, not independent authentication of a vendor.",
                              "Calendar-excluded or missing observations are never interpolated or forward-filled; date-only closes have no invented timestamp.",
                              "Some original historical OPEN values lie outside HIGH/LOW; retained and counted, never repaired or used in these CLOSE-only signals.",
                              "Chronological split is descriptive historical analysis, not an untouched holdout; no hypothesis was selected by outcome.",
                              "Raw Cboe data is a private personal-use copy, not licensed for redistribution or commercial service."]}
    if len(canonical(report)) > MAX_REPORT or len(canonical(summary)) > 4096 or len(canonical(coverage)) > 4096:
        raise VixSignalsError("Index-study output exceeds its bounded report envelope.")
    return report
