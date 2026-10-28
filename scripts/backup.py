"""Create a consistent local backup. Stop missions before restoring it."""
import argparse
import os
import sqlite3
import stat
import sys
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ariadne.config import load_settings, _allowed_path


DATABASES = ("ariadne.sqlite3", "business.sqlite3", "survival.sqlite3", "books.sqlite3", "procedures.sqlite3", "briefing.sqlite3", "internet.sqlite3")
SQLITE_SIDECARS = ("-journal", "-wal", "-shm")


def _regular_file(path):
    """Reject linked or special SQLite inputs, including SQLite-opened sidecars."""
    path = _allowed_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path, False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Backup database and sidecars must be regular files without links")
    return path, True


def _database_path(path):
    database, present = _regular_file(path)
    for suffix in SQLITE_SIDECARS:
        _regular_file(database.with_name(database.name + suffix))
    return database, present


def _snapshot_database(source, destination):
    # Recheck immediately before SQLite opens either file. Read-only mode prevents
    # a disappeared optional source from becoming a newly created empty database.
    source, present = _database_path(source)
    if not present:
        raise ValueError("Database changed during backup; retry after writes settle")
    destination, _ = _database_path(destination)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live, closing(sqlite3.connect(destination)) as saved:
        live.backup(saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="New .zip path on an allowed disk; contains private data")
    args = parser.parse_args()
    destination = _allowed_path(args.destination)
    if destination.exists():
        raise SystemExit("Refusing to overwrite an existing backup")
    settings = load_settings()
    if destination.is_relative_to(settings.workspace):
        raise SystemExit("Backup destination must be outside the workspace to avoid archiving itself.")
    # Preflight every known database and its journal/WAL/SHM before opening any
    # SQLite connection. Missing optional databases remain absent.
    databases = []
    for name in DATABASES:
        source, present = _database_path(settings.data_dir / name)
        if present:
            databases.append((name, source))
    if not any(name == "ariadne.sqlite3" for name, _ in databases):
        raise SystemExit("No database yet. Start the application first.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="backup-", dir=settings.data_dir) as temp:
        snapshots = []
        for name, source in databases:
            snapshot = Path(temp) / name
            _snapshot_database(source, snapshot)
            snapshots.append((name, snapshot))
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, snapshot in snapshots:
                archive.write(snapshot, name)
            for name in ("server.token", "connectors.json", "provision-manifest.json", "voice-config.json", "mcp.json", "ocr-config.json", "media-config.json", "knowledge-config.json", "automation-config.json", "flows.token", "knowledge-vectors.json", "erpnext/bootstrap.env"):
                file = _allowed_path(settings.data_dir / name)
                if file.is_file() and not file.is_symlink():
                    info = file.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Backup configuration must be a regular file without links")
                    archive.write(file, name)
            for directory, folders, files in os.walk(settings.workspace, followlinks=False):
                folders[:] = [name for name in folders if not (Path(directory, name).lstat().st_file_attributes & 0x400 if os.name == "nt" else Path(directory, name).is_symlink())]
                for name in files:
                    file = Path(directory, name)
                    info = file.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        continue
                    archive.write(_allowed_path(file), "workspace/" + file.relative_to(settings.workspace).as_posix())
    print(f"Created {destination}. Keep it private. Models are separate and are not duplicated.")


if __name__ == "__main__":
    main()
