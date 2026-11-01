"""Bounded child transport for the selected F EOD research source.

The offline parent never opens a remote connection. No credentials, general
URLs, broker calls or executable provider content cross this boundary.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import time

from .config import _allowed_path
from .handoff import HandoffConflict, _identifier, _load

MAX_OUTPUT = 12 * 1024 * 1024


def run_collector(workflow, cfg, source_cfg, internet, brief, request_id, required):
    return _run_collector(workflow, cfg, source_cfg, internet, brief, request_id, required, profile="F")


def _run_collector(workflow, cfg, source_cfg, internet, brief, request_id, required, *, profile):
    if profile not in ("F", "KO"):
        raise ValueError("Use one explicitly registered source profile.")
    owner = workflow.owner
    _identifier(request_id)
    if profile == "F":
        from .options_eod_data import required_ids
    else:
        from .options_root_eod_data import required_ids
    required = required_ids(required)
    with owner.database() as db:
        if workflow._policy(db) != (cfg, source_cfg, internet, brief):
            raise HandoffConflict("Ford source authority changed before collection.")
        account = owner._account(db)
        epoch = owner._epoch(db, cfg, brief)
        if (not account or account["state"].get("schema") not in ((3,4) if profile == "F" else (5,))
                or profile == "KO" and (account["state"].get("symbol") != "KO" or source_cfg.get("symbol") != "KO")
                or account["policy"] != epoch):
            raise HandoffConflict("Ford account or permission epoch changed.")
    script = _allowed_path(Path(__file__).resolve().parents[1] / "scripts/options_eod_feed.py")
    folder = _allowed_path(owner.registry.settings.data_dir / ("options-eod-collector" if profile == "F" else "options-ko-collector"))
    with owner.registry._directory(folder, create=True):
        retained = list(folder.iterdir())
        infos = [path.lstat() for path in retained]
        if (len(infos) > 8 or sum(item.st_size for item in infos) > 32 * 1024 * 1024
                or any(not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or getattr(item, "st_file_attributes", 0) & 0x400 for item in infos)):
            raise ValueError("Ford collector scratch requires reviewed maintenance.")
        output = folder / (request_id + ".json")
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        owned = os.fstat(descriptor)
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH") if key in os.environ}
        env.update(ARIADNE_OFFLINE="0", ARIADNE_OPTIONS_EOD_COLLECTOR="1", TEMP=str(folder), TMP=str(folder), USERPROFILE=str(folder), PYTHONDONTWRITEBYTECODE="1")
        if profile == "KO":
            env.pop("ARIADNE_OPTIONS_EOD_COLLECTOR")
            env["ARIADNE_OPTIONS_ROOT_EOD_COLLECTOR"] = "1"
        command = [sys.executable, "-I", "-B", str(script), "worker", "--data-dir", str(owner.registry.settings.data_dir),
                   "--workspace", str(owner.registry.root), "--options-revision", str(cfg["revision"]),
                   "--eod-revision", str(source_cfg["revision"]), "--internet-revision", str(internet["revision"]),
                   "--brief-revision", str(brief["revision"]), "--account-id", account["id"],
                   "--pause-rowid", str(epoch["global_pause_rowid"] if epoch["global_pause_rowid"] is not None else -1)]
        if profile == "KO":
            command.extend(["--profile", "KO"])
        for identifier in required:
            command.extend(["--required-contract", identifier])
        process = None
        try:
            with os.fdopen(descriptor, "wb") as target:
                process = subprocess.Popen(command, env=env, cwd=script.parent.parent, stdout=target,
                                           stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                with workflow.lock:
                    workflow.processes.add(process)
                deadline = time.monotonic() + 35
                while process.poll() is None:
                    if owner.stopped.is_set():
                        raise ValueError("Ford collector stopped with the owner host.")
                    with owner.database() as db:
                        if workflow._policy(db) != (cfg, source_cfg, internet, brief) or owner._epoch(db, cfg, brief) != epoch:
                            raise HandoffConflict("Ford source permissions changed or were revoked.")
                    if time.monotonic() > deadline or output.stat().st_size > MAX_OUTPUT:
                        raise ValueError("Ford collector exceeded its time/output bound.")
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
            if process.returncode:
                raise ValueError("Ford collector stopped without a verified response.")
            with owner.registry._directory(folder) as directory:
                with os.fdopen(owner.registry._open_read(output, directory), "rb") as source:
                    raw = source.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise ValueError("Ford collector response exceeds its bound.")
            result = _load(raw)
            if type(result) is not dict:
                raise ValueError("Ford collector response must be a bounded object.")
            return result
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process is not None:
                with workflow.lock:
                    workflow.processes.discard(process)
            # Unaccepted transport scratch only; never a saved report or retry.
            try:
                info = output.lstat()
                if ((info.st_dev, info.st_ino) == (owned.st_dev, owned.st_ino) and stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1 and not getattr(info, "st_file_attributes", 0) & 0x400):
                    output.unlink()
            except FileNotFoundError:
                pass
