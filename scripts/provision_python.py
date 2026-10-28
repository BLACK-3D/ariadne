"""Build a relocatable Windows Python runtime from this installation, offline."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path


def copy_file(source, target):
    source, target = _allowed_path(source), _allowed_path(target)
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("Portable Python accepts regular source files only.")
    if target.exists() and (not stat.S_ISREG(target.stat().st_mode) or target.stat().st_nlink != 1):
        raise ValueError("Portable Python destinations must be unlinked regular files.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest():
        return
    shutil.copyfile(source, target)


def main():
    if os.name != "nt" or sys.version_info[:2] != (3, 13):
        raise SystemExit("This portable runtime profile requires Windows x64 Python 3.13.")
    source = _allowed_path(sys.base_prefix)
    target = _allowed_path(ROOT / "runtime/python")
    if source == target:
        raise SystemExit("Use the original installation's Python to rebuild its portable copy.")
    target.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and (path.name in {"python.exe", "pythonw.exe", "LICENSE.txt"} or path.suffix.lower() == ".dll"):
            copy_file(path, target / path.name)
    for name in ("Lib", "DLLs"):
        for directory, folders, files in os.walk(source / name, followlinks=False):
            folders[:] = [x for x in folders if x not in {"site-packages", "__pycache__", "test", "tests", "idlelib", "turtledemo"}]
            for folder in folders:
                _allowed_path(Path(directory) / folder)
            for filename in files:
                if filename.endswith((".pyc", ".pyo")):
                    continue
                path = Path(directory) / filename
                copy_file(path, target / path.relative_to(source))
    executable = target / "python.exe"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", TEMP=str(_allowed_path(ROOT / "runtime/tmp")), TMP=str(_allowed_path(ROOT / "runtime/tmp")))
    subprocess.run([str(executable), "-I", "-m", "ensurepip", "--default-pip"], env=env, cwd=ROOT, check=True, timeout=120)
    links = []
    for folder in ("runtime/wheels", "runtime/downloads/mcp", "runtime/ocr/wheels"):
        links += ["--find-links", str(_allowed_path(ROOT / folder))]
    requirements = []
    for name in ("bootstrap", "server", "documents", "analytics", "mcp", "ocr"):
        requirements += ["-r", str(ROOT / f"requirements-{name}.txt")]
    subprocess.run([str(executable), "-I", "-m", "pip", "install", "--no-index", "--require-hashes", *links, *requirements], cwd=ROOT, env=env, check=True, timeout=180)
    probe = "import sys,importlib,json; names=['os','sqlite3','ssl','waitress','pypdf','duckdb','fastmcp','rapidocr']; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':sys.version.split()[0],'modules':{n:importlib.import_module(n).__file__ for n in names}}))"
    proof = json.loads(subprocess.check_output([str(executable), "-I", "-c", probe], cwd=ROOT, env=env, timeout=60, text=True))
    if any(Path(proof[key]).resolve() != target for key in ("prefix", "base_prefix")) or any(not Path(value).resolve().is_relative_to(target) for value in proof["modules"].values()):
        raise ValueError("Portable interpreter or dependencies resolved outside the bundled runtime.")
    manifest = {"python": proof["version"], "origin": "Copy of the active installed Python standard library and DLLs; core packages rebuilt from checksum-pinned local wheels.", "runtime_downloads": False, "sha256": {}}
    for directory, folders, files in os.walk(target, followlinks=False):
        folders[:] = [x for x in folders if x != "__pycache__"]
        for filename in files:
            path = _allowed_path(Path(directory) / filename)
            if path.name == "PORTABLE-MANIFEST.json" or path.suffix == ".pyc":
                continue
            with path.open("rb") as stream:
                manifest["sha256"][path.relative_to(target).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    (target / "PORTABLE-MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Portable Python qualified; standard library and core dependencies resolve within Yikes.")


if __name__ == "__main__":
    main()
