"""Provision pinned local automation assets; normal Ariadne startup never downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path

NODE = "24.22.1"
NODE_RED = "5.0.9"
PLAYWRIGHT = "1.64.1"
BASE = _allowed_path(ROOT / "runtime" / "automation")


def download(url, target, digest=None, maximum=150_000_000):
    target = _allowed_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and digest and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return
    temp = _allowed_path(target.with_suffix(target.suffix + ".part"))
    try:
        with urllib.request.urlopen(url, timeout=60) as source, temp.open("wb") as output:
            total = 0
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > maximum:
                    raise ValueError("Download exceeds its bounded size.")
                output.write(block)
        if digest and hashlib.sha256(temp.read_bytes()).hexdigest() != digest:
            raise ValueError("Publisher checksum mismatch.")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def node_runtime(offline):
    destination = _allowed_path(BASE / f"node-v{NODE}-win-x64")
    executable = destination / "node.exe"
    if offline:
        lock = json.loads((BASE / "node-lock.json").read_text(encoding="utf-8"))
    else:
        url = f"https://nodejs.org/dist/v{NODE}/"
        filename = f"node-v{NODE}-win-x64.zip"
        sums = urllib.request.urlopen(url + "SHASUMS256.txt", timeout=30).read(32_768).decode("ascii")
        digest = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename)
        lock = {"version": NODE, "url": url + filename, "sha256": digest}
        (BASE / "node-lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    archive = _allowed_path(BASE / "cache" / "node.zip")
    if offline:
        if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["sha256"]:
            raise ValueError("Cached Node checksum mismatch.")
    else:
        download(lock["url"], archive, lock["sha256"])
    if not executable.is_file():
        with zipfile.ZipFile(archive) as bundle:
            if sum(x.file_size for x in bundle.infolist()) > 400_000_000:
                raise ValueError("Node archive expands beyond limit.")
            for member in bundle.infolist():
                parts = PurePosixPath(member.filename).parts
                if not parts or parts[0] != destination.name or any(p in {"..", "."} or ":" in p or "\\" in p for p in parts) or member.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("Unsafe Node archive member.")
                output = _allowed_path(BASE.joinpath(*parts))
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, output.open("wb") as target:
                        shutil.copyfileobj(source, target)
    return executable


def node_red(offline):
    node = node_runtime(offline)
    app = _allowed_path(BASE / "node-red")
    app.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "ariadne-local-flows", "private": True, "version": "1.0.0", "dependencies": {"node-red": NODE_RED}}
    (app / "package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    npm = node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    env = os.environ.copy()
    env.update({"PATH": str(node.parent) + os.pathsep + os.environ.get("PATH", ""), "npm_config_cache": str(_allowed_path(BASE / "npm-cache")), "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_update_notifier": "false"})
    command = [str(node), str(npm)]
    if not offline:
        subprocess.run(command + ["install", "--package-lock-only", "--ignore-scripts", "--registry=https://registry.npmjs.org"], cwd=app, env=env, check=True, timeout=180)
    subprocess.run(command + ["ci", "--ignore-scripts", "--no-audit", "--no-fund", *( ["--offline"] if offline else ["--registry=https://registry.npmjs.org"] )], cwd=app, env=env, check=True, timeout=300)
    print("Node-RED runtime cached. No service started and no flows deployed.", flush=True)


def playwright(offline):
    wheels = _allowed_path(BASE / "wheels")
    target = _allowed_path(BASE / "python")
    wheels.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PIP_CACHE_DIR": str(_allowed_path(BASE / "pip-cache")), "PLAYWRIGHT_BROWSERS_PATH": str(_allowed_path(BASE / "browsers"))})
    lockfile = BASE / "playwright-lock.json"
    if not offline:
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels), f"playwright=={PLAYWRIGHT}"], env=env, check=True, timeout=240)
        from pip._vendor.packaging.utils import parse_wheel_filename
        records = []
        for wheel in sorted(wheels.glob("*.whl")):
            package, version, _, _ = parse_wheel_filename(wheel.name)
            metadata = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30))
            published = next(x for x in metadata["urls"] if x["filename"] == wheel.name)
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            if digest != published["digests"]["sha256"]:
                raise ValueError("PyPI wheel checksum mismatch.")
            records.append({"filename": wheel.name, "sha256": digest, "url": published["url"]})
        lockfile.write_text(json.dumps(records, indent=2), encoding="utf-8")
    records = json.loads(lockfile.read_text(encoding="utf-8"))
    paths = []
    for record in records:
        path = _allowed_path(wheels / record["filename"])
        if path.parent != wheels or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Cached wheel checksum mismatch.")
        paths.append(str(path))
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(target), *paths], env=env, check=True, timeout=120)
    env["PYTHONPATH"] = str(target)
    if not offline:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=ROOT, env=env, check=True, timeout=300)
    if not any((BASE / "browsers").glob("chromium-*")):
        raise ValueError("Offline Chromium cache is missing.")
    print("Pinned Playwright and Chromium cached for local reviewed apps.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", choices=["node-red", "playwright", "all"], default="all")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    if args.pack in {"node-red", "all"}:
        node_red(args.offline)
    if args.pack in {"playwright", "all"}:
        playwright(args.offline)


if __name__ == "__main__":
    main()
