"""Export committed, reviewed Git source; never read owner worktree overrides.

A normal local Git checkout is required. Commit intended source changes first.
The exact HEAD tree is packed, with private paths and unsupported modes rejected.
This is a developer source export, not the provisioned offline appliance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.atomic import commit_staged
from ariadne.config import _allowed_path
from scripts import refresh_offline_source as source_policy

MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 10000, 64 * 1024**2, 512 * 1024**2
PRIVATE_NAMES = frozenset({"credentials.json", "connectors.json", "mcp.json", "briefing.json", "app-config.json",
    "server.token", "id_rsa", "id_ed25519", "authorized_keys", ".npmrc", ".pypirc", ".netrc"})
# Extra owner overrides/key containers remain narrower than the consumer policy.
PRIVATE_SUFFIXES = (".p12", ".pfx", ".db-wal", ".db-shm", ".sqlite3-wal", ".sqlite3-shm",
                    ".pt", ".pth", ".tar", ".gz", ".pkl", ".pickle")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)?\Z")


def _environment():
    # No remote/lazy-fetch credentials, replace refs, global filters or caller
    # GIT_DIR overrides are needed to inspect an existing local commit.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git(root, *arguments):
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments], env=_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("A trusted local Git checkout and Git executable are required.") from error
    if result.returncode:
        raise ValueError("Cannot read committed local source; no source export was published.")
    return result.stdout


def _checkout(root):
    root = _allowed_path(root)
    metadata = _allowed_path(root / ".git")
    if not metadata.is_dir():
        raise ValueError("Source export requires a normal Git checkout; unpacked trees and external worktree metadata are not trusted inventories.")
    if (metadata / "commondir").exists() or (metadata / "objects/info/alternates").exists():
        raise ValueError("Shared/external Git object stores require a separately audited source inventory.")
    actual = _git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    if _allowed_path(actual) != root:
        raise ValueError("Source checkout root differs from the requested box.")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise ValueError("Use one exact local commit identity.")
    return root, head


def _member(name):
    # Keep the source ZIP producer inside the unchanged refresh consumer's exact
    # canonical-name/admission/privacy boundary; do not maintain a drifting copy.
    source_policy.member_name(name)
    if not source_policy._source_member(name) or source_policy._private(name):
        raise ValueError("Tracked private or unsupported source member blocks export: " + name)
    parts = name.casefold().split("/")
    filename = parts[-1]
    if (any(part in {"data", "runtime", "staging", "dist", "node_modules"} for part in parts)
            or filename in PRIVATE_NAMES or filename.startswith("service-account")
            or filename.endswith(PRIVATE_SUFFIXES)
            or filename.startswith(".env") and not filename.endswith(".example")):
        raise ValueError("Tracked owner override or private state blocks source export: " + name)
    return name


def _tree(root, head):
    raw = _git(root, "ls-tree", "-r", "-z", "--full-tree", head)
    if len(raw) > 8 * 1024**2:
        raise ValueError("Source inventory is too large.")
    result, seen = [], set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, encoded = entry.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        name = _member(encoded.decode("utf-8"))
        alias = unicodedata.normalize("NFC", name).casefold()
        if alias in seen or mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("Source inventory contains aliases, links, submodules or unsupported modes.")
        seen.add(alias)
        result.append((name, oid, mode))
        if len(result) > MAX_FILES:
            raise ValueError("Source file-count limit exceeded.")
    if not result:
        raise ValueError("The committed source inventory is empty.")
    return sorted(result)


def _blobs(root, entries):
    process = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"], env=_environment(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    total = 0
    try:
        for name, oid, mode in entries:
            process.stdin.write((oid + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline(256).split()
            if len(header) != 3 or header[0].decode("ascii") != oid or header[1] != b"blob":
                raise ValueError("Committed source object is missing or changed.")
            size = int(header[2])
            total += size
            if not 0 <= size <= MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Committed source payload exceeds its fixed size limits.")
            raw = process.stdout.read(size)
            if len(raw) != size or process.stdout.read(1) != b"\n":
                raise ValueError("Committed source blob was truncated.")
            if raw.startswith((b"SQLite format 3\0", b"PK\x03\x04", b"7z\xbc\xaf\x27\x1c", b"MZ", b"version https://git-lfs.github.com/spec/v1")):
                raise ValueError("Database/archive/runtime/LFS payload blocks source export: " + name)
            yield name, raw, mode
        process.stdin.close()
        if process.wait(timeout=30):
            raise ValueError("Local Git source reader failed.")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def export(destination=None, *, root=ROOT):
    root, head = _checkout(root)
    entries = _tree(root, head)
    payloads = list(_blobs(root, entries))
    by_name = {name: raw for name, raw, _ in payloads}
    init = by_name.get("ariadne/__init__.py", b"").decode("utf-8")
    matches = re.findall(r'^__version__\s*=\s*[\"\x27]([^\"\x27]+)[\"\x27]\s*$', init, re.M)
    if len(matches) != 1 or not VERSION.fullmatch(matches[0]):
        raise ValueError("Committed application version is missing or ambiguous.")
    version = matches[0]
    project = tomllib.loads(by_name.get("pyproject.toml", b"").decode("utf-8"))
    if project.get("project", {}).get("version") != version:
        raise ValueError("Committed application and package versions differ.")
    requested = Path(destination) if destination is not None else Path("dist") / f"ariadne-box-{version}-source.zip"
    destination = _allowed_path(requested if requested.is_absolute() else root / requested)
    if not destination.is_relative_to(root) or destination == root or destination.suffix.lower() != ".zip":
        raise ValueError("Source output must be a ZIP inside this source checkout.")
    if destination.exists():
        raise FileExistsError("Source exports are exclusive; choose a new destination.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".partial", dir=destination.parent)
    stage = Path(temporary)
    manifest = {}
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw, mode in payloads:
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (int(mode, 8) & 0o777) << 16
                    archive.writestr(info, raw)
                    manifest[name] = hashlib.sha256(raw).hexdigest()
                # Preserve the strict source-refresh manifest schema.
                archive.writestr("SOURCE-MANIFEST.json", json.dumps({"version": version, "sha256": manifest}, sort_keys=True, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        if _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip() != head:
            raise ValueError("Source HEAD changed before publication.")
        commit_staged(stage, destination)
    finally:
        if stage.exists():
            stage.unlink()
    return {"path": str(destination), "commit": head, "files": len(manifest), "bytes": destination.stat().st_size,
            "version": version, "scope": "Committed Git source only; untracked and modified working-tree bytes excluded."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = export(args.output, root=ROOT)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
