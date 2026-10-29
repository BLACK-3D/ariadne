"""Fixed, read-only repository metadata checks for the local daily brief.

Only the existing enabled briefing collector may use real public networking.
Remote metadata remains data; this module never installs or executes it.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import quote

from .atomic import sync_directory
from .config import _allowed_path
from .locking import InstanceLock

UTC = timezone.utc
MAX_SOURCES = 16
MAX_RESPONSE_BYTES = 262_144
MAX_CACHE_BYTES = 262_144
MAX_SECONDS = 45
STALE_SECONDS = 36 * 3600
WATCHLIST_SHA256 = "df27a94e240e8a6a29b1abac8512744d888f89ff29f16564268c71a6803a5cd4"
WATCHLIST_PATH = Path(__file__).resolve().parents[1] / "config/repository-watchlist.json"
CACHE_NAME = "repository-radar-cache.json"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_STATUSES = {"baseline", "changed", "unchanged", "cached_refresh_failed", "unavailable"}
_OBSERVATIONS = ("version", "revision", "published_at", "title", "license", "notes_sha256")
_ROW_KEYS = {"id", "name", "domain", "repo", "kind", "url", "status", "checked_at", "last_attempt_at",
             "version", "revision", "published_at", "title", "license", "notes_sha256", "response_sha256",
             "previous_version", "previous_revision", "changes", "error"}
_WARNINGS = [
    "Fixed upstream release, commit and model metadata only; not an exhaustive repository or vulnerability scan.",
    "A release observation is not a security approval, license review or installed capability. Nothing is installed or executed.",
    "Offline and failed refreshes retain dated observations; no missing response is presented as no upstream changes.",
    "GitHub release/license/source trees and Hugging Face weight/dependency terms require separate qualification before adoption.",
]


def _paths():
    from .tools import ToolRegistry
    return object.__new__(ToolRegistry)


def _now(value=None):
    value = value or datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Repository radar requires an aware clock.")
    return value.astimezone(UTC)


def _date(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("Repository metadata has an invalid timestamp.")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Repository metadata timestamp needs a timezone.")
    return result.astimezone(UTC).isoformat()


def _text(value, limit):
    if value is None:
        return ""
    if type(value) is not str:
        raise ValueError("Repository metadata text has an invalid type.")
    # Truncation may land on the separating space between two words. Remove it
    # after bounding so validating already-normalized text is idempotent.
    return " ".join("".join(c for c in value if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF
                              and ord(c) not in {127, 0xFFFE, 0xFFFF}).split())[:limit].rstrip()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _parse(payload):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate repository metadata keys.")
            result[key] = value
        return result
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite repository metadata.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Repository metadata is not bounded UTF-8 JSON.") from None


def _read(path, cap):
    path = _allowed_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > cap:
        raise ValueError("Repository radar inputs must be bounded regular files with one link.")
    paths = _paths()
    with paths._directory(path.parent) as parent:
        with os.fdopen(paths._open_read(path, parent), "rb") as source:
            before = os.fstat(source.fileno())
            payload = source.read(cap + 1)
            after = os.fstat(source.fileno())
            current = path.lstat()
            if (len(payload) > cap or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                    (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino) or current.st_nlink != 1):
                raise ValueError("Repository radar input changed while being read.")
    return payload


def watchlist():
    payload = _read(WATCHLIST_PATH, 32_768)
    if payload is None or hashlib.sha256(payload).hexdigest() != WATCHLIST_SHA256:
        raise ValueError("Repository watchlist differs from its reviewed source pin.")
    value = _parse(payload)
    if type(value) is not dict or set(value) != {"schema", "scope", "entries"} or value["schema"] != 1:
        raise ValueError("Unsupported repository watchlist.")
    entries = value["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_SOURCES:
        raise ValueError("Repository watchlist exceeds its source limit.")
    ids, repos = set(), set()
    for row in entries:
        if type(row) is not dict or set(row) not in ({"id", "name", "domain", "kind", "repo"}, {"id", "name", "domain", "kind", "repo", "branch"}):
            raise ValueError("Repository watchlist entry is invalid.")
        if (type(row["id"]) is not str or not _ID.fullmatch(row["id"]) or row["id"] in ids
                or type(row["repo"]) is not str or not _REPO.fullmatch(row["repo"]) or row["repo"].casefold() in repos
                or row["kind"] not in {"github_release", "github_commit", "hf_model"}
                or row["domain"] not in {"business", "trading", "controls", "ai_core"}
                or type(row["name"]) is not str or row["name"] != _text(row["name"], 100)):
            raise ValueError("Repository watchlist identity is invalid.")
        if row["kind"] == "github_commit" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", row.get("branch", "")):
            raise ValueError("Repository commit watch needs a fixed branch.")
        ids.add(row["id"])
        repos.add(row["repo"].casefold())
    return entries


def _endpoint(entry):
    prefix = "https://api.github.com/repos/" + entry["repo"]
    if entry["kind"] == "github_release":
        return prefix + "/releases/latest"
    if entry["kind"] == "github_commit":
        return prefix + "/commits/" + entry["branch"]
    return "https://huggingface.co/api/models/" + entry["repo"]


def _link(entry, observation=None):
    observation = observation or {}
    if entry["kind"] == "hf_model":
        return "https://huggingface.co/" + entry["repo"]
    prefix = "https://github.com/" + entry["repo"]
    if entry["kind"] == "github_commit":
        return prefix + ("/commit/" + observation["revision"] if observation.get("revision") else "/commits/" + entry["branch"])
    return prefix + ("/releases/tag/" + quote(observation["version"], safe="") if observation.get("version") else "/releases")


def _observation(entry, payload):
    if type(payload) is not bytes or len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("Repository response exceeds its 256 KiB parsed-input limit.")
    info = _parse(payload)
    if type(info) is not dict:
        raise ValueError("Repository response is not an object.")
    row = {key: None for key in _OBSERVATIONS}
    if entry["kind"] == "github_release":
        tag = info.get("tag_name")
        if (type(tag) is not str or not 1 <= len(tag) <= 120 or tag != _text(tag, 120)
                or info.get("draft") is not False or info.get("prerelease") is not False):
            raise ValueError("Repository release is missing, draft or prerelease.")
        row.update(version=tag, title=_text(info.get("name") or tag, 240), published_at=_date(info.get("published_at")))
        notes = info.get("body") or ""
        if type(notes) is not str or len(notes) > 180_000:
            raise ValueError("Repository release notes exceed their limit.")
        row["notes_sha256"] = hashlib.sha256(notes.encode("utf-8")).hexdigest()
    elif entry["kind"] == "github_commit":
        revision = info.get("sha")
        if type(revision) is not str or not _COMMIT.fullmatch(revision):
            raise ValueError("Repository commit identity is invalid.")
        commit = info.get("commit")
        if type(commit) is not dict or type(commit.get("committer")) is not dict:
            raise ValueError("Repository commit metadata is invalid.")
        row.update(revision=revision, version=revision[:12], title=_text(commit.get("message"), 240),
                   published_at=_date(commit["committer"].get("date")))
    else:
        revision = info.get("sha")
        if type(revision) is not str or not _COMMIT.fullmatch(revision) or info.get("id") != entry["repo"]:
            raise ValueError("Model repository identity is invalid.")
        card = info.get("cardData") or {}
        if type(card) is not dict:
            raise ValueError("Model card metadata is invalid.")
        license = card.get("license")
        if isinstance(license, list) and len(license) <= 4:
            license = ", ".join(_text(item, 60) for item in license)
        row.update(revision=revision, version=revision[:12], title=entry["name"],
                   published_at=_date(info.get("lastModified")), license=_text(license, 120) or None)
    row["response_sha256"] = hashlib.sha256(payload).hexdigest()
    return row


def _empty_row(entry, stamp):
    return {**{key: entry[key] for key in ("id", "name", "domain", "repo", "kind")}, "url": _link(entry),
            "status": "unavailable", "checked_at": None, "last_attempt_at": stamp,
            **{key: None for key in _OBSERVATIONS}, "response_sha256": None,
            "previous_version": None, "previous_revision": None, "changes": [], "error": None}


def _validate_cache(value, entries, now):
    if (type(value) is not dict or set(value) != {"schema", "watchlist_sha256", "generation", "day_key", "checked_at", "sources"}
            or type(value["schema"]) is not int or value["schema"] != 1 or value["watchlist_sha256"] != WATCHLIST_SHA256
            or type(value["generation"]) is not int or not 1 <= value["generation"] <= 1_000_000_000
            or type(value["day_key"]) is not str or date.fromisoformat(value["day_key"]).isoformat() != value["day_key"]
            or type(value["sources"]) is not list or len(value["sources"]) != len(entries)):
        raise ValueError("Repository cache has an unsupported schema or watchlist identity.")
    clocks = [_date(value["checked_at"])]
    for entry, row in zip(entries, value["sources"]):
        if type(row) is not dict or set(row) != _ROW_KEYS or any(row.get(key) != entry[key] for key in ("id", "name", "domain", "repo", "kind")):
            raise ValueError("Repository cache entry differs from its fixed identity.")
        if row["status"] not in _STATUSES or row["url"] != _link(entry, row):
            raise ValueError("Repository cache status or source link is invalid.")
        for key, limit in (("version", 120), ("title", 240), ("license", 120), ("previous_version", 120), ("error", 200)):
            if row[key] is not None and (type(row[key]) is not str or row[key] != _text(row[key], limit)):
                raise ValueError("Repository cache contains unsupported text.")
        for key in ("revision", "previous_revision"):
            if row[key] is not None and (type(row[key]) is not str or not _COMMIT.fullmatch(row[key])):
                raise ValueError("Repository cache revision is invalid.")
        for key in ("notes_sha256", "response_sha256"):
            if row[key] is not None and (type(row[key]) is not str or not _HEX.fullmatch(row[key])):
                raise ValueError("Repository cache digest is invalid.")
        if type(row["changes"]) is not list or any(key not in _OBSERVATIONS for key in row["changes"]) or len(set(row["changes"])) != len(row["changes"]):
            raise ValueError("Repository cache change fields are invalid.")
        clocks.append(_date(row["last_attempt_at"]))
        if row["checked_at"] is not None:
            clocks.append(_date(row["checked_at"]))
            if row["response_sha256"] is None or not row["version"]:
                raise ValueError("Checked repository cache lacks its observation identity.")
        elif row["status"] != "unavailable" or row["changes"]:
            raise ValueError("Unchecked repository cache claims an observation.")
        if row["published_at"] is not None:
            _date(row["published_at"])
    if any((datetime.fromisoformat(clock) - now).total_seconds() > 300 for clock in clocks):
        raise ValueError("Repository cache was checked in the future; inspect the system clock.")
    return value


def _cache(data_dir, entries, now):
    payload = _read(_allowed_path(data_dir) / CACHE_NAME, MAX_CACHE_BYTES)
    return None if payload is None else _validate_cache(_parse(payload), entries, now)


def _view(cache, entries, now, *, fresh=False, blocked=None):
    rows = []
    for saved in cache["sources"] if cache else []:
        row = dict(saved)
        row["age_seconds"] = max(0, round((now - datetime.fromisoformat(row["checked_at"])).total_seconds())) if row["checked_at"] else None
        row["stale"] = row["age_seconds"] is None or row["age_seconds"] > STALE_SECONDS
        rows.append(row)
    counts = {"changed": sum(row["status"] == "changed" for row in rows),
              "baseline": sum(row["status"] == "baseline" for row in rows),
              "unchanged": sum(row["status"] == "unchanged" for row in rows),
              "failed": sum(row["status"] in {"unavailable", "cached_refresh_failed"} for row in rows)}
    usable = any(row["checked_at"] for row in rows)
    status = "unavailable" if not cache or not usable else "fresh" if fresh and not counts["failed"] else "partial" if fresh else "cached"
    warnings = list(_WARNINGS)
    if blocked:
        warnings.insert(0, blocked)
    elif not cache:
        warnings.insert(0, "No repository update cache is available; no upstream facts are asserted.")
    return {"schema": 1, "watchlist_sha256": WATCHLIST_SHA256, "checked_at": cache["checked_at"] if cache else None,
            "view_at": now.isoformat(), "day_key": cache["day_key"] if cache else None,
            "generation": cache["generation"] if cache else 0, "status": status, "refreshed": fresh,
            "stale": not rows or any(row["stale"] for row in rows), "blocked": bool(blocked),
            "source_count": len(rows), "watchlist_count": len(entries), "counts": counts, "sources": rows,
            "warnings": warnings, "model_calls": 0,
            "limits": {"sources": MAX_SOURCES, "parsed_response_bytes": MAX_RESPONSE_BYTES,
                       "transport_response_bytes": MAX_RESPONSE_BYTES, "cache_bytes": MAX_CACHE_BYTES,
                       "collection_seconds": MAX_SECONDS, "stale_seconds": STALE_SECONDS},
            "scope": "Fixed release/commit/model metadata only; no software, trade or device actions."}


def load_snapshot(data_dir, now=None):
    """Return dated metadata without network, initialization or cache mutation."""
    now = _now(now)
    try:
        entries = watchlist()
        cache = _cache(data_dir, entries, now)
    except (ValueError, OSError, TypeError, KeyError, OverflowError):
        return _view(None, [], now, blocked="Repository cache or reviewed watchlist is invalid; collection and overwrite are held for review.")
    return _view(cache, entries, now)


def _save(path, value, check):
    payload = _json(value)
    if len(payload) > MAX_CACHE_BYTES:
        raise ValueError("Repository cache exceeds its size limit.")
    _read(path, MAX_CACHE_BYTES)  # Revalidate a preexisting leaf; never follow links.
    stage = path.with_name(".repository-radar-cache-" + secrets.token_hex(12) + ".tmp")
    identity = None
    try:
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        identity = os.fstat(fd)
        with os.fdopen(fd, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        check()
        _read(path, MAX_CACHE_BYTES)
        os.replace(stage, path)
        sync_directory(path.parent)
    finally:
        if identity is not None:
            try:
                current = stage.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    stage.unlink()
            except FileNotFoundError:
                pass


def collect_radar(data_dir, fetch=None, now=None, check=None, publication_lease=None, day_key=None):
    """Check the fixed watchlist once per supplied briefing local day.

    Production calls require the existing collector environment, a live policy
    check and a main-settings + briefing-settings publication lease. Injected
    fetches are for deterministic qualification and perform no automatic I/O.
    """
    explicit_now = now
    now = _now(now)
    day_key = day_key or now.date().isoformat()
    if type(day_key) is not str or date.fromisoformat(day_key).isoformat() != day_key:
        raise ValueError("Repository radar day key must be an ISO date.")
    real_network = fetch is None
    if real_network and (os.environ.get("ARIADNE_BRIEFING_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0"
                         or not callable(check) or not callable(publication_lease)):
        raise ValueError("Repository collection requires the enabled briefing collector and its publication lease.")
    guard = check or (lambda: None)
    guard()
    entries = watchlist()
    data_dir = _allowed_path(data_dir)
    paths = _paths()
    if not data_dir.is_dir():
        raise ValueError("Repository collection data directory must already exist.")
    owner = InstanceLock(data_dir / "repository-radar.lock")
    owner.acquire()
    try:
        with paths._directory(data_dir):
            try:
                old = _cache(data_dir, entries, now)
            except (ValueError, OSError, TypeError, KeyError, OverflowError):
                return _view(None, entries, now, blocked="Repository cache is invalid; no collection or overwrite was attempted.")
            if old and old["day_key"] == day_key:
                guard()
                return _view(old, entries, now)
            deadline = time.monotonic() + MAX_SECONDS
            if real_network:
                from .internet import public_get
                def fetch(url):
                    return public_get(url, deadline=min(deadline, time.monotonic() + 10), max_redirects=0, max_bytes=MAX_RESPONSE_BYTES)
            previous = {row["id"]: row for row in old["sources"]} if old else {}
            rows = []
            for entry in entries:
                guard()
                stamp = _now(explicit_now).isoformat()
                saved = previous.get(entry["id"])
                row = _empty_row(entry, stamp)
                if saved and saved["checked_at"]:
                    row = {**saved, "status": "cached_refresh_failed", "last_attempt_at": stamp,
                           "previous_version": None, "previous_revision": None, "changes": [], "error": None}
                try:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Repository collection deadline expired.")
                    endpoint = _endpoint(entry)
                    response = fetch(endpoint)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Repository response arrived after the collection deadline.")
                    if isinstance(response, dict):
                        if response.get("url") != endpoint or response.get("requested_url") != endpoint:
                            raise ValueError("Repository endpoint redirected away from its fixed identity.")
                        response = response.get("payload")
                    observation = _observation(entry, response)
                except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError, http.client.HTTPException):
                    row["error"] = "Upstream metadata was unavailable, redirected, unsupported or exceeded its limit; no new facts are asserted."
                else:
                    changes = [key for key in _OBSERVATIONS if saved and saved["checked_at"] and saved.get(key) != observation.get(key)]
                    row.update(observation, checked_at=_now(explicit_now).isoformat(), last_attempt_at=stamp,
                               status="changed" if changes else "unchanged" if saved and saved["checked_at"] else "baseline",
                               previous_version=saved["version"] if changes else None,
                               previous_revision=saved["revision"] if changes else None,
                               changes=changes, error=None)
                    row["url"] = _link(entry, row)
                guard()  # Revocation is never converted into a source failure.
                rows.append(row)
            cache = {"schema": 1, "watchlist_sha256": WATCHLIST_SHA256, "generation": old["generation"] + 1 if old else 1,
                     "day_key": day_key, "checked_at": _now(explicit_now).isoformat(), "sources": rows}
            _validate_cache(cache, entries, _now(explicit_now))
            guard()
            with publication_lease() if publication_lease else nullcontext():
                guard()
                _save(data_dir / CACHE_NAME, cache, guard)
            return _view(cache, entries, _now(explicit_now), fresh=True)
    finally:
        owner.release()
