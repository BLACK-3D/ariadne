"""A bounded, self-contained one-page view of verified daily-brief facts.

No fonts, images, models, network access or optional packages are needed. The
saved SVG is a dated artifact; an API preview recomputes source age at read time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import html
import re
import textwrap
from urllib.parse import urlsplit

from .briefing_summary import compact_summary, repository_summary

WIDTH, HEIGHT = 1080, 1350
MAX_SVG_BYTES = 36_000
_INK = "#101b21"
_PAPER = "#edf0e7"
_MUTED = "#527077"
_AMBER = "#a34d25"


def _short(value, limit):
    value = " ".join(str(value or "").replace("\x00", "").split())
    # XML 1.0 excludes most controls; the renderer never uses source markup.
    value = "".join(c for c in value if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF
                    and ord(c) not in (0xFFFE, 0xFFFF))
    return value if len(value) <= limit else value[:limit-1]+"…"


def _lines(value, width, count):
    words = textwrap.wrap(_short(value, width*count*3), width=width, break_long_words=True,
                          break_on_hyphens=False, max_lines=count, placeholder="…")
    return words or ["Not supplied"]


def _clock(value):
    if not value:
        return "time unavailable"
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(timezone.utc).strftime("%d %b %H:%M UTC") if stamp.tzinfo else "time unavailable"
    except (ValueError, TypeError):
        return "time unavailable"


def poster_view(report, now=None):
    """Create exactly one 1080×1350 SVG; facts remain linked to saved JSON.

    This is a compact presentation, never a current all-clear. Every selected
    source item carries its own freshness label; omitted details stay in JSON/MD.
    """
    if type(report) is not dict:
        raise ValueError("A verified briefing JSON object is required.")
    # The poster consumes only source/activity summary fields. Financial
    # priorities below are read separately from the complete saved report.
    # Unused account metadata must not displace weather or exhaust this budget.
    source_view = {key: report[key] for key in
                   ("generated_at_utc", "config", "activity", "public_sources", "warnings", "repository_updates")
                   if key in report}
    summary = compact_summary(source_view, now)
    radar = repository_summary(report, now)
    source_index = {row["id"]: row for row in summary["sources"]}
    generated = summary["generated_at_utc"]
    checked = summary["freshness_checked_at_utc"]
    presentation_clipped = False

    def clip(value, limit):
        nonlocal presentation_clipped
        result = _short(value, limit)
        presentation_clipped |= len(" ".join(str(value or "").split())) > limit
        return result

    def status(identifier):
        row = source_index[identifier]
        label = "UNAVAILABLE" if row["status"] in {"unavailable", "unrecognized"} else "STALE" if row["stale"] else "SAVED / RECENT"
        if row["status"] == "cached_refresh_failed":
            label = "STALE / REFRESH FAILED" if row["stale"] else "SAVED / REFRESH FAILED"
        return identifier+" · "+label

    weather = summary["weather"][0] if summary["weather"] else None
    weather_text = weather.get("summary", "") if weather else ""
    temperature = re.search(r"(?:temperature:\s*|^)(-?\d+(?:\.\d+)?)\s*°?\s*F\b", weather_text, re.I)
    temperature = temperature or re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*F\b", weather_text, re.I)
    weather_number = temperature.group(1) if temperature and len(temperature.group(1)) <= 6 else "—"
    conditions = weather_text.split(";")[0] if weather_text else "No saved station observation"
    forecast = summary["forecast"][:2]
    signals = []
    for kind, name in (("alerts", "ALERT"), ("news", "REGIONAL"), ("earthquakes", "SEISMIC")):
        for item in summary[kind]:
            suffix = " / EXPIRED" if item.get("expiry") == "expired" else " / EXPIRY UNKNOWN" if item.get("expiry") == "unknown" else ""
            signals.append({"label": name+" · "+status(item["source"])+suffix,
                            "title": item["title"], "detail": item.get("summary", "")})
    signal_limit = 2 if radar["enabled"] else 3
    shown_signals = signals[:signal_limit]
    if not shown_signals:
        shown_signals = [{"label": "SOURCE COVERAGE", "title": "No regional items available in this view.",
                          "detail": "An empty saved response is not an all-clear. Review official channels."}]
    presentation_clipped |= bool(weather_text and conditions != weather_text) or any(row["detail"] for row in shown_signals)

    raw_activity = report.get("activity") if type(report.get("activity")) is dict else {}
    activity = dict(summary["activity"])
    if type(raw_activity.get("unresolved_approvals")) is not list:
        activity["unresolved_approvals"] = None
    approvals = activity["unresolved_approvals"]
    if approvals:
        priority = f"Review {approvals} unresolved approval{'s' if approvals != 1 else ''}."
        priority_note = "Your decision is required before those actions can proceed."
    elif approvals is None:
        priority = "Review the activity coverage."
        priority_note = "Approval status was not supplied by this saved report."
    elif any(row["stale"] or row["status"] != "available" for row in source_index.values()) or not source_index:
        priority = "Check the source gaps before planning."
        priority_note = "Saved conditions may have changed. Refresh when connected."
    else:
        priority = "Review your day, then set a direction."
        priority_note = "No unresolved approvals were recorded in this activity snapshot."
    venture = report.get("venture_checkin")
    if type(venture) is dict and venture.get("status") not in {"disabled", "paused"}:
        count = len(venture.get("advanced_tasks", []))
        if venture.get("status") in {"held", "unavailable", "failed", "interrupted"}:
            priority_note = "Venture Lab needs review. Inspect the check-in in the full report."
        elif count:
            priority_note = str(count)+" local Venture Lab draft(s) ready. Review evidence in the full report."
        else:
            priority_note = "Venture Lab: review the next evidence decision in the full report."
    qqq = report.get("qqq_paper")
    if type(qqq) is dict and qqq.get("config", {}).get("enabled"):
        paper = qqq.get("paper")
        fresh = (qqq.get("quality") or {}).get("fresh_now") is True
        if qqq.get("hold"):
            priority_note = "QQQ PAPER HELD / inspect the full report. No real orders."
        elif paper:
            priority_note = "QQQ PAPER $"+_short(paper.get("state", {}).get("equity"),18)+(" / saved session " if fresh else " / STALE ")+_short(paper.get("state", {}).get("last_processed"),10)+" / details in report."
        else:
            priority_note = "QQQ paper experiment not started / details in full report."

    options = report.get("options_paper")
    if type(options) is dict and options.get("config", {}).get("enabled"):
        options_summary = (options.get("paper") or {}).get("summary") or {}
        options_fresh = (options.get("forward_quality") or {}).get("fresh_now") is True
        options_budget = "$25,000 total / no top-ups" if options_summary.get("schema") in (6,7) else "$500 monthly budget"
        if options.get("hold") or options_summary.get("hold"):
            priority_note = "OPTIONS PAPER HELD / "+options_budget+" / inspect report."
        elif options_summary.get("schema")==7:
            priority_note = "PUBLIC OPTIONS PAPER / saved P/L $"+_short(options_summary.get("trading_pnl"),18)+" / "+options_budget+" / dated results only."
        elif not options_summary.get("last_daily_result"):
            priority_note = "OPTIONS PAPER / warming indicators / no trading result yet." if options.get("forward_quality") else "OPTIONS PAPER / waiting for recorded quotes / no trading result yet."
        elif not options_fresh:
            priority_note = "OPTIONS PAPER STALE / saved P/L $"+_short(options_summary.get("trading_pnl"), 18)+" / see dated report."
        elif options_summary:
            priority_note = "OPTIONS PAPER P/L $"+_short(options_summary.get("trading_pnl"), 18)+" / "+options_budget+" / saved results in full report."
        else:
            priority_note = "OPTIONS PAPER / waiting for quotes / "+options_budget+"."

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="poster-title poster-description">',
           '<title id="poster-title">Ariadne daily brief</title>',
           '<desc id="poster-description">One-page saved-evidence brief. Source age and gaps are labeled. Full evidence remains in the Markdown and JSON report.</desc>',
           f'<rect width="1080" height="1350" fill="{_PAPER}"/>',
           f'<path d="M0 0H1080V17H0Z" fill="{_INK}"/>',
           '<path d="M848 51h154v154M887 51v115h115M926 51v76h76" stroke="#b9cecb" fill="none" stroke-width="1"/>',
           '<circle cx="1002" cy="51" r="5" fill="#a34d25"/>']

    def text(value, x, y, size=22, color=_INK, weight=400, family="Arial, sans-serif", spacing=None):
        attributes = f'x="{x}" y="{y}" fill="{color}" font-family="{family}" font-size="{size}" font-weight="{weight}"'
        if spacing is not None:
            attributes += f' letter-spacing="{spacing}"'
        svg.append(f'<text {attributes}>{html.escape(clip(value, 400), quote=True)}</text>')

    def lines(value, x, y, width, count, size=22, color=_INK, weight=400, gap=None):
        nonlocal presentation_clipped
        wrapped = _lines(value, width, count)
        presentation_clipped |= any(line.endswith("…") for line in wrapped)
        for index, line in enumerate(wrapped):
            text(line, x, y+index*(gap or size*1.35), size, color, weight, "Consolas, monospace")

    def rule(y):
        svg.append(f'<path d="M64 {y}H1016" stroke="#c6d2ca" stroke-width="1"/>')

    text("A R I A D N E", 64, 69, 22, weight=700)
    text("DAILY SIGNAL / 01", 64, 104, 16, _MUTED, family="Consolas, monospace", spacing=1)
    local_day = clip(report.get("local_date"), 10)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", local_day):
        text("LOCAL DAY / "+local_day, 585, 104, 16, _MUTED, family="Consolas, monospace")
    text("Your day,", 60, 187, 76, weight=700, spacing=-3)
    text("in focus.", 60, 262, 76, weight=700, spacing=-3)
    region = clip(summary["region"], 48)
    lines(region, 585, 207, 28, 2, 24, weight=700)
    text(_clock(generated), 585, 279, 17, _MUTED, family="Consolas, monospace")
    rule(313)

    text("01 / OUTSIDE", 64, 350, 17, _MUTED, family="Consolas, monospace")
    text(weather_number, 58, 456, 90 if len(weather_number) > 4 else 110, weight=700, spacing=-5)
    text("°F" if temperature else "NO DATA", 430, 451, 21, _MUTED, weight=700)
    lines(conditions, 64, 493, 32, 2, 20)
    text(status(weather["source"]) if weather else "UNAVAILABLE / NOT INFERRED", 64, 553, 15, _AMBER if not weather or source_index[weather["source"]]["stale"] else _MUTED, family="Consolas, monospace")
    if forecast:
        for index, item in enumerate(forecast):
            y = 352+index*111
            text(clip(item["title"], 28).upper(), 585, y, 17, weight=700)
            lines(item.get("summary") or "Details not supplied", 585, y+29, 33, 2, 18)
            text(status(item["source"]), 585, y+83, 14, _MUTED, family="Consolas, monospace")
    else:
        lines("Forecast unavailable", 585, 390, 28, 2, 23, weight=700)
        lines("No upcoming forecast period survives this saved view.", 585, 448, 32, 3, 18, _MUTED)
    rule(584)

    for x, label, key in ((64, "MISSIONS TOUCHED", "missions"), (390, "RECORDED EVENTS", "events"), (735, "NEED YOUR DECISION", "unresolved_approvals")):
        value = activity[key]
        text(str(value) if value is not None else "—", x, 645, 43, _AMBER if key == "unresolved_approvals" and value else _INK, weight=700)
        text(label, x, 676, 14, _MUTED, family="Consolas, monospace")
    rule(704)
    text("02 / REGIONAL SIGNALS", 64, 742, 17, _MUTED, family="Consolas, monospace")
    for index, item in enumerate(shown_signals):
        y = 778+index*98
        text(clip(item["label"], 82), 64, y, 14, _AMBER, family="Consolas, monospace")
        lines(item["title"], 64, y+29, 67, 2, 22, weight=700, gap=27)
    if radar["enabled"]:
        text("03 / REPOSITORY WATCH", 64, 974, 17, _MUTED, family="Consolas, monospace")
        if radar["items"]:
            counts = radar["counts"]
            line = (str(counts["changed"])+" CHANGED / "+str(counts["baseline"])+" FIRST OBS. / "+
                    str(counts["unchanged"])+" UNCHANGED / "+str(counts["failed"])+" FAILED")
        else:
            line = "No verified repository snapshot available."
        lines(line, 64, 1003, 88, 1, 18, _INK, weight=700)
        text("LAST ATTEMPT "+_clock(radar["checked_at"])+(" / STALE OR FAILED" if radar["stale"] else " / SAVED CHECKS")+" / DETAILS IN REPORT", 64, 1032, 12, _AMBER if radar["stale"] else _MUTED, family="Consolas, monospace")
    text(("04" if radar["enabled"] else "03")+" / YOUR NEXT MOVE", 64, 1085, 17, _MUTED, family="Consolas, monospace")
    lines(priority, 64, 1123, 65, 1, 23, weight=700)
    lines(priority_note, 64, 1154, 83, 1, 18, _MUTED)
    rule(1175)

    # Source time and provenance fit even with the maximum nine-entry cache.
    text("SAVED EVIDENCE / AGE CHECKED "+_clock(checked), 64, 1204, 13, _MUTED, family="Consolas, monospace")
    for index, row in enumerate(summary["sources"]):
        x, y = 64+(index % 3)*320, 1227+(index//3)*35
        host = urlsplit(row.get("url", "")).hostname or "origin unavailable"
        text(row["id"]+"/"+row["kind"]+" "+clip(host, 24), x, y, 12, _MUTED, family="Consolas, monospace")
        text(_clock(row["retrieved"]), x, y+14, 11, _MUTED, family="Consolas, monospace")
    if not summary["sources"]:
        lines("No matching public-source cache. Weather, forecast and regional facts are unavailable.", 64, 1232, 110, 2, 14, _MUTED)
    omissions = len(signals) > signal_limit or summary["truncated"] or presentation_clipped or radar["omitted"]
    text("Compact view · full details + URLs in report"+(" · items/text omitted" if omissions else ""), 64, 1331, 12, _MUTED)
    text("Saved evidence is not an all-clear. Local recorded activity only. 0 model calls.", 64, 1347, 11, _MUTED)
    svg.append('</svg>')
    rendered = "\n".join(svg)+"\n"
    if len(rendered.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("The one-page briefing exceeded its fixed presentation budget.")
    return {"svg": rendered, "mime": "image/svg+xml", "width": WIDTH, "height": HEIGHT,
            "generated_at_utc": generated, "freshness_checked_at_utc": checked,
            "omissions": bool(omissions), "model_calls": 0,
            "note": "One-page view derived from verified saved JSON. Source freshness checked at "+_clock(checked)+". Full report retains details and URLs.",
            "accessible_text": "\n".join(["Ariadne daily brief", region, "Generated "+_clock(generated),
                "Station: "+conditions+"; "+(status(weather["source"]) if weather else "unavailable"),
                *[item["title"]+" — "+item["label"] for item in shown_signals],
                *(["Repository watch: "+radar["description"]+" Last attempt "+_clock(radar["checked_at"])] if radar["enabled"] else []), priority, priority_note,
                "Saved evidence is not an all-clear. Full details and source URLs remain in the report."])}
