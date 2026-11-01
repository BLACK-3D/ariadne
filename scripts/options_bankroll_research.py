"""Bounded offline $25k options comparison; no owner account operations.

Register immutable local archives, benchmark bytes and a prior JSON intent.
New studies are limited to the last twelve calendar months at registration.
An attempt reserves a new private folder and is never replayed or overwritten.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_campaign import PauseAuthority, ExhaustedStream, _canonical, _root_guard, install_offline_guards
from scripts.options_market import guard_for, read
from scripts.options_universe import SourceLease, _json, SOURCE_FILES as OLD_SOURCE_FILES

MAX_OUTPUT, FAILURE_RESERVE, MAX_SECONDS = 8*1024**2, 16*1024, 600
SOURCE_FILES = tuple(dict.fromkeys((*OLD_SOURCE_FILES, "ariadne/options_bankroll_plan.py",
    "ariadne/options_bankroll_research.py", "scripts/options_bankroll_research.py", "ariadne/research_window.py")))
HASH = re.compile(r"[0-9a-f]{64}\Z")


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", action="append", required=True)
    value.add_argument("--manifest-sha256", action="append", required=True)
    value.add_argument("--benchmark", required=True)
    value.add_argument("--benchmark-sha256", required=True)
    value.add_argument("--benchmark-clock-profile", required=True, choices=("retained_capture", "assumed_eod_plus_15m"))
    value.add_argument("--intent", required=True)
    value.add_argument("--intent-sha256", required=True)
    value.add_argument("--workspace", default="data/workspace")
    value.add_argument("--owner-data", default="data")
    value.add_argument("--detached-research", action="store_true")
    value.add_argument("--output", required=True)
    return value


def _modules(box):
    raw = {name: read(box, name, 2*1024**2) for name in SOURCE_FILES}
    return raw, {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_archive import MAX_ARCHIVE, load_manifest, iter_archive
    from ariadne.options_data import research_symbol_set
    from ariadne.options_regime import build_timeline
    from ariadne.research_window import derive_window, require_archive_manifest, require_option_dataset
    from ariadne import options_bankroll_research as engine
    box = _root_guard(root)
    paths, hashes = args.manifest, args.manifest_sha256
    if (type(paths) is not list or type(hashes) is not list or not 1 <= len(paths) <= 64 or len(paths) != len(hashes)
            or any(type(name) is not str for name in paths) or len(set(name.casefold() for name in paths)) != len(paths)
            or any(type(value) is not str or not HASH.fullmatch(value) for value in (*hashes, args.benchmark_sha256, args.intent_sha256))):
        raise ValueError("Register1-64 unique manifests and exact matching lowercase SHA-256 values.")
    if args.benchmark_clock_profile not in ("retained_capture", "assumed_eod_plus_15m"):
        raise ValueError("Choose an explicit fixed benchmark-clock assumption.")
    parts = box._parts(args.output)
    if len(parts) < 2 or parts[0] != "staging" or "\\" in args.output:
        raise ValueError("A new canonical private staging/... output is required.")
    if type(args.detached_research) is not bool:
        raise ValueError("Detached research must be an explicit boolean.")
    if args.detached_research and (args.owner_data != "data" or box._path("data/ariadne.sqlite3").exists()):
        raise ValueError("Detached research cannot bypass an existing owner database or select a different one.")
    if Path(engine.__file__).resolve() != (ROOT/"ariadne/options_bankroll_research.py").resolve():
        raise ValueError("Research engine import changed.")
    output = box._path(args.output)
    with box._directory(output.parent, create=True): output.mkdir()
    authority, lease_stack = PauseAuthority(box, args.owner_data, args.detached_research), ExitStack()
    used, written, phase, started = 0, {}, "registration", time.monotonic()

    def check():
        if time.monotonic()-started > MAX_SECONDS:
            raise TimeoutError("Finite bankroll research exceeded600 seconds.")
        authority.check()

    def publish(name, content, *, failure=False, verify=None):
        nonlocal used
        if used+len(content) > MAX_OUTPUT-(0 if failure else FAILURE_RESERVE):
            raise ValueError("Finite research output exceeds8MiB; no truncation.")
        destination = box._path(args.output+"/"+name)
        with box._directory(destination.parent, create=True):
            publish_bytes(destination, content, checkpoint=None if failure else (verify or check))
        if read(box, args.output+"/"+name, MAX_OUTPUT) != content:
            raise ValueError("Output publication readback changed.")
        used += len(content)
        written[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    try:
        stamp = datetime.now(timezone.utc) if now is None else now
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError("Use an aware registration clock.")
        window = derive_window(stamp)
        publish("attempt.json", _canonical({"schema": 1, "registered_at": stamp.isoformat(),
                "study_window": window.as_dict(), "replay_allowed": False, "paper_operations": 0}), failure=True)
        check()
        workspace = guard_for(args.workspace, root=box.root)
        module_raw, module_hashes = _modules(box)
        intent_raw = read(box, args.intent, 256*1024)
        if hashlib.sha256(intent_raw).hexdigest() != args.intent_sha256:
            raise ValueError("The prior study intent changed.")
        intent = _json(intent_raw)
        if type(intent) is not dict or type(intent.get("schema")) is not int or intent["schema"] != 1:
            raise ValueError("Use a prior schema1 intent object, never executable instructions.")
        benchmark_raw = read(workspace, args.benchmark, 12*1024**2)
        if hashlib.sha256(benchmark_raw).hexdigest() != args.benchmark_sha256:
            raise ValueError("Benchmark bytes changed.")
        inputs = []
        for name, digest in zip(paths, hashes):
            check()
            raw = read(workspace, name, 256*1024)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Registered manifest changed.")
            value = _json(raw)
            if type(value) is not dict:
                raise ValueError("Use an exact stock archive object.")
            inputs.append((name, digest, raw, value.get("symbol")))
        symbols = tuple(sorted(value[3] for value in inputs if type(value[3]) is str))
        research_symbol_set(symbols)
        if len(symbols) != len(paths):
            raise ValueError("Every archive needs one unique explicit stock identity.")
        manifests, total = [], 0
        for name, digest, raw, _ in inputs:
            checked, value = load_manifest(workspace, name, expected_manifest_sha256=digest, research_symbols=symbols)
            if checked != raw: raise ValueError("Manifest changed during validation.")
            require_archive_manifest(value, window)
            total += sum(item["bytes"] for item in value["chunks"])
            manifests.append((name, digest, raw, value))
        if total > MAX_ARCHIVE:
            raise ValueError("Registered archives exceed512MiB aggregate.")
        if len({(value["selection"]["start"], value["selection"]["end"]) for _, _, _, value in manifests}) != 1:
            raise ValueError("Registered symbols must cover the same complete calendar window.")
        lease = SourceLease(lease_stack, check)
        for name, digest in module_hashes.items(): lease.add(box, name, digest, len(module_raw[name]))
        lease.add(box, args.intent, args.intent_sha256, len(intent_raw))
        lease.add(workspace, args.benchmark, args.benchmark_sha256, len(benchmark_raw))
        for name, digest, raw, value in manifests:
            lease.add(workspace, name, digest, len(raw))
            for chunk in value["chunks"]: lease.add(workspace, chunk["path"], chunk["sha256"], chunk["bytes"])
        rule_sha, rule = engine.RULE_SHA256, engine.profile_rule()
        registration = {"schema": 1, "kind": "pre_iteration_total_bankroll_research_registration", "registered_at": stamp.isoformat(),
            "symbols": list(symbols), "rule": rule, "rule_sha256": rule_sha, "module_sha256": module_hashes,
            "study_window": window.as_dict(),
            "intent": {"path": args.intent, "sha256": args.intent_sha256},
            "benchmark": {"path": args.benchmark, "sha256": args.benchmark_sha256, "clock_profile": args.benchmark_clock_profile},
            "manifests": [{"path": name, "sha256": digest, "symbol": value["symbol"], "selection": value["selection"], "chunks": value["chunks"]} for name, digest, _, value in manifests],
            "limits": {"symbols": 64, "ledgers": 4, "source_bytes": MAX_ARCHIVE, "seconds": MAX_SECONDS, "output_bytes": MAX_OUTPUT},
            "owner_authority": authority.scope(), "research_only": True, "execution_qualified": False,
            "paper_operations": 0, "model_calls": 0, "network_calls": 0, "automatic_adoption": False}
        registration_raw = _canonical(registration)
        publish("registration.json", registration_raw)
        publish("study-intent.json", intent_raw)
        for name in ("ariadne/options_bankroll_plan.py", "ariadne/options_bankroll_research.py", "scripts/options_bankroll_research.py", "ariadne/research_window.py"):
            publish("source_snapshot/"+name.replace("/", "_"), module_raw[name])
        phase = "benchmark_context"
        timeline = build_timeline(_json(benchmark_raw), clock_profile=args.benchmark_clock_profile, checkpoint=check)
        phase = "source_iteration"
        def admitted_chunks(name, digest):
            for chunk in iter_archive(workspace, name, expected_manifest_sha256=digest, checkpoint=check, research_symbols=symbols):
                check()
                require_option_dataset(chunk, window)
                yield chunk
        streams = {value["symbol"]: ExhaustedStream(admitted_chunks(name, digest), check)
                   for name, digest, _, value in manifests}
        result = engine.backtest(streams, symbols=symbols, benchmark=timeline, checkpoint=check)
        result["study_window"] = window.as_dict()
        if not all(stream.exhausted for stream in streams.values()) or not result.get("source_streams_exhausted"):
            raise ValueError("Every registered stock stream must be exhausted.")

        def verify_outputs():
            for name, item in written.items():
                raw = read(box, args.output+"/"+name, MAX_OUTPUT)
                if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("An earlier output changed before completion.")

        def verify():
            check()
            if _modules(box)[1] != module_hashes or engine.RULE_SHA256 != rule_sha or engine.profile_rule() != rule:
                raise ValueError("Research source/rule changed during the attempt.")
            lease.verify()
            verify_outputs()

        phase = "verification"
        verify()
        publish("results.json", _canonical(result))
        publish("benchmark-context.json", _canonical(timeline.describe()))
        verify_outputs()
        receipt = {"schema": 1, "status": "completed", "registered_sha256": hashlib.sha256(registration_raw).hexdigest(),
            "artifacts": dict(written), "source_streams_exhausted": True, "source_stable": True, "symbols": list(symbols),
            "seconds": round(time.monotonic()-started, 3), "study_window": window.as_dict(), "paper_operations": 0, "model_calls": 0,
            "network_calls": 0, "execution_qualified": False, "automatic_adoption": False}
        publish("completed.json", _canonical(receipt), verify=verify)
        return receipt
    except BaseException as error:
        publish("failed.json", _canonical({"schema": 1, "status": "failed", "phase": phase,
            "reason": str(error)[:1000], "error_type": type(error).__name__, "artifacts_retained": dict(written),
            "replay_allowed": False, "paper_operations": 0, "automatic_adoption": False}), failure=True)
        raise
    finally:
        try: lease_stack.close()
        finally: authority.close()


def main(argv=None):
    install_offline_guards()
    args = parser().parse_args(argv)
    try: result = run(args)
    except (ValueError, OSError, TimeoutError) as error:
        print("Bankroll research stopped: "+str(error), file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ("status", "symbols", "source_streams_exhausted", "paper_operations", "network_calls", "model_calls")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
