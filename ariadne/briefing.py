"""Deterministic daily briefing, durable receipts and an isolated public-data collector.

The application never weakens its offline socket policy. Only the explicitly
enabled collector child can contact public HTTPS sources; reports consume its
dated cache. No model is used and external text is never executable instruction.
"""
from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import _allowed_path
from .internet import internet_enabled, SETTINGS_KEY
from .locking import InstanceLock

UTC = timezone.utc
MAX_BYTES = 2_000_000
MAX_ROWS = 1000
MAX_HISTORY = 100_000
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_RESOURCES = Path(__file__).parent / "resources" / "briefing"
_DEFAULT = {"enabled": False, "paused": False, "timezone": "America/Chicago", "hour": 6,
            "minute": 0, "region": None, "online_sources": False, "news_feeds": [], "repository_updates": False,
            "activation_at": None, "pause_reason": None, "revision": 0}
_AGE_LIMITS = {"weather": 2*3600, "forecast": 6*3600, "alerts": 30*60, "earthquakes": 2*3600, "news": 24*3600}
_NWS = "https://api.weather.gov"
_QUAKES = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
_DOCS = [{"title": "NWS public forecast and alerts API", "url": "https://www.weather.gov/documentation/services-web-api"},
         {"title": "USGS GeoJSON feed specification", "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php"}]
_RUN_LOCK = threading.RLock()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)+"\n").encode("utf-8")


def _utc(value=None):
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Briefing timestamps must have an explicit time zone.")
    return value.astimezone(UTC)


def _stamp(value):
    return _utc(value).isoformat()


def _tz(name):
    if name == "UTC":
        return UTC
    if name != "America/Chicago":
        raise ValueError("Supported briefing time zones: America/Chicago and UTC.")
    # Bundle the 1.7 KiB IANA 2026f record so Windows needs no system TZ database.
    with (_RESOURCES / "America_Chicago.tzif").open("rb") as source:
        return ZoneInfo.from_file(source, key="America/Chicago")


def _text(value, maximum=2000):
    if value is None:
        return ""
    return " ".join(str(value).replace("\x00", "").split())[:maximum]


def _md(value):
    return html.escape(_text(value, 8000), quote=False).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def options_eod_brief_view(options):
    """Pure bounded facts from a verified saved EOD account; no source work.

    Receipt clocks and source dates stay separate. Financial values are copied
    from its saved summary, never reconstructed from funding or outside prices.
    """
    if type(options) is not dict:
        return None
    account = options.get("paper") if type(options.get("paper")) is dict else {}
    summary = account.get("summary") if type(account.get("summary")) is dict else {}
    if type(summary.get("schema")) is not int or summary["schema"] not in (3,4,5,6):
        return None
    def mapping(value):
        return value if type(value) is dict else {}
    def text(value, maximum=100):
        return _text(value, maximum) if type(value) is str else None
    def day(value):
        if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return None
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return None
    def stamp(value):
        if type(value) is not str or len(value) > 40:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC).isoformat() if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
        except ValueError:
            return None
    def count(value):
        return value if type(value) is int and 0 <= value <= 1000000000000 else None
    def money(value):
        return value if type(value) is str and len(value) <= 44 and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,8})?", value) else None
    eod = mapping(options.get("eod"))
    quality = mapping(eod.get("quality")) or mapping(options.get("forward_quality"))
    last = mapping(eod.get("last_run"))
    config = mapping(eod.get("config"))
    daily = mapping(summary.get("last_daily_result"))
    hold = mapping(options.get("hold")) or mapping(summary.get("hold"))
    assurance = mapping(eod.get("publication_assurance"))
    source_day = day(quality.get("last_source_date")) or day(summary.get("last_source_date"))
    expected = day(quality.get("expected_last_session_now")) or day(eod.get("expected_market_session"))
    receipt = stamp(quality.get("last_received_at"))
    status = last.get("status")
    check_status = status if type(status) is str and status in {"completed", "failed", "running", "interrupted"} else "unavailable"
    missing = bool(expected and (not source_day or source_day < expected))
    fresh = bool(quality.get("fresh_now") is True and source_day and receipt and expected == source_day)
    source_status = ("failed_check" if check_status == "failed" else "uncertain_check" if check_status in {"running", "interrupted"}
        else "waiting_for_first_source" if not source_day else "missing_source_session" if missing
        else "saved_current_session" if fresh else "saved_stale_or_unverified")
    position, pending = mapping(summary.get("position")), mapping(summary.get("pending"))
    result = {"schema":summary["schema"], "symbol":text(summary.get("symbol"), 8), "source_id":text(summary.get("source_id"), 40),
        "profile":"experimental_stress_put_eod_paper" if summary["schema"] in (4,5,6) else "experimental_eod_date_only_paper", "execution_qualified":False,
        **{key:money(summary.get(key)) for key in ("equity", "cash", "contributions", "trading_pnl", "monthly_pnl", "monthly_budget_used", "monthly_budget_remaining", "fees")},
        "monthly_cap":"25000.00" if summary["schema"]==6 else "500.00", "entry_all_in_cap":"1000.00" if summary["schema"]==6 else "100.00", "shared_portfolio":True,
        "trade_count":count(summary.get("trade_count")), "closed_trade_count":count(summary.get("closed_trade_count")),
        "cap_qualified":summary.get("cap_qualified") is True, "held":bool(hold),
        "hold_reason":text(hold.get("reason") or hold.get("message") or hold.get("code") or hold.get("request_id"),160),
        "position":text(position.get("contract_id"),40), "pending_action":text(pending.get("action"),20),
        "latest_result_date":day(daily.get("date")), "daily_pnl":money(daily.get("daily_pnl")),
        "result_recorded_at":stamp(daily.get("recorded_at")), "source_date":source_day,
        "source_received_at":receipt, "expected_source_date":expected, "missing_source_session":missing,
        "source_status":source_status, "partial_chain":quality.get("partial_chain") if type(quality.get("partial_chain")) is bool else None,
        "fresh_at_report_generation":fresh, "quote_observed_at":None,
        "baseline_count":count(eod.get("baseline_count")), "quote_count":count(quality.get("quotes")),
        "source_enabled":config.get("enabled") is True, "last_check_status":check_status,
        "last_check_at":stamp(last.get("completed_at")) or stamp(last.get("created_at")),
        "last_check_id":text(last.get("request_id"),64), "last_check_reason":text(last.get("reason") or last.get("error"),160),
        "configured_check_at":stamp(eod.get("collection_check_at")), "window_open_at_report_generation":eod.get("collection_window_open") is True,
        "next_check_window":"After09:35Eastern on a market session; publication time unknown; failed work is not retried.",
        "waiting_for_disposal_source":assurance.get("waiting_for_disposal_source") is True,
        "disposal_source_date":day(assurance.get("deadline_source_date")), "rule_sha256":text(summary.get("rule_sha256"),64)}
    if summary["schema"] in (4,5,6):
        gate=mapping(summary.get("last_entry_gate"))
        benchmark=mapping(gate.get("benchmark_gate")) if summary["schema"] in (5,6) else gate
        witness=mapping(benchmark.get("witness"))
        context_status=mapping(eod.get("stress"))
        latest=mapping(context_status.get("last_context")) if not gate else {}
        result.update(stress_policy_active=True,stress_strategy=text(summary.get("strategy_id"),64),
            stress_activated_at=stamp(summary.get("stress_policy_activated_at")),
            stress_gate_stage=text(gate.get("stage") or benchmark.get("stage"),12),stress_gate_allowed=gate.get("allowed") if type(gate.get("allowed")) is bool else None,
            stress_context_reason=text(gate.get("reason") or benchmark.get("reason") or context_status.get("context_status"),100),
            stress_context_label=text(gate.get("label") or benchmark.get("label") or latest.get("label"),32),
            stress_context_published_at=stamp(witness.get("published_at") or latest.get("published_at")))
    if summary["schema"]==6:
        result.update(total_bankroll=money(summary.get("bankroll_total")),
            entry_equity_fraction=money(summary.get("entry_equity_fraction")),
            automatic_topups=summary.get("automatic_topups") is True,
            funding_basis=text(summary.get("funding_basis"),40),
            funding_added_on_activation=money(summary.get("funding_added_on_activation")),
            bankroll_activated_at=stamp(summary.get("bankroll_policy_activated_at")))
    return result


def _source_url(value, *, official=False):
    if type(value) is not str or len(value) > 2000:
        raise ValueError("A source must have a bounded HTTPS URL.")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
            or ".." in host or any(ord(c) <= 32 for c in value)):
        raise ValueError("Source URLs require HTTPS port 443 without credentials or fragments.")
    if official and not (host.lower().endswith(".gov") or host.lower() == "gov"):
        raise ValueError("Owner news feeds currently support official .gov HTTPS sources.")
    return value


def _validate_config(value, previous, now):
    keys = {"enabled", "paused", "timezone", "hour", "minute", "region", "online_sources", "news_feeds", "repository_updates"}
    if type(value) is not dict or set(value)-keys:
        raise ValueError("Unsupported briefing settings.")
    cfg = {**previous, **value}
    for key in ("enabled", "paused", "online_sources", "repository_updates"):
        if type(cfg[key]) is not bool:
            raise ValueError(key+" must be boolean.")
    _tz(cfg["timezone"])
    for key, upper in (("hour", 23), ("minute", 59)):
        if type(cfg[key]) is not int or not 0 <= cfg[key] <= upper:
            raise ValueError("Invalid daily briefing time.")
    region = cfg["region"]
    if region is not None:
        if type(region) is not dict or set(region)-{"name", "latitude", "longitude", "country"}:
            raise ValueError("Region requires name, latitude, longitude and country US.")
        if not isinstance(region.get("name"), str) or not 1 <= len(region["name"].strip()) <= 120:
            raise ValueError("Region needs a name of 1–120 characters.")
        if region.get("country", "US") != "US":
            raise ValueError("The bundled forecast collector currently covers US regions only.")
        for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            if type(region.get(key)) not in (int, float) or not math.isfinite(region[key]) or not low <= region[key] <= high:
                raise ValueError("Region needs valid numeric coordinates.")
        cfg["region"] = {"name": region["name"].strip(), "latitude": round(region["latitude"], 4),
                         "longitude": round(region["longitude"], 4), "country": "US"}
    feeds = cfg["news_feeds"]
    if type(feeds) is not list or len(feeds) > 5:
        raise ValueError("At most five owner-configured official news feeds are supported.")
    cleaned = []
    for feed in feeds:
        if type(feed) is not dict or set(feed)-{"title", "url", "region_terms"}:
            raise ValueError("News feed fields: title, url, region_terms.")
        title = feed.get("title")
        terms = feed.get("region_terms", [])
        if type(title) is not str or not 1 <= len(title) <= 120 or type(terms) is not list or len(terms) > 12 or any(type(term) is not str or not 1 <= len(term) <= 80 for term in terms):
            raise ValueError("Use a bounded news title and up to twelve regional match terms.")
        cleaned.append({"title": title, "url": _source_url(feed.get("url"), official=True), "region_terms": terms})
    if len({row["url"] for row in cleaned}) != len(cleaned):
        raise ValueError("Duplicate news feed URLs are not allowed.")
    cfg["news_feeds"] = cleaned
    if not previous["enabled"] and cfg["enabled"]:
        cfg["activation_at"] = _stamp(now)
    if "paused" in value and not value["paused"]:
        cfg["pause_reason"] = None
    cfg["revision"] = previous["revision"] + 1
    return cfg


def _region_digest(config):
    return hashlib.sha256(_json({"region": config["region"], "news_feeds": config["news_feeds"]})).hexdigest()


class BriefingConflict(ValueError):
    def __init__(self, request_id, status, message):
        self.request_id, self.status = request_id, status
        super().__init__(message)


class BriefingCollectionStopped(RuntimeError):
    """Policy cancellation must escape source-level fetch error handling."""


class BriefingService:
    def __init__(self, settings, store, registry):
        self.settings, self.store, self.registry = settings, store, registry
        self.path = settings.data_dir / "briefing.sqlite3"
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.collector_lock = threading.RLock()
        self.collectors = set()
        self.thread = None
        from .venture_lab import VentureLab
        self.venture = VentureLab(registry, store)
        if getattr(registry, "procedures", None) and registry.procedures.store is None:
            registry.procedures.store = store
        from .qqq_workflow import QQQWorkflow
        self.qqq = QQQWorkflow(registry, store, controls=self.qqq_controls, publication_lease=self.qqq_publication_lease)
        from .options_workflow import OptionsWorkflow
        self.options = OptionsWorkflow(registry, store, controls=self.qqq_controls, publication_lease=self.qqq_publication_lease,
                                       benchmark_guard=self.qqq._unresolved)
        from .revenue_workflow import RevenueWorkbench
        self.revenue = RevenueWorkbench(registry, store)

    @staticmethod
    def _qqq_brief_hold(db, cfg):
        today=datetime.now(UTC).astimezone(_tz(cfg["timezone"])).date().isoformat()
        return bool(db.execute("SELECT 1 FROM briefing_runs WHERE day IS NOT NULL AND (status='running' OR (day=? AND status IN ('failed','interrupted'))) LIMIT 1",(today,)).fetchone())

    def qqq_controls(self):
        cfg=self.config()
        with self.database() as db:
            return {**cfg,"execution_hold":self._qqq_brief_hold(db,cfg)}

    @contextmanager
    def qqq_publication_lease(self, expected):
        """Hold brief permission stable through the QQQ main-database commit."""
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
            current={**_DEFAULT,**json.loads(row[0])} if row else dict(_DEFAULT)
            current["execution_hold"]=self._qqq_brief_hold(db,current)
            if current!=expected or not current["enabled"] or current["paused"] or current["execution_hold"]:
                raise ValueError("Brief permissions changed before QQQ publication.")
            yield

    @contextmanager
    def database(self):
        with self.lock, self.registry._directory(self.path.parent, create=True):
            path = _allowed_path(self.path)
            for suffix in ("", "-journal", "-wal", "-shm"):
                candidate = _allowed_path(str(path)+suffix)
                if candidate.exists():
                    info = candidate.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Briefing database files must be regular files with one link.")
            db = sqlite3.connect(path, timeout=10)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic()+10
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS briefing_settings(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS briefing_cache(id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL, collected TEXT NOT NULL, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS briefing_runs(request_id TEXT PRIMARY KEY, day TEXT, status TEXT NOT NULL, created REAL NOT NULL, receipt TEXT NOT NULL);
                    CREATE UNIQUE INDEX IF NOT EXISTS briefing_day ON briefing_runs(day) WHERE day IS NOT NULL;
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def config(self):
        with self.database() as db:
            row = db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
        return {**_DEFAULT, **json.loads(row["value"])} if row else dict(_DEFAULT)

    def configure(self, value, now=None, *, expected_revision=None):
        now = _utc(now)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError("expected_revision must be a non-negative integer.")
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
            previous = {**_DEFAULT, **json.loads(old[0])} if old else dict(_DEFAULT)
            if expected_revision is not None and expected_revision != previous["revision"]:
                raise BriefingConflict("config", "conflict", "Briefing settings changed. Refresh and review the current settings before saving.")
            cfg = _validate_config(value, previous, now)
            db.execute("INSERT OR REPLACE INTO briefing_settings VALUES(1,?)", (_json(cfg).decode(),))
        return self.status(now)

    @staticmethod
    def _slot(config, day):
        return datetime(day.year, day.month, day.day, config["hour"], config["minute"], tzinfo=_tz(config["timezone"])).astimezone(UTC)

    def next_due(self, now=None, config=None):
        now, config = _utc(now), config or self.config()
        if not config["enabled"] or config["paused"]:
            return None
        day = now.astimezone(_tz(config["timezone"])).date()
        slot = self._slot(config, day)
        if slot <= now:
            slot = self._slot(config, day+timedelta(days=1))
        return _stamp(slot)

    def status(self, now=None):
        now, cfg = _utc(now), self.config()
        with self.database() as db:
            rows = list(db.execute("SELECT receipt,status FROM briefing_runs ORDER BY created DESC LIMIT 1"))
            cache = db.execute("SELECT collected,digest FROM briefing_cache WHERE id=1").fetchone()
        last = {**json.loads(rows[0]["receipt"]), "status": rows[0]["status"]} if rows else None
        today = now.astimezone(_tz(cfg["timezone"])).date().isoformat()
        today_report = last if last and datetime.fromtimestamp(last["created_at"], UTC).astimezone(_tz(cfg["timezone"])).date().isoformat() == today else None
        master_enabled = internet_enabled(self.store, default=True)
        return {"config": cfg, "next_due_at": self.next_due(now, cfg), "last_report": last,
                "today_report": today_report, "autonomy_paused": bool(self.store.setting("paused", False)),
                "internet_enabled": master_enabled,
                "collection_enabled": bool(master_enabled and cfg["online_sources"] and not cfg["paused"] and not self.store.setting("paused", False) and (cfg["region"] or cfg["repository_updates"])),
                "repository_updates": self.repository_snapshot(cfg, now),
                "cache": {"collected_at": cache["collected"], "region_matches": cache["digest"] == _region_digest(cfg)} if cache else None,
                "region_required": cfg["region"] is None, "model_calls": 0,
                "daemon_running": bool(self.thread and self.thread.is_alive()),
                "supported_timezones": ["America/Chicago", "UTC"], "source_docs": _DOCS,
                "scope": "Ariadne activity and dated public sources only. Offline briefs mark unavailable or stale sources. This is not comprehensive regional news coverage.",
                "wake_limit": "The app timer runs while Ariadne is running. OS wake-from-sleep requires the separately installed Windows task; powered-off PCs cannot wake through this timer."}

    def history(self):
        with self.database() as db:
            rows = list(db.execute("SELECT receipt,status FROM briefing_runs ORDER BY created DESC LIMIT 100"))
        return {"runs": [{**json.loads(row["receipt"]), "status": row["status"]} for row in rows]}

    @contextmanager
    def _execution(self, request_id):
        # A Windows wake task and the web server can run in separate processes.
        # Recovery must not adopt an actually live reporter as a crashed one.
        owner = InstanceLock(self.settings.data_dir / "briefing.lock")
        try:
            owner.acquire()
        except ValueError as error:
            if str(error) != "Another Ariadne worker already owns this data directory":
                raise
            raise BriefingConflict(request_id, "running", "Another live briefing process owns execution; its report will not be duplicated or recovered.") from None
        try:
            yield
        finally:
            owner.release()

    def cache(self, config=None, now=None):
        cfg, now = config or self.config(), _utc(now)
        with self.database() as db:
            row = db.execute("SELECT * FROM briefing_cache WHERE id=1").fetchone()
        if not row or row["digest"] != _region_digest(cfg):
            return {"collected_at": None, "sources": [], "warnings": ["No cached public sources match the configured region and feeds."]}
        result = json.loads(row["value"])
        for source in result["sources"]:
            try:
                age = max(0, (now-datetime.fromisoformat(source["fetched_at"])).total_seconds())
                source["age_seconds"] = round(age)
                source["stale"] = age > _AGE_LIMITS[source["kind"]]
                source["freshness_basis"] = "retrieval_age"
                if source["kind"] in {"weather", "forecast"} and source.get("issued_at"):
                    issued_age = max(0, (now-datetime.fromisoformat(source["issued_at"])).total_seconds())
                    source["issued_age_seconds"] = round(issued_age)
                    source["stale"] = source["stale"] or issued_age > _AGE_LIMITS[source["kind"]]
                    source["freshness_basis"] = "retrieval_and_source_issue_age"
                if source["kind"] == "alerts":
                    for item in source.get("items", []):
                        item["expired"] = bool(item.get("expires") and datetime.fromisoformat(item["expires"]) <= now)
            except (KeyError, ValueError, TypeError):
                source["stale"] = True
        return result

    def internet_revision(self):
        """None is the legacy bare-service policy; real app settings have a revision."""
        value = self.store.setting(SETTINGS_KEY, None)
        if value is None:
            return None
        if type(value) is not dict or type(value.get("revision")) is not int or value["revision"] < 0:
            raise BriefingCollectionStopped("Internet settings are invalid; collection was stopped.")
        return value["revision"]

    def _check_collection_master(self, internet_revision):
        if self.stop_event.is_set():
            raise BriefingCollectionStopped("Briefing service stopped; public collection was cancelled.")
        if not internet_enabled(self.store, default=True):
            raise BriefingCollectionStopped("Internet access is disabled; dated briefing cache is retained.")
        if self.internet_revision() != internet_revision:
            raise BriefingCollectionStopped("Internet settings changed during collection; its response was discarded.")
        if self.store.setting("paused", False):
            raise BriefingCollectionStopped("Autonomy is paused; public collection was cancelled.")

    def check_collection(self, config, internet_revision):
        self._check_collection_master(internet_revision)
        current = self.config()
        if (not current["online_sources"] or current["paused"] or not (current["region"] or current["repository_updates"])
                or current["revision"] != config["revision"]):
            raise BriefingCollectionStopped("Briefing collection settings changed, are paused or await a region; dated cache is retained.")

    def check_repository_collection(self, config, internet_revision):
        self.check_collection(config, internet_revision)
        if not self.config()["repository_updates"]:
            raise BriefingCollectionStopped("Repository updates were disabled; dated cache is retained.")

    @contextmanager
    def repository_publication_lease(self, config, internet_revision):
        # Keep canonical Internet/autonomy settings first, then briefing settings.
        # Both leases span the external cache file commit, so a concurrent
        # disable, pause or revision change cannot land in a check/publish gap.
        with self._collection_publication_lease((config, internet_revision)), self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
            current = {**_DEFAULT, **json.loads(row[0])} if row else dict(_DEFAULT)
            self._check_collection_master(internet_revision)
            if (current["revision"] != config["revision"] or not current["repository_updates"]
                    or not current["online_sources"] or current["paused"]):
                raise BriefingCollectionStopped("Repository collection settings changed before cache publication; dated cache is retained.")
            yield

    def collect_repository_updates(self, config, internet_revision, now=None):
        from .repository_radar import collect_radar
        self.check_repository_collection(config, internet_revision)
        return collect_radar(self.settings.data_dir, now=now,
            check=lambda: self.check_repository_collection(config, internet_revision),
            publication_lease=lambda: self.repository_publication_lease(config, internet_revision),
            day_key=_utc(now).astimezone(_tz(config["timezone"])).date().isoformat())

    def repository_snapshot(self, config=None, now=None, collection=None):
        config, now = config or self.config(), _utc(now)
        enabled = config["repository_updates"]
        if not enabled:
            return {"enabled": False, "status": "disabled", "checked_at": None, "sources": [], "counts": {},
                    "stale": False, "warnings": [], "model_calls": 0}
        from .repository_radar import load_snapshot
        try:
            result = load_snapshot(self.settings.data_dir, now=now)
        except (OSError, ValueError):
            result = {"status": "unavailable", "checked_at": None, "sources": [], "counts": {}, "stale": True,
                      "warnings": ["Repository cache is missing, corrupt or unsupported; no current repository facts are asserted."], "model_calls": 0}
        allowed = bool(config["online_sources"] and internet_enabled(self.store, default=True)
                       and not config["paused"] and not self.store.setting("paused", False))
        result = {**result, "enabled": True, "collection_enabled": allowed}
        if not allowed:
            result["collection_note"] = "Online refresh is disabled or paused; repository facts retain their saved check times."
        elif collection and not collection.get("collected"):
            result["collection_note"] = collection.get("reason", "Collector unavailable; using dated repository cache.")
        return result

    @contextmanager
    def _collection_publication_lease(self, collection_policy):
        if collection_policy is None:
            yield
            return
        # Match InternetService's lock order: canonical settings first, then
        # the evidence cache. Hold its write lease through the cache commit so
        # another process cannot revoke access between the check and publish.
        with self.registry._directory(self.store.path.parent):
            for suffix in ("", "-journal", "-wal", "-shm"):
                candidate = _allowed_path(str(self.store.path)+suffix)
                if candidate.exists():
                    info = candidate.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Internet settings database files must be regular files with one link.")
            with self.store.connect() as settings_db:
                settings_db.execute("PRAGMA trusted_schema=OFF")
                settings_db.execute("BEGIN IMMEDIATE")
                self._check_collection_master(collection_policy[1])
                yield

    def save_cache(self, value, expected_digest, *, collection_policy=None):
        if collection_policy is not None:
            self.check_collection(*collection_policy)
        if type(value) is not dict or len(_json(value)) > MAX_BYTES or type(value.get("sources")) is not list or len(value["sources"]) > 9:
            raise ValueError("Collector cache exceeds the bounded schema.")
        datetime.fromisoformat(value["collected_at"])
        for source in value["sources"]:
            if source.get("kind") not in _AGE_LIMITS or type(source.get("items")) is not list or len(source["items"]) > 100:
                raise ValueError("Invalid collector source schema.")
            _source_url(source["url"])
            fetched = datetime.fromisoformat(source["fetched_at"])
            _utc(fetched)
        with self._collection_publication_lease(collection_policy), self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
            cfg = {**_DEFAULT, **json.loads(current[0])} if current else dict(_DEFAULT)
            if collection_policy is not None:
                self._check_collection_master(collection_policy[1])
                if (cfg["revision"] != collection_policy[0]["revision"] or not cfg["online_sources"]
                        or cfg["paused"] or not cfg["region"]):
                    raise BriefingCollectionStopped("Briefing settings changed before cache publication; dated cache is retained.")
            if _region_digest(cfg) != expected_digest:
                raise ValueError("Region or feeds changed during collection; stale cache was not adopted.")
            previous = db.execute("SELECT digest,value FROM briefing_cache WHERE id=1").fetchone()
            if previous and previous["digest"] == expected_digest:
                saved = {(row["kind"], row["url"]): row for row in json.loads(previous["value"])["sources"]}
                for index, source in enumerate(value["sources"]):
                    old = saved.get((source["kind"], source["url"]))
                    if source["status"] == "unavailable" and old and old["status"] != "unavailable":
                        # Failed WAN refreshes never erase the last usable offline snapshot.
                        value["sources"][index] = {**old, "status": "cached_refresh_failed",
                            "last_refresh_attempt_at": source["fetched_at"], "refresh_error": source["error"]}
                if len(_json(value)) > MAX_BYTES:
                    raise ValueError("Merged collector cache exceeds the bounded artifact size.")
            if collection_policy is not None:
                self._check_collection_master(collection_policy[1])
            db.execute("INSERT OR REPLACE INTO briefing_cache VALUES(1,?,?,?)", (expected_digest, value["collected_at"], _json(value).decode()))

    def collect(self):
        cfg = self.config()
        if not internet_enabled(self.store, default=True):
            return {"collected": False, "reason": "Internet access is disabled; dated briefing cache is retained."}
        if not cfg["online_sources"] or cfg["paused"] or self.store.setting("paused", False) or not (cfg["region"] or cfg["repository_updates"]):
            return {"collected": False, "reason": "Public-source collection is disabled, paused or awaiting the region."}
        script = _allowed_path(Path(__file__).resolve().parent.parent / "scripts" / "briefing.py")
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA") if key in os.environ}
        process = None
        try:
            revision = self.internet_revision()
            self.check_collection(cfg, revision)
            env.update(ARIADNE_BRIEFING_COLLECTOR="1", ARIADNE_OFFLINE="0", PYTHONDONTWRITEBYTECODE="1",
                       ARIADNE_BRIEFING_INTERNET_REVISION=json.dumps(revision),
                       ARIADNE_BRIEFING_CONFIG_REVISION=str(cfg["revision"]))
            command = [sys.executable, "-I", "-B", str(script), "collect-worker", "--data-dir", str(self.settings.data_dir), "--workspace", str(self.settings.workspace)]
            with self.collector_lock:
                self.check_collection(cfg, revision)
                process = subprocess.Popen(command, env=env, cwd=script.parent.parent,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.collectors.add(process)
            deadline = time.monotonic()+100
            while process.poll() is None:
                self.check_collection(cfg, revision)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return {"collected": False, "reason": "Collector exceeded 100 seconds; dated cache is retained."}
                try:
                    process.wait(timeout=min(.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
            self.check_collection(cfg, revision)
            if process.returncode:
                return {"collected": False, "reason": "Collector failed; dated cache is retained.", "returncode": process.returncode}
            return {"collected": True, "cache": self.cache(cfg), "repository_updates": self.repository_snapshot(cfg)}
        except BriefingCollectionStopped as error:
            return {"collected": False, "reason": str(error)}
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                with self.collector_lock:
                    self.collectors.discard(process)

    def _activity(self, start, end):
        with self.store.connect() as db:
            deadline = time.monotonic()+5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            runs_count = db.execute("SELECT count(*) FROM runs WHERE created_at>=? AND created_at<=? OR updated_at>=? AND updated_at<=?", (start, end, start, end)).fetchone()[0]
            runs = [dict(row) for row in db.execute("SELECT id,title,status,created_at,updated_at,step_count,verification_status,error FROM runs WHERE created_at>=? AND created_at<=? OR updated_at>=? AND updated_at<=? ORDER BY updated_at DESC LIMIT ?", (start, end, start, end, MAX_ROWS))]
            event_count = db.execute("SELECT count(*) FROM events WHERE created_at>=? AND created_at<=?", (start, end)).fetchone()[0]
            event_kinds = [dict(row) for row in db.execute("SELECT kind,count(*) AS count FROM events WHERE created_at>=? AND created_at<=? GROUP BY kind ORDER BY kind", (start, end))]
            events = [dict(row) for row in db.execute("SELECT id,run_id,kind,data,created_at FROM events WHERE created_at>=? AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT ?", (start, end, MAX_ROWS))]
            approvals = [dict(row) for row in db.execute("SELECT id,run_id,tool,status,created_at FROM approvals WHERE status IN ('pending','executing','uncertain') ORDER BY created_at DESC LIMIT ?", (MAX_ROWS,))]
            schedules = [dict(row) for row in db.execute("SELECT id,name,enabled,next_run,last_error FROM schedules ORDER BY next_run LIMIT 100")]
            memories = db.execute("SELECT count(*) FROM memories WHERE created_at>=? AND created_at<=?", (start, end)).fetchone()[0]
            documents = db.execute("SELECT count(*) FROM documents WHERE created_at>=? AND created_at<=?", (start, end)).fetchone()[0]
        for event in events:
            # Logs are local data. Keep bounded evidence without copying full tool outputs.
            data = json.loads(event.pop("data"))
            event["tool"] = _text(data.get("tool", ""), 160) if isinstance(data, dict) else ""
            event["message"] = _text(data.get("message", ""), 400) if isinstance(data, dict) else ""
        procedures = []
        if getattr(self.registry, "procedures", None):
            service = self.registry.procedures.service()
            if service.path.exists():
                with service.database() as db:
                    procedures = [dict(row) for row in db.execute("SELECT request_id,procedure_id,status,created,completed FROM procedure_runs WHERE created>=? AND created<=? OR completed>=? AND completed<=? ORDER BY created DESC LIMIT ?", (start, end, start, end, MAX_ROWS))]
        return {"window_start_utc": datetime.fromtimestamp(start, UTC).isoformat(), "window_end_utc": datetime.fromtimestamp(end, UTC).isoformat(),
                "runs_count": runs_count, "runs": runs, "events_count": event_count, "event_kinds": event_kinds,
                "events": events, "unresolved_approvals": approvals, "schedules": schedules, "new_memories": memories,
                "new_documents": documents, "procedures": procedures, "detail_limit": MAX_ROWS,
                "details_truncated": runs_count > len(runs) or event_count > len(events) or len(procedures) == MAX_ROWS,
                "coverage": "Recorded Ariadne missions, events, fixed reports, approvals and imported metadata; not unlogged PC activity or other applications."}

    def _render(self, report):
        cfg, activity = report["config"], report["activity"]
        lines = ["# Ariadne daily brief", "", "Generated: "+report["generated_at_utc"], "Local date: "+report["local_date"],
                 "Region: "+_md((cfg["region"] or {}).get("name", "Not configured")), "Model calls: 0.", "",
                 "## Recorded activity", "", "Window: "+activity["window_start_utc"]+" to "+activity["window_end_utc"], "",
                 f"Missions touched: {activity['runs_count']}. Events: {activity['events_count']}. New memories: {activity['new_memories']}. New documents: {activity['new_documents']}.",
                 "Unresolved approvals: "+str(len(activity["unresolved_approvals"]))+". Fixed local procedures: "+str(len(activity["procedures"]))+".", "",
                 "| Mission | Status | Steps | Verification |", "|---|---|---|---|"]
        lines.extend("| "+" | ".join(_md(row[key]) for key in ("title", "status", "step_count", "verification_status"))+" |" for row in activity["runs"])
        lines += ["", "Activity coverage: "+activity["coverage"]]
        if activity["details_truncated"]:
            lines.append("Detail rows are truncated at 1,000. Aggregate mission/event counts cover the full recorded window.")
        lines += ["", "## Weather, forecast and regional impacts", ""]
        sources = report["public_sources"]["sources"]
        if not sources:
            lines.append("Unavailable: no public-source cache matches this region. No weather, forecast, alert or news facts are inferred.")
        for source in sources:
            lines += ["", "### "+_md(source["title"]), "", "Source: "+source.get("response_url", source["url"]), "Retrieved: "+source["fetched_at"]+". Status: "+source["status"]+("; STALE" if source.get("stale") else ""),
                      "Issued/updated: "+_md(source.get("issued_at") or "Not supplied by source")]
            if source.get("error"):
                lines.append("Unavailable: "+_md(source["error"])+". An empty result does not mean no hazards.")
            if source.get("refresh_error"):
                lines.append("Refresh failed at "+_md(source.get("last_refresh_attempt_at"))+". Showing the older saved response from the retrieval time above.")
            elif not source["items"]:
                lines.append("No matching items in this saved response; this is not an all-clear or comprehensive news search.")
            for item in source["items"]:
                lines.append("- "+_md(item.get("title", "Item"))+(" (EXPIRED — historical cache)" if item.get("expired") else ""))
                for key in ("summary", "starts", "ends", "expires", "published_at", "url", "distance_km", "severity"):
                    if item.get(key) is not None:
                        lines.append("  "+key.replace("_", " ")+": "+_md(item[key]))
        from .briefing_summary import repository_summary
        radar = repository_summary(report, datetime.fromisoformat(report["generated_at_utc"]))
        lines += ["", "## Repository advances and capability watch", "", radar["description"]]
        if radar["enabled"]:
            lines += ["", "Last saved poll attempt: "+_md(radar["checked_at"] or "Unavailable")+". Status: "+_md(radar["status"])+( "; STALE" if radar["stale"] else ""),
                      "Scope: fixed upstream watchlist; not a new scan of all GitHub, a safety verdict or installation permission."]
            if radar.get("collection_note"):
                lines.append(_md(radar["collection_note"]))
            for item in radar["items"]:
                lines += ["", "- "+_md(item["name"])+": "+_md(item["status"])+"; saved version "+_md(item.get("version") or "not supplied"),
                          "  Checked: "+_md(item.get("checked_at") or "Unavailable")+("; STALE" if item["stale"] else "")]
                if item.get("previous_version"):
                    lines.append("  Previous observed version: "+_md(item["previous_version"]))
                if item.get("published_at"):
                    lines.append("  Publisher release date: "+_md(item["published_at"]))
                if item.get("changes"):
                    lines.append("  Changed fields: "+", ".join(_md(value) for value in item["changes"]))
                if item.get("url"):
                    lines.append("  Source: "+item["url"])
            if radar["omitted"]:
                lines.append("Additional repository rows remain in brief.json; this view is bounded.")
        venture = report.get("venture_checkin", {})
        if venture:
            lines += ["", "## Business and ETF daily check-in", "", "Status: "+_md(venture.get("status", "unavailable"))+". Model calls: 0. Network calls: 0.",
                      "Local drafts are not verified customers, revenue, executable prices or proof of future trading profit."]
            if venture.get("reason"):
                lines.append(_md(venture["reason"]))
            if venture.get("replayed"):
                lines.append("The earlier completed check-in for this local day was verified and reused; no new drafts were created.")
            for draft in venture.get("advanced_tasks", [])[:2]:
                lines.append("- Draft ready: "+_md(draft["title"])+" ("+_md(draft["task_id"])+").")
            for action in venture.get("next_actions", [])[:8]:
                lines.append("- Next: "+_md(action.get("title", action.get("task_id", "Review evidence")))+"; "+_md("; ".join(action.get("blockers", [])) or "Eligible local draft"))
            for artifact in venture.get("artifacts", [])[:2]:
                lines.append("- Local artifact: "+_md(artifact["path"])+"; SHA256 "+artifact["sha256"])
        lines += ["", "## Attention and source limits", ""]
        market = report.get("qqq_paper", {})
        if market and market.get("config", {}).get("enabled"):
            lines += ["", "## QQQ recovery: forward paper account", "", "SIMULATED ONLY. No broker orders. Daily-bar next-open fill proxies; zero model calls."]
            paper = market.get("paper")
            if paper:
                state = paper["state"]
                lines += ["- Equity: $"+_md(state["equity"])+"; cash: $"+_md(state["cash"])+"; QQQ synthetic shares: "+_md(state["shares"]),
                          "- Last completed paper session: "+_md(state["last_processed"])+". Pending: "+_md(state.get("pending") or "none")]
            else:
                lines.append("- Forward paper experiment has not started.")
            quality = market.get("quality") or {}
            lines.append("- Data freshness: "+("current completed session" if quality.get("fresh_now") else "STALE / unavailable; no new paper fills authorized"))
            if market.get("hold"):
                lines.append("- HELD: inspect request "+_md(market["hold"]["request_id"])+"; uncertain work is never replayed.")
            if market.get("backtest"):
                lines.append("- Historical evidence gate: "+_md(market["backtest"].get("research_gate","research only"))+". This is not a real-money recommendation.")
            for event in market.get("events", [])[:5]:
                lines.append("- Recorded paper event: "+_md(json.dumps(event, ensure_ascii=False)))
            lines += ["- Historical backtests are research, not evidence of future profit. Cash distributions use modeled ex-date credit, not actual payment dates."]
        options = report.get("options_paper", {})
        if options and options.get("config", {}).get("enabled"):
            eod_view = options_eod_brief_view(options)
            bankroll_mode = bool(eod_view and eod_view["schema"]==6)
            lines += ["", "## Aggressive options: $25,000 total simulated bankroll" if bankroll_mode else "## Aggressive options: $500 monthly simulated loss budget", "",
                      "SIMULATED ONLY. One total $25,000 bankroll, no automatic top-ups. One shared position; no broker, exercise or real orders. The entire bankroll can be lost; the commitment ceiling is not a drawdown stop." if bankroll_mode else "SIMULATED ONLY. Fully paid long calls/puts share ONE $500 monthly allowance and one open position; no broker, exercise or real orders. Net loss from a flat month start is bounded in the model; this is not a trailing drawdown guarantee."]
            if eod_view:
                lines += ["- Active "+_md(eod_view["symbol"] or "unavailable")+" source: "+_md(eod_view["source_id"] or "unavailable")+". EXPERIMENTAL DATE-ONLY EOD PAPER PRICE PROXIES; no execution qualification or live-price claim.",
                          "- Entry limit: min($1,000, 4% of current equity, available cash, remaining shared monthly gross commitment), including both fees per contract. The $25,000 monthly gross ceiling resets only when verified flat; it adds no cash. No within-month profit recycling." if bankroll_mode else "- All-in entry ceiling: $100 including premium, entry fee and reserved exit fee. All strategies share the existing $500 monthly gross commitment allowance; profits do not replenish it and migration adds no funding.",
                          "- Indicator baseline: "+_md(eod_view["baseline_count"] if eod_view["baseline_count"] is not None else "unavailable")+" saved stock references. Seeds are not trades or returns."]
            account = options.get("paper")
            if account:
                summary = account.get("summary", {})
                for label, key in (("Equity", "equity"), ("External simulated contributions", "contributions"), ("Trading P/L excluding contributions", "trading_pnl"), ("Monthly net P/L", "monthly_pnl"), ("Monthly committed risk including fees", "monthly_budget_used"), ("Monthly risk allowance remaining", "monthly_budget_remaining")):
                    lines.append("- "+label+": $"+_md(summary.get(key, "not measured")))
                lines.append("- Monthly cap assurance: "+("within modeled assumptions" if summary.get("cap_qualified") else "UNAVAILABLE / held; no new risk permitted"))
                lines.append("- Positions: "+_md(summary.get("position") or "none")+". Pending: "+_md(summary.get("pending") or "none"))
                if eod_view:
                    lines.append("- Saved cash: $"+_md(eod_view["cash"] or "not measured")+". Recorded cumulative fees: $"+_md(eod_view["fees"] or "not measured")+". Modeled entries: "+_md(eod_view["trade_count"] if eod_view["trade_count"] is not None else "unavailable")+"; closed positions: "+_md(eod_view["closed_trade_count"] if eod_view["closed_trade_count"] is not None else "unavailable")+".")
                daily = summary.get("last_daily_result")
                if daily and eod_view:
                    lines.append("- Latest saved model result: source date "+_md(eod_view["latest_result_date"] or "unavailable")+", daily P/L $"+_md(eod_view["daily_pnl"] or "not measured")+", actually recorded "+_md(eod_view["result_recorded_at"] or "unavailable")+". This is dated paper accounting, not a verified market fill.")
                elif daily:
                    lines.append("- Latest dated paper result: "+_md(json.dumps(daily, ensure_ascii=False)))
                else:
                    lines.append("- No forward daily trading result yet; starting cash and indicator warmup are not returns.")
                if summary.get("schema") in (4,5,6):
                    lines.append("- Active forward hypothesis: stress-filtered long puts only on the same "+_md(summary.get("symbol") or "unavailable")+" account; experimental, not a selected proven winner.")
                    lines.append("- New BUY decisions require fresh stress context; modeled fills require context published before the source session open. Missing context blocks entries, while position-management and disposal checks continue.")
                    lines.append("- Latest saved stress gate: "+_md(eod_view["stress_gate_stage"] or "not evaluated")+" / "+_md(eod_view["stress_context_reason"] or "waiting for forward observations")+". Context publication: "+_md(eod_view["stress_context_published_at"] or "unavailable")+".")
                if summary.get("schema") in {2, 3, 4, 5, 6}:
                    lines.append("- Active portfolio rule SHA-256: "+_md(eod_view["rule_sha256"] if eod_view else options.get("portfolio_rule_sha256")))
                    lines.append("- Next fixed selector priority: "+_md(summary.get("next_strategy"))+(". Owner-requested stress hypothesis; no outcome ranking." if summary.get("schema") in (4,5,6) else ". Priority rotates after fills, never by backtest profit."))
                    for strategy, stats in summary.get("strategy_stats", {}).items():
                        lines.append("- Forward strategy attribution "+_md(strategy)+": "+_md(json.dumps(stats, ensure_ascii=False)))
            else:
                lines.append("- Forward options experiment has not started.")
            quality = options.get("quality") or {}
            readiness = options.get("paper_readiness")
            if type(readiness) is dict:
                roots = readiness.get("configured_active_paper_roots")
                configured = [root for root in roots[:16] if type(root) is str and len(root)<=16] if type(roots) is list else []
                rows = readiness.get("roots")
                count = len(rows[:16]) if type(rows) is list else 0
                lines.append("- Research/source inventory: "+str(count)+" declared roots; configured paper roots "+_md(", ".join(configured) or "none")+". Historical windows and saved spots do not establish current below-$100 eligibility or additional paper-feed support. The single shared allowance is unchanged.")
                if readiness.get("diagnostic_status")=="unavailable":
                    lines.append("- Optional source inventory unavailable; existing financial controls remain in force.")
            lines.append(("- Retained historical archive evidence: " if eod_view else "- Options data evidence: ")+_md(json.dumps(quality, ensure_ascii=False)))
            forward_quality = options.get("forward_quality") or {}
            if eod_view:
                lines += ["- Saved source session: "+_md(eod_view["source_date"] or "unavailable")+". Actual receipt: "+_md(eod_view["source_received_at"] or "unavailable")+". Option quote-event clock: UNKNOWN; no timestamp is inferred from the source date.",
                          "- Returned chain: "+("PARTIAL" if eod_view["partial_chain"] is True else "no partial warning recorded" if eod_view["partial_chain"] is False else "unavailable")+"; saved quotes: "+_md(eod_view["quote_count"] if eod_view["quote_count"] is not None else "unavailable")+". Missing strikes and side sizes are never reconstructed.",
                          "- Source check: "+_md(eod_view["source_status"])+"; expected completed session: "+_md(eod_view["expected_source_date"] or "unavailable")+". Saved data is "+("current for that session at report generation" if eod_view["fresh_at_report_generation"] else "STALE / unavailable at report generation")+"; this does not establish current executable prices.",
                          "- Collector: "+("enabled" if eod_view["source_enabled"] else "paused / disabled")+". Next eligible check window: "+_md(eod_view["next_check_window"])+" Recorded configured slot: "+_md(eod_view["configured_check_at"] or "unavailable")+"; window "+("open" if eod_view["window_open_at_report_generation"] else "closed")+" at report generation.",
                          "- Last saved collection receipt: "+_md(eod_view["last_check_status"])+" / "+_md(eod_view["last_check_id"] or "unavailable")+" / "+_md(eod_view["last_check_at"] or "unavailable")+". No failed or interrupted work is automatically replayed."]
                if eod_view["missing_source_session"]:
                    lines.append("- Missing publication / source-session gap: no new fill or settlement is inferred; existing exposure and holds remain.")
                if eod_view["last_check_reason"]:
                    lines.append("- Recorded source-check failure: "+_md(eod_view["last_check_reason"]))
                if eod_view["held"]:
                    lines.append("- HELD: "+_md(eod_view["hold_reason"] or "inspect the protected saved receipt")+". No new risk or funding reset is inferred.")
                if eod_view["waiting_for_disposal_source"]:
                    lines.append("- Required disposal source "+_md(eod_view["disposal_source_date"] or "unavailable")+" is missing. Waiting for publication is not a sale or authority to roll funding.")
                lines.append("- Source limitations: personal educational research; original-provider rights, standard contract deliverables and ongoing reliability are not independently verified. This model cannot establish a profitable strategy.")
            else:
                lines.append("- Forward quote evidence: "+_md(json.dumps(forward_quality, ensure_ascii=False)) if forward_quality else "- Forward quotes: waiting_for_recorded_quotes. No new forward daily trading result is available.")
                if forward_quality and not forward_quality.get("fresh_now"):
                    lines.append("- Forward quotes are STALE at this report's generation; no fresh price or completed fill is asserted.")
            if options.get("hold"):
                lines.append("- HELD: "+_md(options["hold"].get("reason", options["hold"].get("request_id"))))
            feed = options.get("feed") or {}
            if not eod_view and feed.get("config", {}).get("initialized"):
                lines.append("- Public AAPL source: "+("enabled" if feed["config"].get("enabled") else "paused")+". "+_md(feed.get("publication")))
                lines.append("- Source delay does not make a stale quote fresh or clear a persisted hold. No account/key or paid data subscription is connected.")
                lines.append("- Source/research retention capacity: "+_md(json.dumps(feed.get("retention") or {},ensure_ascii=False)))
                last_feed = feed.get("last_run") or {}
                lines.append("- Latest source receipt: "+_md(json.dumps({key:last_feed.get(key) for key in ("request_id","status","source_observed_at","retrieved_at","source_sha256","paper_status","provenance")},ensure_ascii=False)))
            if options.get("backtest"):
                lines.append("- Historical research gate: "+_md(options["backtest"].get("research_gate", "not qualified")))
            comparison = options.get("comparison") or {}
            if comparison:
                lines.append("- Expanded historical comparison: "+_md(comparison.get("research_gate"))+". One shared portfolio; single-strategy results below are separate counterfactual $500 accounts, never combined or automatically selected.")
                for label, value in (("shared_portfolio", comparison), *comparison.get("strategy_results", {}).items()):
                    lines.append("- Historical "+_md(label)+": "+_md(json.dumps({key:value.get(key) for key in ("metrics", "coverage", "research_gate")}, ensure_ascii=False)))
                lines.append("- Archive manifest SHA-256: "+_md(comparison.get("manifest_sha256"))+". Assumed EOD/unverified sources remain research only; incomplete ledgers are last dated marks, not full-window realized results.")
                for artifact in comparison.get("artifacts", []):
                    lines.append("- Retained comparison artifact: "+_md(artifact.get("path"))+" / SHA-256 "+_md(artifact.get("sha256")))
            research = options.get("research") or {}
            if research:
                lines.append("- Regime-filtered research only: "+_md(research.get("research_gate"))+". These counterfactuals do not change the active paper strategies or add to its $500 allowance.")
                for label, value in (("new_shared_research", research), ("original_portfolio_same_input", research.get("baseline") or {}), *research.get("strategy_results", {}).items()):
                    lines.append("- Research "+_md(label)+": "+_md(json.dumps({key:value.get(key) for key in ("metrics","coverage","research_gate","eligibility")},ensure_ascii=False)))
                lines.append("- Partial ledgers with different stop dates cannot establish superiority. A zero-trade result is not a profitable strategy; all studies remain retrospective.")
            vix = options.get("vix") or {}
            if vix:
                lines.append("- VIX/VIXW: "+_md(vix.get("paper_status"))+". Forward VIX execution is DISABLED; no forward VIX fills or paper P/L exist. The existing "+_md((account or {}).get("summary", {}).get("symbol") or "options")+" paper account retains the sole shared $500 allowance; no VIX funding was added.")
                lines.append("- VIX index source freshness at report generation: "+_md(json.dumps(vix.get("index_freshness") or {}, ensure_ascii=False))+". A dated index close is not an executable option quote.")
                for reason in vix.get("readiness", []):
                    lines.append("- VIX readiness: "+_md(reason))
                for label in ("signals", "backtest"):
                    saved = vix.get(label) or {}
                    if saved:
                        lines.append("- VIX "+label+" / saved "+_md(saved.get("created_at"))+": "+_md(json.dumps({key:saved.get(key) for key in ("research_gate","summary","coverage")}, ensure_ascii=False)))
                        for artifact in saved.get("artifacts", []):
                            lines.append("- Retained VIX artifact: "+_md(artifact.get("path"))+" / SHA-256 "+_md(artifact.get("sha256")))
                if not vix.get("backtest"):
                    lines.append("- VIX option backtest: unavailable; no qualified historical option bid/ask and matching-expiry reference data imported. Index changes are not option returns.")
                lines.append("- VIX source assessment checked: "+_md((vix.get("source_assessment") or {}).get("checked_at"))+". This is a dated research finding, not a current feed availability guarantee.")
                connection = vix.get("connection_readiness") or {}
                if connection:
                    lines.append("- VIX existing-account route: Robinhood official authenticated MCP / "+_md(connection.get("status"))+"; review "+_md(connection.get("checked_at"))+". Saved source assessment, not a live connection check. Public no-account feed remains unqualified.")
                    for gate in connection.get("gates", []):
                        lines.append("- VIX data gate: "+_md(gate.get("id"))+" / "+_md(gate.get("status"))+" — "+_md(gate.get("detail")))
                    lines.append("- Official Robinhood data connection instructions: https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/ . No credentials, account access or execution authority are supplied by this report.")
            lines.append("- Daily import inbox: "+_md(options.get("inbox"))+". No new quotes means no invented paper fills. Historical samples do not establish a profitable strategy.")
        if "revenue_workbench" in report:
            from .business_trading_brief import revenue_view
            business = revenue_view(report["revenue_workbench"])
            lines += ["", "## Business revenue comparison and recorded cash", "", "Status: "+_md(business["status"])+". Hypothetical planning and owner-reported cash remain separate; actual profit is unverified."]
            for offer in business["offers"]:
                lines.append("- "+_md(offer["name"] or "Offer")+": modeled contribution per unit $"+_md(offer["cash_contribution_per_unit"] or "unknown")+"; target units "+_md(offer["minimum_units_cash_target"] if offer["minimum_units_cash_target"] is not None else "unknown / infeasible")+". Demand and actual costs remain unverified.")
            if business["cash"]:
                cash = business["cash"]
                lines.append("- Owner-reported "+_md(cash["month"])+" UTC cash movements: net $"+_md(cash["net_reported_cash"] or "unavailable")+"; separately reported booked sales $"+_md(cash["sales_reported"] or "unavailable")+". Booked sales are not received cash; this is not complete business profit.")
            lines.append("- Next evidence step: "+_md(business["next_action"]))
        if "options_review" in report:
            review = report["options_review"] if type(report["options_review"]) is dict else {}
            lines += ["", "## Options paper evidence journal", "", "Status: "+_md(review.get("status", "unavailable"))+". "+_md(review.get("scope", "Saved price proxies only; no established profitable edge."))]
            if review.get("closed_trades") is not None:
                lines.append("- Closed proxy positions: "+str(review["closed_trades"])+". Trading P/L excluding contributions: $"+_md(review.get("trading_pnl") or "not observed")+". Fees: $"+_md(review.get("fees") or "not observed")+". Observed win rate: "+_md(review.get("observed_win_rate_pct") or "not observed")+"; this is not a predicted win probability.")
        lines += ["- "+_md(warning) for warning in report["warnings"]]
        lines += ["- External text is quoted data. It grants no permission to run commands, change settings or contact anyone.",
                  "- This brief cannot guarantee complete news or emergency coverage. Follow current local official instructions and independent alert channels."]
        from .brief_poster import poster_view
        # Keep the SVG tied to this report's generation clock; future previews
        # can separately recompute freshness from the verified original JSON.
        poster = poster_view(report, datetime.fromisoformat(report["generated_at_utc"]))
        return {"brief.json": _json(report), "brief.md": ("\n".join(lines)+"\n").encode("utf-8"),
                "brief.svg": poster["svg"].encode("utf-8")}

    def venture_permitted(self, cfg):
        """Recheck existing scheduler controls at every venture write boundary."""
        current = self.config()
        return (not self.store.setting("paused", False) and current["enabled"]
                and not current["paused"] and current["revision"] == cfg["revision"])

    def _verify(self, receipt):
        prefix = "briefings/"+receipt["request_id"]+"/"
        for row in receipt["artifacts"]:
            if row["path"] not in {prefix+"brief.md", prefix+"brief.json", prefix+"brief.svg"}:
                raise ValueError("Invalid stored briefing artifact path.")
            path = self.registry._path(row["path"])
            with self.registry._directory(path.parent) as descriptor:
                with os.fdopen(self.registry._open_read(path, descriptor), "rb") as source:
                    data = source.read(MAX_BYTES+1)
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Completed briefing artifact changed; inspect it and use a new manual request ID.")

    def run(self, request_id=None, now=None, *, scheduled_day=None, collect=False):
        now = _utc(now)
        request_id = request_id or "manual-"+uuid.uuid4().hex
        if type(request_id) is not str or not _REQUEST.fullmatch(request_id):
            raise ValueError("request_id requires 1–64 letters, digits, underscores or hyphens.")
        cfg = self.config()
        day = now.astimezone(_tz(cfg["timezone"])).date()
        if scheduled_day is not None and scheduled_day != day.isoformat():
            raise ValueError("A scheduled brief must belong to the current local day.")
        # Serialize report execution without holding the tool registry or the
        # short metadata lock during a potentially slow collector child.
        with _RUN_LOCK, self._execution(request_id):
            cfg = self.config()
            if self.store.setting("paused", False) or scheduled_day and (cfg["paused"] or not cfg["enabled"]):
                raise BriefingConflict(request_id, "paused", "Briefing execution is paused or the daily schedule was disabled.")
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM briefing_runs WHERE request_id=? OR day=?", (request_id, scheduled_day)).fetchone()
                if row:
                    if row["request_id"] != request_id:
                        raise BriefingConflict(request_id, row["status"], "A brief already owns this scheduled local day.")
                    if row["status"] != "completed":
                        raise BriefingConflict(request_id, row["status"], "This brief previously started or failed; inspect history. It will not replay automatically.")
                    receipt = json.loads(row["receipt"])
                    self._verify(receipt)
                    return {**receipt, "replayed": True}
                if db.execute("SELECT count(*) FROM briefing_runs").fetchone()[0] >= MAX_HISTORY:
                    raise ValueError("Briefing history is full; archive reviewed history before continuing.")
                receipt = {"ok": False, "request_id": request_id, "day": scheduled_day, "status": "running", "created_at": now.timestamp(),
                           "generated_at_utc": _stamp(now), "model_calls": 0, "artifacts": [], "replayed": False}
                db.execute("INSERT INTO briefing_runs VALUES(?,?,?,?,?)", (request_id, scheduled_day, "running", now.timestamp(), _json(receipt).decode()))
            started = time.monotonic()
            try:
                collection = None
                if collect and not cfg["paused"]:
                    try:
                        collection = self.collect()
                    except (OSError, subprocess.SubprocessError, ValueError):
                        collection = {"collected": False, "reason": "Collector unavailable; using dated cache."}
                cache = self.cache(cfg, now)
                end = self._slot(cfg, day) if scheduled_day else now
                start = self._slot(cfg, day-timedelta(days=1)) if scheduled_day else end-timedelta(hours=24)
                warnings = list(cache.get("warnings", []))
                if not cfg["region"]:
                    warnings.append("Region is not configured. Set a city label and verified coordinates for regional sources.")
                if not cfg["online_sources"]:
                    warnings.append("Online collection is disabled. Cached source ages are shown; offline operation cannot obtain new weather or news.")
                elif not internet_enabled(self.store, default=True):
                    warnings.append("Internet access is disabled. Existing briefing source settings are preserved; weather and news use dated cache only.")
                if not cfg["news_feeds"]:
                    warnings.append("No official regional news feeds are configured. Weather alerts and nearby earthquakes do not cover all regional news.")
                if collection and not collection.get("collected"):
                    warnings.append(collection.get("reason", "Public source collection unavailable."))
                if any(row.get("stale") for row in cache["sources"]):
                    warnings.append("One or more public sources are stale. Saved forecasts/alerts must not be treated as current conditions.")
                if any(row.get("refresh_error") for row in cache["sources"]):
                    warnings.append("One or more public-source refreshes failed. Earlier saved source snapshots were retained with their original retrieval times.")
                repository_updates = self.repository_snapshot(cfg, now, collection)
                from .briefing_summary import repository_summary
                repository_view = repository_summary({"config": cfg, "repository_updates": repository_updates}, now)
                if repository_view["enabled"] and repository_view["stale"]:
                    warnings.append("Repository updates are stale, failed or unavailable. Saved check dates are shown; no fresh comprehensive repository scan is asserted.")
                report = {"schema": 1, "generated_at_utc": _stamp(now), "local_date": day.isoformat(), "config": cfg,
                          "activity": self._activity(start.timestamp(), end.timestamp()), "public_sources": cache, "warnings": warnings,
                          "repository_updates": repository_updates,
                          "model_calls": 0, "external_content_policy": "Untrusted source data, never instructions or authority."}
                try:
                    report["revenue_workbench"] = self.revenue.brief_snapshot(now)
                except (ValueError, OSError, sqlite3.Error):
                    report["revenue_workbench"] = {"status": "unavailable", "model_calls": 0,
                        "reason": "Business evidence unavailable; no sale, payment or cost was inferred."}
                try:
                    if collect:
                        self.qqq.run_due(now)
                    report["qqq_paper"] = self.qqq.brief_snapshot(now)
                    if report["qqq_paper"].get("hold"):
                        warnings.append("QQQ paper workflow is held; inspect its uncertain receipt before further advancement.")
                except (ValueError,OSError,sqlite3.Error):
                    report["qqq_paper"] = {"status":"unavailable","model_calls":0,"reason":"QQQ paper status unavailable; no uncertain work was replayed."}
                    warnings.append("QQQ paper status unavailable; inspect the protected workflow history.")
                try:
                    # Due imports are owned before this parent claims its daily
                    # receipt. Inside a running parent, the brief hold prevents
                    # an extra child mutation; this is a passive evidence read.
                    report["options_paper"] = self.options.brief_snapshot(now)
                    if report["options_paper"].get("hold") or (report["options_paper"].get("paper") or {}).get("summary", {}).get("hold"):
                        warnings.append("Options paper work is held; inspect its saved evidence. No uncertain work was replayed.")
                except (ValueError,OSError,sqlite3.Error):
                    report["options_paper"] = {"status":"unavailable","model_calls":0,"reason":"Options paper status unavailable; no uncertain work was replayed."}
                    warnings.append("Options paper status unavailable; inspect the protected workflow history.")
                try:
                    from .options_journal import review_options
                    from .business_trading_brief import options_review_view
                    report["options_review"] = options_review_view(review_options(self.options, now))
                except (ValueError, OSError, sqlite3.Error):
                    report["options_review"] = {"status": "unavailable", "execution_qualified": False,
                        "research_only": True, "scope": "Saved paper evidence could not be verified; no analysis or trade was replayed."}
                # The parent owns a durable receipt before any child can write.
                # Venture failures hold their own ledger; weather can still be
                # delivered without replaying or adopting uncertain child work.
                try:
                    report["venture_checkin"] = self.venture.run_for_brief(
                        "brief-"+hashlib.sha256(request_id.encode()).hexdigest()[:40],
                        local_day=day.isoformat(), now=now,
                        permitted=lambda: self.venture_permitted(cfg))
                except (ValueError, OSError, sqlite3.Error):
                    report["venture_checkin"] = {"status": "unavailable", "model_calls": 0, "artifacts": [],
                        "reason": "Venture check-in unavailable; inspect its protected history. No retry was attempted."}
                if report["venture_checkin"]["status"] in {"held", "unavailable", "interrupted", "failed"}:
                    warnings.append(report["venture_checkin"].get("reason", "Venture work is held for owner review."))
                outputs = self._render(report)
                if any(len(payload) > MAX_BYTES for payload in outputs.values()):
                    raise ValueError("Briefing output exceeds the bounded 2 MB artifact size.")
                relative = "briefings/"+request_id
                destination = self.registry._path(relative)
                from .procedures import Procedures
                publisher = Procedures(self.registry, self.store)
                with self.registry._directory(destination.parent, create=True) as parent_fd:
                    if os.name == "nt":
                        destination.mkdir()
                    else:
                        os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
                    with self.registry._directory(destination) as descriptor:
                        for name, payload in outputs.items():
                            publisher._publish(destination/name, payload, descriptor)
                            receipt["artifacts"].append({"path": relative+"/"+name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
                receipt.update(ok=True, status="completed", completed_at=time.time(), wall_seconds=round(time.monotonic()-started, 6),
                               activity_counts={"missions": report["activity"]["runs_count"], "events": report["activity"]["events_count"]},
                               warnings=warnings, source_count=len(cache["sources"]))
                receipt["repository_source_count"] = len(repository_view["items"]) if repository_view["enabled"] else 0
                receipt["repository_counts"] = repository_view["counts"] if repository_view["enabled"] else None
                with self.database() as db:
                    db.execute("UPDATE briefing_runs SET status='completed',receipt=? WHERE request_id=? AND status='running'", (_json(receipt).decode(), request_id))
                return receipt
            except BaseException as error:
                receipt.update(status="failed", error="Briefing failed; inspect any partial artifacts before starting a new manual request.", wall_seconds=round(time.monotonic()-started, 6))
                try:
                    with self.database() as db:
                        db.execute("UPDATE briefing_runs SET status='failed',receipt=? WHERE request_id=?", (_json(receipt).decode(), request_id))
                    if scheduled_day:
                        self._pause("The scheduled brief failed. Inspect its receipt and artifacts, then explicitly resume the schedule.")
                except (OSError, sqlite3.Error):
                    pass
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ValueError("Briefing failed: "+str(error)[:300]) from error

    def _pause(self, reason):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
            cfg = {**_DEFAULT, **json.loads(row[0])} if row else dict(_DEFAULT)
            cfg.update(paused=True, pause_reason=reason, revision=cfg["revision"]+1)
            db.execute("INSERT OR REPLACE INTO briefing_settings VALUES(1,?)", (_json(cfg).decode(),))

    def run_due(self, now=None):
        now, cfg = _utc(now), self.config()
        if not cfg["enabled"] or cfg["paused"] or self.store.setting("paused", False):
            return None
        day = now.astimezone(_tz(cfg["timezone"])).date()
        slot = self._slot(cfg, day)
        activation = datetime.fromisoformat(cfg["activation_at"]) if cfg["activation_at"] else now
        key = "daily-"+day.isoformat()
        with self.database() as db:
            old = db.execute("SELECT status FROM briefing_runs WHERE day=?", (day.isoformat(),)).fetchone()
        if old:
            if old["status"] in {"failed", "interrupted"}:
                self._pause("Previous daily brief failed or was interrupted. Inspect history before explicitly resuming.")
            return None
        try:
            self.qqq.run_due(now)
        except (ValueError,OSError,sqlite3.Error):
            # QQQ owns its failure hold and cannot suppress weather reporting.
            pass
        if self.qqq.busy():
            return None  # Another due owner must finish before this parent claims the brief.
        try:
            self.options.run_due(now)
        except (ValueError,OSError,sqlite3.Error):
            pass  # An options hold cannot suppress unrelated weather reporting.
        if self.options.busy():
            return None
        if now < slot or slot < activation:
            return None
        try:
            return self.run(key, now, scheduled_day=day.isoformat(), collect=True)
        except BriefingConflict:
            return None  # Another process claimed this exact day.

    def recover(self):
        try:
            with _RUN_LOCK, self._execution("recovery"):
                with self.database() as db:
                    # The execution lock proves no other reporter is live.
                    count = db.execute("SELECT count(*) FROM briefing_runs WHERE status='running' AND day IS NOT NULL").fetchone()[0]
                    db.execute("UPDATE briefing_runs SET status='interrupted' WHERE status='running'")
                if count:
                    self._pause("A scheduled brief was interrupted. Inspect its partial output before explicitly resuming.")
            return True
        except BriefingConflict:
            return False

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                # This source publishes after the open, independently of the
                # immutable6AM brief which may already be complete today.
                try:
                    self.options.collect_due()
                except (ValueError, OSError, sqlite3.Error):
                    pass  # Its durable failure hold must not pause regional reporting.
                self.run_due()
            except Exception:
                # Never loop over a substantive failure every minute.
                try:
                    self._pause("The daily briefing service stopped after an error. Review the saved configuration and history before resuming.")
                except Exception:
                    return
            self.stop_event.wait(60)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.recover()
            self.qqq.stopped.clear()
            self.options.stopped.clear()
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="ariadne-daily-brief", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.qqq.stop()
        self.options.stop()
        with self.collector_lock:
            for process in tuple(self.collectors):
                if process.poll() is None:
                    process.kill()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


def _public_https(url, timeout=8):
    """Pinned public-address HTTPS, no redirects, proxies, cookies or credentials."""
    _source_url(url, official=True)
    parsed = urlsplit(url)
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Public sources must resolve exclusively to public network addresses.")
    family, socktype, protocol, _, address = addresses[0]
    raw = socket.socket(family, socktype, protocol)
    raw.settimeout(timeout)
    connection = None
    try:
        raw.connect(address)
        wrapped = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout)
        connection.sock = wrapped
        path = parsed.path or "/"
        if parsed.query:
            path += "?"+parsed.query
        connection.request("GET", path, headers={"User-Agent": "Ariadne-Local-Brief/0.6 (https://www.weather.gov/documentation/services-web-api)",
                                                 "Accept": "application/geo+json,application/json,application/rss+xml,application/atom+xml;q=0.9",
                                                 "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Public source returned HTTP "+str(response.status)+"; redirects are not followed.")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Compressed public-source responses are not accepted.")
        length = response.getheader("Content-Length")
        if length and int(length) > MAX_BYTES:
            raise ValueError("Public source exceeds the 2 MB response limit.")
        payload = response.read(MAX_BYTES+1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Public source exceeds the 2 MB response limit.")
        return payload
    finally:
        if connection:
            connection.close()
        raw.close()


def _distance_km(lat1, lon1, lat2, lon2):
    a1, a2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = a2-a1, math.radians(lon2-lon1)
    value = math.sin(dlat/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dlon/2)**2
    return 6371*2*math.asin(min(1, math.sqrt(value)))


def _measurement(measure):
    value, unit = measure.get("value"), measure.get("unitCode", "")
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    if unit == "wmoUnit:degC":
        return f"{value*9/5+32:g} °F ({value:g} °C)"
    if unit in {"wmoUnit:percent", "wmoUnit:percentUnit"}:
        return f"{value:g}%"
    if unit == "wmoUnit:km_h-1":
        return f"{value/1.609344:.1f} mph ({value:g} km/h)"
    if unit == "wmoUnit:m_s-1":
        return f"{value*2.2369362921:.1f} mph ({value:g} m/s)"
    return str(value)+" "+_text(unit, 60)


def _feed_items(payload, terms):
    # Restrict feeds to UTF-8: UTF-16/32 must not evade the DTD/entity guard.
    text = payload.decode("utf-8-sig")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD and entity declarations are forbidden in public feeds.")
    declaration = re.match(r"\s*<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)['\"]", text, re.I)
    if declaration and declaration[1].casefold() not in {"utf-8", "utf8", "us-ascii"}:
        raise ValueError("Public feeds must use UTF-8 or ASCII encoding.")
    root = ET.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Expected RSS or Atom feed.")
    items = []
    entries = [row for row in root.iter() if row.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    for row in entries[:500]:
        fields = {}
        for child in row:
            key = child.tag.rsplit("}", 1)[-1]
            if key == "link":
                fields[key] = child.get("href") or child.text or ""
            else:
                fields[key] = "".join(child.itertext())
        title = _text(fields.get("title"), 500)
        summary = _text(html.unescape(re.sub(r"<[^>]+>", " ", fields.get("description") or fields.get("summary") or fields.get("content", ""))), 1500)
        if terms and not any(term.casefold() in (title+" "+summary).casefold() for term in terms):
            continue
        try:
            url = _source_url(fields.get("link", ""))
        except ValueError:
            url = None
        items.append({"title": title, "summary": summary, "url": url,
                      "published_at": _text(fields.get("pubDate") or fields.get("published") or fields.get("updated"), 120) or None})
        if len(items) == 50:
            break
    return items


def collect_external(config, fetch=None, now=None, *, check=None):
    """Fetch a bounded official-source snapshot. Called only in the collector child."""
    collected_clock = (lambda: _utc(now)) if now is not None else _utc
    now, request = _utc(now), fetch or _public_https
    def guard():
        if check is not None:
            check()
    def fetch(url):
        guard()
        payload = request(url)
        guard()
        return payload
    guard()
    region = config.get("region")
    if not region:
        raise ValueError("Configure a verified US region before collecting public sources.")
    lat, lon = region["latitude"], region["longitude"]
    point = f"{lat:.4f},{lon:.4f}"
    sources = []
    point_info = None
    def source(kind, title, url, load):
        guard()
        row = {"kind": kind, "title": title, "url": url, "fetched_at": _stamp(now), "issued_at": None,
               "status": "unavailable", "items": [], "stale": False}
        try:
            loaded = load()
            data, issued, items = loaded[:3]
            row.update(status="available", issued_at=_text(issued, 120) or None, items=items,
                       response_sha256=hashlib.sha256(data).hexdigest())
            if len(loaded) == 4:
                row["response_url"] = _source_url(loaded[3], official=True)
        except (ValueError, KeyError, TypeError, IndexError, OverflowError, OSError, ET.ParseError, http.client.HTTPException):
            row["error"] = "The official source could not be fetched or parsed. No current facts are asserted."
        # Stamp after the response arrives, independently for each source.
        row["fetched_at"] = _stamp(collected_clock())
        sources.append(row)
    def forecast():
        nonlocal point_info
        point_info = json.loads(fetch(_NWS+"/points/"+point))
        url = point_info["properties"]["forecast"]
        if not re.fullmatch(r"https://api\.weather\.gov/gridpoints/[A-Z]{3}/[0-9]{1,4},[0-9]{1,4}/forecast", url):
            raise ValueError("NWS returned an unexpected forecast endpoint.")
        payload = fetch(url)
        info = json.loads(payload)["properties"]
        items = [{"title": _text(row.get("name"), 120), "summary": _text(row.get("detailedForecast"), 3000),
                  "starts": _text(row.get("startTime"), 100), "ends": _text(row.get("endTime"), 100), "url": url}
                 for row in info["periods"][:14]]
        return payload, info.get("updated") or info.get("generatedAt"), items, url
    source("forecast", "National Weather Service seven-day forecast", _NWS+"/points/"+point, forecast)
    def weather():
        nonlocal point_info
        if point_info is None:
            point_info = json.loads(fetch(_NWS+"/points/"+point))
        stations = point_info["properties"]["observationStations"]
        if not re.fullmatch(r"https://api\.weather\.gov/gridpoints/[A-Z]{3}/[0-9]{1,4},[0-9]{1,4}/stations", stations):
            raise ValueError("NWS returned an unexpected station endpoint.")
        features = json.loads(fetch(stations))["features"]
        station = features[0]["properties"]["stationIdentifier"]
        if type(station) is not str or not re.fullmatch(r"[A-Z0-9]{3,8}", station):
            raise ValueError("NWS returned an invalid station identifier.")
        url = _NWS+"/stations/"+station+"/observations/latest"
        payload = fetch(url)
        info = json.loads(payload)["properties"]
        conditions = []
        for key, label in (("temperature", "temperature"), ("relativeHumidity", "humidity"), ("windSpeed", "wind")):
            measure = info.get(key) or {}
            formatted = _measurement(measure)
            if formatted is not None:
                conditions.append(label+": "+formatted)
        summary = _text(info.get("textDescription"), 300)+("; "+"; ".join(conditions) if conditions else "")
        return payload, info.get("timestamp"), [{"title": "Latest reported station observation: "+station,
                                               "summary": summary, "published_at": _text(info.get("timestamp"), 100), "url": url}], url
    source("weather", "National Weather Service latest station observation", _NWS+"/points/"+point, weather)
    alert_url = _NWS+"/alerts/active?point="+point
    def alerts():
        payload = fetch(alert_url)
        info = json.loads(payload)
        items = []
        for feature in info["features"][:100]:
            row = feature["properties"]
            items.append({"title": _text(row.get("headline") or row.get("event"), 500),
                          "summary": _text(row.get("description"), 4000), "severity": _text(row.get("severity"), 40),
                          "expires": _text(row.get("expires"), 100) or None,
                          "published_at": _text(row.get("sent"), 100) or None, "url": _text(feature.get("id"), 2000) or alert_url})
        return payload, info.get("updated"), items
    source("alerts", "National Weather Service point alerts", alert_url, alerts)
    def quakes():
        payload = fetch(_QUAKES)
        info = json.loads(payload)
        items = []
        for feature in info["features"][:5000]:
            coordinates, row = feature["geometry"]["coordinates"], feature["properties"]
            distance = _distance_km(lat, lon, float(coordinates[1]), float(coordinates[0]))
            if distance <= 500:
                items.append({"title": _text(row.get("title") or row.get("place"), 500),
                              "summary": "USGS M"+str(row.get("mag"))+"; location "+_text(row.get("place"), 200)+". Preliminary source data may be revised.",
                              "distance_km": round(distance, 1), "published_at": datetime.fromtimestamp(row["time"]/1000, UTC).isoformat(),
                              "url": _source_url(row["url"])})
        items.sort(key=lambda row: row["distance_km"])
        issued = datetime.fromtimestamp(info["metadata"]["generated"]/1000, UTC).isoformat()
        return payload, issued, items[:100]
    source("earthquakes", "USGS magnitude 2.5+ earthquakes within 500 km, past day", _QUAKES, quakes)
    for feed in config.get("news_feeds", []):
        url = _source_url(feed["url"], official=True)
        def news(feed=feed, url=url):
            payload = fetch(url)
            return payload, None, _feed_items(payload, feed.get("region_terms", []))
        source("news", feed["title"], url, news)
    guard()
    return {"schema": 1, "collected_at": _stamp(now), "region": region, "sources": sources,
            "warnings": ["These are bounded official feeds, not a comprehensive regional news search. Source and retrieval times are distinct."]}
