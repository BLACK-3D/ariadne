"""Provision finite CPU speech assets and hash-locked wheels inside Yikes.

Network is used only by this explicit operator command. Runtime workers do not
fetch models. No microphone/camera, services, PATH changes or E: access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from scripts.provision_voice import download

TORCH_URL = "https://download-r2.pytorch.org/whl/cu130/torch-2.14.1%2Bcu130-cp313-cp313-win_amd64.whl"
TORCH_SHA256 = "f064c797bb497daf2b2b5e32b6d794abef087e3f24e758e6c6f8010bd2698319"


def provision_licenses(offline=False):
    """Retain full publisher notices, with a hash pin independent of runtime models."""
    specification = json.loads((ROOT / "scripts" / "media-license-assets.json").read_text(encoding="utf-8"))
    directory = _allowed_path(ROOT / "runtime" / "media" / "licenses")
    for item in specification["files"]:
        target = _allowed_path(directory / item["path"])
        if not target.is_relative_to(directory):
            raise ValueError("Invalid license artifact destination.")
        valid = False
        if target.is_file() and target.stat().st_nlink == 1 and target.stat().st_size == item["bytes"]:
            valid = hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]
        if not valid:
            if offline:
                raise ValueError("Offline publisher notice is missing or changed: " + item["path"])
            url = item["url"]
            host = urllib.parse.urlsplit(url).hostname
            if urllib.parse.urlsplit(url).scheme != "https" or host not in {"raw.githubusercontent.com", "huggingface.co", "modelscope.cn"}:
                raise ValueError("Unexpected publisher notice origin.")
            with urllib.request.urlopen(url, timeout=30) as response:
                blob = response.read(1_000_001)
            if len(blob) != item["bytes"] or hashlib.sha256(blob).hexdigest() != item["sha256"]:
                raise ValueError("Upstream publisher notice differs from its reviewed snapshot.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
    (directory / "model-notice-provenance.json").write_text(json.dumps(specification, indent=2), encoding="utf-8")
    print("Full pinned model licenses/cards retained for offline use.", flush=True)


def retain_wheel_notices():
    destination = _allowed_path(ROOT / "runtime" / "media" / "licenses" / "python")
    entries = []
    for relative, manifest_name in (("wheels", "wheel-manifest.json"), ("flux-wheels", "flux-wheel-manifest.json")):
        manifest = ROOT / "runtime" / "media" / manifest_name
        if not manifest.exists():
            continue
        for item in json.loads(manifest.read_text(encoding="utf-8")):
            wheel = _allowed_path(ROOT / "runtime" / "media" / relative / item["filename"])
            if wheel.name != item["filename"]:
                raise ValueError("Invalid wheel notice source.")
            with wheel.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError("Wheel notice source failed integrity verification.")
            with zipfile.ZipFile(wheel) as archive:
                for info in archive.infolist():
                    parts = info.filename.split("/")
                    if info.is_dir() or any(part in {"", ".", ".."} or ":" in part or "\\" in part for part in parts):
                        continue
                    basename = parts[-1].upper()
                    if not (".dist-info/licenses/" in info.filename or basename == "METADATA"
                            or basename.startswith(("LICENSE", "COPYING", "NOTICE"))):
                        continue
                    if info.file_size > 1_000_000:
                        raise ValueError("Wheel notice exceeds its size limit.")
                    blob = archive.read(info)
                    target = _allowed_path(destination / wheel.stem / Path(*parts))
                    if not target.is_relative_to(destination):
                        raise ValueError("Wheel notice escaped its destination.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob)
                    entries.append({"wheel": item["filename"], "wheel_sha256": item["sha256"], "source": item["source"],
                        "entry": info.filename, "path": target.relative_to(destination.parent).as_posix(),
                        "sha256": hashlib.sha256(blob).hexdigest()})
    destination.mkdir(parents=True, exist_ok=True)
    (destination.parent / "python-notice-provenance.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    print("Retained original dependency wheel license/notice files and metadata.", flush=True)

HF_BASE = "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/672fbf1b30579d6585301139bb363f42a0ad4a24/"
KO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
ASSETS = {
    "kokoro": {
        "model": (KO_BASE + "kokoro-v1.0.int8.onnx", "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c", 120_000_000),
        "voices": (KO_BASE + "voices-v1.0.bin", "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 30_000_000)},
    "silero": {"model": ("https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.onnx",
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3", 3_000_000)},
    "sherpa": {
        "encoder": (HF_BASE + "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx", "563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1", 75_000_000),
        "decoder": (HF_BASE + "decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx", "98da299f471e38bb4e1a8df579b8cc9122d6039576a77e357b3c60f17dd83b02", 2_000_000),
        "joiner": (HF_BASE + "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx", "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297", 1_000_000),
        "tokens": (HF_BASE + "tokens.txt", "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb", 10_000)},
    "diarization": {
        "segmentation": ("https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/model.int8.onnx",
            "d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d", 2_000_000),
        "embedding": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
            "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b", 31_000_000)}}


def provision_assets(offline=False):
    from ariadne.media_tools import load_profile
    data = _allowed_path(ROOT / "data")
    profile = load_profile(data)
    for backend, roles in ASSETS.items():
        profile["backends"][backend] = {"assets": {}}
        for role, (url, digest, limit) in roles.items():
            filename = url.rsplit("/", 1)[1]
            target = _allowed_path(ROOT / "runtime" / "media" / backend / filename)
            if offline:
                if not target.is_file() or target.stat().st_nlink != 1 or target.stat().st_size > limit:
                    raise ValueError("Offline media asset is missing, linked or oversized: " + filename)
                with target.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                        raise ValueError("Offline media asset integrity failed: " + filename)
            else:
                download(url, target, digest, limit)
            profile["backends"][backend]["assets"][role] = {"path": target.relative_to(ROOT).as_posix(), "sha256": digest}
    data.mkdir(parents=True, exist_ok=True)
    target = _allowed_path(data / "media-config.json")
    temporary = _allowed_path(data / "media-config.json.new")
    if temporary.exists():
        temporary.unlink()
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(profile, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    print("Pinned speech model assets provisioned; no live recording was performed.", flush=True)


def provision_wheels(install=False, offline=False):
    """Cache the resolver result and verify every wheel against PyPI's own digest."""
    directory = _allowed_path(ROOT / "runtime" / "media" / "wheels")
    directory.mkdir(parents=True, exist_ok=True)
    temp = _allowed_path(ROOT / "runtime" / "media" / "tmp")
    temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PIP_CACHE_DIR": str(ROOT / "runtime" / "pip-cache"), "TMP": str(temp), "TEMP": str(temp)})
    lock_path = _allowed_path(ROOT / "requirements-media.txt")
    if offline:
        manifest_path = _allowed_path(directory.parent / "wheel-manifest.json")
        if not manifest_path.is_file() or manifest_path.stat().st_size > 500_000 or not lock_path.is_file():
            raise ValueError("Offline wheel manifest or hash lock is missing.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if type(manifest) is not list or not 1 <= len(manifest) <= 100:
            raise ValueError("Invalid offline wheel manifest.")
        for item in manifest:
            if type(item) is not dict or set(item) != {"name", "version", "filename", "sha256", "source"}:
                raise ValueError("Invalid offline wheel entry.")
            if Path(item["filename"]).name != item["filename"] or not item["filename"].endswith(".whl"):
                raise ValueError("Invalid offline wheel filename.")
            path = _allowed_path(directory / item["filename"])
            if path.stat().st_nlink != 1:
                raise ValueError("Offline wheels must not be linked.")
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError("Offline wheel integrity failed.")
        if install:
            subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(directory),
                "--upgrade", "--target", str(_allowed_path(ROOT / "runtime" / "media" / "python")),
                "--require-hashes", "--no-deps", "-r", str(lock_path)], env=env, cwd=ROOT, check=True)
        print("Offline cached assets and wheels verified without network access.", flush=True)
        return
    constraints = directory.parent / "constraints.txt"
    lines = []
    for item in (ROOT / "requirements-ocr.txt").read_text(encoding="utf-8").splitlines():
        if "==" in item and not item.startswith("#"):
            lines.append(item.split(" --hash", 1)[0])
    constraints.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(directory),
        "--constraint", str(constraints), "kokoro-onnx==0.6.1", "sherpa-onnx==2.0.1"], env=env, cwd=ROOT, check=True)
    manifest, lock = [], ["# Exact offline Windows CPython 3.13 media wheel lock. Generated from verified public PyPI digests."]
    for wheel in sorted(directory.glob("*.whl")):
        _allowed_path(wheel)
        if wheel.stat().st_nlink != 1:
            raise ValueError("Wheels must not be hard linked.")
        name, version = wheel.name.split("-", 2)[:2]
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            release = json.load(response)
        matches = [x for x in release["urls"] if x["filename"] == wheel.name]
        with wheel.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if len(matches) != 1 or matches[0]["digests"]["sha256"] != digest:
            raise ValueError("Cached wheel does not match its authoritative PyPI release digest.")
        manifest.append({"name": name, "version": version, "filename": wheel.name, "sha256": digest, "source": matches[0]["url"]})
        lock.append(f"{name}=={version} --hash=sha256:{digest}")
    lock_path.write_text("\n".join(lock) + "\n", encoding="utf-8")
    (directory.parent / "wheel-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if install:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(directory),
            "--upgrade", "--target", str(_allowed_path(ROOT / "runtime" / "media" / "python")),
            "--require-hashes", "--no-deps", "-r", str(lock_path)], env=env, cwd=ROOT, check=True)
    print("Media wheel cache and exact SHA-256 lock are ready.", flush=True)


def provision_flux(install=False, offline=False):
    """Provision the complete 16 GB Apache-licensed model and isolated CUDA stack."""
    from ariadne.media_tools import load_profile
    specification = json.loads((ROOT / "scripts" / "media-flux-assets.json").read_text(encoding="utf-8"))
    directory = _allowed_path(ROOT / "runtime" / "media" / "flux")
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for asset in specification["files"]:
        target = _allowed_path(directory / asset["path"])
        if not target.is_relative_to(directory):
            raise ValueError("Escaping FLUX asset path.")
        if offline:
            if not target.is_file() or target.stat().st_nlink != 1 or target.stat().st_size != asset["bytes"]:
                raise ValueError("Offline FLUX asset missing, linked or wrong size.")
            with target.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != asset["sha256"]:
                    raise ValueError("Offline FLUX asset integrity mismatch.")
        else:
            download(asset["url"], target, asset["sha256"], asset["bytes"])
        files[asset["path"]] = asset["sha256"]
    manifest = directory / "ariadne-manifest.json"
    manifest.write_text(json.dumps({"model": specification["model"], "files": files}, indent=2), encoding="utf-8")
    profile = load_profile(ROOT / "data")
    profile["backends"]["flux"] = {"assets": {"manifest": {"path": manifest.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}}}
    # Publish only when every model artifact is complete and integrity-checked.
    (ROOT / "data" / "media-config.json.new").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    os.replace(ROOT / "data" / "media-config.json.new", ROOT / "data" / "media-config.json")
    wheels = _allowed_path(ROOT / "runtime" / "media" / "flux-wheels")
    wheels.mkdir(parents=True, exist_ok=True)
    lock_path = ROOT / "requirements-image.txt"
    wheel_manifest = wheels.parent / "flux-wheel-manifest.json"
    env = os.environ.copy()
    env.update({"PIP_CACHE_DIR": str(ROOT / "runtime" / "pip-cache"), "TMP": str(ROOT / "runtime" / "media" / "tmp"),
                "TEMP": str(ROOT / "runtime" / "media" / "tmp")})
    if not offline:
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels),
            TORCH_URL + "#sha256=" + TORCH_SHA256, "diffusers==0.42.1", "transformers==5.21.0", "accelerate==1.15.0",
            "safetensors==0.8.0", "pillow==13.0.0"], env=env, cwd=ROOT, check=True)
        inventory, lock = [], ["# Pinned Windows CUDA image-generation wheel lock; authoritative PyPI/PyTorch hashes verified."]
        for wheel in sorted(wheels.glob("*.whl")):
            name, version = wheel.name.split("-", 2)[:2]
            with wheel.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if name == "torch":
                expected, origin = TORCH_SHA256, TORCH_URL
            else:
                with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
                    release = json.load(response)
                matches = [item for item in release["urls"] if item["filename"] == wheel.name]
                if len(matches) != 1:
                    raise ValueError("Image wheel is missing its upstream release record.")
                expected, origin = matches[0]["digests"]["sha256"], matches[0]["url"]
            if expected != digest:
                raise ValueError("Image wheel integrity mismatch.")
            inventory.append({"name": name, "version": version, "filename": wheel.name, "sha256": digest, "source": origin})
            lock.append(f"{name}=={version} --hash=sha256:{digest}")
        lock_path.write_text("\n".join(lock) + "\n", encoding="utf-8")
        wheel_manifest.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    else:
        inventory = json.loads(wheel_manifest.read_text(encoding="utf-8"))
        for item in inventory:
            wheel = _allowed_path(wheels / item["filename"])
            if wheel.parent != wheels or wheel.stat().st_nlink != 1:
                raise ValueError("Offline image wheel path/link is invalid.")
            with wheel.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError("Offline image wheel integrity failed.")
    if install:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
            "--upgrade", "--target", str(_allowed_path(ROOT / "runtime" / "media" / "flux-python")),
            "--require-hashes", "--no-deps", "-r", str(lock_path)], env=env, cwd=ROOT, check=True)
    print("Full local FLUX pipeline assets and isolated CUDA wheels are ready.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", action="store_true", help="Download and hash-lock dependencies.")
    parser.add_argument("--install", action="store_true", help="Install only from the verified local wheel cache.")
    parser.add_argument("--assets-only", action="store_true", help="Provision model assets without resolving wheels.")
    parser.add_argument("--offline", action="store_true", help="Verify and optionally install only cached assets/wheels; never use the network.")
    parser.add_argument("--flux", action="store_true", help="Also provision the complete ~16 GB FLUX.2-klein-4B pipeline and CUDA runtime wheels.")
    args = parser.parse_args()
    if (args.wheels or args.install or args.flux) and (os.name != "nt" or sys.version_info[:2] != (3, 13)
            or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise SystemExit("This pinned binary cache targets Windows x64 / CPython 3.13. Other platforms need a native wheel cache and qualification; model assets alone are portable.")
    provision_licenses(offline=args.offline)
    provision_assets(offline=args.offline)
    if args.wheels or args.install:
        provision_wheels(install=args.install, offline=args.offline)
    if args.flux:
        provision_flux(install=args.install, offline=args.offline)
    retain_wheel_notices()


if __name__ == "__main__":
    main()
