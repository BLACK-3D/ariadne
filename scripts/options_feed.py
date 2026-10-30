"""One authorized fixed anonymous AAPL request; never a broker or downloader."""
import argparse
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
    parser.add_argument("--options-revision", type=int, required=True)
    parser.add_argument("--feed-revision", type=int, required=True)
    parser.add_argument("--internet-revision", type=int, required=True)
    parser.add_argument("--brief-revision", type=int, required=True)
    parser.add_argument("--required-contract", action="append", default=[])
    args = parser.parse_args()
    if os.environ.get("ARIADNE_OPTIONS_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
        raise SystemExit("Only an explicitly authorized online options child may run.")
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    from ariadne.config import Settings, _allowed_path
    from ariadne.store import Store
    from ariadne.tools import ToolRegistry
    from ariadne.briefing import BriefingService
    from ariadne.options_feed import collect_bundle, required_ids, OptionsFeedUnavailable
    from ariadne.options_data import canonical
    def local_path(value):
        path = Path(os.path.abspath(value))
        if not path.is_relative_to(ROOT):
            raise ValueError("Options worker paths must remain inside this box.")
        path = _allowed_path(path)
        if not path.is_relative_to(ROOT):
            raise ValueError("Resolved options worker paths must remain inside this box.")
        return path
    if any(value < 0 for value in (args.options_revision, args.feed_revision, args.internet_revision, args.brief_revision)):
        raise ValueError("Worker permission revisions must be nonnegative.")
    required = required_ids(args.required_contract)
    settings = Settings(data_dir=local_path(args.data_dir), workspace=local_path(args.workspace))
    store = Store(settings.data_dir / "ariadne.sqlite3")
    service = BriefingService(settings, store, ToolRegistry(settings))
    def check():
        with service.options.database() as db:
            current, feed, internet, brief = service.options.feed._policy(db, expected_feed_revision=args.feed_revision)
            if (type(internet) is not dict or internet.get("enabled") is not True or type(internet.get("revision")) is not int
                    or current["revision"] != args.options_revision or internet["revision"] != args.internet_revision
                    or brief["revision"] != args.brief_revision):
                raise ValueError("Options, Internet or briefing permission changed.")
    check()
    try:
        result = collect_bundle(check=check, required_contracts=required)
    except (OptionsFeedUnavailable, OSError, TimeoutError) as error:
        result = {"unavailable": True, "reason": str(error)[:1000]}
        if getattr(error, "source_evidence", None) is not None:
            result.update(schema=1, raw=error.source_evidence, network_requests=1)
    check()
    raw = canonical(result)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Options worker output exceeds its bound.")
    sys.stdout.buffer.write(raw)


if __name__ == "__main__":
    main()
