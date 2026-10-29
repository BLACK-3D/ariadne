"""One-click checked private backup for this box; never restores or starts services."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ariadne.atomic import publish_bytes
from ariadne.backup import create_backup, verify_backup
from ariadne.config import Settings, _allowed_path
from ariadne.tools import ToolRegistry


def _inside_box(path):
    root = _allowed_path(ROOT)
    result = _allowed_path(path)
    if not result.is_relative_to(root):
        raise ValueError("This box's backup controls accept paths inside the box only.")
    return result


def run(*, verify=None):
    """Return a receipt only after the complete archive has passed verification."""
    backup_dir = _inside_box(ROOT / "backups")
    guard = object.__new__(ToolRegistry)
    with guard._directory(backup_dir, create=True):
        if verify is not None:
            # Verify-only bypasses Settings/load_settings and never creates a token.
            return verify_backup(_inside_box(verify), backup_dir)
        settings = Settings(data_dir=_inside_box(ROOT / "data"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = _inside_box(backup_dir / ("ariadne-private-" + stamp + "-" + secrets.token_hex(4) + ".zip"))
        receipt = create_backup(destination, settings)
        # The core has already verified and exclusively published the final ZIP.
        # This separate receipt is useful evidence, never a prerequisite for reading it.
        receipt_path = destination.with_suffix(".zip.verified.json")
        publish_bytes(receipt_path, (json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"))
        return {**receipt, "archive": str(destination), "receipt": str(receipt_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="Verify a backup inside this box without restoring it")
    parser.add_argument("--json", action="store_true", help="Print only the bounded verification receipt")
    args = parser.parse_args(argv)
    try:
        receipt = run(verify=args.verify)
    except (OSError, ValueError, RuntimeError) as error:
        # File/DB contents and connection credentials never belong in console output.
        print("Backup operation failed: " + type(error).__name__ + ". No verified result is claimed.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print("ARIADNE / PRIVATE RECOVERY SNAPSHOT")
        print("Archive verified. Database snapshots passed their integrity checks.")
        print(str(receipt["member_count"]) + " files / " + str(receipt["database_count"]) + " databases / " + format(receipt["bytes"], ",") + " archive bytes")
        print("SHA-256: " + receipt["archive_sha256"])
        if receipt["omitted_workspace_entries"]:
            print("Excluded unsafe workspace entries: " + str(receipt["omitted_workspace_entries"]) + ". Review these omissions before relying on this snapshot.")
        if "archive" in receipt:
            print("Saved: " + receipt["archive"])
            print("Receipt: " + receipt["receipt"])
        print("Keep this backup private. It contains owner data and access keys.")
        print("Live files are copied sequentially; this is not a global point-in-time snapshot.")
        print("Models and runtimes are separate. Verification does not restore or launch anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
