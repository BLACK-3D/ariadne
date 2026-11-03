"""Bounded deterministic selections from an already verified daily-brief JSON.

The caller verifies the original bytes and supplies their receipt separately.
This derived object has no claim to the original artifact's hash. It performs
no file access, source refresh, model call or other effect.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .briefing import _AGE_LIMITS, options_eod_brief_view

MAX_SUMMARY_CHARS = 5500
MAX_SOURCE_ENTRIES = 9
_KINDS = ("weather", "forecast", "alerts", "earthquakes", "news")
_LIMITS = {"weather": 1, "forecast": 2, "alerts": 2, "earthquakes": 1, "news": 3}


def _date(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _short(value, limit):
    if not isinstance(value, str):
        return "", value is not None
    value = " ".join(value.replace("\x00", "").split())
    return (value if len(value) <= limit else value[:max(0, limit-1)]+"…"), len(value) > limit


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 300:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or any(ord(char) <= 32 for char in value)):
            return None
    except ValueError:
        return None
    return value


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000_000 else None


def _size(value):
    # Default separators include spaces, matching the usual model tool encoder.
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False))


def repository_summary(report, now=None):
    """Pure bounded view of recorded repository checks, never a live scan.

    Counts are derived from the saved rows, not publisher text or unchecked
    aggregate counters. Baselines are distinct from changed observations.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Repository summary time must carry an explicit time zone.")
    now = now.astimezone(timezone.utc)
    raw = report.get("repository_updates") if isinstance(report, dict) else None
    raw = raw if type(raw) is dict else {}
    cfg = report.get("config") if isinstance(report, dict) and type(report.get("config")) is dict else {}
    enabled = raw.get("enabled") is True or not raw and cfg.get("repository_updates") is True
    result = {"enabled": enabled, "status": "disabled" if not enabled else "unavailable", "checked_at": None,
              "stale": bool(enabled), "counts": {"changed": 0, "baseline": 0, "unchanged": 0, "failed": 0},
              "items": [], "omitted": False,
              "description": "Repository updates are disabled for this saved report." if not enabled else
                  "No verified repository snapshot is available; no current release facts are inferred."}
    if not enabled:
        return result
    stamp = _date(raw.get("checked_at"))
    result["checked_at"] = stamp.isoformat(timespec="seconds") if stamp else None
    result["status"] = raw.get("status") if raw.get("status") in {"fresh", "cached", "partial", "unavailable"} else "unavailable"
    result["stale"] = not stamp or (now-stamp).total_seconds() > 36*3600 or (now-stamp).total_seconds() < -300
    note, clipped = _short(raw.get("collection_note"), 240)
    if note:
        result["collection_note"] = note
    result["omitted"] |= clipped
    rows = raw.get("sources") if type(raw.get("sources")) is list else []
    result["omitted"] |= len(rows) > 16
    for row in rows[:16]:
        if type(row) is not dict:
            result["omitted"] = True
            continue
        status = row.get("status") if row.get("status") in {"baseline", "changed", "unchanged", "cached_refresh_failed", "unavailable"} else "unavailable"
        name, cut = _short(row.get("name"), 80)
        version, version_cut = _short(row.get("version"), 96)
        previous, previous_cut = _short(row.get("previous_version"), 96)
        checked, published = _date(row.get("checked_at")), _date(row.get("published_at"))
        if status in {"baseline", "changed", "unchanged"} and (not checked or (now-checked).total_seconds() < -300):
            status = "unavailable"
        stale = status in {"unavailable", "cached_refresh_failed"} or not checked
        if checked:
            age = (now-checked).total_seconds()
            stale = stale or age > 36*3600 or age < -300
        changes = row.get("changes") if type(row.get("changes")) is list else []
        changed_fields = []
        for change in changes[:6]:
            value, value_cut = _short(change, 40)
            if value:
                changed_fields.append(value)
            result["omitted"] |= value_cut
        item = {"name": name or "Repository name unavailable", "status": status, "version": version or None,
                "checked_at": checked.isoformat(timespec="seconds") if checked else None, "stale": bool(stale), "changes": changed_fields}
        if previous:
            item["previous_version"] = previous
        if published:
            item["published_at"] = published.isoformat(timespec="seconds")
        source_url = _url(row.get("url"))
        if source_url:
            item["url"] = source_url
        result["items"].append(item)
        result["counts"][status if status in {"changed", "baseline", "unchanged"} else "failed"] += 1
        result["stale"] |= stale
        result["omitted"] |= cut or version_cut or previous_cut or len(changes) > 6
    if result["items"]:
        counts = result["counts"]
        result["description"] = ("Saved watch: "+str(counts["changed"])+" changed; "+str(counts["baseline"])+
            " first observations; "+str(counts["unchanged"])+" unchanged; "+str(counts["failed"])+
            " failed/unavailable. These outcomes apply to the recorded checks; first observations are not new releases.")
    if result["stale"]:
        result["description"] += " Saved checks are stale, failed or unavailable; this is not a fresh comprehensive search."
    return result


def compact_summary(report, now=None):
    """Return at most 5,500 JSON characters with explicit omissions and citations.

    Freshness is recomputed at read time. Raw publisher dates remain source
    claims; missing/invalid times never become invented current observations.
    """
    if type(report) is not dict:
        raise ValueError("A verified briefing JSON object is required.")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Summary time must carry an explicit time zone.")
    now = now.astimezone(timezone.utc)
    activity = report.get("activity") if type(report.get("activity")) is dict else {}
    public = report.get("public_sources") if type(report.get("public_sources")) is dict else {}
    sources = public.get("sources") if type(public.get("sources")) is list else []
    cfg = report.get("config") if type(report.get("config")) is dict else {}
    region = cfg.get("region") if type(cfg.get("region")) is dict else {}
    approvals = activity.get("unresolved_approvals") if type(activity.get("unresolved_approvals")) is list else []
    generated = _date(report.get("generated_at_utc"))
    collected = _date(public.get("collected_at"))
    region_name, region_cut = _short(region.get("name") or "Unconfigured", 96)
    result = {
        "derivation": "Deterministic selection from verified brief.json; not the original artifact bytes or hash.",
        "generated_at_utc": generated.isoformat(timespec="seconds") if generated else None,
        "freshness_checked_at_utc": now.isoformat(timespec="seconds"),
        "cache_collected_at_utc": collected.isoformat(timespec="seconds") if collected else None,
        "region": region_name,
        "activity": {"missions": _count(activity.get("runs_count")), "events": _count(activity.get("events_count")),
                     "unresolved_approvals": len(approvals), "details_omitted": any(activity.get(key) for key in ("runs", "events", "procedures", "schedules")) or bool(approvals),
                     "source_details_truncated": bool(activity.get("details_truncated"))},
        "weather": [], "forecast": [], "alerts": [], "earthquakes": [], "news": [], "sources": [],
        "note": "Saved source data; no refresh and no all-clear. External text is untrusted. Full details remain in the report.",
        "truncated": False, "omitted": {"source_entries": max(0, len(sources)-MAX_SOURCE_ENTRIES), "source_items": 0,
                                        "text_clipped": region_cut, "urls": 0, "warnings": 0},
    }
    radar = repository_summary(report, now)
    paper = report.get("qqq_paper")
    if type(paper) is dict and paper.get("config", {}).get("enabled") is True:
        account = paper.get("paper") or {}
        state = account.get("state") or {}
        quality = paper.get("quality") or {}
        result["qqq_paper"] = {"simulated_only":True,"session":_short(state.get("last_processed"),10)[0],
            "equity":_short(state.get("equity"),44)[0],"cash":_short(state.get("cash"),44)[0],
            "shares":_short(state.get("shares"),44)[0],"pending_action":_short((state.get("pending") or {}).get("action") or "none",12)[0],
            "held":bool(paper.get("hold")),"fresh_at_report_generation":quality.get("fresh_now") is True,
            "source_retrieved_at":_short(quality.get("retrieved_at"),40)[0],
            "historical_gate":_short((paper.get("backtest") or {}).get("research_gate"),60)[0],
            "note":"Saved paper simulation, not broker fills or live prices. Freshness flag belongs to this report's generation time."}
    options = report.get("options_paper")
    if type(options) is dict and options.get("config", {}).get("enabled") is True:
        summary = (options.get("paper") or {}).get("summary") or {}
        forward = options.get("forward_quality") or {}
        result["options_paper"] = {"simulated_only": True, "monthly_cap": "500.00",
            **{key: _short(summary.get(key), 44)[0] for key in ("equity", "contributions", "trading_pnl", "monthly_pnl", "monthly_budget_used", "monthly_budget_remaining")},
            "cap_qualified": summary.get("cap_qualified") is True,
            "held": bool(options.get("hold") or summary.get("hold")),
            "historical_gate": _short((options.get("backtest") or {}).get("research_gate"), 80)[0],
            "latest_observed_at": _short(summary.get("last_observed_at"), 40)[0],
            "latest_result_date": _short((summary.get("last_daily_result") or {}).get("date"), 10)[0],
            "forward_retrieved_at": _short(forward.get("retrieved_at"), 40)[0],
            "forward_fresh_at_report_generation": forward.get("fresh_now") is True,
            "forward_status": ("waiting_for_first_forward_result" if forward else "waiting_for_recorded_quotes") if not summary.get("last_daily_result") else "saved_current_session" if forward.get("fresh_now") else "saved_stale_result",
            "shared_portfolio": summary.get("schema") == 2,
            "symbol": _short(summary.get("symbol"),8)[0],
            "public_feed_enabled": (options.get("feed") or {}).get("config",{}).get("enabled") is True,
            "public_feed_receipt": _short(((options.get("feed") or {}).get("last_run") or {}).get("status"),20)[0],
            "public_feed_observed_at": _short(((options.get("feed") or {}).get("last_run") or {}).get("source_observed_at"),40)[0],
            "public_feed_quarantine_count": _count((((options.get("feed") or {}).get("last_run") or {}).get("provenance") or {}).get("quarantined_rows")),
            "regime_research_gate": _short((options.get("research") or {}).get("research_gate"),80)[0],
            "strategy_attribution": {key: {"entries":_count(stats.get("entry_count")),"closed":_count(stats.get("closed_count")), **{field:_short(stats.get(field),44)[0] for field in ("realized_pnl","monthly_committed")}} for key,stats in list(summary.get("strategy_stats", {}).items())[:3]},
            "comparison_gate": _short((options.get("comparison") or {}).get("research_gate"),80)[0],
            "comparison_coverage": {"source_snapshots":_count(((options.get("comparison") or {}).get("coverage") or {}).get("source_snapshots")),"processed_snapshots":_count(((options.get("comparison") or {}).get("coverage") or {}).get("processed_snapshots")),"last_processed_date":_short(((options.get("comparison") or {}).get("coverage") or {}).get("last_processed_date"),10)[0]},
            "note": "Saved options simulation, not live quotes/broker fills. Contributions are not returns; cap applies to modeled monthly net loss, not peak drawdown."}
        eod = options_eod_brief_view(options)
        if eod:
            # Keep this branch smaller than the legacy research/comparison
            # view. It reports recorded accounting and source availability,
            # never converts date-only data into a quote-event timestamp.
            fields = ("symbol", "source_id", "monthly_cap", "entry_all_in_cap", "shared_portfolio",
                      "equity", "cash", "contributions", "trading_pnl", "monthly_pnl", "fees",
                      "monthly_budget_used", "monthly_budget_remaining", "trade_count", "closed_trade_count",
                      "cap_qualified", "held", "position", "pending_action", "baseline_count",
                      "latest_result_date", "daily_pnl", "source_date", "source_received_at",
                      "partial_chain", "source_status", "missing_source_session", "source_enabled",
                      "last_check_status", "waiting_for_disposal_source", "disposal_source_date")
            result["options_paper"] = {key:eod[key] for key in fields}
            result["options_paper"].update(simulated_only=True, profile=eod["profile"],
                execution_qualified=False, quote_observed_at=None,
                fresh_at_report_generation=eod["fresh_at_report_generation"],
                configured_check_at=eod["configured_check_at"],
                next_check_window=eod["next_check_window"],
                note="Saved experimental EOD model; source dates are not quote-event times. Contributions are not returns; no verified market fills.")
            if eod["schema"]==6:
                result["options_paper"].update(schema=6, **{key:eod[key] for key in
                    ("total_bankroll","entry_equity_fraction","automatic_topups","funding_basis",
                     "funding_added_on_activation","bankroll_activated_at")})
            if eod.get("stress_policy_active") is True:
                result["options_paper"].update({key:eod[key] for key in
                    ("stress_policy_active","stress_strategy","stress_activated_at","stress_gate_stage",
                     "stress_gate_allowed","stress_context_reason","stress_context_label","stress_context_published_at")})
    from .options_public_brief import view as public_view
    public = public_view(options)
    if public and type(options) is dict and options.get("config", {}).get("enabled") is True:
        result["options_paper"] = public
    if type(options) is dict and "options_paper" in result and type(options.get("paper_readiness")) is dict:
        readiness=options["paper_readiness"]
        roots=readiness.get("roots")
        result["options_paper"]["source_inventory"]={"declared_roots":len(roots[:16]) if type(roots) is list else 0,
            "configured_paper_roots":list(readiness["configured_active_paper_roots"]) if readiness.get("configured_active_paper_roots") in (["F"],["KO"]) else [],
            "additional_current_sources_qualified":False}
    if type(options) is dict and options.get("config", {}).get("enabled") is True and options.get("vix"):
        vix = options["vix"]
        result["options_paper"]["vix"] = {"paper_status":_short(vix.get("paper_status"),64)[0], "paper_fills_enabled":False,
            "source_route":_short((vix.get("connection_readiness") or {}).get("status"),64)[0],
            "source_reviewed_at":_short((vix.get("connection_readiness") or {}).get("checked_at"),40)[0],
            "index_source_date":_short((vix.get("index_freshness") or {}).get("source_date"),10)[0],
            "index_stale":(vix.get("index_freshness") or {}).get("stale") is not False,
            "option_backtest_gate":_short((vix.get("backtest") or {}).get("research_gate"),80)[0],
            "note":"Index diagnostics are not option P/L; no forward VIX fills or added allowance."}
    if radar["enabled"]:
        # Reserve a small explicit repository header in the model-facing view;
        # full row metadata and URLs remain in the verified Markdown/JSON.
        result["repository_updates"] = {key: radar[key] for key in ("status", "checked_at", "stale", "counts")}
        result["repository_updates"]["note"] = "Saved watchlist outcomes, not a fresh comprehensive scan. First observations are not new releases. Details in full report."
        result["omitted"]["repository_rows"] = len(radar["items"])
    indexed, groups = {}, {kind: [] for kind in _KINDS}
    for number, source in enumerate(sources[:MAX_SOURCE_ENTRIES], 1):
        if type(source) is not dict:
            result["omitted"]["source_entries"] += 1
            continue
        identifier = "S"+str(number)
        kind = source.get("kind") if source.get("kind") in _KINDS else "unsupported"
        items = source.get("items") if type(source.get("items")) is list else []
        retrieved, issued = _date(source.get("fetched_at")), _date(source.get("issued_at"))
        limit = _AGE_LIMITS.get(kind, 0)
        unavailable = source.get("status") == "unavailable"
        stale = unavailable or not retrieved or kind == "unsupported"
        time_unknown = not retrieved or kind in {"weather", "forecast"} and not issued
        if retrieved:
            age = (now-retrieved).total_seconds()
            stale = stale or age > limit or age < -300
        if issued:
            issue_age = (now-issued).total_seconds()
            stale = stale or issue_age > limit or issue_age < -300
        elif kind in {"weather", "forecast"}:
            stale = True
        source_url_value = source.get("response_url") or source.get("url")
        url = _url(source_url_value)
        index = {"id": identifier, "kind": kind,
                 "status": source.get("status") if source.get("status") in {"available", "unavailable", "cached_refresh_failed"} else "unrecognized",
                 "retrieved": retrieved.isoformat(timespec="seconds") if retrieved else None,
                 "issued": issued.isoformat(timespec="seconds") if issued else None,
                 "stale": bool(stale), "items": len(items), "shown": 0, "omitted": len(items)}
        if time_unknown:
            index["time_unknown"] = True
        if url:
            index["url"] = url
        elif source_url_value:
            index["url_omitted"] = True
        result["sources"].append(index)
        indexed[identifier] = index
        if kind in groups:
            groups[kind].append((identifier, [item for item in items if type(item) is dict]))

    def add(kind, identifier, raw):
        title, title_cut = _short(raw.get("title") or "Untitled saved item", 110)
        summary, summary_cut = _short(raw.get("summary"), 170 if kind in {"weather", "forecast"} else 110)
        item = {"source": identifier, "title": title}
        if summary:
            item["summary"] = summary
        if title_cut or summary_cut:
            item["clipped"] = True
            result["omitted"]["text_clipped"] = True
        for key in ("published_at", "starts", "ends", "expires", "severity"):
            if raw.get(key):
                value, cut = _short(raw[key], 80)
                if value:
                    item[key] = value
                if cut:
                    item["clipped"] = True
                    result["omitted"]["text_clipped"] = True
        distance = raw.get("distance_km")
        if kind == "earthquakes" and type(distance) in (int, float) and math.isfinite(distance) and 0 <= distance <= 50_000:
            item["distance_km"] = distance
        if kind == "alerts":
            expires = _date(raw.get("expires"))
            item["expiry"] = "expired" if expires and expires <= now else "unexpired_in_saved_response" if expires else "unknown"
        url = _url(raw.get("url"))
        if url and url != indexed[identifier].get("url"):
            item["url"] = url
        elif raw.get("url") and not url:
            item["url_omitted"] = True
        result[kind].append(item)

    for kind in ("weather", "earthquakes"):
        candidates = [(identifier, item) for identifier, items in groups[kind] for item in items]
        for identifier, item in candidates[:_LIMITS[kind]]:
            add(kind, identifier, item)
    forecast = [(identifier, item) for identifier, items in groups["forecast"] for item in items
                if _date(item.get("ends")) and _date(item["ends"]) > now]
    forecast.sort(key=lambda row: _date(row[1]["ends"]))
    for identifier, item in forecast[:2]:
        add("forecast", identifier, item)
    alerts = [(identifier, item) for identifier, items in groups["alerts"] for item in items]
    def expiry_rank(pair):
        expires = _date(pair[1].get("expires"))
        return 0 if expires and expires > now else 1 if expires is None else 2
    alerts.sort(key=expiry_rank)
    for identifier, item in alerts[:2]:
        add("alerts", identifier, item)
    # Give each configured news feed one slot before filling a second slot from
    # a busy feed. The total remains three, rather than three for every feed.
    for offset in range(3):
        for identifier, items in groups["news"]:
            if offset < len(items) and len(result["news"]) < 3:
                add("news", identifier, items[offset])
    if "revenue_workbench" in report:
        from .business_trading_brief import revenue_view
        business = revenue_view(report["revenue_workbench"])
        result["business_revenue"] = {"status": business["status"], "profit_verified": False,
            "net_reported_cash": (business.get("cash") or {}).get("net_reported_cash"),
            "scope": "Owner-reported cash movements; booked sales and scenarios are not complete profit."}
    if "options_review" in report:
        journal = report["options_review"] if type(report["options_review"]) is dict else {}
        result["options_journal"] = {"status": _short(journal.get("status"),40)[0],
            "closed_trades": _count(journal.get("closed_trades")), "execution_qualified": False}
    warnings = report.get("warnings") if type(report.get("warnings")) is list else []
    if warnings:
        result["warnings"] = []
        for warning in warnings[:2]:
            value, cut = _short(warning, 120)
            result["warnings"].append(value)
            result["omitted"]["text_clipped"] |= cut
        result["omitted"]["warnings"] = max(0, len(warnings)-2)

    def recount():
        for source in result["sources"]:
            source["shown"] = sum(item["source"] == source["id"] for kind in _KINDS for item in result[kind])
            source["omitted"] = source["items"]-source["shown"]
        result["omitted"]["source_items"] = sum(source["omitted"] for source in result["sources"])
        result["omitted"]["urls"] = sum(bool(source.get("url_omitted")) for source in result["sources"])+sum(bool(item.get("url_omitted")) for kind in _KINDS for item in result[kind])
        result["truncated"] = bool(any(result["omitted"].values()) or result["activity"]["details_omitted"] or result["activity"]["source_details_truncated"])

    recount()
    # Keep all source statuses, times and item omission counts even when a
    # pathological set of long headlines/URLs consumes the model input budget.
    for summary_limit in (90, 45, 0):
        if _size(result) <= MAX_SUMMARY_CHARS:
            break
        for kind in _KINDS:
            for item in result[kind]:
                if "summary" in item:
                    value, cut = _short(item["summary"], summary_limit)
                    if summary_limit:
                        item["summary"] = value
                    else:
                        item.pop("summary")
                    if cut or not summary_limit:
                        item["clipped"] = True
                        result["omitted"]["text_clipped"] = True
        recount()
    for url_limit in (160, 0):
        if _size(result) <= MAX_SUMMARY_CHARS:
            break
        for row in result["sources"]+[item for kind in _KINDS for item in result[kind]]:
            if "url" in row and len(row["url"]) > url_limit:
                row.pop("url")
                row["url_omitted"] = True
        recount()
    while _size(result) > MAX_SUMMARY_CHARS and any(result[kind] for kind in _KINDS):
        # Remove least essential extra detail first; omission counts identify
        # exactly which source's saved items no longer fit the summary.
        kind = next(kind for kind in ("earthquakes", "forecast", "news", "alerts", "weather") if result[kind])
        result[kind].pop()
        recount()
    if _size(result) > MAX_SUMMARY_CHARS:
        raise ValueError("Verified report metadata cannot fit the summary bound.")
    return result
