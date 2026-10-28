"""Acquire fixed engineering assets into Yikes without installing Windows programs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
BASE = ROOT / "runtime/production"


def fetch(item, offline):
    path = _allowed_path(BASE / item["archive"])
    if not path.exists():
        if offline:
            raise ValueError("Required native archive is absent from the offline cache.")
        partial = _allowed_path(path.with_suffix(".part"))
        try:
            size = 0
            with urllib.request.urlopen(item["url"], timeout=90) as source, partial.open("xb") as output:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > 3_000_000_000:
                        raise ValueError("Engineering archive exceeds 3 GB.")
                    output.write(block)
            os.rename(partial, path)
        finally:
            partial.unlink(missing_ok=True)
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
            raise ValueError("Engineering archive differs from the official checksum.")
    return path


def msi_extract(source, output):
    extractor = _allowed_path(BASE / "lessmsi/lessmsi.exe")
    output = _allowed_path(output)
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, TEMP=str(_allowed_path(ROOT / "runtime/tmp")), TMP=str(_allowed_path(ROOT / "runtime/tmp")))
    with (output / "extract.log").open("wb") as log:
        subprocess.run([str(extractor), "x", str(source), str(output) + os.sep], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)


def register(name, binary, version):
    binary = _allowed_path(binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    subprocess.run([sys.executable, str(ROOT / "scripts/provision_production.py"), "--register", name, str(binary), "--version", version, "--sha256", digest], cwd=ROOT, check=True, timeout=120)


def native(name, offline):
    pins = json.loads((ROOT / "config/engineering-assets.json").read_text(encoding="utf-8"))
    source = fetch(pins[name], offline)
    output = _allowed_path(BASE / name)
    if name == "qgis":
        binaries = list(output.rglob("qgis_process-qgis-ltr.exe")) if output.exists() else []
        if not binaries:
            msi_extract(source, output)
            binaries = list(output.rglob("qgis_process-qgis-ltr.exe")) or list(output.rglob("qgis_process.exe"))
    else:
        seven = _allowed_path(BASE / "7zip")
        tools = list(seven.rglob("7z.exe")) if seven.exists() else []
        if not tools:
            msi_extract(fetch(pins["7zip"], offline), seven)
            tools = list(seven.rglob("7z.exe"))
        if len(tools) != 1:
            raise ValueError("Expected one fixed 7-Zip executable.")
        binary = _allowed_path(tools[0])
        listing = subprocess.check_output([str(binary), "l", "-slt", str(source)], timeout=90, text=True, encoding="utf-8", errors="replace")
        if len(listing) > 10_000_000:
            raise ValueError("Archive directory exceeds its limit.")
        started = False
        for line in listing.splitlines():
            if line.startswith("----------"):
                started = True
            if started and line.startswith("Path = "):
                path = line[7:]
                win = PureWindowsPath(path)
                if win.drive or path.startswith(("/", "\\")) or ".." in win.parts or ":" in path:
                    raise ValueError("Unsafe engineering archive member.")
            if line.startswith(("Symbolic Link = ", "Hard Link = ")):
                raise ValueError("Archive links are unsupported.")
        output.mkdir(parents=True, exist_ok=True)
        with (output / "extract.log").open("wb") as log:
            subprocess.run([str(binary), "x", "-y", "-bd", "-o" + str(output), str(source)], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        binaries = list(output.rglob("kicad-cli.exe"))
    if len(binaries) != 1:
        raise ValueError("Expected one native engineering executable.")
    register(name, binaries[0], pins[name]["version"])


def blender(offline):
    wheels = _allowed_path(BASE / "blender-wheels")
    target = _allowed_path(BASE / "blender-python")
    lock_path = BASE / "blender-wheels-lock.json"
    wheels.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PIP_CACHE_DIR=str(_allowed_path(ROOT / "runtime/pip-cache")))
    if not offline:
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels), "bpy==5.2.3"], env=env, check=True, timeout=300)
        from pip._vendor.packaging.utils import parse_wheel_filename
        records = []
        for path in sorted(wheels.glob("*.whl")):
            name, version, _, _ = parse_wheel_filename(path.name)
            release = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30))
            item = next(x for x in release["urls"] if x["filename"] == path.name)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item["digests"]["sha256"]:
                raise ValueError("Blender dependency checksum mismatch.")
            records.append({"filename": path.name, "name": str(name), "version": str(version), "sha256": digest, "url": item["url"]})
        lock_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    records = json.loads(lock_path.read_text(encoding="utf-8"))
    paths = []
    for item in records:
        path = _allowed_path(wheels / item["filename"])
        if path.parent != wheels or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Cached Blender wheel checksum mismatch.")
        paths.append(str(path))
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--upgrade", "--target", str(target), *paths], env=env, check=True, timeout=180)
    manifest_path = BASE / "installed.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("python_engines", {})["blender"] = {"version": "5.2.3", "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Blender Python engine cached and registered.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", choices=["blender", "qgis", "kicad", "all"], default="all")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.pack in {"blender", "all"}:
        blender(args.offline)
    for name in ("qgis", "kicad"):
        if args.pack in {name, "all"}:
            native(name, args.offline)


if __name__ == "__main__":
    main()
