"""One authorized anonymous F historical-data collection; no broker interface."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("worker",))
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--pause-rowid", type=int, required=True)
    for name in ("options", "eod", "internet", "brief"):
        parser.add_argument("--" + name + "-revision", type=int, required=True)
    parser.add_argument("--required-contract", action="append", default=[])
    args = parser.parse_args()
    if os.environ.get("ARIADNE_OPTIONS_EOD_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
        raise SystemExit("Only an explicitly authorized Ford source child may run.")
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    from ariadne.config import Settings, _allowed_path
    from ariadne.store import Store
    from ariadne.tools import ToolRegistry
    from ariadne.briefing import BriefingService
    from ariadne.handoff import _identifier
    from ariadne.options_eod_data import fetch_bundle, normalize_bundle, required_ids, canonical

    def local_path(value):
        candidate = Path(os.path.abspath(value))
        if not candidate.is_relative_to(ROOT):
            raise ValueError("Ford worker paths must remain inside this box.")
        path = _allowed_path(candidate)
        if not path.is_relative_to(ROOT):
            raise ValueError("Resolved Ford worker paths must remain inside this box.")
        return path

    _identifier(args.account_id)
    if args.pause_rowid < -1 or any(getattr(args, name + "_revision") < 0 for name in ("options", "eod", "internet", "brief")):
        raise ValueError("Use bounded nonnegative worker permission revisions.")
    required = required_ids(args.required_contract)
    settings = Settings(data_dir=local_path(args.data_dir), workspace=local_path(args.workspace))
    service = BriefingService(settings, Store(settings.data_dir / "ariadne.sqlite3"), ToolRegistry(settings))
    source_day = None

    def check():
        nonlocal source_day
        with service.options.database() as db:
            current, feed, internet, brief = service.options.eod._policy(db)
            account = service.options._account(db)
            epoch = service.options._epoch(db, current, brief)
            rowid = epoch["global_pause_rowid"] if epoch["global_pause_rowid"] is not None else -1
            if (current["revision"] != args.options_revision or feed["revision"] != args.eod_revision
                    or internet["revision"] != args.internet_revision or brief["revision"] != args.brief_revision
                    or rowid != args.pause_rowid or not account or account["id"] != args.account_id
                    or account["state"].get("schema") not in (3,4) or account["policy"] != epoch):
                raise ValueError("Ford account or source permission changed.")
            ready, expected, _ = service.options.eod._window(datetime.now(timezone.utc))
            if not ready or source_day is not None and source_day != expected:
                raise ValueError("Ford source publication-check window changed.")
            source_day = expected

    check()
    try:
        result = fetch_bundle(source_day, required_contracts=required, check=check)
        normalize_bundle(result, expected_session=source_day, required_contracts=required)
    except (ValueError, OSError, TimeoutError) as error:
        result = {"unavailable": True, "reason": str(error)[:1000]}
        if getattr(error, "source_evidence", None) is not None:
            result["source_evidence"] = error.source_evidence
    check()
    raw = canonical(result)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Ford worker output exceeds its bound.")
    sys.stdout.buffer.write(raw)


if __name__ == "__main__":
    main()
