"""Provision the optional CPU vision pack from pinned local assets.

Offline by default. --download explicitly fetches only the reviewed HTTPS URLs.
No pip, camera access, owner settings, global installation or services are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import struct
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path

CATALOG = "config/vision-assets.json"
SEALED = "ariadne/resources/vision/model-manifest.json"


def local(root, name):
    if type(name) is not str or not name or "\\" in name:
        raise ValueError("Invalid vision asset path")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise ValueError("Invalid vision asset path")
    path = _allowed_path(root / name)
    if not path.is_relative_to(root):
        raise ValueError("Vision paths must stay in this installation")
    return path


def digest(path):
    path = _allowed_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Vision assets must be regular files")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def publish(path, payload):
    path = _allowed_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _allowed_path(path.with_name(path.name + ".part-" + secrets.token_hex(6)))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, _allowed_path(path))


def catalog(root=ROOT):
    root = _allowed_path(root)
    value = json.loads(local(root, CATALOG).read_text(encoding="utf-8"))
    if value.get("schema") != 1 or value.get("platform") != "win_amd64" or value.get("python") != "3.13":
        raise ValueError("Vision pack is qualified for Windows x64 CPython 3.13")
    return value


def assets(value):
    # Corresponding source is a redistribution requirement, not an optional
    # compiler cache. Keep source-role inputs in every export/install inventory.
    source_inputs = [item for item in value.get("decoder_build", {}).get("inputs", []) if item.get("role") == "source"]
    result = {}
    for item in [*value["assets"], *value["wheels"], *source_inputs]:
        prior = result.get(item["path"])
        if prior and any(prior[key] != item[key] for key in ("bytes", "sha256")):
            raise ValueError("Conflicting vision source/cache asset pins")
        result[item["path"]] = item
    return list(result.values())


def verify_asset(root, item):
    path = local(root, item["path"])
    if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
        raise ValueError("Vision asset hash/size mismatch: " + item["path"])
    return path


def fetch(root, item):
    path = local(root, item["path"])
    if path.exists():
        return verify_asset(root, item)
    url = item["url"]
    if not url.startswith(("https://github.com/", "https://raw.githubusercontent.com/", "https://files.pythonhosted.org/", "https://ffmpeg.org/releases/")):
        raise ValueError("Vision download host is outside the reviewed catalog")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _allowed_path(path.with_name(path.name + ".partial"))
    if temporary.exists():
        raise FileExistsError("Inspect the previous partial download before retrying")
    with urllib.request.urlopen(url, timeout=60) as source, temporary.open("xb") as target:
        size = 0
        while block := source.read(1024 * 1024):
            size += len(block)
            if size > item["bytes"]:
                raise ValueError("Vision download exceeds its pinned size")
            target.write(block)
        target.flush()
        os.fsync(target.fileno())
    if size != item["bytes"] or digest(temporary) != item["sha256"]:
        raise ValueError("Vision download did not match the reviewed pin")
    os.replace(temporary, _allowed_path(path))
    return path


def wheel_members(wheel):
    """Keep runtime code and notices; omit build headers, tests and entry points."""
    with zipfile.ZipFile(wheel) as archive:
        seen = set()
        total = 0
        for item in archive.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name
                    or any(part in {".", ".."} or ":" in part for part in parts)
                    or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("Unsafe vision wheel member")
            if item.is_dir():
                continue
            if any(part in {"tests", "test", "__pycache__", "include"} for part in parts) or parts[0].endswith(".data"):
                continue
            if name.endswith((".pyc", ".pyo", "/direct_url.json")):
                continue
            if name.casefold() in seen:
                raise ValueError("Duplicate vision wheel member")
            seen.add(name.casefold())
            total += item.file_size
            if item.file_size > 150 * 1024**2 or total > 256 * 1024**2:
                raise ValueError("Vision wheel extraction exceeds its bound")
            yield name, archive.read(item)


def expected_manifest(root=ROOT):
    root = _allowed_path(root)
    value = catalog(root)
    rows = {}
    for item in value["wheels"]:
        for name, raw in wheel_members(verify_asset(root, item)):
            path = "python/" + name
            if path.casefold() in rows:
                raise ValueError("Vision packages contain conflicting members")
            rows[path.casefold()] = {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    for item in value.get("native_files", []):
        name = item["path"]
        if name.casefold() in rows:
            raise ValueError("Conflicting native vision runtime member")
        rows[name.casefold()] = item
    result = {"schema_version": 1, "model": value["model"], "runtime": {
        "python_dir": "python", "opencv_version": "5.0.0", "numpy_version": "2.5.4",
        "files": sorted(rows.values(), key=lambda item: item["path"])}}
    if "decoder" in value:
        result["decoder"] = value["decoder"]
    return result


def verified_files(root=ROOT, *, include_cache=True):
    """Return a closed, hash-checked public inventory, never an arbitrary walk."""
    root = _allowed_path(root)
    value = catalog(root)
    source = json.loads(local(root, SEALED).read_text(encoding="utf-8"))
    manifest = local(root, "runtime/vision/manifest.json")
    if json.loads(manifest.read_text(encoding="utf-8")) != source:
        raise ValueError("Vision runtime manifest differs from the source seal")
    rows = [("runtime/vision/" + item["path"], item) for item in source["runtime"]["files"]]
    rows += [(item["path"], item) for item in assets(value) if include_cache or not item["path"].startswith("runtime/vision/wheels/")]
    result = {}
    for name, item in rows:
        path = verify_asset(root, {**item, "path": name})
        result[name] = (path, item["sha256"])
    result["runtime/vision/manifest.json"] = (manifest, digest(manifest))
    exact_runtime_inventory(root, result)
    return result


def exact_runtime_inventory(root, expected):
    """An unsealed Python config/module may execute before an import completes."""
    for directory in ("python", "models", "ffmpeg"):
        base = local(root, "runtime/vision/" + directory)
        for parent, directories, files in os.walk(base, followlinks=False):
            local(root, Path(parent).relative_to(root).as_posix())
            for name in directories:
                child = local(root, (Path(parent) / name).relative_to(root).as_posix())
                prefix = child.relative_to(root).as_posix() + "/"
                if not any(name.startswith(prefix) for name in expected):
                    raise ValueError("Unsealed executable/runtime directory blocks vision: " + prefix)
            for name in files:
                path = local(root, (Path(parent) / name).relative_to(root).as_posix())
                if path.relative_to(root).as_posix() not in expected:
                    raise ValueError("Unsealed executable/runtime file blocks vision: " + path.relative_to(root).as_posix())


def self_test(root):
    # Only a generated constant tensor is inferred. No video/camera is opened.
    code = """import sys,json
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
import cv2,numpy as np
cv2.setNumThreads(1)
net=cv2.dnn.readNetFromONNX(sys.argv[2])
net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
net.setInput(np.full((1,3,416,416),114,dtype=np.float32))
out=net.forward()
assert out.shape==(1,3549,85) and np.isfinite(out).all()
assert cv2.__version__=='5.0.0' and np.__version__=='2.5.4'
print(json.dumps({'opencv':cv2.__version__,'numpy':np.__version__,'output_shape':list(out.shape)}))
"""
    completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
        str(local(root, "runtime/vision/python")), str(local(root, "runtime/vision/models/yolox_nano.onnx"))],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise ValueError("Vision CPU qualification failed: " + completed.stderr.decode("utf-8", "replace")[-1000:])
    return json.loads(completed.stdout)


def provision(root=ROOT, *, download=False):
    root = _allowed_path(root)
    if os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info[:2] != (3, 13):
        raise ValueError("Run with Windows x64 CPython 3.13; this pack never installs into global Python")
    value = catalog(root)
    for item in assets(value):
        (fetch if download else verify_asset)(root, item)
    for item in value.get("native_files", []):
        try:
            verify_asset(root, {**item, "path": "runtime/vision/" + item["path"]})
        except FileNotFoundError as error:
            raise ValueError("The sealed FFmpeg adapter is missing; install the offline vision pack or explicitly run scripts/build_vision_decoder.py --download") from error
    expected = expected_manifest(root)
    source = json.loads(local(root, SEALED).read_text(encoding="utf-8"))
    if source != expected:
        raise ValueError("Pinned wheels do not reproduce the reviewed source manifest")
    admitted = {"runtime/vision/" + item["path"] for item in source["runtime"]["files"]}
    admitted.add("runtime/vision/" + source["model"]["path"])
    exact_runtime_inventory(root, admitted)
    for item in value["wheels"]:
        for name, raw in wheel_members(verify_asset(root, item)):
            target = local(root, "runtime/vision/python/" + name)
            wanted = hashlib.sha256(raw).hexdigest()
            if target.exists():
                if digest(target) != wanted:
                    raise ValueError("Existing vision runtime differs; inspect it before repair: " + name)
            else:
                publish(target, raw)
    qualification = self_test(root)
    publish(local(root, "runtime/vision/manifest.json"), encoded(source))
    verified = verified_files(root)
    return {"prepared": True, "model": source["model"]["id"], "files": len(verified),
        "qualification": qualification, "network_used": download,
        "camera_opened": False, "owner_settings_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Explicitly fetch only the pinned official assets over HTTPS")
    parser.add_argument("--verify", action="store_true", help="Verify the existing sealed pack without loading the detector")
    args = parser.parse_args()
    try:
        result = {"verified": True, "files": len(verified_files())} if args.verify else provision(download=args.download)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        parser.exit(1, "Vision provisioning stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
