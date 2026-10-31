"""Finite offline options research, never a paper-account or broker operation.

Supply immutable archive manifests and their exact hashes. Every run reserves a
new private staging folder; failed/partial work cannot be replayed or adopted.
The default owner check reads only global pause authority. Detached research
must be explicit and makes no claim to have checked owner state.
"""
from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_OUTPUT = 8 * 1024 * 1024
FAILURE_RESERVE = 16 * 1024
MAX_SOURCE_FILE = 2 * 1024 * 1024
SOURCE_FILES = (
    "ariadne/options_campaign.py", "ariadne/options_recovery.py",
    "ariadne/options_portfolio.py", "ariadne/options_data.py",
    "ariadne/options_archive.py", "ariadne/qqq_data.py",
    "ariadne/briefing.py", "ariadne/resources/briefing/America_Chicago.tzif",
    "ariadne/atomic.py", "ariadne/tools.py", "ariadne/config.py",
    "ariadne/backup.py", "ariadne/network_policy.py",
    "ariadne/resources/market/America_New_York.tzif",
    "scripts/options_market.py", "scripts/options_campaign.py",
)
SNAPSHOTS = {"ariadne/options_campaign.py": "source_snapshot/ariadne_options_campaign.py",
             "scripts/options_campaign.py": "source_snapshot/scripts_options_campaign.py"}
PROFILES = ("strict_sizes", "unknown_size_price_proxy")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--stage", required=True, choices=("conventional", "original", "combined"),
                        help="Registration metadata; never selects a winner or grants account authority.")
    result.add_argument("--manifest", action="append", required=True, help="1-4 workspace-relative archive manifests.")
    result.add_argument("--manifest-sha256", action="append", required=True, help="Matching exact lowercase manifest SHA-256, in the same order.")
    result.add_argument("--workspace", default="data/workspace")
    result.add_argument("--output", required=True, help="New box-relative private staging/... folder; never overwritten.")
    result.add_argument("--candidate", action="append", help="Fixed engine candidate ID; repeat up to the ledger limit.")
    result.add_argument("--profile", action="append", choices=PROFILES, help="Default both, fanned out from one verified source stream.")
    result.add_argument("--owner-data", default="data", help="Existing box-relative owner data folder, pause reads only.")
    result.add_argument("--detached-research", action="store_true", help="Explicitly skip all owner-data reads; no owner-readiness claim.")
    return result


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError("Campaign metadata/report is not bounded strict JSON.") from None


def _root_guard(root):
    from ariadne.config import _allowed_path
    from ariadne.tools import ToolRegistry
    guard = object.__new__(ToolRegistry)
    guard.root = _allowed_path(root)
    return guard


def _source_inputs():
    from scripts.options_market import read
    guard = _root_guard(ROOT)
    raw = {name: read(guard, name, MAX_SOURCE_FILE) for name in SOURCE_FILES}
    return raw, {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}


class PauseAuthority:
    """One pinned RO session; every check ends its own fresh read transaction."""
    def __init__(self, box, relative, detached):
        self.box, self.relative, self.detached = box, relative, detached
        self.epoch = None
        self.opened = False
        self.db = None
        self.pins = ExitStack()
        self.closed = False
        self.deadline = 0
        self.path = None
        self.main_identity = None

    def _check_main_identity(self):
        from ariadne.backup import regular_file
        path, present = regular_file(self.path)
        if not present or path != self.path:
            raise ValueError("Owner pause database pathname changed; research is held.")
        info = path.lstat()
        if (info.st_dev, info.st_ino) != self.main_identity:
            raise ValueError("Owner pause database identity changed; research is held.")

    def _execute(self, statement):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Owner pause check exceeded its five-second budget.")
            # Short native busy slices avoid a long handler overshooting its
            # advertised duration. All retries share this check's one deadline.
            with closing(self.db.execute("PRAGMA busy_timeout=" + str(max(1, min(100, int(remaining * 1000)))))):
                pass
            try:
                cursor = self.db.execute(statement)
                break
            except sqlite3.OperationalError as error:
                if getattr(error, "sqlite_errorcode", 0) & 255 != sqlite3.SQLITE_BUSY:
                    raise
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Owner pause check exceeded its five-second budget.") from error
                time.sleep(min(0.005, remaining))
        if time.monotonic() > self.deadline:
            cursor.close()
            raise TimeoutError("Owner pause check exceeded its five-second budget.")
        return cursor

    def _open(self):
        from ariadne.backup import database_path, _pin_database_files
        path = self.box._path(self.relative + "/ariadne.sqlite3")
        try:
            self.pins.enter_context(self.box._directory(path.parent))
            path, present = database_path(path)
            if not present:
                raise ValueError("Existing owner pause database is missing; nothing was created.")
            info = path.lstat()
            self.path, self.main_identity = path, (info.st_dev, info.st_ino)
            self.pins.enter_context(_pin_database_files(path))
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Owner pause check exceeded its five-second budget.")
            self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                      timeout=remaining, isolation_level=None)
            self.opened = True
            self.db.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 1000)
            for statement in ("PRAGMA query_only=ON", "PRAGMA trusted_schema=OFF"):
                with closing(self._execute(statement)):
                    pass
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.closed:
            raise ValueError("Owner pause reader is closed; it cannot be replayed.")
        if self.detached:
            return
        from scripts.options_market import metadata
        self.deadline = time.monotonic() + 5
        if self.db is None:
            self._open()
        try:
            self._check_main_identity()
            with closing(self._execute("BEGIN")):
                pass
            with closing(self._execute("SELECT rowid,typeof(value),length(CAST(value AS BLOB)),"
                    "substr(CAST(value AS BLOB),1,17) FROM settings WHERE key='paused'")) as cursor:
                row = cursor.fetchone()
            if not row or row[1] != "text" or not 1 <= row[2] <= 16:
                raise ValueError("Owner pause authority is missing or malformed; research is held.")
            paused = metadata(bytes(row[3]))
            if type(paused) is not bool or paused:
                raise ValueError("Owner autonomy is paused or unknown; research is held.")
            observed = (row[0], hashlib.sha256(bytes(row[3])).hexdigest())
            if self.epoch is not None and observed != self.epoch:
                raise ValueError("Owner pause epoch changed; research is held without replay.")
            if self.db.total_changes:
                raise ValueError("Owner authority check cannot write state.")
            self._check_main_identity()
            with closing(self._execute("COMMIT")):
                pass
            self.epoch = observed
        except BaseException:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def close(self):
        db, self.db = self.db, None
        self.closed = True
        try:
            if db is not None:
                db.close()
        finally:
            self.pins.close()

    def scope(self):
        return {"mode": "detached_research_owner_unchecked" if self.detached else "read_only_pause_authority",
                "owner_database_opened": self.opened,
                "owner_readiness_established": False,
                "pause_epoch": None if self.detached else self.epoch[0] if self.epoch else None,
                "paper_account_accessed": False, "owner_settings_written": False}


class ExhaustedStream:
    def __init__(self, values, checkpoint):
        self.iterator, self.checkpoint, self.exhausted = iter(values), checkpoint, False

    def __iter__(self):
        return self

    def __next__(self):
        self.checkpoint()
        try:
            value = next(self.iterator)
        except StopIteration:
            self.exhausted = True
            raise
        self.checkpoint()
        return value


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_archive import MAX_ARCHIVE, MAX_MANIFEST, iter_archive, load_manifest
    from ariadne.options_data import MAX_DATA
    from scripts.options_market import guard_for, read
    box = _root_guard(root)
    if args.stage not in {"conventional", "original", "combined"}:
        raise ValueError("Choose one fixed research stage.")
    paths, hashes = args.manifest, args.manifest_sha256
    if (type(paths) is not list or type(hashes) is not list or not 1 <= len(paths) <= 4
            or len(paths) != len(hashes) or len({name.casefold() for name in paths if type(name) is str}) != len(paths)
            or any(type(value) is not str or not _HASH.fullmatch(value) for value in hashes)):
        raise ValueError("Supply 1-4 unique manifests and their matching exact SHA-256 values.")
    output = box._path(args.output)
    parts = box._parts(args.output)
    if parts[0] != "staging" or len(parts) < 2 or "\\" in args.output:
        raise ValueError("Campaign outputs require a new canonical private staging/... folder inside the box.")
    box._parts(args.owner_data)
    if args.detached_research and args.owner_data != "data":
        raise ValueError("Detached research cannot also select an owner-data folder.")
    with box._directory(output.parent, create=True):
        output.mkdir()
    authority = PauseAuthority(box, args.owner_data, args.detached_research)
    written, used, phase = {}, 0, "registration"
    held = ExitStack()

    def publish(name, raw, *, checkpoint=True, failure=False, verify=None):
        nonlocal used
        if used + len(raw) > MAX_OUTPUT - (0 if failure else FAILURE_RESERVE):
            raise ValueError("Campaign aggregate output exceeds8MiB; nothing was truncated.")
        relative = args.output + "/" + name
        path = box._path(relative)
        with box._directory(path.parent, create=True):
            publish_bytes(path, raw, checkpoint=(verify or authority.check) if checkpoint else None)
        if read(box, relative, MAX_OUTPUT) != raw:
            raise ValueError("Campaign output failed exact publication readback.")
        used += len(raw)
        written[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    try:
        stamp = datetime.now(timezone.utc) if now is None else now
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Use an aware campaign registration timestamp.")
        publish("attempt.json", _canonical({"schema": 1, "stage": args.stage,
            "registered_at": stamp.astimezone(timezone.utc).isoformat(), "replay_allowed": False,
            "detached_research": bool(args.detached_research), "paper_operations": 0}), checkpoint=False)
        authority.check()
        guard = guard_for(args.workspace, root=box.root)
        originals, module_hashes = _source_inputs()
        engine = importlib.import_module("ariadne.options_campaign")
        if Path(engine.__file__).resolve() != (ROOT / "ariadne/options_campaign.py").resolve():
            raise ValueError("The fixed campaign engine import changed.")
        candidates = list(engine.CANDIDATE_IDS) if args.candidate is None else args.candidate
        if (type(candidates) is not list or not candidates or len(candidates) != len(set(candidates))
                or any(type(value) is not str or value not in engine.CANDIDATE_IDS for value in candidates)):
            raise ValueError("Choose unique current fixed campaign candidate IDs; no arbitrary rules/code.")
        candidates = [value for value in engine.CANDIDATE_IDS if value in candidates]
        profiles = list(PROFILES) if args.profile is None else args.profile
        if (type(profiles) is not list or not profiles or len(profiles) != len(set(profiles))
                or any(value not in PROFILES for value in profiles) or len(candidates) * len(profiles) > 16):
            raise ValueError("Choose unique fixed liquidity profiles within16 counterfactual ledgers.")
        profiles = [value for value in PROFILES if value in profiles]
        registry_sha = engine.RULE_SHA256
        profile_hashes = {name: engine.profile_rule_sha256(name) for name in profiles}
        manifests, total = [], 0
        for name, digest in zip(paths, hashes):
            authority.check()
            raw, value = load_manifest(guard, name, expected_manifest_sha256=digest)
            if value["symbol"] != "QQQ":
                raise ValueError("This registered campaign supports the QQQ historical research exception only.")
            total += sum(chunk["bytes"] for chunk in value["chunks"])
            manifests.append((name, digest, raw, value))
        if total > MAX_ARCHIVE:
            raise ValueError("All selected archive chunks together exceed512MiB.")
        # Keep the existing reviewed native directory/leaf read guards alive
        # through study and publication. Read handles prevent path replacement
        # where the platform enforces that sharing mode; they are not advertised
        # as a universal write lock. Completion also rehashes every source byte.
        checks, parents = {}, set()
        inputs = [(_root_guard(ROOT), name, digest, len(originals[name])) for name, digest in module_hashes.items()]
        for name, digest, raw, value in manifests:
            inputs.append((guard, name, digest, len(raw)))
            inputs.extend((guard, chunk["path"], chunk["sha256"], chunk["bytes"]) for chunk in value["chunks"])
        for source_guard, name, digest, size in inputs:
            path = source_guard._path(name)
            key = os.path.normcase(str(path.absolute()))
            if key in checks:
                if checks[key][2:4] != (digest, size):
                    raise ValueError("One source path has conflicting registered identities.")
                continue
            parent_key = os.path.normcase(str(path.parent.absolute()))
            if parent_key not in parents:
                directory = held.enter_context(source_guard._directory(path.parent))
                parents.add(parent_key)
            else:
                directory = None
                if os.name != "nt":
                    directory = held.enter_context(source_guard._directory(path.parent))
            source = held.enter_context(os.fdopen(source_guard._open_read(path, directory), "rb"))
            info = os.fstat(source.fileno())
            if info.st_size != size:
                raise ValueError("A registered source changed before iteration.")
            checks[key] = (source_guard, name, digest, size, source,
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
        registration = {"schema": 1, "kind": "pre_iteration_options_campaign_registration", "stage": args.stage,
            "registered_at": stamp.astimezone(timezone.utc).isoformat(), "candidate_ids": candidates,
            "liquidity_profiles": profiles, "registry_sha256": registry_sha,
            "profile_rule_sha256": profile_hashes, "module_sha256": module_hashes,
            "source_snapshots": {SNAPSHOTS[name]: {"sha256": module_hashes[name], "bytes": len(originals[name])} for name in SNAPSHOTS},
            "manifests": [{"path": name, "sha256": digest, "bytes": len(raw), "symbol": value["symbol"],
                "evidence_kind": value["evidence_kind"], "selection": value["selection"],
                "chunks": value["chunks"]} for name, digest, raw, value in manifests],
            "source_bytes_declared": total, "owner_authority": authority.scope(),
            "limits": {"manifests": 4, "source_bytes": MAX_ARCHIVE, "output_bytes": MAX_OUTPUT, "counterfactual_ledgers_per_manifest": 16},
            "research_only": True, "execution_qualified": False, "automatic_adoption": False,
            "source_authenticity_established": False, "paper_operations": 0, "model_calls": 0, "network_calls": 0}
        raw_registration = _canonical(registration)
        publish("registration.json", raw_registration)
        registration_sha = hashlib.sha256(raw_registration).hexdigest()
        for name, target in SNAPSHOTS.items():
            publish(target, originals[name])

        def verify_modules():
            if _source_inputs()[1] != module_hashes or engine.RULE_SHA256 != registry_sha:
                raise ValueError("Campaign source/kernel/registry changed during the registered study.")
            if {name: engine.profile_rule_sha256(name) for name in profiles} != profile_hashes:
                raise ValueError("Campaign profile rules changed during the registered study.")

        def verify_all_source_bytes():
            for source_guard, name, expected, size, source, identity in checks.values():
                authority.check()
                path = source_guard._path(name)
                # Re-open the current name as well as checking the retained leaf;
                # POSIX read descriptors alone do not prevent pathname swaps.
                with source_guard._directory(path.parent) as directory:
                    descriptor = source_guard._open_read(path, directory)
                    try:
                        current = os.fstat(descriptor)
                    finally:
                        os.close(descriptor)
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != identity:
                    raise ValueError("Registered source identity changed before completion.")
                source.seek(0)
                digest, count = hashlib.sha256(), 0
                while True:
                    authority.check()
                    block = source.read(min(1024 * 1024, size - count + 1))
                    if not block:
                        break
                    count += len(block)
                    if count > size:
                        raise ValueError("Registered source grew during final verification.")
                    digest.update(block)
                after = os.fstat(source.fileno())
                if (count != size or digest.hexdigest() != expected
                        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != identity):
                    raise ValueError("Registered source content changed before completion.")

        pending = []
        phase = "source_iteration"
        for index, (name, digest, _, _) in enumerate(manifests, 1):
            stream = ExhaustedStream(iter_archive(guard, name, expected_manifest_sha256=digest,
                                                   checkpoint=authority.check), authority.check)
            if profiles == list(PROFILES):
                result = engine.campaign_profiles(stream, symbol="QQQ", candidates=candidates, checkpoint=authority.check)
                reports = result["profiles"]
            else:
                reports = {profiles[0]: engine.campaign(stream, symbol="QQQ", candidates=candidates,
                    liquidity_profile=profiles[0], checkpoint=authority.check)}
            if not stream.exhausted or set(reports) != set(profiles):
                raise ValueError("Every selected source iterator/profile must finish before publication.")
            for profile in profiles:
                report = reports[profile]
                if (report.get("rule_sha256") != profile_hashes[profile] or report.get("registry_sha256") != registry_sha
                        or report.get("candidate_ids") != candidates or report.get("research_only") is not True
                        or report.get("execution_qualified") is not False or report.get("deployable") is not False):
                    raise ValueError("Campaign engine report lost its exact registered research contract.")
                envelope = {"schema": 1, "stage": args.stage, "manifest_sha256": digest,
                    "registration_sha256": registration_sha, "owner_authority": authority.scope(), "report": report}
                encoded = _canonical(envelope)
                if used + sum(len(item[1]) for item in pending) + len(encoded) > MAX_OUTPUT - FAILURE_RESERVE:
                    raise ValueError("Campaign aggregate reports exceed8MiB; no truncated report was published.")
                pending.append((f"archive-{index:02d}--{profile}.json", encoded))

        phase = "final_source_verification"
        verify_all_source_bytes()
        verify_modules()

        def final_checkpoint():
            authority.check()
            verify_modules()
            for name, _, raw, _ in manifests:
                if read(guard, name, MAX_MANIFEST) != raw:
                    raise ValueError("Manifest changed during report publication.")
            for source_guard, name, _, _, source, identity in checks.values():
                info = os.fstat(source.fileno())
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != identity:
                    raise ValueError("Studied source identity changed during report publication.")

        def verify_artifacts():
            for name, item in written.items():
                raw = read(box, args.output + "/" + name, MAX_OUTPUT)
                if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("Published research artifacts changed before completion.")

        def completion_checkpoint():
            final_checkpoint()
            verify_all_source_bytes()
            verify_artifacts()
            authority.check()

        phase = "publication"
        for name, encoded in pending:
            final_checkpoint()
            publish(name, encoded, verify=final_checkpoint)
        index = {"schema": 1, "stage": args.stage, "registration_sha256": registration_sha,
            "reports": [{"path": name, "sha256": written[name]["sha256"], "bytes": written[name]["bytes"]} for name, _ in pending],
            "candidate_ids": candidates, "liquidity_profiles": profiles,
            "ranking_scope": "Separate same-window/profile exploratory comparisons only; no pooled capital, best-overall claim, untouched holdout or automatic adoption.",
            "owner_authority": authority.scope(), "paper_operations": 0, "model_calls": 0, "network_calls": 0}
        final_checkpoint()
        publish("index.json", _canonical(index), verify=final_checkpoint)
        final_checkpoint()
        verify_artifacts()
        completed = {"schema": 1, "status": "completed", "stage": args.stage,
            "registration_sha256": registration_sha, "artifacts": written.copy(), "output_bytes_before_receipt": used,
            "owner_authority": authority.scope(), "source_streams_exhausted": True,
            "source_kernel_rules_rechecked": True, "automatic_adoption": False,
            "paper_operations": 0, "model_calls": 0, "network_calls": 0}
        final_checkpoint()
        publish("completed.json", _canonical(completed), verify=completion_checkpoint)
        return completed
    except BaseException as error:
        try:
            failed = {"schema": 1, "status": "failed", "stage": args.stage, "phase": phase,
                "error_type": type(error).__name__, "reason": str(error)[:1000],
                "partial_artifacts": written.copy(), "replay_allowed": False, "adopted": False,
                "owner_authority": authority.scope(), "owner_settings_written": False, "paper_operations": 0}
            publish("failed.json", _canonical(failed), checkpoint=False, failure=True)
        except (ValueError, OSError, UnicodeError, OverflowError, RecursionError):
            pass
        raise
    finally:
        try:
            authority.close()
        finally:
            held.close()


def install_offline_guards():
    """Fixed CLI process only; no OS-sandbox claim for arbitrary native code."""
    os.environ["ARIADNE_OFFLINE"] = "1"
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    def audit(event, arguments):
        if (event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork", "os.forkpty", "pty.spawn"}
                or event.startswith("socket.")):
            raise PermissionError("Offline campaign forbids network, model transport and native process actions.")
    sys.addaudithook(audit)


def main(argv=None):
    install_offline_guards()
    args = parser().parse_args(argv)
    try:
        receipt = run(args)
    except (ValueError, OSError, UnicodeError, OverflowError, RecursionError, sqlite3.Error) as error:
        print("Options campaign stopped: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps({"status": receipt["status"], "stage": receipt["stage"],
        "output": args.output, "source_streams_exhausted": True, "paper_operations": 0,
        "scope": "Private historical research only; no strategy or funds adopted."}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
