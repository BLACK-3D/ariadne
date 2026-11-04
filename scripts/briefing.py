"""Run, inspect or collect Ariadne's daily briefing without loading a model."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ariadne.briefing import BriefingService, BriefingCollectionStopped, collect_external, _region_digest
from ariadne.config import Settings, _allowed_path
from ariadne.network_policy import install_network_policy
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.internet import internet_enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="status", choices=("status", "run", "due", "collect", "collect-worker"))
    parser.add_argument("--due", action="store_true", help="Run only if the configured daily briefing is due")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"))
    parser.add_argument("--workspace")
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if args.due:
        if args.action not in {"status", "due"}:
            parser.error("--due cannot be combined with another action")
        args.action = "due"
    if args.action == "collect-worker":
        if os.environ.get("ARIADNE_BRIEFING_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
            raise SystemExit("The collector worker is started only through the enabled briefing service.")
    else:
        os.environ["ARIADNE_OFFLINE"] = "1"
    install_network_policy()
    settings = Settings(data_dir=_allowed_path(args.data_dir), workspace=_allowed_path(args.workspace) if args.workspace else None)
    store = Store(settings.data_dir / "ariadne.sqlite3")
    registry = ToolRegistry(settings)
    service = BriefingService(settings, store, registry)
    if args.action == "collect-worker":
        cfg = service.config()
        if not internet_enabled(store, default=True) or not cfg["online_sources"] or cfg["paused"] or store.setting("paused", False):
            raise SystemExit("Collection is disabled or paused.")
        try:
            revision = json.loads(os.environ["ARIADNE_BRIEFING_INTERNET_REVISION"])
            if revision is not None and (type(revision) is not int or revision < 0):
                raise ValueError("Invalid Internet revision.")
            config_revision = int(os.environ["ARIADNE_BRIEFING_CONFIG_REVISION"])
            if config_revision < 0:
                raise ValueError("Invalid briefing revision.")
        except (KeyError, ValueError):
            raise SystemExit("Collector configuration is missing or invalid; dated cache is retained.") from None
        try:
            if config_revision != cfg["revision"]:
                raise BriefingCollectionStopped("Briefing settings changed before collection.")
            check = lambda: service.check_collection(cfg, revision)
            check()
            if cfg["repository_updates"]:
                service.collect_repository_updates(cfg, revision)
            cache = None
            if cfg["region"]:
                cache = collect_external(cfg, check=check)
                check()
                service.save_cache(cache, _region_digest(cfg), collection_policy=(cfg, revision))
            check()
        except BriefingCollectionStopped:
            raise SystemExit("Collection settings changed or were revoked; dated cache is retained.") from None
        except (KeyError, ValueError):
            raise SystemExit("Public-source collection failed validation; dated cache is retained.") from None
        result = {"ok": True, "sources": len(cache["sources"]) if cache else 0, "repository_updates": cfg["repository_updates"]}
    elif args.action == "status":
        result = service.status()
    elif args.action == "run":
        result = service.run(args.request_id, collect=True)
    elif args.action == "due":
        result = service.run_due()
    else:
        result = service.collect()
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
