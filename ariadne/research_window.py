"""Admission of NEW trading studies to a frozen twelve-calendar-month window.

This is a scope guard, not quote authentication, a financial kernel, an archive
repair, or permission to replay work. Original clocks and datasets are never
rewritten. Existing full schema/hash/session validation remains mandatory.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re

from .qqq_data import exchange_day, last_closed_session


RULE = {
    "schema": 1,
    "id": "new-trading-study-last12-calendar-months-v1",
    "anchor": "Actual aware registration clock, frozen once; New York calendar date.",
    "oldest": "Inclusive same calendar date one year earlier; February 29 clamps to February 28.",
    "newest": "Inclusive latest finalized US-equity session at registration, including the existing 15-minute finalization lag.",
    "admission": "Every declared option-study date and actual option-observation NY date must be inside the window. Reject entire out-of-window or mixed cohorts; never silently clip, join, repair or forward-fill.",
    "clocks": "Preserve observed_at and available_at. Require observed<=available<=retrieved_at<=dataset_as_of<=registration. Actual receipt/as_of may follow the final market session but cannot be future relative to registration.",
    "archives": "Selection and every chunk observation bound must lie within the same window; actual streamed chunks also need dataset admission and existing immutable archive/schema/session checks.",
    "context": "Older benchmark stock prices may support causal context warmup only. They cannot create old option decisions, fills, trade metrics or new old-period study results.",
    "legacy": "Completed historical reports and frozen receipt/financial replay remain readable; this rule does not alter prior engines, ledgers, limits, clocks or holds.",
    "source_authentication": False,
    "financial_authority": False,
    "network_calls": 0,
    "model_calls": 0,
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class ResearchWindowError(ValueError):
    """A new study is outside its registered scope or uses invalid clocks."""


def _verify():
    if hashlib.sha256(_canonical(RULE)).hexdigest() != RULE_SHA256:
        raise ResearchWindowError("The registered research-window policy changed.")


def _day(value, name):
    if type(value) is not str or not _DATE.fullmatch(value):
        raise ResearchWindowError(name+" requires a strict ISO calendar-date string.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ResearchWindowError(name+" is not a valid calendar date.") from None


def _clock(value, name):
    if type(value) is str:
        if not 19 <= len(value) <= 64:
            raise ResearchWindowError(name+" requires a bounded aware ISO clock.")
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ResearchWindowError(name+" requires a valid aware ISO clock.") from None
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ResearchWindowError(name+" requires an aware datetime or ISO clock; naive/bool values are rejected.")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ResearchWindowError(name+" is outside supported aware-clock bounds.") from None


@dataclass(frozen=True)
class ResearchWindow:
    registered_at: str
    anchor_day: str
    oldest: str
    newest: str
    rule_sha256: str

    def as_dict(self):
        _window(self)
        return {"schema": 1, "kind": "rolling_12_calendar_month_research_window",
                "registered_at": self.registered_at, "anchor_day": self.anchor_day,
                "oldest": self.oldest, "newest": self.newest,
                "market_timezone": "America/New_York", "calendar_months": 12,
                "inclusive": True, "rule_sha256": self.rule_sha256,
                "pre_window_option_rows_allowed": False,
                "older_benchmark_role": "causal_context_warmup_only",
                "source_clocks_rewritten": False}


def _derive(clock):
    try:
        anchor = exchange_day(clock)
        try:
            oldest = anchor.replace(year=anchor.year-1)
        except ValueError:
            oldest = anchor.replace(year=anchor.year-1, day=28)
        newest = last_closed_session(clock)
    except (ValueError, OverflowError):
        raise ResearchWindowError("Registration is outside the reviewed US-equity calendar.") from None
    return ResearchWindow(clock.isoformat(), anchor.isoformat(), oldest.isoformat(),
                          newest.isoformat(), RULE_SHA256)


def derive_window(now=None):
    """Freeze scope once. ``now`` is trusted host/test injection, not CLI input."""
    _verify()
    clock = datetime.now(timezone.utc) if now is None else _clock(now, "Registration")
    return _derive(clock)


def _window(value):
    _verify()
    if type(value) is not ResearchWindow:
        raise ResearchWindowError("Use the immutable host-derived research window.")
    clock = _clock(value.registered_at, "Registered window")
    if value != _derive(clock):
        raise ResearchWindowError("Research-window bounds or policy do not match their frozen registration clock.")
    return clock, _day(value.oldest, "Oldest study date"), _day(value.newest, "Newest study date")


def require_range(start, end, window):
    """Reject the entire declared range if any part falls outside the window."""
    _, oldest, newest = _window(window)
    first, last = _day(start, "Study start"), _day(end, "Study end")
    if last < first:
        raise ResearchWindowError("The declared study range is reversed.")
    if first < oldest or last > newest:
        raise ResearchWindowError("New studies must stay within "+oldest.isoformat()+" through "+newest.isoformat()
                                  +" inclusive; the entire supplied cohort is rejected, not clipped.")
    return {"start": start, "end": end, "scope_only": True, "window": window.as_dict()}


def _capture(value, window, name):
    if type(value) is not dict or type(value.get("source")) is not dict:
        raise ResearchWindowError(name+" needs original dataset/source clock fields.")
    registered, _, _ = _window(window)
    as_of = _clock(value.get("as_of"), name+" as_of")
    retrieved = _clock(value["source"].get("retrieved_at"), name+" retrieved_at")
    if retrieved > as_of or as_of > registered:
        raise ResearchWindowError(name+" retrieval/as_of is future relative to the frozen registration, or capture order is invalid.")
    return registered, as_of, retrieved


def require_option_dataset(dataset, window):
    """Check all option observation dates without mutating or authenticating them.

    Call this for EVERY checked archive chunk. The existing canonical quote
    validator still owns instrument, session, lag, size and economic checks.
    """
    registered, as_of, retrieved = _capture(dataset, window, "Option dataset")
    rows = dataset.get("snapshots")
    if type(rows) is not list or not 1 <= len(rows) <= 4000:
        raise ResearchWindowError("Date admission requires1-4000 bounded option snapshots.")
    first = last = None
    for row in rows:
        if type(row) is not dict:
            raise ResearchWindowError("Each option snapshot must retain its clock fields.")
        observed = _clock(row.get("observed_at"), "Option observed_at")
        available = _clock(row.get("available_at"), "Option available_at")
        if observed > available or available > retrieved or available > as_of or available > registered:
            raise ResearchWindowError("Option observation/publication/capture clocks are invalid or unavailable at registration.")
        try:
            day = exchange_day(observed).isoformat()
        except ValueError:
            raise ResearchWindowError("Option observation is outside the reviewed market calendar.") from None
        require_range(day, day, window)
        first = day if first is None else min(first, day)
        last = day if last is None else max(last, day)
    return {"snapshots_checked": len(rows), "first_observed_date": first,
            "last_observed_date": last, "scope_only": True, "window": window.as_dict()}


def require_archive_manifest(manifest, window):
    """Scope-check declared selection/chunk clocks; never clip or repair a source."""
    registered, _, retrieved = _capture(manifest, window, "Option archive")
    selection = manifest.get("selection")
    if type(selection) is not dict:
        raise ResearchWindowError("An archive requires an explicit selection range.")
    start, end = selection.get("start"), selection.get("end")
    require_range(start, end, window)
    first_day, last_day = _day(start, "Archive start"), _day(end, "Archive end")
    chunks = manifest.get("chunks")
    if type(chunks) is not list or not 1 <= len(chunks) <= 96:
        raise ResearchWindowError("Date admission requires1-96 bounded archive chunks.")
    previous = None
    for chunk in chunks:
        if type(chunk) is not dict:
            raise ResearchWindowError("Every archive chunk needs original observation bounds.")
        first = _clock(chunk.get("first_observed_at"), "Chunk first_observed_at")
        last = _clock(chunk.get("last_observed_at"), "Chunk last_observed_at")
        if first > last or last > retrieved or last > registered or previous is not None and first <= previous:
            raise ResearchWindowError("Archive chunk clocks are reversed, overlapping, future or unavailable at capture.")
        try:
            first_date, last_date = exchange_day(first), exchange_day(last)
        except ValueError:
            raise ResearchWindowError("Chunk observations are outside the reviewed market calendar.") from None
        require_range(first_date.isoformat(), last_date.isoformat(), window)
        if first_date < first_day or last_date > last_day:
            raise ResearchWindowError("Chunk observations fall outside the archive's declared selection.")
        previous = last
    return {"chunks_checked": len(chunks), "selection": {"start": start, "end": end},
            "scope_only": True, "window": window.as_dict()}
