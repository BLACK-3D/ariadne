"""Explicit owner CLI: provision pinned packages, discover, then register tools.

Discovery writes review evidence only. Registration requires its exact file hash
and an explicit read-only/approval policy. The model has no CLI capability.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ariadne.config import Settings, _allowed_path
from ariadne.mcp_tools import MCPTools, read_json, write_json, validate_tool, validate_catalog, canonical


def provision(install: bool):
    root = _allowed_path(ROOT)
    environment = _allowed_path(root / ".venv")
    python = _allowed_path(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if not python.is_file():
        raise ValueError("Create Ariadne's project .venv before optional MCP provisioning.")
    paths = [_allowed_path(root / item) for item in ("runtime/downloads/mcp", "runtime/tmp/mcp", "runtime/pip-cache")]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    wheelhouse, temporary, cache = paths
    lock = _allowed_path(root / "requirements-mcp.txt")
    if not lock.is_file() or lock.stat().st_nlink != 1:
        raise ValueError("MCP hash lock must be an ordinary, unlinked project file.")
    command = [str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check"]
    if install:
        command += ["install", "--no-index", "--find-links", str(wheelhouse)]
    else:
        command += ["download", "--index-url", "https://pypi.org/simple", "--dest", str(wheelhouse), "--retries", "0"]
    command += ["--require-hashes", "--only-binary=:all:", "--cache-dir", str(cache), "-r", str(lock)]
    env = os.environ.copy()
    env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    completed = subprocess.run(command, cwd=root, env=env, shell=False, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise ValueError("Pinned MCP provisioning did not complete. Existing application configuration was not changed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Approved local Ariadne state directory.")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("download", help="Download only hash-locked official wheels; no installation.")
    commands.add_parser("install", help="Install the retained wheelhouse offline into project .venv.")
    commands.add_parser("status", help="Show local readiness without network discovery.")
    discover = commands.add_parser("discover", help="Save bounded metadata for owner review; activate nothing.")
    discover.add_argument("--server", required=True)
    register = commands.add_parser("register", help="Register one tool from reviewed local discovery evidence.")
    register.add_argument("--server", required=True)
    register.add_argument("--tool", required=True)
    register.add_argument("--snapshot-sha256", required=True)
    policy = register.add_mutually_exclusive_group(required=True)
    policy.add_argument("--read-only", action="store_true")
    policy.add_argument("--approval", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action in {"download", "install"}:
            provision(args.action == "install")
            return 0
        data_dir = _allowed_path(args.data_dir)
        data_dir.relative_to(_allowed_path(ROOT))
        registry = MCPTools(Settings(data_dir=data_dir))
        if args.action == "status":
            print(canonical(registry.status()).decode())
            return 0
        with registry.mutation():
            return _owner_action(registry, args, data_dir)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        print("MCP owner action rejected or failed. Check the approved configuration, pinned packages, snapshot hash, and documented limits.", file=sys.stderr)
        return 1


def _owner_action(registry, args, data_dir):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", args.server):
        raise ValueError("Invalid server identifier.")
    path = data_dir / "mcp-discovery" / (args.server + ".json")
    if args.action == "discover":
        snapshot = registry.discover(args.server)
        raw = write_json(path, snapshot)
        print(f"Saved {path}\nSHA256 {hashlib.sha256(raw).hexdigest()}")
        print("Review the file, then register each selected tool with its explicit owner policy.")
        return 0
    snapshot, raw = read_json(path)
    if not re.fullmatch(r"[0-9a-f]{64}", args.snapshot_sha256) or hashlib.sha256(raw).hexdigest() != args.snapshot_sha256:
        raise ValueError("Discovery snapshot hash differs from the owner's reviewed hash.")
    if not isinstance(snapshot, dict) or set(snapshot) != {"version", "server", "identity", "tools"} or snapshot["version"] != 1:
        raise ValueError("Discovery snapshot is invalid.")
    config = registry.config()
    server = next((s for s in config["servers"] if s["id"] == args.server), None)
    if server is None or canonical(snapshot["server"]) != canonical({k: v for k, v in server.items() if k not in {"tools", "identity"}}):
        raise ValueError("Server configuration changed since discovery.")
    metadata = next((t for t in snapshot["tools"] if isinstance(t, dict) and t.get("name") == args.tool), None)
    validate_tool(metadata)
    if server.get("tools") and canonical(server.get("identity")) != canonical(snapshot["identity"]):
        raise ValueError("Server identity changed; remove old registrations before accepting a new identity.")
    server["identity"] = snapshot["identity"]
    server["tools"] = [t for t in server.get("tools", []) if t["metadata"]["name"] != args.tool]
    server["tools"].append({"metadata": metadata, "read_only": args.read_only})
    # Validate complete proposed configuration before committing it.
    from ariadne.mcp_tools import _server, MAX_SELECTED
    _server(server)
    if sum(len(s.get("tools", [])) for s in config["servers"]) > MAX_SELECTED:
        raise ValueError("Selected tool count exceeds its limit.")
    validate_catalog(config)
    write_json(registry.path, config)
    print(f"Registered {args.server}/{args.tool} with {'read-only' if args.read_only else 'approval'} policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
