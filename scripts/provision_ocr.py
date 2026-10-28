"""Explicit offline OCR provisioning from the project's verified binary wheels.

Does not run pip, contact the network, or import an OCR library. The installation
owner installs requirements-ocr.txt in .venv separately under the package lock.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import secrets
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from ariadne.config import _allowed_path
from ariadne.ocr import MODEL_HASHES, CORE_VERSIONS, _local_file, processor_identity


def _hash(path):
    with _local_file(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path, payload):
    path = _allowed_path(path)
    path.relative_to(PROJECT_ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _local_file(path)
    temporary = _allowed_path(path.with_name(path.name + ".part-" + secrets.token_hex(8)))
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        _allowed_path(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            _allowed_path(temporary).unlink()


def prepare():
    lock = _local_file(PROJECT_ROOT / "requirements-ocr.txt", 32_768)
    pins = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+) --hash=sha256:([a-f0-9]{64})", line)
        if not match:
            raise ValueError("OCR lock must contain only exact versions and SHA-256 hashes")
        name, version, digest = match.groups()
        pins[name.lower().replace("_", "-")] = (name, version, digest)
    manifest_path = _local_file(PROJECT_ROOT / "config/ocr-wheels-manifest.json", 100_000)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("platform") != "win_amd64" or manifest.get("python") != "3.13":
        raise ValueError("This OCR wheel lock is qualified for Windows x64 Python 3.13")
    packages, verified = {}, []
    for item in manifest["packages"]:
        key = item["name"].lower().replace("_", "-")
        if key not in pins or pins[key][1:] != (item["version"], item["sha256"]) or key in packages:
            raise ValueError("OCR wheel manifest differs from the dependency lock")
        filename = item["filename"]
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".whl"):
            raise ValueError("Invalid OCR wheel filename")
        wheel = _local_file(PROJECT_ROOT / "runtime/ocr/wheels" / filename, 100_000_000)
        if _hash(wheel) != item["sha256"]:
            raise ValueError("A cached OCR wheel does not match its published SHA-256")
        packages[key] = item["version"]
        verified.append((item, wheel))
    if set(packages) != set(pins):
        raise ValueError("OCR wheelhouse does not contain every locked dependency")
    model_paths = {}
    notices_count = 0
    for item, wheel in verified:
        with zipfile.ZipFile(wheel) as archive:
            if item["name"].lower() == "rapidocr":
                for role, (filename, digest) in MODEL_HASHES.items():
                    payload = archive.read("rapidocr/models/" + filename)
                    if len(payload) > 50_000_000 or hashlib.sha256(payload).hexdigest() != digest:
                        raise ValueError("The bundled OCR model differs from its official registry digest")
                    target = PROJECT_ROOT / "runtime/ocr/models" / filename
                    _write(target, payload)
                    model_paths[role] = str(target)
            for info in archive.infolist():
                parts = PurePosixPath(info.filename).parts
                if (not parts or any(part in {".", ".."} or ":" in part or "\\" in part for part in parts)
                        or info.filename.startswith("/") or info.is_dir()):
                    continue
                if not ".dist-info" in parts[0] or not any(any(label in part.lower() for label in ("license", "notice", "copying")) for part in parts[1:]):
                    continue
                if info.file_size > 1_000_000:
                    raise ValueError("An OCR package notice exceeds the preservation limit")
                target = PROJECT_ROOT / "runtime/ocr/notices" / wheel.stem / Path(*parts[1:])
                _write(target, archive.read(info))
                notices_count += 1
    if set(model_paths) != set(MODEL_HASHES):
        raise ValueError("The RapidOCR wheel did not contain all pinned models")
    canonical_packages = {pins[key][0]: value for key, value in packages.items()}
    return model_paths, canonical_packages, notices_count


def provision(config_path=None, prepare_only=False):
    models, packages, count = prepare()
    if prepare_only:
        return {"prepared": True, "configured": False, "models": len(models), "package_notices": count}
    expected_python = _allowed_path(sys.executable)
    allowed_pythons = {_allowed_path(PROJECT_ROOT / ".venv/Scripts/python.exe"), _allowed_path(PROJECT_ROOT / "runtime/python/python.exe")}
    if os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info[:2] != (3, 13) or expected_python not in allowed_pythons:
        raise ValueError("Run this qualified provisioner with this box's private or bundled Python 3.13")
    for name, expected in packages.items():
        if importlib.metadata.version(name) != expected:
            raise ValueError("Install the exact hashed OCR lock in .venv before enabling OCR")
    if any(packages.get(name) != expected for name, expected in CORE_VERSIONS.items()):
        raise ValueError("OCR lock does not match the pinned processor profile")
    config = {"schema": 1, "python": str(expected_python), "models": models, "packages": packages,
              "processor": processor_identity(packages), "lock_sha256": _hash(PROJECT_ROOT / "requirements-ocr.txt")}
    target = _allowed_path(config_path or PROJECT_ROOT / "data/ocr-config.json")
    target.relative_to(PROJECT_ROOT)
    _write(target, (json.dumps(config, indent=2) + "\n").encode())
    return {"configured": True, "config": str(target), "processor": config["processor"], "models": len(models), "package_notices": count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Verify/extract local assets without enabling the worker")
    parser.add_argument("--config", type=Path, help="Configuration path within this installation; defaults to data/ocr-config.json")
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.config, args.prepare_only), indent=2))
    except (ValueError, OSError, KeyError, importlib.metadata.PackageNotFoundError, zipfile.BadZipFile) as error:
        parser.exit(1, "OCR provisioning failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
