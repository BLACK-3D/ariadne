"""Finite offline KO experiments; frozen DESIGN selection precedes full outcomes.

The fixed source is125 observations within the last twelve calendar months, not
twelve months of option history. Every configuration is a separate $25k
counterfactual. No source collection, owner account change or model operation.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import Context, Decimal, localcontext
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_campaign import PauseAuthority, _canonical, _root_guard, install_offline_guards
from scripts.options_market import guard_for, read
from scripts.options_universe import SourceLease, _json, SOURCE_FILES as OLD_SOURCE_FILES

PROFILES = ("assumed_eod_plus_15m", "assumed_next_session_0935")
START, END = "2026-05-04", "2026-10-29"
DESIGN_CUTOFF = "2026-07-31T23:59:59-04:00"
DEFAULT_WORKSPACE = "staging/options_working_source_2026_11_01/paired-quotes-v3"
MAX_SECONDS, MAX_OUTPUT, FAILURE_RESERVE = 600, 32*1024**2, 64*1024
MAX_CAPTURE_BYTES, MAX_SOURCE_ROWS, MAX_CONFIGS = 24*1024**2, 125, 100
SOURCE_FILES = tuple(dict.fromkeys((*OLD_SOURCE_FILES,
    "scripts/options_experiments.py", "ariadne/options_experiments.py",
    "ariadne/options_experiment_signals.py", "ariadne/options_bankroll_plan.py",
    "ariadne/research_window.py")))
HASH = re.compile(r"[0-9a-f]{64}\Z")


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    value.add_argument("--source-completed-sha256", required=True)
    value.add_argument("--eod-manifest-sha256", required=True)
    value.add_argument("--delayed-manifest-sha256", required=True)
    value.add_argument("--benchmark-sha256", required=True)
    value.add_argument("--intent", required=True)
    value.add_argument("--intent-sha256", required=True)
    value.add_argument("--owner-data", default="data")
    value.add_argument("--detached-research", action="store_true")
    value.add_argument("--output", required=True)
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError("Exact lowercase SHA-256 values are required.")
    return value


def _modules(box):
    raw = {name: read(box, name, 2*1024**2) for name in SOURCE_FILES}
    return raw, {name: _sha(body) for name, body in raw.items()}


def _session_open(day):
    # The reviewed Chicago/NY calendars share DST transitions;08:30CT is the
    # regular09:30NY open even on an early-close session. Never subtract6.5h
    # from an early closing time.
    from ariadne.briefing import _tz
    return datetime.combine(day, clock_time(8, 30), _tz("America/Chicago")).astimezone(timezone.utc)


def _coverage(rows):
    return {"rows": len(rows), "source_start": rows[0]["observed_at"],
            "source_end": rows[-1]["observed_at"], "available_start": rows[0]["available_at"],
            "available_end": rows[-1]["available_at"]}


def _money(value):
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError("Experimental money must remain a bounded decimal string.")
    number = Decimal(value)
    if not number.is_finite() or abs(number) > Decimal("1e12"):
        raise ValueError("Experimental money must be finite and bounded.")
    return number


def _development(identifier, design, full, count):
    state, final = design["state"], full["state"]
    if (design["complete"] is not True or state["position"] is not None
            or state["pending"] is not None or state["hold"] is not None):
        raise ValueError("A frozen selection needs a completed flat DESIGN cash baseline.")
    if state["contributions"] != "25000" or final["contributions"] != "25000":
        raise ValueError("A phase boundary cannot add funding or reset capital.")
    boundary = full["curve"][count-1]
    with localcontext(Context(prec=60)):
        cash = _money(state["cash"])
        if (_money(boundary["cash"]) != cash or boundary["quantity"] != 0
                or boundary["held"] or boundary["equity"] is None
                or _money(boundary["equity"]) != cash):
            raise ValueError("The continuous full path changed the frozen DESIGN cash boundary.")
        eligible = (full["complete"] is True and final["position"] is None
                    and final["pending"] is None and final["hold"] is None
                    and full["terminal_pnl"] is not None)
        increment = format(_money(final["cash"])-cash, "f") if eligible else None
    return {"candidate_id": identifier, "design_cash": state["cash"],
        "design_terminal_pnl": design["terminal_pnl"], "full_terminal_pnl": full["terminal_pnl"],
        "development_increment": increment,
        "reason": None if eligible else "full_path_not_completed_flat_known_cash",
        "design_complete": design["complete"], "full_complete": full["complete"],
        "nav_complete": full["nav_complete"], "full_hold": deepcopy(final["hold"]),
        "design_closed_trades": state["closed_trades"], "full_closed_trades": final["closed_trades"],
        "new_funding": "0", "no_reset": True,
        "previously_seen": True, "independent_holdout": False}


def run(args, *, root=ROOT, now=None, _authority=None):
    from ariadne import options_experiments as engine, options_experiment_signals as hypotheses
    from ariadne.atomic import publish_bytes
    from ariadne.options_archive import load_manifest, iter_archive
    from ariadne.options_regime import build_timeline
    from ariadne.qqq_data import session_dates, session_close_utc, exchange_day
    from ariadne.research_window import derive_window, require_archive_manifest, require_option_dataset

    started = time.monotonic()
    box = _root_guard(root)
    window = derive_window(now)
    hashes = {PROFILES[0]: _digest(args.eod_manifest_sha256),
              PROFILES[1]: _digest(args.delayed_manifest_sha256)}
    for value in (args.source_completed_sha256, args.benchmark_sha256, args.intent_sha256):
        _digest(value)
    parts = box._parts(args.output)
    if len(parts) < 2 or parts[0] != "staging" or "\\" in args.output:
        raise ValueError("Use a new canonical private staging/... output.")
    box._parts(args.owner_data)
    if type(args.detached_research) is not bool:
        raise ValueError("Detached research must be an explicit boolean.")
    if args.detached_research and (args.owner_data != "data" or box._path("data/ariadne.sqlite3").exists()):
        raise ValueError("Detached research cannot bypass an existing owner database.")
    workspace = guard_for(args.workspace, root=box.root)
    module_raw, module_hashes = _modules(box)
    # Imports execute trusted local code, not a supplied manifest. Verify their
    # actual location and bytes agree with the files being preregistered.
    runtime_box = _root_guard(ROOT)
    for module, name in ((engine, "ariadne/options_experiments.py"),
                         (hypotheses, "ariadne/options_experiment_signals.py")):
        if Path(module.__file__).resolve() != (ROOT/name).resolve() or _sha(read(runtime_box, name, 2*1024**2)) != module_hashes[name]:
            raise ValueError("Imported experiment code differs from its registered source.")
    configs = engine.configs()
    if (type(configs) is not list or not 1 <= len(configs) <= MAX_CONFIGS
            or any(type(item) is not dict for item in configs)
            or len({item.get("id") for item in configs}) != len(configs)
            or sum(item.get("id") == "cash" for item in configs) != 1):
        raise ValueError("Use the unique bounded fixed experiment registry including cash.")
    for config in configs:
        engine.validate_config(config)
    rules = {"engine": deepcopy(engine.RULE), "signals": deepcopy(hypotheses.RULE)}
    rule_hashes = {"engine": _digest(engine.RULE_SHA256), "signals": _digest(hypotheses.RULE_SHA256)}
    if any(_sha(_canonical(rules[name])) != rule_hashes[name] for name in rules):
        raise ValueError("The experiment rule descriptor no longer matches its immutable digest.")
    intent_raw = read(box, args.intent, 256*1024)
    if _sha(intent_raw) != args.intent_sha256:
        raise ValueError("The prior experiment intent changed.")
    intent = _json(intent_raw)
    if type(intent) is not dict or type(intent.get("schema")) is not int or intent["schema"] != 1:
        raise ValueError("Use a prior schema1 intent object, never executable rules.")
    complete_raw = read(workspace, "completed.json", 256*1024)
    if _sha(complete_raw) != args.source_completed_sha256:
        raise ValueError("The source capture completion receipt changed.")
    capture = _json(complete_raw)
    if (type(capture) is not dict or type(capture.get("schema")) is not int or capture["schema"] != 1
            or capture.get("status") != "completed"
            or capture.get("kind") != "fixed_KO_historical_nullable_sizes_reference_research"
            or type(capture.get("source_records")) is not int or capture["source_records"] != MAX_SOURCE_ROWS
            or capture.get("source_stable") is not True or capture.get("execution_qualified") is not False
            or capture.get("automatic_adoption") is not False
            or type(capture.get("paper_operations")) is not int or capture["paper_operations"] != 0
            or type(capture.get("model_calls")) is not int or capture["model_calls"] != 0
            or type(capture.get("artifacts")) is not dict or type(capture.get("outputs")) is not dict
            or not 1 <= len(capture["artifacts"]) <= 256):
        raise ValueError("Only the completed immutable historical source capture is admitted.")
    inventory, total = {}, 0
    for name, item in capture["artifacts"].items():
        workspace._parts(name)
        if ("\\" in name or type(item) is not dict or set(item) != {"bytes", "sha256"}
                or type(item["bytes"]) is not int or not 1 <= item["bytes"] <= MAX_CAPTURE_BYTES):
            raise ValueError("Malformed or oversized captured source artifact.")
        _digest(item["sha256"])
        if name.casefold() in inventory:
            raise ValueError("Source artifact aliases are not allowed.")
        inventory[name.casefold()] = (name, item)
        total += item["bytes"]
    if total > MAX_CAPTURE_BYTES:
        raise ValueError("Captured source artifacts exceed the fixed24MiB bound.")
    def captured(name, digest=None):
        if name.casefold() not in inventory:
            raise ValueError("A required consumed file is absent from capture evidence.")
        actual, item = inventory[name.casefold()]
        if actual != name or digest is not None and item["sha256"] != digest:
            raise ValueError("Capture filename/hash identity differs from the registered input.")
        return item
    benchmark_item = captured("benchmark.json", args.benchmark_sha256)
    manifests = {}
    for profile in PROFILES:
        name = profile+"/catalog.json"
        item = captured(name, hashes[profile])
        declared = capture.get("outputs", {}).get(profile)
        if declared != {"catalog_path": name, "catalog_sha256": hashes[profile]}:
            raise ValueError("Captured clock-profile catalog identity changed.")
        raw, value = load_manifest(workspace, name, expected_manifest_sha256=hashes[profile], research_symbols=("KO",))
        if len(raw) != item["bytes"] or value["symbol"] != "KO" or value["evidence_kind"] != "historical_quotes":
            raise ValueError("Only the explicit KO historical catalog is admitted.")
        require_archive_manifest(value, window)
        if (value["selection"]["start"], value["selection"]["end"]) != (START, END):
            raise ValueError("The fixed125-session source range changed; no clipping.")
        for chunk in value["chunks"]:
            binding = captured(chunk["path"], chunk["sha256"])
            if binding["bytes"] != chunk["bytes"]:
                raise ValueError("Captured canonical chunk length changed.")
        manifests[profile] = (name, raw, value)
    destination = box._path(args.output)
    with box._directory(destination.parent, create=True):
        destination.mkdir()
    authority = (_authority or PauseAuthority)(box, args.owner_data, args.detached_research)
    held = ExitStack()
    used, written, phase = 0, {}, "registration"

    def check():
        if time.monotonic()-started >= MAX_SECONDS:
            raise TimeoutError("Finite experiments exceeded600seconds; no replay.")
        authority.check()
        if time.monotonic()-started >= MAX_SECONDS:
            raise TimeoutError("Authority check crossed the fixed experiment deadline.")

    def publish(name, raw, *, failure=False, verify=None):
        nonlocal used
        if used+len(raw) > MAX_OUTPUT-(0 if failure else FAILURE_RESERVE):
            raise ValueError("Experiment output exceeds32MiB; no truncation.")
        path = box._path(args.output+"/"+name)
        with box._directory(path.parent, create=True):
            publish_bytes(path, raw, checkpoint=None if failure else (verify or check))
        if read(box, args.output+"/"+name, len(raw)) != raw:
            raise ValueError("Experiment publication readback changed.")
        used += len(raw)
        written[name] = {"bytes": len(raw), "sha256": _sha(raw)}

    try:
        publish("attempt.json", _canonical({"schema": 1, "registered_at": window.registered_at,
            "phase": phase, "replay_allowed": False, "paper_operations": 0}), failure=True)
        check()
        lease = SourceLease(held, check)
        for name, digest in module_hashes.items():
            lease.add(box, name, digest, len(module_raw[name]))
        lease.add(box, args.intent, args.intent_sha256, len(intent_raw))
        lease.add(workspace, "completed.json", args.source_completed_sha256, len(complete_raw))
        for name, item in inventory.values():
            lease.add(workspace, name, item["sha256"], item["bytes"])

        def verify():
            check()
            if (_modules(box)[1] != module_hashes or engine.configs() != configs
                    or engine.RULE != rules["engine"] or hypotheses.RULE != rules["signals"]
                    or engine.RULE_SHA256 != rule_hashes["engine"] or hypotheses.RULE_SHA256 != rule_hashes["signals"]
                    or any(_sha(_canonical(rules[name])) != rule_hashes[name] for name in rules)):
                raise ValueError("Frozen source, configuration or rule changed during the experiment.")
            lease.verify()
            for name, item in written.items():
                raw = read(box, args.output+"/"+name, item["bytes"])
                if len(raw) != item["bytes"] or _sha(raw) != item["sha256"]:
                    raise ValueError("An earlier experiment artifact changed before publication.")

        registration = {"schema": 1, "kind": "pre_price_fixed_KO_experiment_registration",
            "registered_at": window.registered_at, "study_window": window.as_dict(),
            "configs": deepcopy(configs), "configuration_count": len(configs), "rules": rules,
            "rule_sha256": rule_hashes, "module_sha256": module_hashes,
            "intent": {"path": args.intent, "sha256": args.intent_sha256},
            "source": {"workspace": args.workspace, "completed_sha256": args.source_completed_sha256,
                "artifact_inventory": deepcopy(capture["artifacts"]),
                "profiles": {profile: {"catalog_path": manifests[profile][0], "catalog_sha256": hashes[profile]}
                             for profile in PROFILES}},
            "benchmark": {"path": "benchmark.json", "sha256": args.benchmark_sha256,
                "clock_profile": "assumed_eod_plus_15m", "builds": 1,
                "fill_context_query": "Regular09:30NewYork SOURCE session open, including early-close sessions; never current source close context."},
            "phase_policy": {"DESIGN": "Prefix with available_at<=cutoff, publish both profiles+selection before full run",
                "cutoff": DESIGN_CUTOFF, "DEVELOPMENT": "After cutoff through each profile's actual final availability; previously seen, not independent holdout",
                "FULL": "One initial25000 contribution from first source frame, continuous state; no reset at selection or month"},
            "limits": {"configurations": MAX_CONFIGS, "source_frames_per_profile": MAX_SOURCE_ROWS,
                "seconds": MAX_SECONDS, "output_bytes": MAX_OUTPUT},
            "limitations": ["125source sessions withinlast12months, not12months of options history",
                "Independent reported-close source has UNKNOWNadjustmentbasis and FAILED cross-source/calendar reconciliation; projection excludes unusable outside-cohort anomalies only",
                "Partial one-monthly-expiry chains; unknown sizes remain null; source missingness and contract exits cannot be repaired",
                "Source event/publication clocks, historical revision vintages and standard100deliverables are unverified assumptions",
                "New research partial-exit/targets/holding/quantity policies are not the current paper account contract",
                "178paths perphase are configurations, not178independenttrades; previously seen data cannot prove future edge"],
            "owner_authority": authority.scope(), "paper_operations": 0, "model_calls": 0,
            "network_calls": 0, "automatic_adoption": False, "execution_qualified": False}
        registration_raw = _canonical(registration)
        # The immutable registration must exist BEFORE a full lease hash reads
        # benchmark prices or canonical option chunks. add() above only pins
        # handles/identities; metadata catalogs contain no option prices.
        publish("registration.json", registration_raw)
        registration_sha = _sha(registration_raw)
        verify()
        publish("study-intent.json", intent_raw)
        for name in ("scripts/options_experiments.py", "ariadne/options_experiments.py", "ariadne/options_experiment_signals.py"):
            publish("source_snapshot/"+name.replace("/", "_"), module_raw[name])
        # No benchmark-price or option-chunk bytes have been read before the
        # immutable registration above. Catalogs and completion are metadata.
        phase = "benchmark_context"
        benchmark_raw = read(workspace, "benchmark.json", benchmark_item["bytes"])
        if _sha(benchmark_raw) != args.benchmark_sha256:
            raise ValueError("The registered benchmark changed.")
        timeline = build_timeline(_json(benchmark_raw), clock_profile="assumed_eod_plus_15m", checkpoint=check)
        phase = "source_iteration"
        data, context_audit, decision, fill, design_counts = {}, {}, {}, {}, {}
        expected = session_dates(date.fromisoformat(START), date.fromisoformat(END))
        cutoff = datetime.fromisoformat(DESIGN_CUTOFF)
        for profile in PROFILES:
            rows = []
            for chunk in iter_archive(workspace, manifests[profile][0], expected_manifest_sha256=hashes[profile],
                                      checkpoint=check, research_symbols=("KO",)):
                require_option_dataset(chunk, window)
                rows.extend(chunk["snapshots"])
                if len(rows) > MAX_SOURCE_ROWS:
                    raise ValueError("The fixed source-frame bound changed.")
            if len(rows) != MAX_SOURCE_ROWS or [exchange_day(row["observed_at"]) for row in rows] != expected:
                raise ValueError("Every fixed source session must be present exactly once.")
            decision[profile], fill[profile] = [], []
            context_audit[profile] = {"decision": [], "fill": []}
            for day, row in zip(expected, rows):
                check()
                if datetime.fromisoformat(row["observed_at"]) != session_close_utc(day):
                    raise ValueError("The captured assumed source-close convention changed.")
                observed = session_close_utc(day)
                if profile == PROFILES[0]:
                    available = observed+timedelta(minutes=15)
                else:
                    following = session_dates(day+timedelta(days=1), day+timedelta(days=8))[0]
                    available = _session_open(following)+timedelta(minutes=5)
                if datetime.fromisoformat(row["available_at"]) != available:
                    raise ValueError("The captured availability profile changed.")
                opened = _session_open(day)
                dc, fc = timeline.at(row["available_at"]), timeline.at(opened)
                if (fc.get("status") == "ready" and (fc.get("available_at") is None
                        or datetime.fromisoformat(fc["available_at"]) > opened
                        or fc.get("source_session") is None or fc["source_session"] >= day.isoformat())):
                    raise ValueError("Fill benchmark context used current or unpublished information.")
                decision[profile].append(dc); fill[profile].append(fc)
                context_audit[profile]["decision"].append({"query_at": row["available_at"], "context": dc})
                context_audit[profile]["fill"].append({"query_at": opened.isoformat(), "context": fc})
            design_counts[profile] = sum(datetime.fromisoformat(row["available_at"]) <= cutoff for row in rows)
            if not 1 <= design_counts[profile] < len(rows):
                raise ValueError("Both chronological phases must contain source observations.")
            data[profile] = rows
        for left, right in zip(data[PROFILES[0]], data[PROFILES[1]]):
            if {key: value for key, value in left.items() if key != "available_at"} != {key: value for key, value in right.items() if key != "available_at"}:
                raise ValueError("Clock profiles must retain identical economic source rows.")
        row_hashes = {profile: _sha(_canonical(rows)) for profile, rows in data.items()}
        contexts = {"schema": 1, "benchmark": timeline.describe(), "profiles": context_audit}
        publish("contexts.json", _canonical(contexts))
        phase = "DESIGN"
        design_results = {}
        for profile in PROFILES:
            count = design_counts[profile]
            design_results[profile] = []
            for config in configs:
                check()
                result = engine.run(data[profile][:count], deepcopy(config), decision_contexts=decision[profile][:count],
                    fill_contexts=fill[profile][:count], checkpoint=check)
                if result.get("all_source_frames_accounted") is not True or _sha(_canonical(data[profile])) != row_hashes[profile]:
                    raise ValueError("A DESIGN case omitted or changed registered source rows.")
                design_results[profile].append(result)
        design_output = {"schema": 1, "phase": "DESIGN", "cutoff": DESIGN_CUTOFF,
            "results": design_results,
            "coverage": {profile: _coverage(data[profile][:design_counts[profile]]) for profile in PROFILES}}
        design_raw = _canonical(design_output)
        publish("design-results.json", design_raw, verify=verify)
        selection = engine.select(design_results)
        identifiers = {config["id"] for config in configs}
        if type(selection) is not dict or selection.get("candidate_id") not in identifiers:
            raise ValueError("Selection must name one preregistered configuration or cash.")
        selection.update(registration_sha256=registration_sha, design_results_sha256=_sha(design_raw))
        selection_raw = _canonical(selection)
        publish("selection.json", selection_raw, verify=verify)
        # This publication is the irreversible phase boundary. No full-path
        # engine call is made before both DESIGN reports and selection exist.
        phase = "FULL_CONTINUOUS"
        full_results = {}
        for profile in PROFILES:
            full_results[profile] = []
            for config in configs:
                check()
                result = engine.run(data[profile], deepcopy(config), decision_contexts=decision[profile],
                                    fill_contexts=fill[profile], checkpoint=check)
                if result.get("all_source_frames_accounted") is not True or _sha(_canonical(data[profile])) != row_hashes[profile]:
                    raise ValueError("A full case omitted or changed registered source rows.")
                full_results[profile].append(result)
        publish("full-results.json", _canonical({"schema": 1, "phase": phase, "results": full_results,
            "coverage": {profile: _coverage(data[profile]) for profile in PROFILES}}))
        identifier = selection["candidate_id"]
        selected = {"candidate_id": identifier, "selection_sha256": _sha(selection_raw),
                    "profiles": {}, "no_reset": True, "new_funding": "0", "previously_seen": True,
                    "independent_holdout": False, "automatic_adoption": False}
        for profile in PROFILES:
            before = next(report for report in design_results[profile] if report["config"]["id"] == identifier)
            after = next(report for report in full_results[profile] if report["config"]["id"] == identifier)
            selected["profiles"][profile] = _development(identifier, before, after, design_counts[profile])
            selected["profiles"][profile]["coverage"] = _coverage(data[profile][design_counts[profile]:])
        publish("selected-development.json", _canonical(selected))
        phase = "verification"
        verify()
        receipt = {"schema": 1, "status": "completed", "kind": "fixed_KO_causal_experiments",
            "registration_sha256": registration_sha, "artifacts": dict(written),
            "source_stable": True, "source_streams_exhausted": True,
            "configuration_count": len(configs), "clock_profiles": list(PROFILES),
            "phase_counts": {"DESIGN": len(configs)*2, "FULL_CONTINUOUS": len(configs)*2},
            "selection_sha256": _sha(selection_raw), "seconds": round(time.monotonic()-started, 3),
            "study_window": window.as_dict(), "paper_operations": 0, "model_calls": 0,
            "network_calls": 0, "execution_qualified": False, "automatic_adoption": False,
            "replay_allowed": False}
        publish("completed.json", _canonical(receipt), verify=verify)
        return receipt
    except BaseException as error:
        publish("failed.json", _canonical({"schema": 1, "status": "failed_stopped_no_replay",
            "phase": phase, "error_type": type(error).__name__, "reason": str(error)[:1000],
            "artifacts_retained": dict(written), "paper_operations": 0, "network_calls": 0,
            "model_calls": 0, "automatic_adoption": False, "replay_allowed": False}), failure=True)
        raise
    finally:
        try:
            held.close()
        finally:
            authority.close()


def main(argv=None):
    install_offline_guards()
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (ValueError, OSError, TimeoutError) as error:
        print("Offline experiments stopped: "+str(error), file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ("status", "configuration_count", "phase_counts",
        "source_stable", "source_streams_exhausted", "paper_operations", "network_calls")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
