"""Prepare one fresh Windows box; leave no provisioner model service running.

The PowerShell wrapper prepares the virtual environment and pinned core wheels.
This helper provisions only the factory model and stops only processes created
by its own provisioner callback. It never starts the application or microphone.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from scripts import provision

MODEL = "qwen3.5:9b"
MODEL_PORT = 11434
OWNER_MARKERS = ("data/server.token", "data/ariadne.sqlite3", "data/host/status.json")
ASSETS = (".venv/Scripts/python.exe", "runtime/ollama/ollama.exe",
          "data/models/manifests/registry.ollama.ai/library/qwen3.5/9b")


def _platform_ok():
    return os.name == "nt"


def _python_ok():
    return (sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
            and sysconfig.get_platform() == "win-amd64")


def _module_available(name):
    return importlib.util.find_spec(name) is not None


def inventory():
    """Read paths and package metadata only; never initialize application state."""
    root = _allowed_path(ROOT)
    present = {name: _allowed_path(root / name).is_file() for name in ASSETS}
    markers = [name for name in OWNER_MARKERS if _allowed_path(root / name).exists()]
    missing_packages = [name for name in ("waitress", "pypdf") if not _module_available(name)]
    prerequisites = ([] if present[ASSETS[0]] else [ASSETS[0]]) + missing_packages
    supported = _platform_ok() and _python_ok()
    return {"schema": 1, "mode": "check", "platform_supported": _platform_ok(),
            "python_supported": _python_ok(), "path_allowed": True,
            "status": "unsupported_platform_or_python" if not supported else
                      "existing_installation" if markers else
                      "prerequisites_missing" if prerequisites else "ready_for_setup",
            "model": MODEL, "model_port": MODEL_PORT,
            "missing": [name for name, exists in present.items() if not exists],
            "existing": [name for name, exists in present.items() if exists] + markers,
            "prerequisites_missing": prerequisites,
            "private_installation_present": bool(markers),
            "owner_markers": markers,
            "qualification": "File/package presence only; no model, port, database, or offline-operation test.",
            "downloads": False, "services_started": False}


def _validate_environment():
    selected = os.environ.get("ARIADNE_MODEL_PORT")
    if selected is not None and selected != str(MODEL_PORT):
        raise ValueError("First setup uses factory model port 11434; unset ARIADNE_MODEL_PORT.")
    if os.environ.get("ARIADNE_OLLAMA_URL"):
        raise ValueError("First setup provisions its own model runtime; unset ARIADNE_OLLAMA_URL.")
    if os.environ.get("ARIADNE_DATA_DIR") and _allowed_path(os.environ["ARIADNE_DATA_DIR"]) != _allowed_path(ROOT / "data"):
        raise ValueError("First setup uses this folder's data directory; unset ARIADNE_DATA_DIR.")


def _listener_present():
    """Probe the numeric local port only in explicitly requested setup mode."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(.5)
        return connection.connect_ex(("127.0.0.1", MODEL_PORT)) == 0


def _cleanup(created):
    """Use only captured native process objects, never PID files or process names."""
    errors = []
    seen = set()
    for process in reversed(created):
        if id(process) in seen:
            continue
        seen.add(id(process))
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.poll() is None:
                raise OSError("Newly created model process did not exit.")
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(type(error).__name__)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only inventory; no installs, network probes or processes.")
    args = parser.parse_args(argv)
    try:
        state = inventory()
        if args.check:
            print(json.dumps(state, indent=2))
            return 0 if state["platform_supported"] and state["python_supported"] else 1
        if not state["platform_supported"] or not state["python_supported"]:
            raise ValueError("Setup requires native Windows x64 and Python 3.13 x64.")
        if state["private_installation_present"]:
            raise ValueError("Existing private installation detected; setup changed nothing. Use Start Ariadne.cmd or a fresh folder.")
        if state["prerequisites_missing"]:
            raise ValueError("Prepare the project .venv and pinned server/document packages with Setup Ariadne.cmd first.")
        _validate_environment()
        if _listener_present():
            raise ValueError("Model port 11434 is already in use. Stop its owner yourself or use a fresh setup after it exits; no process was changed.")
        created = []
        failed = None
        try:
            # No existing model listener is ever adopted, including a listener
            # appearing after the preflight. The default provisioner CLI keeps
            # its existing behavior; this lifecycle-managed invocation does not.
            provision.main(["--model", MODEL], on_process=created.append, allow_owned=False)
            if len(created) != 1:
                raise ValueError("Setup did not capture exactly one new model process; no ready installation is claimed.")
        except (Exception, SystemExit, KeyboardInterrupt) as error:
            failed = error
        finally:
            cleanup_errors = _cleanup(created)
        if cleanup_errors:
            raise ValueError("Provisioner model cleanup could not be confirmed. Inspect the local setup/model logs before continuing; Ariadne was not started.")
        if failed is not None:
            if isinstance(failed, KeyboardInterrupt):
                raise ValueError("Setup was interrupted. Its newly created model process was stopped; inspect cached partial files before retrying.") from failed
            detail = " ".join(str(failed).split())[:500]
            raise ValueError("Model provisioning failed (" + type(failed).__name__ + "): " + detail +
                             ". Its newly created process was stopped; inspect data/model-pull.log and data/ollama.log.") from failed
        print("Factory qwen3.5:9b model verified. The provisioner model process has stopped.")
        print("Next: double-click Start Ariadne.cmd; pair using your private local data/server.token.")
        print("No microphone, scheduled task or operating-system startup entry was enabled.")
        return 0
    except (OSError, ValueError) as error:
        print("Ariadne setup: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
