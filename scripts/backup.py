"""Create or verify a checked private local backup; never restore or replay work."""
import argparse
import json
import os
from pathlib import Path
import sqlite3  # Retained for callers that instrument snapshot database opens.
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import Settings, _allowed_path
from ariadne import backup

DATABASES = backup.DATABASES
SQLITE_SIDECARS = backup.SQLITE_SIDECARS
MANIFEST_NAME = backup.MANIFEST_NAME


def load_settings():
    """Read locations without bootstrapping a token or application state."""
    return Settings(data_dir=_allowed_path(os.environ.get("ARIADNE_DATA_DIR", "data")))


def _regular_file(path):
    return backup.regular_file(path, path_guard=_allowed_path)


def _database_path(path):
    return backup.database_path(path, path_guard=_allowed_path)


def _snapshot_database(source, destination):
    return backup.snapshot_database(source, destination, path_guard=_allowed_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", help="New .zip path on an allowed disk; contains private data")
    parser.add_argument("--verify", metavar="ARCHIVE", help="Verify without restoring or starting anything")
    parser.add_argument("--temp-root", help="Existing allowed folder for temporary SQLite verification copies")
    parser.add_argument("--json", action="store_true", help="Print counts and hashes, never private contents")
    args = parser.parse_args()
    if bool(args.destination) == bool(args.verify):
        parser.error("Choose either a new destination or --verify ARCHIVE.")
    if args.temp_root and not args.verify:
        parser.error("--temp-root applies only to verification.")
    if args.verify:
        receipt = backup.verify_backup(args.verify, args.temp_root, path_guard=_allowed_path)
    else:
        destination = _allowed_path(args.destination)
        if destination.exists():
            raise SystemExit("Refusing to overwrite an existing backup")
        settings = load_settings()
        if destination.is_relative_to(settings.workspace):
            raise SystemExit("Backup destination must be outside the workspace to avoid archiving itself.")
        receipt = backup.create_backup(destination, settings, path_guard=_allowed_path)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print("Verified private backup: " + str(receipt["member_count"]) + " files; " + str(receipt["database_count"]) + " SQLite snapshots.")
        print("SHA-256: " + receipt["archive_sha256"])
        for warning in receipt["warnings"]:
            print(warning)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except OSError:
        raise SystemExit("Backup could not complete because a file was unavailable or storage access failed. No success was recorded.") from None
