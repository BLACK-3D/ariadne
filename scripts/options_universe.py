"""Finite offline multi-stock options research, with one shared allowance.

Supply immutable local stock archives and a retained QQQ/SPY source bundle.
Neither this command nor historical results can change an owner paper account.
Every attempt reserves a new private folder; interruptions are never replayed.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_campaign import PauseAuthority, ExhaustedStream, _canonical, _root_guard, install_offline_guards
from scripts.options_market import guard_for, read

MAX_OUTPUT = 8 * 1024**2
FAILURE_RESERVE = 16 * 1024
MAX_SECONDS = 600
SOURCE_FILES = (
    "scripts/options_universe.py", "scripts/options_campaign.py", "scripts/options_market.py",
    "ariadne/options_universe.py", "ariadne/options_regime.py", "ariadne/options_campaign.py",
    "ariadne/options_data.py", "ariadne/options_archive.py", "ariadne/options_recovery.py",
    "ariadne/options_portfolio.py", "ariadne/qqq_data.py", "ariadne/atomic.py", "ariadne/tools.py",
    "ariadne/config.py", "ariadne/backup.py", "ariadne/briefing.py", "ariadne/network_policy.py",
    "ariadne/resources/market/America_New_York.tzif", "ariadne/resources/briefing/America_Chicago.tzif",
)
HASH = re.compile(r"[0-9a-f]{64}\Z")
PRESETS = ("conventional_regime", "volatility_regime")
PROFILES = ("strict_sizes", "unknown_size_price_proxy")
_TIMELINES = OrderedDict()


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError("Duplicate research metadata fields are rejected.")
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite research metadata.")))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("Research metadata must be bounded strict JSON.") from None


def _timeline(raw, clock_profile, module_hashes, checkpoint, builder):
    """Reuse only locally built immutable source-identical context, at most2.

    Sequential CLI orchestration can study many stocks without repeating a
    measured13.8second full-history calculation for every group. A source/code/
    timezone or clock-policy change is a miss; inputs are still leased/rehash-
    verified by each attempt. This cache is not a persisted approval or model.
    """
    checkpoint()
    key = (hashlib.sha256(raw).hexdigest(), clock_profile, tuple(sorted(module_hashes.items())))
    if key in _TIMELINES:
        value = _TIMELINES.pop(key); _TIMELINES[key] = value
        checkpoint()
        return value, True
    value = builder(_json(raw), clock_profile=clock_profile, checkpoint=checkpoint)
    checkpoint()
    _TIMELINES[key] = value
    while len(_TIMELINES) > 2: _TIMELINES.popitem(last=False)
    return value, False


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", action="append", required=True, help="1-64 workspace-relative single-stock manifests.")
    value.add_argument("--manifest-sha256", action="append", required=True)
    value.add_argument("--benchmark", required=True, help="Workspace-relative complete saved QQQ/SPY bundle.")
    value.add_argument("--benchmark-sha256", required=True)
    value.add_argument("--benchmark-clock-profile", choices=("retained_capture", "assumed_eod_plus_15m"), default="retained_capture")
    value.add_argument("--intent", required=True, help="Box-relative predeclared study intent, data, not executable code.")
    value.add_argument("--intent-sha256", required=True)
    value.add_argument("--preset", choices=PRESETS, default="conventional_regime")
    value.add_argument("--mode", choices=("pooled", "individual"), default="pooled",
        help="Individual mode studies at most8stocks sequentially; capital/results are separate counterfactuals.")
    value.add_argument("--profile", choices=PROFILES, action="append")
    value.add_argument("--workspace", default="data/workspace")
    value.add_argument("--owner-data", default="data")
    value.add_argument("--detached-research", action="store_true", help="Only for an isolated checkout without an owner database.")
    value.add_argument("--output", required=True, help="New private staging/... directory; no overwrite/replay.")
    return value


def _modules(box):
    raw = {name: read(box, name, 2 * 1024**2) for name in SOURCE_FILES}
    return raw, {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}


class SourceLease:
    """Keep reviewed read handles and rehash every input before publication."""
    def __init__(self, stack, checkpoint):
        self.stack, self.checkpoint, self.inputs, self.parents = stack, checkpoint, {}, {}

    def add(self, box, name, digest, size):
        path = box._path(name)
        key = os.path.normcase(str(path.absolute()))
        if key in self.inputs:
            if self.inputs[key][2:4] != (digest, size): raise ValueError("Conflicting identities for one source path.")
            return
        parent = os.path.normcase(str(path.parent.absolute()))
        if parent not in self.parents or os.name != "nt":
            self.parents[parent] = self.stack.enter_context(box._directory(path.parent))
        stream = self.stack.enter_context(os.fdopen(box._open_read(path, self.parents[parent]), "rb"))
        info = os.fstat(stream.fileno())
        if info.st_size != size: raise ValueError("Source changed before its registered iteration.")
        self.inputs[key] = (box, name, digest, size, stream, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))

    def verify(self):
        for box, name, expected, size, stream, identity in self.inputs.values():
            self.checkpoint()
            with box._directory(box._path(name).parent) as directory:
                descriptor = box._open_read(box._path(name), directory)
                try: current = os.fstat(descriptor)
                finally: os.close(descriptor)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != identity:
                raise ValueError("Registered source pathname/identity changed.")
            stream.seek(0)
            digest, count = hashlib.sha256(), 0
            while True:
                self.checkpoint()
                block = stream.read(min(1024**2, size-count+1))
                if not block: break
                count += len(block)
                if count > size: raise ValueError("Registered source grew.")
                digest.update(block)
            current = os.fstat(stream.fileno())
            if (count != size or digest.hexdigest() != expected
                    or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != identity):
                raise ValueError("Registered source bytes changed.")


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_archive import MAX_ARCHIVE, load_manifest, iter_archive
    from ariadne.options_data import research_symbol_set
    from ariadne.options_regime import build_timeline

    box = _root_guard(root)
    paths, hashes = args.manifest, args.manifest_sha256
    if (type(paths) is not list or type(hashes) is not list or not 1 <= len(paths) <= 64
            or len(paths) != len(hashes) or len(set(name.casefold() for name in paths if type(name) is str)) != len(paths)
            or any(type(value) is not str or not HASH.fullmatch(value) for value in hashes)):
        raise ValueError("Use 1-64 unique manifests with exact matching lowercase SHA-256 values.")
    if any(type(value) is not str or not HASH.fullmatch(value) for value in (args.benchmark_sha256, args.intent_sha256)):
        raise ValueError("Benchmark and prior intent require exact SHA-256 values.")
    if args.preset not in PRESETS or args.benchmark_clock_profile not in ("retained_capture", "assumed_eod_plus_15m"):
        raise ValueError("Choose fixed preset and explicit benchmark clock interpretation.")
    if args.mode not in ("pooled", "individual") or args.mode == "individual" and len(paths) > 8:
        raise ValueError("Choose pooled mode or at most8 sequential individual stock studies.")
    profiles = list(PROFILES) if args.profile is None else args.profile
    if type(profiles) is not list or not profiles or len(set(profiles)) != len(profiles) or any(value not in PROFILES for value in profiles):
        raise ValueError("Choose unique fixed liquidity profiles.")
    profiles = tuple(value for value in PROFILES if value in profiles)
    parts = box._parts(args.output)
    if len(parts) < 2 or parts[0] != "staging" or "\\" in args.output:
        raise ValueError("A new canonical private staging/... output is required.")
    if type(args.detached_research) is not bool:
        raise ValueError("Detached research must be an explicit boolean.")
    if args.detached_research:
        if args.owner_data != "data" or box._path("data/ariadne.sqlite3").exists():
            raise ValueError("Detached research cannot bypass an existing owner database or select a different one.")
    output = box._path(args.output)
    with box._directory(output.parent, create=True): output.mkdir()
    authority = PauseAuthority(box, args.owner_data, args.detached_research)
    held = ExitStack()
    used, written, phase = 0, {}, "registration"
    started = time.monotonic()

    def check():
        if time.monotonic()-started > MAX_SECONDS: raise TimeoutError("Finite research exceeded600seconds; no partial adoption.")
        authority.check()

    def publish(name, content, *, failure=False, verify=None):
        nonlocal used
        if used + len(content) > MAX_OUTPUT-(0 if failure else FAILURE_RESERVE):
            raise ValueError("Research output exceeds8MiB; no truncation.")
        destination = box._path(args.output+"/"+name)
        with box._directory(destination.parent, create=True):
            publish_bytes(destination, content, checkpoint=None if failure else (verify or check))
        if read(box, args.output+"/"+name, MAX_OUTPUT) != content: raise ValueError("Publication readback changed.")
        used += len(content)
        written[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    try:
        stamp = datetime.now(timezone.utc) if now is None else now
        if not isinstance(stamp, datetime) or stamp.tzinfo is None: raise ValueError("Use an aware registration clock.")
        publish("attempt.json", _canonical({"schema": 1, "registered_at": stamp.isoformat(), "replay_allowed": False,
            "preset": args.preset, "paper_operations": 0}), failure=True)
        check()
        workspace = guard_for(args.workspace, root=box.root)
        module_raw, module_hashes = _modules(box)
        engine = importlib.import_module("ariadne.options_universe")
        if Path(engine.__file__).resolve() != (ROOT/"ariadne/options_universe.py").resolve(): raise ValueError("Research engine import changed.")
        intent_raw = read(box, args.intent, 256*1024)
        if hashlib.sha256(intent_raw).hexdigest() != args.intent_sha256: raise ValueError("Predeclared intent changed.")
        # Strict bounded JSON, never a command/plugin/rule source.
        intent = _json(intent_raw)
        if type(intent) is not dict or type(intent.get("schema")) is not int or intent.get("schema") != 1:
            raise ValueError("Choose a schema1 prior intent object.")
        benchmark_raw = read(workspace, args.benchmark, 12*1024**2)
        if hashlib.sha256(benchmark_raw).hexdigest() != args.benchmark_sha256: raise ValueError("Benchmark source changed.")
        # Read symbol identities first under the exact supplied manifest hashes;
        # authoritative parsing below binds them to a bounded explicit allowlist.
        manifest_inputs = []
        for name, digest in zip(paths, hashes):
            check()
            raw = read(workspace, name, 256*1024)
            if hashlib.sha256(raw).hexdigest() != digest: raise ValueError("Manifest changed.")
            value = _json(raw)
            if type(value) is not dict: raise ValueError("Use an exact stock archive object.")
            manifest_inputs.append((name, digest, raw, value.get("symbol")))
        symbols = tuple(sorted(value[3] for value in manifest_inputs if type(value[3]) is str))
        research_symbol_set(symbols)
        if len(symbols) != len(paths): raise ValueError("Each selected archive must have one unique stock identity.")
        manifests, total = [], 0
        for name, digest, raw, _ in manifest_inputs:
            checked, value = load_manifest(workspace, name, expected_manifest_sha256=digest, research_symbols=symbols)
            if checked != raw: raise ValueError("Manifest changed between identity and validation.")
            total += sum(item["bytes"] for item in value["chunks"])
            manifests.append((name, digest, raw, value))
        if total > MAX_ARCHIVE: raise ValueError("Selected archives together exceed512MiB.")
        windows = {(value["selection"]["start"], value["selection"]["end"]) for _, _, _, value in manifests}
        if len(windows) != 1: raise ValueError("Compare stocks on the same complete predeclared calendar window.")
        lease = SourceLease(held, check)
        for name, digest in module_hashes.items(): lease.add(box, name, digest, len(module_raw[name]))
        lease.add(box, args.intent, args.intent_sha256, len(intent_raw))
        lease.add(workspace, args.benchmark, args.benchmark_sha256, len(benchmark_raw))
        for name, digest, raw, value in manifests:
            lease.add(workspace, name, digest, len(raw))
            for chunk in value["chunks"]: lease.add(workspace, chunk["path"], chunk["sha256"], chunk["bytes"])
        registry_sha = engine.RULE_SHA256
        rules = {profile: engine.profile_rule_sha256(args.preset, profile) for profile in profiles}
        registration = {"schema": 1, "kind": "pre_iteration_options_universe_registration", "registered_at": stamp.isoformat(),
            "symbols": list(symbols), "preset": args.preset, "mode": args.mode, "liquidity_profiles": list(profiles),
            "rule_sha256": registry_sha, "profile_rule_sha256": rules, "module_sha256": module_hashes,
            "intent": {"path": args.intent, "sha256": args.intent_sha256},
            "benchmark": {"path": args.benchmark, "sha256": args.benchmark_sha256, "clock_profile": args.benchmark_clock_profile},
            "manifests": [{"path": name, "sha256": digest, "symbol": value["symbol"], "selection": value["selection"], "chunks": value["chunks"]} for name, digest, _, value in manifests],
            "limits": {"stocks": 8 if args.mode == "individual" else 64, "source_bytes": MAX_ARCHIVE,
                "output_bytes": MAX_OUTPUT, "seconds": MAX_SECONDS, "peak_live_ledgers": 16, "cached_timelines": 2},
            "owner_authority": authority.scope(), "research_only": True, "execution_qualified": False,
            "paper_operations": 0, "model_calls": 0, "network_calls": 0, "automatic_adoption": False}
        registration_raw = _canonical(registration)
        publish("registration.json", registration_raw)
        publish("study-intent.json", intent_raw)
        for name in ("ariadne/options_universe.py", "ariadne/options_regime.py", "ariadne/options_campaign.py", "scripts/options_universe.py"):
            publish("source_snapshot/"+name.replace("/", "_"), module_raw[name])
        phase = "benchmark_context"
        timeline, cached = _timeline(benchmark_raw, args.benchmark_clock_profile, module_hashes, check, build_timeline)
        phase = "source_iteration"
        streams = {value["symbol"]: ExhaustedStream(iter_archive(workspace, name, expected_manifest_sha256=digest,
                    checkpoint=check, research_symbols=symbols), check) for name, digest, _, value in manifests}
        if args.mode == "pooled":
            result = engine.universe_campaign(streams, symbols=symbols, benchmark=timeline, preset=args.preset,
                liquidity_profiles=profiles, checkpoint=check)
        else:
            individual = {}
            for symbol in symbols:
                check()
                item = engine.universe_campaign({symbol: streams[symbol]}, symbols=(symbol,), benchmark=timeline,
                    preset=args.preset, liquidity_profiles=profiles, checkpoint=check)
                if not streams[symbol].exhausted or not item.get("source_streams_exhausted"):
                    raise ValueError("Every individual stock source must be exhausted.")
                individual[symbol] = item
            result = {"schema": 1, "mode": "individual", "individual_results": individual,
                "source_streams_exhausted": True, "research_only": True, "execution_qualified": False,
                "capital_scope": "Separate500USD counterfactual per stock/profile/preset. Never add capital or returns."}
        if not all(stream.exhausted for stream in streams.values()) or not result.get("source_streams_exhausted"):
            raise ValueError("Every stock source must be exhausted before a result is published.")

        def verify_outputs():
            for name, item in written.items():
                content = read(box, args.output+"/"+name, MAX_OUTPUT)
                if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError("Earlier output changed before completion.")

        def verify():
            check()
            if _modules(box)[1] != module_hashes or engine.RULE_SHA256 != registry_sha:
                raise ValueError("Research source changed.")
            if {profile: engine.profile_rule_sha256(args.preset, profile) for profile in profiles} != rules:
                raise ValueError("Research profile rules changed.")
            lease.verify()
            verify_outputs()

        phase = "verification"
        verify()
        publish("results.json", _canonical(result))
        publish("benchmark-context.json", _canonical({"timeline": timeline.describe(), "cache_hit": cached, "current_context": timeline.at(stamp),
            "current_analogs": timeline.analogs(stamp), "assumption_note": "Historical gates use only prefix prices but not authenticated publication/revision vintages."}))
        verify_outputs()
        completed = {"schema": 1, "status": "completed", "registered_sha256": hashlib.sha256(registration_raw).hexdigest(),
            "artifacts": dict(written), "source_streams_exhausted": True, "source_stable": True,
            "symbols": list(symbols), "preset": args.preset, "mode": args.mode, "benchmark_cache_hit": cached,
            "seconds": round(time.monotonic()-started, 3),
            "paper_operations": 0, "model_calls": 0, "network_calls": 0, "execution_qualified": False, "automatic_adoption": False}
        publish("completed.json", _canonical(completed), verify=verify)
        return completed
    except BaseException as error:
        failure = {"schema": 1, "status": "failed", "phase": phase, "reason": str(error)[:1000],
            "error_type": type(error).__name__, "artifacts_retained": dict(written), "replay_allowed": False,
            "paper_operations": 0, "automatic_adoption": False}
        publish("failed.json", _canonical(failure), failure=True)
        raise
    finally:
        try:
            held.close()
        finally:
            authority.close()


def main(argv=None):
    install_offline_guards()
    args = parser().parse_args(argv)
    try: result = run(args)
    except (ValueError, OSError, TimeoutError) as error:
        print("Options universe stopped: "+str(error), file=sys.stderr); return 2
    print(json.dumps({key: result[key] for key in ("status", "symbols", "preset", "source_streams_exhausted", "paper_operations", "model_calls", "network_calls")}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
