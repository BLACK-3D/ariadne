"""Export a complete relocatable offline appliance, excluding owner state.

The plan inventories sizes without hashing every multi-GB model. Export streams
each approved regular file once into a ZIP64 stored archive and hashes its bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.box_profiles import make_template

SOURCE_DIRS = ("ariadne", "scripts", "tests")
SOURCE_FILES = {"LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "Dockerfile", "compose.yaml",
    "compose.provision.yaml", "Start Ariadne.cmd", "start.ps1", "start.sh", ".gitignore", ".dockerignore"}
CONFIG_FILES = {"engineering-assets.json", "knowledge-model-assets.json", "knowledge-wheels-manifest.json", "ocr-wheels-manifest.json",
    "production-assets.json", "production-blender-assets.json", "mcp.example.json", "connectors.example.json", "box-profiles.json"}
DOCS = {"ARCHITECTURE.md", "ANALYTICS_0_3.md", "OCR_0_3.md", "MCP_0_3.md", "KNOWLEDGE_0_4.md", "MEDIA_0_4.md", "PRODUCTION_0_4.md", "PORTABLE_0_4.md", "DELIVERY_0_4.md",
    "DIFFERENTIAL_REVIEW_0_4_SHARED.md", "REVIEW_0_4_EXTENSIONS.md", "REVIEW_PORTABLE_PYTHON_0_4.md", "SURVIVAL_0_5.md", "SURVIVAL-SOURCES.md", "WORKFLOWS_0_5.md", "AGENTS_0_6.md", "DAILY-BRIEFING.md", "WAKE_VOICE.md", "INTERNET.md", "COMMAND_CENTER_0_8.md", "OFFLINE_ROUTINES_0_9.md", "BUILD_FROM_SOURCE.md", "GITHUB_SOURCE.md"}
RUNTIME_DIRS = ("runtime/python", "runtime/ollama", "runtime/whisper", "runtime/ocr/models", "runtime/ocr/wheels", "runtime/ocr/notices",
    "runtime/media/python", "runtime/media/flux-python", "runtime/media/kokoro", "runtime/media/sherpa", "runtime/media/silero",
    "runtime/media/diarization", "runtime/media/flux", "runtime/media/licenses", "runtime/media/wheels", "runtime/media/flux-wheels",
    "runtime/knowledge/python", "runtime/knowledge/model", "runtime/knowledge/docling-models", "runtime/knowledge/kiwix",
    "runtime/knowledge/library", "runtime/knowledge/notices", "runtime/knowledge/wheels", "runtime/wheels", "runtime/downloads/mcp",
    "runtime/production/python", "runtime/production/blender-python", "runtime/production/blender-wheels", "runtime/production/wheels",
    "runtime/production/7zip", "runtime/production/lessmsi", "runtime/production/libreoffice", "runtime/production/freecad",
    "runtime/production/kicad", "runtime/production/qgis", "runtime/production/ffmpeg", "runtime/automation/python",
    "runtime/automation/wheels", "runtime/automation/node-red", "runtime/automation/browsers", "data/models", "data/voice")
EXCLUDED = ["All owner databases, pairing/API tokens, credentials, MCP/HTTP registrations and automation owner applications",
    "Missions, events, memories, imported documents, semantic indexes, schedules, source-folder registrations and workspace artifacts",
    "Backups, logs, screenshots, browser user profiles, private ERP configuration, caches, temporary files and the development virtualenv",
    "Redundant downloaded native installers/archives; installed engines, model assets, wheel caches and license notices are retained"]
RUNTIME_MANIFESTS = {"runtime/media": {"wheel-manifest.json", "flux-wheel-manifest.json"},
    "runtime/knowledge": {"kiwix-manifest.json", "layout-manifest.json", "model-manifest.json"},
    "runtime/ocr": {"wheels-manifest.json"},
    "runtime/production": {"installed.json", "wheels-lock.json", "blender-wheels-lock.json", "ffmpeg-source.json"},
    "runtime/automation": {"node-lock.json", "playwright-lock.json"}}


def _regular(path):
    path = _allowed_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Export accepts regular files with one link only: " + path.name)
    return info


def _walk(root, relative):
    base = _allowed_path(root / relative)
    if not base.exists():
        return
    if not base.is_relative_to(root):
        raise ValueError("Export input escapes the box.")
    pending = [base]
    while pending:
        directory = _allowed_path(pending.pop())
        with os.scandir(directory) as iterator:
            for entry in iterator:
                path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Export refuses symbolic links/reparse points: " + str(path.relative_to(root)))
                if entry.name in {"__pycache__", ".git", ".links", ".cache"} or entry.name.endswith((".pyc", ".pyo", ".part")) or ".part-" in entry.name:
                    continue
                rel = path.relative_to(root).as_posix()
                if rel.startswith("runtime/automation/node-red/") and not (rel.startswith("runtime/automation/node-red/node_modules/")
                        or rel == "runtime/automation/node-red/node_modules" or rel in {"runtime/automation/node-red/package.json", "runtime/automation/node-red/package-lock.json"}):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if relative != "data/voice":
                        pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    if os.name == "nt" and info.st_nlink == 0:
                        info = path.stat(follow_symlinks=False)
                    if info.st_nlink != 1:
                        raise ValueError("Export refuses hard-linked files: " + rel)
                    if relative == "data/voice" and entry.name != "ggml-base.en.bin":
                        continue
                    yield rel, path, info.st_size
                else:
                    raise ValueError("Export refuses special filesystem entries.")


def inventory(root=ROOT):
    root = _allowed_path(root)
    files = {}
    for relative in SOURCE_DIRS + RUNTIME_DIRS:
        for name, path, size in _walk(root, relative):
            files[name] = (path, size)
    automation = _allowed_path(root / "runtime/automation")
    if automation.exists():
        for candidate in automation.iterdir():
            if candidate.name.startswith("node-v") and candidate.is_dir():
                for name, path, size in _walk(root, candidate.relative_to(root).as_posix()):
                    files[name] = (path, size)
    for path in root.iterdir():
        if path.name in SOURCE_FILES or (path.name.startswith("requirements-") and path.suffix == ".txt"):
            info = _regular(path)
            files[path.name] = (path, info.st_size)
    for path in (root / "docs").glob("*"):
        if path.name in DOCS:
            info = _regular(path)
            files["docs/" + path.name] = (path, info.st_size)
    config = _allowed_path(root / "config")
    if config.exists():
        for path in config.iterdir():
            if path.is_file() and path.name in CONFIG_FILES:
                info = _regular(path)
                files["config/" + path.name] = (path, info.st_size)
    for relative in ("config/erpnext/compose.yaml", "config/erpnext/images.json", "config/erpnext/LICENSE"):
        path = _allowed_path(root / relative)
        if path.exists():
            info = _regular(path)
            files[relative] = (path, info.st_size)
    # Only root-level runtime provenance, never runtime tmp/cache subtrees.
    for relative in ("runtime/media", "runtime/knowledge", "runtime/ocr", "runtime/production", "runtime/automation"):
        directory = _allowed_path(root / relative)
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file() and path.name in RUNTIME_MANIFESTS[relative]:
                    info = _regular(path)
                    files[path.relative_to(root).as_posix()] = (path, info.st_size)
    return sorted((name, *item) for name, item in files.items())


def write_template(root=ROOT):
    root = _allowed_path(root)
    value = make_template(root)
    destination = _allowed_path(root / "config/box-profiles.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if not destination.exists() or destination.read_bytes() != encoded:
        if destination.exists():
            _regular(destination)
        temporary = _allowed_path(destination.with_suffix(".json.new"))
        with temporary.open("xb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    return value


def plan(root=ROOT):
    root = _allowed_path(root)
    profiles = write_template(root)
    files = inventory(root)
    missing = [name for name in ("runtime/python/python.exe", "runtime/python/PORTABLE-MANIFEST.json", "runtime/ollama/ollama.exe")
        if not (root / name).is_file()]
    portable = root / "runtime/python/PORTABLE-MANIFEST.json"
    if portable.is_file():
        qualified = json.loads(portable.read_text(encoding="utf-8"))
        hashes = qualified.get("sha256") if type(qualified) is dict else None
        if type(hashes) is not dict or any(type(name) is not str or type(digest) is not str for name, digest in hashes.items()):
            raise ValueError("Portable Python qualification manifest is invalid.")
        names = {name for name, _, _ in files}
        missing += ["runtime/python/" + name for name in hashes if "runtime/python/" + name not in names]
    return {"format": "Ariadne offline appliance ZIP64/ZIP_STORED", "schema": 1, "files": len(files),
        "payload_bytes": sum(size for _, _, size in files), "profile_names": sorted(profiles["profiles"]),
        "default_model": profiles["default_model"], "excluded_categories": EXCLUDED,
        "missing_required": missing, "inventory": [{"path": name, "bytes": size} for name, _, size in files]}


def export(destination, root=ROOT):
    root = _allowed_path(root)
    destination = _allowed_path(destination)
    if not destination.is_relative_to(root) or destination.suffix.lower() != ".zip":
        raise ValueError("Export output must be a ZIP inside the box workspace.")
    if destination.exists():
        raise FileExistsError("Export never overwrites an existing archive.")
    sidecars = (destination.with_suffix(".zip.sha256"), destination.with_suffix(".zip.receipt.json"))
    for sidecar in sidecars:
        _allowed_path(sidecar)
        if sidecar.exists():
            raise FileExistsError("Export never overwrites a checksum or receipt sidecar.")
    report = plan(root)
    if report["missing_required"]:
        raise ValueError("Portable runtime is incomplete: " + ", ".join(report["missing_required"]))
    # Reuse the validated inventory; re-read each file through guarded handles below.
    files = [(item["path"], root / item["path"], item["bytes"]) for item in report["inventory"]]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _allowed_path(destination.with_suffix(".zip.partial"))
    if temporary.exists():
        raise FileExistsError("A prior partial export exists; inspect it before retrying.")
    entries = []
    from ariadne.tools import ToolRegistry
    guard = object.__new__(ToolRegistry)
    try:
        with temporary.open("xb") as outer, zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            parent_guard = None
            parent_path = None
            parent_directory = None
            for index, (name, source, expected_size) in enumerate(files, 1):
                digest = hashlib.sha256()
                written = 0
                if source.parent != parent_path:
                    if parent_guard:
                        parent_guard.__exit__(None, None, None)
                    parent_guard = guard._directory(source.parent)
                    parent_directory = parent_guard.__enter__()
                    parent_path = source.parent
                try:
                    with os.fdopen(guard._open_read(source, parent_directory), "rb") as stream:
                        before = os.fstat(stream.fileno())
                        if before.st_size != expected_size:
                            raise ValueError("Export input changed after inventory: " + name)
                        info = zipfile.ZipInfo(name)
                        info.compress_type = zipfile.ZIP_STORED
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                        with archive.open(info, "w", force_zip64=True) as target:
                            while True:
                                chunk = stream.read(1024 * 1024)
                                if not chunk:
                                    break
                                target.write(chunk)
                                digest.update(chunk)
                                written += len(chunk)
                        after = os.fstat(stream.fileno())
                        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or written != expected_size:
                            raise ValueError("Export input changed while streaming: " + name)
                except BaseException:
                    if parent_guard:
                        parent_guard.__exit__(*sys.exc_info())
                        parent_guard = None
                    raise
                entries.append({"path": name, "bytes": written, "sha256": digest.hexdigest()})
                if index % 1000 == 0:
                    print(f"Streamed {index}/{len(files)} files", flush=True)
            if parent_guard:
                parent_guard.__exit__(None, None, None)
            manifest = {"schema": 1, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "format": "ZIP64/ZIP_STORED", "payload_bytes": sum(item["bytes"] for item in entries),
                "default_model": "qwen3.5:9b", "private_state_included": False, "excluded_categories": EXCLUDED, "files": entries}
            archive.writestr("BOX-MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            archive.close()
            outer.flush()
            os.fsync(outer.fileno())
        with guard._directory(destination.parent):
            from ariadne.atomic import commit_staged
            commit_staged(temporary, destination)
    except BaseException:
        # Leave the explicitly named partial artifact for inspection/recovery.
        raise
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt = {"path": destination.name, "bytes": destination.stat().st_size, "sha256": digest,
        "files": len(entries) + 1, "payload_bytes": manifest["payload_bytes"], "manifest": "BOX-MANIFEST.json"}
    for sidecar, content, encoding in ((sidecars[0], digest + "  " + destination.name + "\n", "ascii"),
            (sidecars[1], json.dumps(receipt, indent=2), "utf-8")):
        with guard._directory(destination.parent):
            path = _allowed_path(sidecar)
            from ariadne.atomic import publish_bytes
            publish_bytes(path, content.encode(encoding))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.plan) == bool(args.output):
        parser.error("Choose --plan or --output PATH.")
    if args.plan:
        result = plan()
        target = ROOT / "dist/offline-export-plan.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "inventory"}, indent=2))
    else:
        print(json.dumps(export(args.output), indent=2))


if __name__ == "__main__":
    main()
