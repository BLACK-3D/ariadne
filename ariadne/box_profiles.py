"""Sanitized factory profiles and exclusive first-launch bootstrap for a moved box."""
from __future__ import annotations

import json
import os
import re
import stat
import importlib.util
import secrets
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from .config import _allowed_path, local_service_port
from .atomic import publish_bytes, commit_staged, sync_directory

PROFILE_NAMES = ("voice-config.json", "ocr-config.json", "media-config.json", "knowledge-config.json")
VOICE_KEYS = {"whisper_bin", "whisper_model", "piper_bin", "piper_model"}
KNOWLEDGE_KEYS = {"schema", "semantic", "monty", "docling", "kiwix"}
KNOWLEDGE_FIELDS = {"site_packages", "packages", "assets", "lock_sha256", "binary", "binary_sha256", "model", "dimensions",
    "model_path", "model_repository", "model_revision", "mode", "version", "archives", "download", "sources", "artifacts_path"}
_PRIVATE_KEY = re.compile(r"(?:^|[_-])(?:password|passwd|secret|credentials?|authorization|api[_-]?key|token|headers|endpoint|url_base)(?:$|[_-])", re.I)


def regular(path, maximum=1_000_000):
    path = _allowed_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValueError("Factory profiles require regular unlinked bounded files.")
    return path


def _shape(name, value):
    if type(value) is not dict:
        raise ValueError("Factory profile must be a JSON object.")
    allowed = {"voice-config.json": VOICE_KEYS, "ocr-config.json": {"schema", "python", "models", "packages", "processor", "lock_sha256"},
        "media-config.json": {"version", "backends"}, "knowledge-config.json": KNOWLEDGE_KEYS}[name]
    if set(value) - allowed:
        raise ValueError("Profile contains fields outside the reviewed factory schema.")
    if name == "knowledge-config.json":
        for key, profile in value.items():
            if key == "schema":
                continue
            if type(profile) is not dict or set(profile) - KNOWLEDGE_FIELDS:
                raise ValueError("Knowledge profile contains unreviewed owner settings.")
            if key == "kiwix" and profile.get("mode") != "managed":
                raise ValueError("Owner-registered Kiwix servers are not factory configuration.")
    if name == "media-config.json":
        from .media_tools import ASSET_KEYS
        if value.get("version") != 1 or type(value.get("backends")) is not dict or set(value["backends"]) - set(ASSET_KEYS):
            raise ValueError("Unsupported factory media backend.")
        for backend, profile in value["backends"].items():
            if (type(profile) is not dict or set(profile) != {"assets"} or type(profile["assets"]) is not dict
                    or set(profile["assets"]) != ASSET_KEYS[backend]):
                raise ValueError("Unreviewed media backend fields cannot enter factory configuration.")
            for asset in profile["assets"].values():
                if type(asset) is not dict or set(asset) != {"path", "sha256"}:
                    raise ValueError("Unreviewed media asset metadata cannot enter factory configuration.")


def _transform(value, root, *, expand=False):
    if type(value) is dict:
        if any(type(key) is not str or _PRIVATE_KEY.search(key) for key in value):
            raise ValueError("Private credentials or authority settings cannot enter a factory profile.")
        return {key: _transform(item, root, expand=expand) for key, item in value.items()}
    if type(value) is list:
        return [_transform(item, root, expand=expand) for item in value]
    if type(value) is str:
        if expand:
            if value == "${BOX}" or value.startswith("${BOX}/"):
                relative = value.removeprefix("${BOX}").lstrip("/")
                if any(part in {"", ".", ".."} for part in relative.split("/")):
                    raise ValueError("Factory path has ambiguous components.")
                target = _allowed_path(root / relative)
                if not target.is_relative_to(root):
                    raise ValueError("Factory path escapes the relocated box.")
                return str(target)
            if "${" in value:
                raise ValueError("Unsupported factory interpolation marker.")
        else:
            if value.startswith("${BOX}/"):
                return value
            if "${" in value:
                raise ValueError("Profile contains unsupported interpolation.")
        if value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or parsed.query:
                raise ValueError("Factory provenance URLs must be public HTTPS addresses.")
            return value
        if Path(value).is_absolute() or PureWindowsPath(value).drive:
            path = _allowed_path(value)
            if not path.is_relative_to(root):
                raise ValueError("Factory profiles cannot expose paths outside the box.")
            if expand:
                raise ValueError("Factory templates must use relocatable markers, not absolute paths.")
            return "${BOX}/" + path.relative_to(root).as_posix()
        return value
    if value is None or type(value) in {bool, int, float}:
        return value
    raise ValueError("Unsupported factory profile value.")


def make_template(root, data_dir=None):
    root = _allowed_path(root)
    data_dir = _allowed_path(data_dir or root / "data")
    profiles = {}
    for name in PROFILE_NAMES:
        path = _allowed_path(data_dir / name)
        if not path.exists():
            continue
        profile = json.loads(regular(path).read_text(encoding="utf-8"))
        _shape(name, profile)
        sanitized = _transform(profile, root)
        if name == "ocr-config.json":
            sanitized["python"] = "${BOX}/runtime/python/python.exe"
        profiles[name] = sanitized
    return {"schema": 1, "default_model": "qwen3.5:9b", "factory_local_flows": True, "profiles": profiles}


def _checkpoint(stage):
    """Fault-injection seam. Production performs no action here."""


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _publish(path, payload, label):
    publish_bytes(path, payload, checkpoint=lambda: _checkpoint(label + ":staged"))
    _checkpoint(label + ":committed")


@contextmanager
def _bootstrap_lock(data_dir):
    """OS locks release on process death; a persistent lock name is not a stale lock."""
    path = _allowed_path(data_dir / ".box-bootstrap.lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1:
            raise ValueError("Invalid bootstrap lock file.")
        if info.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = time.monotonic() + 30
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another first-launch bootstrap is still active.") from None
                time.sleep(.05)
        yield
    finally:
        if acquired:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _flow_payloads(root):
    script = regular(root / "scripts/local_services.py")
    specification = importlib.util.spec_from_file_location("_ariadne_factory_local_services", script)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    payloads = module.factory_payloads()
    if set(payloads) != {"settings.js", "flows.json"} or any(type(value) is not bytes for value in payloads.values()):
        raise ValueError("Unsupported fixed flow payloads.")
    return payloads


def _flow_config():
    routes = [{"id": identifier, "method": method, "url": f"http://127.0.0.1:{local_service_port('ARIADNE_FLOW_PORT', 1880)}/ariadne/" + path,
        "token_file": "flows.token", "description": "Fixed local runtime " + path + "; no external effect"}
        for identifier, method, path in [("flows_health", "GET", "health"), ("local_event", "POST", "event")]]
    return {"apps": [], "flows": routes, "erpnext": None}


def _new_journal(root, data_dir, enabled):
    assets = [root / "runtime/automation/node-v24.22.1-win-x64/node.exe", root / "runtime/automation/node-red/node_modules/node-red/red.js"]
    clear = not any(_allowed_path(data_dir / name).exists() for name in ("automation-config.json", "flows", "flows.token"))
    factory = enabled and data_dir == root / "data" and clear and all(_allowed_path(path).is_file() for path in assets)
    return {"schema": 1, "transaction": secrets.token_hex(16), "default_model": "qwen3.5:9b",
        "factory_local_flows": bool(factory), "flow_token": secrets.token_urlsafe(32) if factory else None}


def _read_journal(path):
    value = json.loads(regular(path, 4096).read_text(encoding="utf-8"))
    if (type(value) is not dict or set(value) != {"schema", "transaction", "default_model", "factory_local_flows", "flow_token"}
            or value["schema"] != 1 or value["default_model"] != "qwen3.5:9b"
            or type(value["transaction"]) is not str or not re.fullmatch("[0-9a-f]{32}", value["transaction"])
            or type(value["factory_local_flows"]) is not bool
            or (value["factory_local_flows"] and (type(value["flow_token"]) is not str or not re.fullmatch("[A-Za-z0-9_-]{43}", value["flow_token"])))
            or (not value["factory_local_flows"] and value["flow_token"] is not None)):
        raise ValueError("Invalid pending factory bootstrap journal; existing state was preserved.")
    return value


def _database_owned(database, transaction):
    """Read the transaction claim without migrating or changing a possibly owner-created DB."""
    regular(database, 1 << 40)
    for suffix in ("-wal", "-shm"):
        candidate = _allowed_path(str(database) + suffix)
        if candidate.exists():
            regular(candidate, 1 << 40)
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT value FROM settings WHERE key='box_bootstrap_transaction'").fetchone()
            return row is not None and json.loads(row[0]) == transaction
    except (sqlite3.Error, ValueError):
        return False


def _seed_database(database, journal):
    from .store import Store
    stage = _allowed_path(database.with_name(".ariadne.sqlite3.box-" + journal["transaction"] + "-" + secrets.token_hex(8) + ".tmp"))
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    try:
        store = Store(stage)
        with store.connect() as connection:
            connection.execute("INSERT INTO settings VALUES (?,?)", ("model", json.dumps(journal["default_model"])))
            connection.execute("INSERT INTO settings VALUES (?,?)", ("box_bootstrap_transaction", json.dumps(journal["transaction"])))
        with store.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with stage.open("r+b") as source:
            os.fsync(source.fileno())
        _checkpoint("database:staged")
        commit_staged(stage, database)
        _checkpoint("database:committed")
    finally:
        for path in (stage, Path(str(stage) + "-wal"), Path(str(stage) + "-shm")):
            try:
                _allowed_path(path).unlink()
            except FileNotFoundError:
                pass


def _factory_flows(root, data_dir, journal, guard):
    """Resume only the claimed new installation, accepting existing exact factory bytes."""
    if not journal["factory_local_flows"]:
        return False
    if data_dir != root / "data":
        raise ValueError("Pending factory routes require the original default data directory.")
    payloads = _flow_payloads(root)
    expected = {"flows.token": journal["flow_token"].encode("ascii"),
        "automation-config.json": _json_bytes(_flow_config()),
        **{"flows/" + name: payload for name, payload in payloads.items()}}
    directory = _allowed_path(data_dir / "flows")
    if directory.exists():
        # Only exact names from this unfinished transaction may already be present.
        if not directory.is_dir() or any(item.name not in payloads and not (item.name.startswith(".") and ".box-" in item.name and item.name.endswith(".tmp")) for item in directory.iterdir()):
            raise ValueError("Pending factory flow directory contains owner state; nothing was replaced.")
    for name, payload in expected.items():
        path = _allowed_path(data_dir / name)
        if path.exists() and regular(path).read_bytes() != payload:
            raise ValueError("Pending factory files conflict with owner state; nothing was replaced.")
    directory.mkdir(exist_ok=True)
    sync_directory(data_dir)
    _checkpoint("flows:directory")
    for name in ("flows.token", "flows/settings.js", "flows/flows.json", "automation-config.json"):
        path = _allowed_path(data_dir / name)
        with guard._directory(path.parent):
            if not path.exists():
                _publish(path, expected[name], name)
            elif regular(path).read_bytes() != expected[name]:
                raise ValueError("Pending factory file changed; nothing was replaced.")
    return True


def bootstrap_profiles(root=None, data_dir=None):
    """Create only missing factory profiles; an existing installation is never reset."""
    root = _allowed_path(root or Path(__file__).resolve().parents[1])
    data_dir = _allowed_path(data_dir or root / "data")
    source = _allowed_path(root / "config" / "box-profiles.json")
    if not source.exists():
        return {"created": [], "seeded_database": False, "factory_flows": False}
    template = json.loads(regular(source).read_text(encoding="utf-8"))
    if (type(template) is not dict or set(template) != {"schema", "default_model", "factory_local_flows", "profiles"} or template["schema"] != 1
            or template["default_model"] != "qwen3.5:9b" or type(template["profiles"]) is not dict
            or type(template["factory_local_flows"]) is not bool
            or set(template["profiles"]) - set(PROFILE_NAMES)):
        raise ValueError("Factory template has an unsupported schema or profile.")
    expanded = {}
    for name, value in template["profiles"].items():
        _shape(name, value)
        expanded[name] = _transform(value, root, expand=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    from .tools import ToolRegistry
    guard = object.__new__(ToolRegistry)
    created = []
    with guard._directory(data_dir), _bootstrap_lock(data_dir):
        for name, profile in expanded.items():
            path = _allowed_path(data_dir / name)
            if not path.exists():
                try:
                    _publish(path, _json_bytes(profile), name)
                    created.append(name)
                except FileExistsError:
                    pass  # An independently created owner profile wins the exclusive commit.
        database = _allowed_path(data_dir / "ariadne.sqlite3")
        pending = _allowed_path(data_dir / ".box-bootstrap-pending.json")
        seeded = False
        flows = False
        journal = _read_journal(pending) if pending.exists() else None
        if journal is None and not database.exists():
            journal = _new_journal(root, data_dir, template["factory_local_flows"])
            _publish(pending, _json_bytes(journal), "journal")
        if journal is not None:
            if not database.exists():
                try:
                    _seed_database(database, journal)
                    seeded = True
                except FileExistsError:
                    pass
            if not _database_owned(database, journal["transaction"]):
                raise ValueError("Pending bootstrap does not own the existing database; owner state was preserved.")
            flows = _factory_flows(root, data_dir, journal, guard)
            _checkpoint("journal:completing")
            pending.unlink()
            sync_directory(data_dir)
            _checkpoint("journal:completed")
    return {"created": created, "seeded_database": seeded, "factory_flows": flows}
