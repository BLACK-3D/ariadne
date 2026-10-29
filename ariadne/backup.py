"""Bounded private snapshots and read-back checks; never restore or execute data.

Each SQLite database is a separate consistent snapshot. Files and databases are
not a single transaction. Checks establish integrity, not safe application replay.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import struct
import tempfile
import time
import zipfile
import zlib

from . import __version__
from .atomic import commit_staged
from .config import _allowed_path

DATABASES = ("ariadne.sqlite3", "business.sqlite3", "survival.sqlite3", "books.sqlite3", "procedures.sqlite3", "briefing.sqlite3", "internet.sqlite3")
CONFIGURATIONS = ("server.token", "connectors.json", "provision-manifest.json", "voice-config.json", "mcp.json", "ocr-config.json", "media-config.json", "knowledge-config.json", "automation-config.json", "flows.token", "knowledge-vectors.json", "erpnext/bootstrap.env", "repository-radar-cache.json")
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")
MANIFEST_NAME = "BACKUP-MANIFEST.json"
MAX_FILES = 10_000
MAX_FILE_BYTES = 1_073_741_824
MAX_TOTAL_BYTES = 8_589_934_592
MAX_MANIFEST_BYTES = 2_000_000
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 64_000_000
MAX_CENTRAL_BYTES = 16_000_000
MAX_CONFIG_BYTES = 16_000_000
MAX_SECONDS = 300
SQLITE_SECONDS = 30
CHUNK = 1024 * 1024
WARNINGS = (
    "SQLite databases are individually consistent; databases, configuration and workspace files are not one global point-in-time snapshot.",
    "This private archive contains credentials and owner documents. It is not encrypted; protect it and keep a separate verified copy on suitable storage.",
    "Model weights, native runtimes, external references, OS tasks, logs, locks, host process state and regenerated fixed flow files are excluded.",
    "Verification does not restore files, launch services, replay work, or prove that a recovered application is safe to run.",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])\Z", re.I)


def _deadline(deadline):
    if time.monotonic() > deadline:
        raise ValueError("Backup operation exceeded its time limit; no success is recorded.")


def _guard():
    from .tools import ToolRegistry
    return object.__new__(ToolRegistry)


@contextmanager
def _temporary(root, prefix, *, path_guard=_allowed_path):
    with tempfile.TemporaryDirectory(prefix=prefix, dir=root) as temporary:
        temporary = path_guard(temporary)
        with _guard()._directory(temporary):
            yield temporary


def _plain(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not getattr(info, "st_file_attributes", 0) & 0x400


def regular_file(path, *, path_guard=_allowed_path):
    path = path_guard(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path, False
    if not _plain(info):
        raise ValueError("Backup database and sidecars must be regular files without links")
    return path, True


def database_path(path, *, path_guard=_allowed_path):
    path, present = regular_file(path, path_guard=path_guard)
    for suffix in SQLITE_SIDECARS:
        regular_file(path.with_name(path.name + suffix), path_guard=path_guard)
    return path, present


def _no_rollback_journal(path, *, path_guard=_allowed_path):
    _, present = regular_file(str(path) + "-journal", path_guard=path_guard)
    if present:
        # A DELETE-mode writer needs to unlink this leaf at commit. Pinning it
        # without delete sharing would interfere with the live transaction.
        raise ValueError("A rollback journal is present; retry the backup after database writes settle.")


@contextmanager
def _pin_sqlite_file(path, *, path_guard=_allowed_path):
    """Keep a present SQLite leaf from being renamed while allowing live writes.

    Windows denies delete/rename on this exact opened leaf, with regular-file,
    link-count and final-path checks on its handle. POSIX retains an O_NOFOLLOW
    descriptor and checks pathname identity again, without claiming to prevent a
    same-user rename. Absent SQLite sidecars cannot be pinned without creating
    them; all known names are revalidated before and after the snapshot.
    """
    path, present = regular_file(path, path_guard=path_guard)
    if not present:
        raise ValueError("Database leaf disappeared before it could be pinned.")
    before = path.lstat()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        class FileInfo(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                        ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                        ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                        ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                        ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
        get_info = kernel.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        get_info.restype = wintypes.BOOL
        get_final = kernel.GetFinalPathNameByHandleW
        get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final.restype = wintypes.DWORD
        # GENERIC_READ; FILE_SHARE_READ|FILE_SHARE_WRITE, deliberately no DELETE.
        # OPEN_REPARSE_POINT ensures a leaf link is inspected, never followed.
        handle = create(str(path), 0x80000000, 3, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "Cannot pin SQLite backup input.")
        try:
            info, buffer = FileInfo(), ctypes.create_unicode_buffer(32768)
            count = get_final(handle, buffer, len(buffer), 0)
            final = buffer.value.removeprefix("\\\\?\\")
            if final.startswith("UNC\\"):
                final = "\\\\" + final[4:]
            if (not get_info(handle, ctypes.byref(info)) or info.attributes & (0x400 | 0x10) or info.links != 1
                    or not count or count >= len(buffer) or os.path.normcase(final) != os.path.normcase(str(path))):
                raise ValueError("SQLite backup input failed its opened-handle identity check.")
            current = path.lstat()
            if not _plain(current) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("SQLite backup input changed while being pinned.")
            yield
        finally:
            close(handle)
    else:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            opened = os.fstat(descriptor)
            if not _plain(opened) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("SQLite backup input changed while being pinned.")
            yield
        finally:
            os.close(descriptor)
    current = path.lstat()
    if not _plain(current) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
        raise ValueError("SQLite backup input identity changed during capture.")


@contextmanager
def _pin_database_files(path, *, path_guard=_allowed_path):
    path, present = database_path(path, path_guard=path_guard)
    if not present:
        raise ValueError("Database disappeared before capture.")
    _no_rollback_journal(path, path_guard=path_guard)
    with ExitStack() as stack:
        for suffix in ("", "-wal", "-shm"):
            file, present = regular_file(str(path) + suffix, path_guard=path_guard)
            if present:
                stack.enter_context(_pin_sqlite_file(file, path_guard=path_guard))
        database_path(path, path_guard=path_guard)
        _no_rollback_journal(path, path_guard=path_guard)
        yield
        database_path(path, path_guard=path_guard)


@contextmanager
def _read(path, *, path_guard=_allowed_path):
    path, present = regular_file(path, path_guard=path_guard)
    if not present:
        raise ValueError("Backup input disappeared; retry after writes settle.")
    paths = _guard()
    with paths._directory(path.parent) as parent:
        descriptor = paths._open_read(path, parent)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not _plain(before):
                raise ValueError("Backup inputs must be regular files without links.")
            yield source
            after = os.fstat(source.fileno())
            current = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino) or not _plain(current):
                raise ValueError("Backup input changed while it was being read; retry after writes settle.")


def _sqlite_limits(connection, deadline):
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        connection.enable_load_extension(False)
    except AttributeError:
        pass
    for name, limit in (("SQLITE_LIMIT_LENGTH", 16_000_000), ("SQLITE_LIMIT_SQL_LENGTH", 100_000),
                        ("SQLITE_LIMIT_COLUMN", 2000), ("SQLITE_LIMIT_EXPR_DEPTH", 100),
                        ("SQLITE_LIMIT_ATTACHED", 0), ("SQLITE_LIMIT_VDBE_OP", 1_000_000),
                        ("SQLITE_LIMIT_WORKER_THREADS", 0)):
        if hasattr(sqlite3, name) and hasattr(connection, "setlimit"):
            connection.setlimit(getattr(sqlite3, name), limit)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    connection.execute("PRAGMA cache_size=-2048")
    connection.execute("PRAGMA mmap_size=0")
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)


def _quick_check(path, deadline):
    # Only isolated saved copies are opened immutable: no sidecars or user SQL.
    deadline = min(deadline, time.monotonic() + SQLITE_SECONDS)
    with _pin_sqlite_file(path):
        with _read(path) as header:
            if os.fstat(header.fileno()).st_size < 512 or header.read(16) != b"SQLite format 3\x00":
                raise ValueError("Saved SQLite database is corrupt or has no SQLite header.")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)) as saved:
                _sqlite_limits(saved, deadline)
                if saved.execute("PRAGMA page_count").fetchone()[0] * saved.execute("PRAGMA page_size").fetchone()[0] > MAX_FILE_BYTES:
                    raise ValueError("Saved SQLite database exceeds the size limit.")
                rows = saved.execute("PRAGMA quick_check(1)").fetchmany(2)
                _deadline(deadline)
                if rows != [("ok",)]:
                    raise ValueError("Saved SQLite database failed its integrity check.")
        except sqlite3.Error:
            raise ValueError("Saved SQLite database is corrupt, unsupported or exceeded its verification limit.") from None


def snapshot_database(source, destination, *, path_guard=_allowed_path, deadline=None):
    deadline = min(deadline or time.monotonic() + SQLITE_SECONDS, time.monotonic() + SQLITE_SECONDS)
    source, present = database_path(source, path_guard=path_guard)
    if not present:
        raise ValueError("Database changed during backup; retry after writes settle")
    destination, present = database_path(destination, path_guard=path_guard)
    if present:
        raise ValueError("Refusing to overwrite an existing database snapshot.")
    paths = _guard()
    with paths._directory(source.parent), paths._directory(destination.parent), _pin_database_files(source, path_guard=path_guard):
        before = source.lstat()
        database_path(source, path_guard=path_guard)
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as live, closing(sqlite3.connect(destination, timeout=1)) as saved:
                live.execute("PRAGMA trusted_schema=OFF")
                live.execute("PRAGMA busy_timeout=1000")
                live.execute("PRAGMA cache_size=-2048")
                live.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                page_size = live.execute("PRAGMA page_size").fetchone()[0]
                if live.execute("PRAGMA page_count").fetchone()[0] * page_size > MAX_FILE_BYTES:
                    raise ValueError("Database exceeds the backup size limit.")
                saved.execute("PRAGMA trusted_schema=OFF")
                saved.execute("PRAGMA max_page_count=" + str(MAX_FILE_BYTES // page_size))
                def progress(status, remaining, total):
                    _deadline(deadline)
                    if total * page_size > MAX_FILE_BYTES:
                        raise ValueError("Database exceeds the backup page limit.")
                live.backup(saved, pages=128, progress=progress, sleep=0.05)
            current = source.lstat()
            if not _plain(current) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("Database identity changed during backup.")
            database_path(source, path_guard=path_guard)
            if destination.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Database exceeds the backup size limit.")
            _quick_check(destination, deadline)
        except sqlite3.Error:
            raise ValueError("Database snapshot failed or exceeded its limit; retry after writes settle.") from None


def _name(name, *, manifest=False):
    if type(name) is not str or not 1 <= len(name) <= 1024 or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError("Backup contains an unsupported member path.")
    parts = name.split("/")
    if any(not part or part in {".", ".."} or part.endswith((" ", ".")) or _DEVICE.fullmatch(part.split(".", 1)[0])
           or any(ord(c) < 32 or ord(c) == 127 or c in '<>"|?*' for c in part) for part in parts):
        raise ValueError("Backup contains an ambiguous or unsafe member path.")
    if name in DATABASES:
        return "database"
    if name in CONFIGURATIONS:
        return "configuration"
    if name.startswith("workspace/") and len(parts) > 1:
        return "workspace"
    if manifest and name == MANIFEST_NAME:
        return "manifest"
    raise ValueError("Backup contains a member outside the private snapshot allowlist.")


def _json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Backup manifest contains duplicate JSON keys.")
            result[key] = value
        return result
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Backup manifest contains non-finite JSON.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Backup manifest is not supported bounded JSON.") from None


def _zip_directory(source, size, deadline):
    """Bound central-directory memory before parsing an untrusted ZIP."""
    if size < 22 or size > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive is empty or exceeds its size limit.")
    source.seek(max(0, size - 65557))
    tail = source.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset != 22:
        raise ValueError("Backup requires an uncommented, complete ZIP directory.")
    _, disk, start_disk, on_disk, count, central_size, central_offset, comment = struct.unpack("<4s4H2LH", tail[offset:])
    eocd = size - len(tail) + offset
    if disk or start_disk or on_disk != count or comment:
        raise ValueError("Split or commented backup archives are unsupported.")
    if count == 65535 or central_size == 0xffffffff or central_offset == 0xffffffff:
        if eocd < 20:
            raise ValueError("Invalid ZIP64 directory.")
        source.seek(eocd - 20)
        locator = source.read(20)
        if len(locator) != 20:
            raise ValueError("Invalid ZIP64 directory.")
        signature, disk, position, disks = struct.unpack("<4sLQL", locator)
        if signature != b"PK\x06\x07" or disk or disks != 1 or position > eocd - 76:
            raise ValueError("Invalid ZIP64 directory.")
        source.seek(position)
        record = source.read(56)
        if len(record) != 56:
            raise ValueError("Invalid ZIP64 directory.")
        signature, record_size, _, _, disk, start_disk, on_disk, count, central_size, central_offset = struct.unpack("<4sQ2H2L4Q", record)
        if signature != b"PK\x06\x06" or record_size != 44 or disk or start_disk or on_disk != count or position + 56 != eocd - 20 or central_offset + central_size != position:
            raise ValueError("Invalid ZIP64 directory.")
    elif central_offset + central_size != eocd:
        raise ValueError("Backup ZIP directory has unsupported trailing data.")
    if not 1 <= count <= MAX_FILES + 1 or not 0 < central_size <= MAX_CENTRAL_BYTES or central_offset + central_size > size:
        raise ValueError("Backup ZIP directory exceeds its limits.")
    # EOCD counts are claims. Check the actual central records before ZipFile
    # allocates a ZipInfo for each one, including a lying count/header fixture.
    source.seek(central_offset)
    end, actual = central_offset + central_size, 0
    while source.tell() < end:
        _deadline(deadline)
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ValueError("Invalid backup central-directory record.")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", header, 28)
        actual += 1
        if actual > MAX_FILES + 1 or not 1 <= name_size <= 4096 or extra_size > 64 or comment_size or source.tell() + name_size + extra_size > end:
            raise ValueError("Backup central-directory record exceeds its limits.")
        source.seek(name_size + extra_size, os.SEEK_CUR)
    if source.tell() != end or actual != count:
        raise ValueError("Backup central-directory count does not match its records.")
    source.seek(0)
    if source.read(4) != b"PK\x03\x04":
        raise ValueError("Backup ZIP has an unsupported executable or prefix.")
    source.seek(0)


def _extra(raw):
    while raw:
        if len(raw) < 4:
            raise ValueError("Invalid backup ZIP metadata.")
        tag, length = struct.unpack("<HH", raw[:4])
        if tag != 1 or length > len(raw) - 4:
            raise ValueError("Unsupported backup ZIP metadata.")
        raw = raw[4 + length:]


def _members(archive):
    infos = archive.infolist()
    if not 1 <= len(infos) <= MAX_FILES + 1 or archive.comment:
        raise ValueError("Backup member count exceeds its limit.")
    names, total, compressed = {}, 0, 0
    for info in infos:
        _name(info.filename, manifest=True)
        key = info.filename.casefold()
        if info.orig_filename != info.filename or key in names or info.is_dir():
            raise ValueError("Backup contains duplicate, aliased or directory members.")
        for parent in Path(info.filename).parents:
            if parent.as_posix() != "." and parent.as_posix().casefold() in names:
                raise ValueError("Backup has colliding file and directory names.")
        if any(existing.startswith(key + "/") for existing in names):
            raise ValueError("Backup has colliding file and directory names.")
        mode = info.external_attr >> 16
        if (info.create_system not in {0, 3} or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.external_attr & 0x410
                or info.flag_bits & ~0x808 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or info.comment or info.volume):
            raise ValueError("Backup contains encrypted, linked, special or unsupported ZIP members.")
        _extra(info.extra)
        maximum = MAX_MANIFEST_BYTES if info.filename == MANIFEST_NAME else (MAX_CONFIG_BYTES if info.filename in CONFIGURATIONS else MAX_FILE_BYTES)
        if not 0 <= info.file_size <= maximum or not 0 <= info.compress_size <= MAX_ARCHIVE_BYTES or info.file_size > max(1, info.compress_size) * 2000:
            raise ValueError("Backup member exceeds its size or compression limit.")
        if info.filename != MANIFEST_NAME:
            total += info.file_size
        compressed += info.compress_size
        if total > MAX_TOTAL_BYTES or compressed > MAX_ARCHIVE_BYTES:
            raise ValueError("Backup expanded payload exceeds its limit.")
        names[key] = info
    if MANIFEST_NAME.casefold() not in names or names[MANIFEST_NAME.casefold()].filename != MANIFEST_NAME:
        raise ValueError("Legacy backup has no checked manifest; its recovery format is unverified.")
    return infos


def _manifest(archive, infos):
    info = next(i for i in infos if i.filename == MANIFEST_NAME)
    with archive.open(info) as source:
        payload = source.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("Backup manifest exceeds its limit.")
    manifest = _json(payload)
    required = {"schema", "format", "application_version", "created_utc", "files", "omitted_workspace_entries", "consistency", "warnings"}
    if type(manifest) is not dict or set(manifest) != required or type(manifest["schema"]) is not int or manifest["schema"] != 1 or manifest["format"] != "ariadne-private-backup":
        raise ValueError("Backup manifest uses an unsupported format.")
    if type(manifest["application_version"]) is not str or not re.fullmatch(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", manifest["application_version"]):
        raise ValueError("Backup manifest version is unsupported.")
    if type(manifest["created_utc"]) is not str or len(manifest["created_utc"]) > 40:
        raise ValueError("Backup manifest timestamp is invalid.")
    try:
        created = datetime.fromisoformat(manifest["created_utc"])
        if created.tzinfo is None or created.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Backup manifest timestamp is invalid.") from None
    if type(manifest["omitted_workspace_entries"]) is not int or not 0 <= manifest["omitted_workspace_entries"] <= MAX_FILES:
        raise ValueError("Backup omitted-entry count is invalid.")
    if manifest["consistency"] != "separate-sqlite-snapshots-and-live-files" or manifest["warnings"] != list(WARNINGS):
        raise ValueError("Backup manifest lacks its supported consistency limits.")
    rows = manifest["files"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("Backup manifest file count is invalid.")
    by_name = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "kind", "bytes", "sha256"}:
            raise ValueError("Backup manifest file record is invalid.")
        kind = _name(row["path"])
        if row["path"] in by_name or row["kind"] != kind or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_FILE_BYTES or type(row["sha256"]) is not str or not _HEX.fullmatch(row["sha256"]):
            raise ValueError("Backup manifest file identity is invalid.")
        by_name[row["path"]] = row
    actual = {i.filename: i for i in infos if i.filename != MANIFEST_NAME}
    if set(actual) != set(by_name) or "ariadne.sqlite3" not in by_name or any(actual[name].file_size != row["bytes"] for name, row in by_name.items()):
        raise ValueError("Backup manifest and archive members do not match.")
    return manifest, by_name


def _stream(source, target, maximum, deadline):
    digest, count = hashlib.sha256(), 0
    while True:
        _deadline(deadline)
        block = source.read(min(CHUNK, maximum - count + 1))
        if not block:
            break
        count += len(block)
        if count > maximum:
            raise ValueError("Backup input exceeded its size limit while being read.")
        digest.update(block)
        if target is not None:
            target.write(block)
    return count, digest.hexdigest()


def verify_backup(path, temp_root=None, *, path_guard=_allowed_path, deadline=None):
    """Hash every entry and quick-check isolated DB copies, without restore.

    The receipt has no record text, credentials, schema or member names. The
    colocated manifest establishes integrity, not independent authenticity.
    """
    deadline = min(deadline or time.monotonic() + MAX_SECONDS, time.monotonic() + MAX_SECONDS)
    path = path_guard(path)
    temp_root = path_guard(temp_root or path.parent)
    if not temp_root.is_dir():
        raise ValueError("Verification temporary root must already exist.")
    paths = _guard()
    try:
        with _read(path, path_guard=path_guard) as raw, paths._directory(temp_root):
            archive_size = os.fstat(raw.fileno()).st_size
            _zip_directory(raw, archive_size, deadline)
            with zipfile.ZipFile(raw, "r") as archive:
                infos = _members(archive)
                manifest, rows = _manifest(archive, infos)
                total, databases = 0, 0
                with _temporary(temp_root, "backup-verify-", path_guard=path_guard) as temporary:
                    for info in infos:
                        if info.filename == MANIFEST_NAME:
                            continue
                        row = rows[info.filename]
                        snapshot = temporary / "snapshot.sqlite3"
                        with archive.open(info) as source:
                            if row["kind"] == "database":
                                with snapshot.open("xb") as target:
                                    count, digest = _stream(source, target, row["bytes"], deadline)
                                    target.flush()
                                    os.fsync(target.fileno())
                            else:
                                count, digest = _stream(source, None, row["bytes"], deadline)
                        if count != row["bytes"] or digest != row["sha256"]:
                            raise ValueError("Backup member hash or size failed verification.")
                        total += count
                        if row["kind"] == "database":
                            _quick_check(snapshot, deadline)
                            databases += 1
                            snapshot.unlink()
                raw.seek(0)
                measured, digest = _stream(raw, None, MAX_ARCHIVE_BYTES, deadline)
                if measured != archive_size:
                    raise ValueError("Backup archive changed during verification.")
        return {"schema": 1, "verified": True, "format": "ariadne-private-backup", "application_version": manifest["application_version"],
                "archive_sha256": digest, "bytes": measured, "member_count": len(rows), "database_count": databases,
                "payload_bytes": total, "omitted_workspace_entries": manifest["omitted_workspace_entries"],
                "warnings": list(WARNINGS), "limits": {"files": MAX_FILES, "member_bytes": MAX_FILE_BYTES, "payload_bytes": MAX_TOTAL_BYTES,
                  "operation_seconds": MAX_SECONDS, "sqlite_seconds": SQLITE_SECONDS}, "sqlite_check": "quick_check(1)"}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, NotImplementedError, zlib.error, UnicodeError, struct.error):
        raise ValueError("Backup ZIP is incomplete, corrupt or unsupported.") from None


def create_backup(destination, settings, *, path_guard=_allowed_path, snapshotter=None):
    """Publish one checked private archive exclusively; never modify owner state."""
    destination = path_guard(destination)
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing backup")
    data = path_guard(settings.data_dir)
    workspace = path_guard(settings.workspace)
    if destination.is_relative_to(workspace):
        raise ValueError("Backup destination must be outside the workspace to avoid archiving itself.")
    deadline = time.monotonic() + MAX_SECONDS
    paths = _guard()
    databases = []
    for name in DATABASES:
        source, present = database_path(data / name, path_guard=path_guard)
        _no_rollback_journal(source, path_guard=path_guard)
        if present:
            databases.append((name, source))
    if not any(name == "ariadne.sqlite3" for name, _ in databases):
        raise ValueError("No database yet. Start the application first.")
    if not workspace.is_dir():
        raise ValueError("Backup workspace must already exist.")
    for name in CONFIGURATIONS:
        regular_file(data / name, path_guard=path_guard)
    if snapshotter is None:
        def snapshotter(source, target):
            snapshot_database(source, target, path_guard=path_guard, deadline=deadline)
    stage = destination.with_name("." + destination.name + ".backup-" + secrets.token_hex(12) + ".partial")
    stage_identity = None
    committed = False
    with paths._directory(data), paths._directory(destination.parent, create=True):
        try:
            with _temporary(data, "backup-", path_guard=path_guard) as temporary:
                snapshots = []
                for name, source in databases:
                    _deadline(deadline)
                    target = temporary / name
                    snapshotter(source, target)
                    snapshots.append((name, target))
                descriptor = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
                stage_identity = os.fstat(descriptor)
                rows, used, omitted, visited, keys = [], 0, 0, 0, set()
                with os.fdopen(descriptor, "w+b") as target:
                    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
                        def add(name, file):
                            nonlocal used
                            kind = _name(name)
                            if name.casefold() in keys or len(rows) >= MAX_FILES:
                                raise ValueError("Backup has duplicate paths or exceeds its file-count limit.")
                            keys.add(name.casefold())
                            with _read(file, path_guard=path_guard) as source:
                                size = os.fstat(source.fileno()).st_size
                                maximum = MAX_CONFIG_BYTES if kind == "configuration" else MAX_FILE_BYTES
                                if size > maximum or used + size > MAX_TOTAL_BYTES:
                                    raise ValueError("Backup payload exceeds its size limit.")
                                info = zipfile.ZipInfo(name)
                                info.create_system = 3
                                info.external_attr = (stat.S_IFREG | 0o600) << 16
                                info.compress_type = zipfile.ZIP_DEFLATED
                                info._compresslevel = 1
                                info.file_size = size
                                with archive.open(info, "w", force_zip64=size >= zipfile.ZIP64_LIMIT) as output:
                                    count, digest = _stream(source, output, maximum, deadline)
                                if count != size:
                                    raise ValueError("Backup input changed size during capture.")
                            used += count
                            rows.append({"path": name, "kind": kind, "bytes": count, "sha256": digest})
                        for name, file in snapshots:
                            add(name, file)
                        for name in CONFIGURATIONS:
                            file, present = regular_file(data / name, path_guard=path_guard)
                            if present:
                                add(name, file)
                        def walk(directory, prefix, depth=0):
                            nonlocal omitted, visited
                            _deadline(deadline)
                            if depth > 64:
                                raise ValueError("Workspace traversal exceeds its directory-depth limit.")
                            with paths._directory(directory):
                                with os.scandir(directory) as iterator:
                                    entries = []
                                    for entry in iterator:
                                        visited += 1
                                        if visited > MAX_FILES:
                                            raise ValueError("Workspace traversal exceeds its entry-count limit.")
                                        entries.append(entry)
                                for entry in sorted(entries, key=lambda e: e.name.casefold()):
                                    _deadline(deadline)
                                    # Windows DirEntry.stat omits nlink/identity;
                                    # lstat is required to check hard links.
                                    info = Path(entry.path).lstat()
                                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                                        omitted += 1
                                    elif stat.S_ISDIR(info.st_mode):
                                        walk(Path(entry.path), prefix + entry.name + "/", depth + 1)
                                    elif _plain(info):
                                        add(prefix + entry.name, Path(entry.path))
                                    else:
                                        omitted += 1
                        walk(workspace, "workspace/")
                        manifest = {"schema": 1, "format": "ariadne-private-backup", "application_version": __version__,
                                    "created_utc": datetime.now(timezone.utc).isoformat(), "files": rows,
                                    "omitted_workspace_entries": omitted, "consistency": "separate-sqlite-snapshots-and-live-files", "warnings": list(WARNINGS)}
                        payload = (json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
                        if len(payload) > MAX_MANIFEST_BYTES:
                            raise ValueError("Backup manifest exceeds its size limit.")
                        archive.writestr(MANIFEST_NAME, payload)
                    target.flush()
                    os.fsync(target.fileno())
                receipt = verify_backup(stage, temporary, path_guard=path_guard, deadline=deadline)
                _deadline(deadline)
                current = stage.lstat()
                if not _plain(current) or (current.st_dev, current.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                    raise ValueError("Backup staging identity changed before publication.")
                commit_staged(stage, destination)
                committed = True
                return receipt
        finally:
            if stage_identity is not None and not committed:
                try:
                    current = stage.lstat()
                    if (current.st_dev, current.st_ino) == (stage_identity.st_dev, stage_identity.st_ino):
                        stage.unlink()
                except FileNotFoundError:
                    pass
