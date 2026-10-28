"""Owner-only knowledge provisioning; normal Ariadne workers never download.

Use --lock-wheels --online to verify cached wheels against PyPI's published
digests. Install that exact lock with pip --no-index --require-hashes --target
runtime/knowledge/python. Default operation verifies local assets and writes a
versioned knowledge-config.json; --fetch-model explicitly downloads the one
fixed embedding model and records its immutable upstream revision.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.knowledge_tools import MODEL, DIMENSIONS, canonical, file_hash, local_file

BASE = ROOT / "runtime/knowledge"
LOCK = ROOT / "requirements-knowledge.txt"
MANIFEST = ROOT / "config/knowledge-wheels-manifest.json"
ASSET_LOCK = ROOT / "config/knowledge-model-assets.json"
HF_REPO = "Qdrant/bge-small-en-v1.5-onnx-Q"


def write(path, data):
    path = _allowed_path(path)
    path.relative_to(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        local_file(path, 2_000_000_000)
    temporary = _allowed_path(path.with_name(path.name + ".part"))
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def get(url, limit):
    if not url.startswith(("https://pypi.org/", "https://huggingface.co/", "https://download.kiwix.org/")):
        raise ValueError("Provisioning origin is unsupported.")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Ariadne-offline-provisioner/0.4"}), timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Provisioning download exceeds its byte limit.")
    return data


def lock_wheels(online):
    if not online:
        raise ValueError("--online is required to verify package digests with PyPI.")
    packages, lines = [], []
    seen = set()
    for wheel in sorted((_allowed_path(BASE / "wheels")).glob("*.whl")):
        with zipfile.ZipFile(local_file(wheel, 2_000_000_000)) as archive:
            metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            value = email.message_from_bytes(archive.read(metadata))
            name, version = value["Name"], value["Version"]
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            raise ValueError("Wheelhouse contains multiple versions of one package.")
        seen.add(key)
        release = json.loads(get("https://pypi.org/pypi/" + urllib.parse.quote(name) + "/" + urllib.parse.quote(version) + "/json", 5_000_000))
        official = next(item for item in release["urls"] if item["filename"] == wheel.name)
        digest = file_hash(wheel, 2_000_000_000)
        if digest != official["digests"]["sha256"] or wheel.stat().st_size != official["size"]:
            raise ValueError("Cached wheel differs from PyPI's published digest/size.")
        packages.append({"name": name, "version": version, "filename": wheel.name, "sha256": digest,
                         "bytes": wheel.stat().st_size, "url": official["url"]})
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    if not packages:
        raise ValueError("Cache binary wheels before creating the lock.")
    write(LOCK, ("# Windows x64 Python 3.13; hashes verified with PyPI published metadata.\n" + "\n".join(lines) + "\n").encode())
    write(MANIFEST, canonical({"schema": 1, "platform": "win_amd64", "python": "3.13", "packages": packages}))
    return {"locked": len(packages)}


def asset_lock():
    result = json.loads(local_file(ASSET_LOCK, 200_000).read_text(encoding="utf-8"))
    if result.get("schema") != 1 or result["embedding"]["repository"] != HF_REPO:
        raise ValueError("Invalid qualified knowledge asset lock.")
    sources = [result["embedding"], *result["layout"]["sources"]]
    allowed = {HF_REPO, "docling-project/docling-layout-heron", "docling-project/docling-models"}
    if any(source["repository"] not in allowed or not re.fullmatch(r"[a-f0-9]{40}", source["revision"]) for source in sources):
        raise ValueError("Knowledge sources must use reviewed repositories and immutable commits.")
    for group in ("embedding", "layout", "kiwix"):
        assets = result[group]["assets"]
        if not 1 <= len(assets) <= 1000:
            raise ValueError("Invalid qualified asset count.")
        for item in assets:
            parts = item["path"].split("/")
            if any(part in {"", ".", ".."} or ":" in part or "\\" in part for part in parts) or type(item["bytes"]) is not int or not 0 < item["bytes"] <= 300_000_000 or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                raise ValueError("Invalid qualified asset path, size or SHA-256.")
    return result


def fetch_asset(item, url):
    target = _allowed_path(BASE / item["path"])
    target.relative_to(BASE)
    if target.exists():
        if target.stat().st_size == item["bytes"] and file_hash(target, 300_000_000) == item["sha256"]:
            return
        raise ValueError("A cached asset differs from the frozen qualification lock; move it aside before retrying.")
    payload = get(url, item["bytes"])
    if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
        raise ValueError("Downloaded artifact differs from the frozen qualification SHA-256/size.")
    write(target, payload)


def fetch_model(online):
    if not online:
        raise ValueError("--online is required for explicit model provisioning.")
    model = asset_lock()["embedding"]
    for item in model["assets"]:
        if not item["path"].startswith("model/"):
            raise ValueError("Invalid frozen embedding asset path.")
        name = item["path"][len("model/"):]
        fetch_asset(item, "https://huggingface.co/" + model["repository"] + "/resolve/" + model["revision"] + "/" + name)
    write(BASE / "model-manifest.json", canonical(model))
    return {"verified": len(model["assets"]), "revision": model["revision"]}


def fetch_layout(online):
    """Only Heron layout and the smaller Fast TableFormer, no OCR/VLM bundle."""
    if not online:
        raise ValueError("--online is required for explicit layout model provisioning.")
    layout = asset_lock()["layout"]
    for item in layout["assets"]:
        source = next((source for source in layout["sources"] if item["path"].startswith("docling-models/" + source["repository"].replace("/", "--") + "/")), None)
        if not source:
            raise ValueError("Invalid frozen layout asset path.")
        prefix = "docling-models/" + source["repository"].replace("/", "--") + "/"
        fetch_asset(item, "https://huggingface.co/" + source["repository"] + "/resolve/" + source["revision"] + "/" + item["path"][len(prefix):])
    write(BASE / "layout-manifest.json", canonical(layout))
    return {"verified": len(layout["assets"]), "sources": layout["sources"]}


def fetch_kiwix(online):
    if not online:
        raise ValueError("--online is required for explicit Kiwix provisioning.")
    frozen = asset_lock()["kiwix"]
    name = "kiwix-tools_win-x86_64-3.8.1.zip"
    url = "https://download.kiwix.org/release/kiwix-tools/" + name
    if frozen["download"]["url"] != url or frozen["version"] != "3.8.1":
        raise ValueError("Unsupported frozen Kiwix package.")
    checksum = frozen["download"]["official_md5"]
    archive_path = BASE / "downloads" / name
    payload = local_file(archive_path, 50_000_000).read_bytes() if archive_path.exists() else get(url, 50_000_000)
    if (not re.fullmatch(r"[a-f0-9]{32}", checksum) or hashlib.md5(payload).hexdigest() != checksum
            or hashlib.sha256(payload).hexdigest() != frozen["download"]["sha256"]):
        raise ValueError("Kiwix package differs from the frozen qualification checksums.")
    if not archive_path.exists():
        write(archive_path, payload)
    expected_assets = {item["path"]: item for item in frozen["assets"]}
    assets, binary = [], None
    import io
    import stat
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(item.file_size for item in members) > 150_000_000:
            raise ValueError("Kiwix distribution exceeds extraction limits.")
        seen = set()
        for item in members:
            if item.is_dir():
                continue
            parts = item.filename.split("/")
            if any(part in {"", ".", ".."} or ":" in part or "\\" in part for part in parts) or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG} or item.flag_bits & 1 or item.filename.casefold() in seen:
                raise ValueError("Unsafe Kiwix distribution member.")
            seen.add(item.filename.casefold())
            data = archive.read(item)
            relative = "kiwix/" + item.filename
            expected = expected_assets.get(relative)
            if expected is None or len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("Kiwix extracted artifact differs from the frozen qualification lock.")
            target = BASE / relative
            if target.exists():
                if file_hash(target, 50_000_000) != expected["sha256"]:
                    raise ValueError("Cached Kiwix artifact differs; move it aside before retrying.")
            else:
                write(target, data)
            assets.append(expected)
            if parts[-1] == "kiwix-serve.exe":
                binary = relative
    if not binary:
        raise ValueError("Kiwix Windows distribution has no server executable.")
    archives = []
    for entry in frozen["archives"]:
        relative = "library/" + entry["name"] + ".zim"
        item = expected_assets[relative]
        fetch_asset(item, entry["url"])
        assets.append(item)
        archives.append(entry)
    if {item["path"] for item in assets} != set(expected_assets):
        raise ValueError("Frozen Kiwix package has an unexpected artifact set.")
    write(BASE / "kiwix-manifest.json", canonical(frozen))
    return {"cached": len(assets), "archives": [item["name"] for item in archives], "version": "3.8.1"}


def rebase_kiwix(kiwix, asset_paths):
    """Reconstruct host paths only from already-verified relative assets."""
    binaries = [name for name in asset_paths if name.startswith("kiwix/") and Path(name).name == "kiwix-serve.exe"]
    if len(binaries) != 1:
        raise ValueError("The Kiwix manifest must contain one fixed server executable.")
    archives = []
    for item in kiwix["archives"]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", item["name"]):
            raise ValueError("Unsafe archive name.")
        relative = "library/" + item["name"] + ".zim"
        if relative not in asset_paths:
            raise ValueError("Kiwix archive is absent from its verified relative asset manifest.")
        archives.append({**item, "path": str(BASE / relative)})
    return {"mode": "managed", "packages": {}, "binary": str(BASE / binaries[0]), "version": kiwix["version"],
            "assets": kiwix["assets"], "archives": archives, "download": kiwix["download"]}


def provision(config=None, docling=False, layout=False):
    frozen = asset_lock()
    manifest = json.loads(local_file(MANIFEST, 500_000).read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("platform") != "win_amd64" or manifest.get("python") != "3.13":
        raise ValueError("The optional knowledge lock targets Windows x64 Python 3.13.")
    target = _allowed_path(BASE / "python")
    distributions = {re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower(): dist.version
                     for dist in importlib.metadata.distributions(path=[str(target)])}
    packages = {}
    for item in manifest["packages"]:
        if Path(item["filename"]).name != item["filename"]:
            raise ValueError("Unsafe wheel filename.")
        wheel = local_file(BASE / "wheels" / item["filename"], 2_000_000_000)
        if file_hash(wheel, 2_000_000_000) != item["sha256"]:
            raise ValueError("A cached wheel hash differs from its verified manifest.")
        key = re.sub(r"[-_.]+", "-", item["name"]).lower()
        if distributions.get(key) != item["version"]:
            raise ValueError("Install the exact knowledge lock in runtime/knowledge/python first: " + key)
        packages[item["name"]] = item["version"]
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                parts = entry.filename.split("/")
                if entry.is_dir() or not parts[0].endswith(".dist-info") or not any(any(label in part.lower() for label in ("license", "notice", "copying")) for part in parts[1:]):
                    continue
                if any(part in {"", ".", ".."} or ":" in part or "\\" in part for part in parts) or entry.file_size > 2_000_000:
                    raise ValueError("Unsafe/oversized package notice.")
                write(BASE / "notices" / wheel.stem / Path(*parts[1:]), archive.read(entry))
    config_path = _allowed_path(config or ROOT / "data/knowledge-config.json")
    config_path.relative_to(ROOT)
    current = json.loads(local_file(config_path).read_text(encoding="utf-8")) if config_path.exists() else {"schema": 1}
    common = {"site_packages": str(target), "packages": packages, "assets": [], "lock_sha256": file_hash(LOCK)}
    binary = _allowed_path(target / "bin/monty.exe")
    if not binary.exists():
        binary = _allowed_path(target / "Scripts/monty.exe")
    if binary.exists():
        current["monty"] = {**common, "binary": str(binary), "binary_sha256": file_hash(binary, 100_000_000)}
    model_manifest = BASE / "model-manifest.json"
    if model_manifest.exists():
        model = json.loads(local_file(model_manifest).read_text(encoding="utf-8"))
        if model != frozen["embedding"]:
            raise ValueError("Embedding manifest differs from the frozen qualification lock.")
        for item in model["assets"]:
            path = _allowed_path(BASE / item["path"])
            path.relative_to(BASE)
            if file_hash(path, 2_000_000_000) != item["sha256"]:
                raise ValueError("Local model artifact hash mismatch.")
        current["semantic"] = {**common, "model": MODEL, "dimensions": DIMENSIONS, "model_path": str(BASE / "model"),
                               "assets": model["assets"], "model_repository": model["repository"], "model_revision": model["revision"]}
    if docling or layout:
        if not any(re.sub(r"[-_.]+", "-", name).lower() == "docling" and version == "2.144.0" for name, version in packages.items()):
            raise ValueError("Pinned Docling 2.144.0 is absent from the knowledge wheel lock.")
        current["docling"] = {**common, "mode": "native"}
        if layout:
            model = json.loads(local_file(BASE / "layout-manifest.json").read_text(encoding="utf-8"))
            if model != frozen["layout"]:
                raise ValueError("Layout manifest differs from the frozen qualification lock.")
            for item in model["assets"]:
                path = _allowed_path(BASE / item["path"])
                path.relative_to(BASE)
                if file_hash(path, 2_000_000_000) != item["sha256"]:
                    raise ValueError("Docling layout artifact hash mismatch.")
            current["docling"].update({"mode": "layout", "assets": model["assets"], "sources": model["sources"], "artifacts_path": str(BASE / "docling-models")})
    kiwix_manifest = BASE / "kiwix-manifest.json"
    if kiwix_manifest.exists():
        kiwix = json.loads(local_file(kiwix_manifest).read_text(encoding="utf-8"))
        if kiwix["assets"] != frozen["kiwix"]["assets"] or kiwix["version"] != frozen["kiwix"]["version"] or kiwix["download"] != frozen["kiwix"]["download"]:
            raise ValueError("Kiwix manifest differs from the frozen qualification lock.")
        asset_paths = set()
        for item in kiwix["assets"]:
            path = _allowed_path(BASE / item["path"])
            path.relative_to(BASE)
            if file_hash(path, 2_000_000_000) != item["sha256"]:
                raise ValueError("A Kiwix asset differs from its recorded SHA-256.")
            asset_paths.add(item["path"])
        # Rebase installation paths from verified relative assets so an offline
        # appliance can move to another PC/folder without the original C: path.
        current["kiwix"] = rebase_kiwix(kiwix, asset_paths)
    write(config_path, (json.dumps(current, indent=2) + "\n").encode("utf-8"))
    return {"configured": [key for key in current if key != "schema"], "packages": len(packages), "config": str(config_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-wheels", action="store_true")
    parser.add_argument("--fetch-model", action="store_true")
    parser.add_argument("--fetch-layout", action="store_true")
    parser.add_argument("--fetch-kiwix", action="store_true")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--docling", action="store_true")
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        result = lock_wheels(args.online) if args.lock_wheels else fetch_model(args.online) if args.fetch_model else fetch_layout(args.online) if args.fetch_layout else fetch_kiwix(args.online) if args.fetch_kiwix else provision(args.config, args.docling, args.layout)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, StopIteration, zipfile.BadZipFile) as error:
        parser.exit(1, "Knowledge provisioning failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
