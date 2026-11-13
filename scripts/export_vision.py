"""Export the sealed optional offline vision add-on, excluding all owner state."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import __version__
from ariadne.atomic import commit_staged, publish_bytes
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry
from scripts.provision_vision import verified_files, digest

PUBLIC_SOURCE_FILES = ("config/vision-assets.json", "requirements-vision.txt", "ariadne/resources/vision/model-manifest.json",
    "scripts/provision_vision.py", "scripts/build_vision_decoder.py", "scripts/build_vision_decoder.sh",
    "scripts/install_vision_pack.py", "docs/VISION_LOCAL.md", "docs/VISION_PREVIEW.md")


def inventory(root=ROOT):
    root = _allowed_path(root)
    files = verified_files(root)
    for name in PUBLIC_SOURCE_FILES:
        path = _allowed_path(root / name)
        files[name] = (path, digest(path))
    return files


def plan(root=ROOT):
    files = inventory(root)
    return {"schema": 1, "profile": "ariadne-offline-vision-v1", "application_version": __version__,
        "platform": "win_amd64", "python": "3.13", "files": len(files),
        "payload_bytes": sum(path.stat().st_size for path, _ in files.values()),
        "private_state_included": False, "runtime_network_required": False,
        "inventory": [{"path": name, "bytes": path.stat().st_size, "sha256": sha} for name, (path, sha) in sorted(files.items())]}


def export(destination, *, root=ROOT):
    root = _allowed_path(root)
    destination = _allowed_path(destination)
    if not destination.is_relative_to(root) or destination.suffix.lower() != ".zip":
        raise ValueError("Vision export must be a ZIP inside this installation")
    partial = _allowed_path(destination.with_suffix(".zip.partial"))
    sidecars = [destination, partial, destination.with_suffix(".zip.sha256"), destination.with_suffix(".zip.receipt.json")]
    if any(path.exists() for path in sidecars):
        raise FileExistsError("Inspect the prior export/partial/receipt; choose another output name")
    files = inventory(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    guard = object.__new__(ToolRegistry)
    rows = []
    with guard._directory(destination.parent):
        with partial.open("xb") as output, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, (path, expected) in sorted(files.items()):
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                sha = hashlib.sha256()
                size = 0
                with guard._directory(path.parent) as directory:
                    with os.fdopen(guard._open_read(path, directory), "rb") as source, archive.open(info, "w", force_zip64=True) as target:
                        before = os.fstat(source.fileno())
                        while block := source.read(1024 * 1024):
                            target.write(block)
                            sha.update(block)
                            size += len(block)
                        after = os.fstat(source.fileno())
                if sha.hexdigest() != expected or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or size != before.st_size:
                    raise ValueError("Vision export input changed: " + name)
                rows.append({"path": name, "bytes": size, "sha256": expected})
            manifest = {"schema": 1, "profile": "ariadne-offline-vision-v1", "application_version": __version__,
                "platform": "win_amd64", "python": "3.13", "private_state_included": False,
                "requires": f"Ariadne {__version__} Windows x64 offline core with bundled CPython 3.13", "files": rows}
            archive.writestr("VISION-PACK-MANIFEST.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            archive.close()
            output.flush()
            os.fsync(output.fileno())
        commit_staged(partial, destination)
        sha = digest(destination)
        receipt = {"schema": 1, "status": "completed", "archive": destination.name, "application_version": __version__,
            "profile": manifest["profile"], "bytes": destination.stat().st_size, "sha256": sha,
            "files": len(rows) + 1, "private_state_included": False, "internet_required_for_local_use": False}
        publish_bytes(destination.with_suffix(".zip.sha256"), (sha + "  " + destination.name + "\n").encode())
        publish_bytes(destination.with_suffix(".zip.receipt.json"), (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.plan) == bool(args.output):
        parser.error("Choose --plan or --output PATH")
    try:
        result = plan() if args.plan else export(args.output)
        print(json.dumps({key: value for key, value in result.items() if key != "inventory"}, indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, "Vision export stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
