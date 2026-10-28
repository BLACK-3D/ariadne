"""Windows portable-runtime identity and opt-in cross-folder qualification."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from ariadne.config import _allowed_path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime/python"
RECEIPT = ROOT / "staging/portable-python-relocation-proof.json"

PROBE = r'''
import importlib,json,pathlib,struct,sys
app_root=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(app_root))
names=['os','json','sqlite3','_sqlite3','ssl','_ssl','waitress','pypdf','duckdb','fastmcp','rapidocr','onnxruntime','numpy','PIL','cryptography','ariadne.server','ariadne.worker']
modules={name:str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}
print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable,
                 'base_executable':sys._base_executable,'version':sys.version.split()[0],
                 'pointer_bytes':struct.calcsize('P'),'path':sys.path,
                 'isolated':sys.flags.isolated,'ignore_environment':sys.flags.ignore_environment,
                 'no_user_site':sys.flags.no_user_site,'modules':modules},sort_keys=True))
'''


def qualified_runtime():
    return os.name == "nt" and (RUNTIME / "python.exe").is_file() and (RUNTIME / "PORTABLE-MANIFEST.json").is_file()


def probe(runtime, temporary):
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC") if key in os.environ}
    # Poison Python's optional search environment with a nonexistent directory
    # and remove the original user's AppData/PATH context. -I must ignore it.
    environment.update({"PATH": str(runtime) + os.pathsep + str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
                        "TEMP": str(temporary), "TMP": str(temporary), "HOME": str(temporary),
                        "USERPROFILE": str(temporary), "APPDATA": str(temporary), "LOCALAPPDATA": str(temporary),
                        "PYTHONHOME": str(temporary / "unusable base"), "PYTHONPATH": str(temporary / "unusable modules"),
                        "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    process = subprocess.run([str(runtime / "python.exe"), "-I", "-B", "-c", PROBE, str(ROOT)],
                             cwd=temporary, env=environment, capture_output=True, text=True,
                             encoding="utf-8", timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.returncode:
        raise AssertionError("Portable runtime import probe failed: " + process.stderr[-4000:])
    return json.loads(process.stdout.splitlines()[-1])


def copy_checked_tree(source, destination):
    """Copy only regular files/directories; never traverse a reparse point."""
    source, destination = _allowed_path(source), _allowed_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    files = total = 0
    with os.scandir(source) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Portable relocation refuses linked/reparse source entries.")
            if stat.S_ISDIR(info.st_mode):
                if entry.name == "__pycache__":
                    continue
                count, size = copy_checked_tree(source / entry.name, destination / entry.name)
                files += count
                total += size
            elif stat.S_ISREG(info.st_mode):
                original = _allowed_path(source / entry.name)
                target = _allowed_path(destination / entry.name)
                shutil.copyfile(original, target)
                files += 1
                total += info.st_size
            else:
                raise ValueError("Portable relocation accepts regular source files only.")
    return files, total


@unittest.skipUnless(qualified_runtime(), "Qualified portable Windows Python has not been built")
class PortablePythonTests(unittest.TestCase):
    def assert_self_contained(self, result, runtime):
        runtime = runtime.resolve()
        for key in ("prefix", "base_prefix"):
            self.assertEqual(Path(result[key]).resolve(), runtime, result)
        for key in ("executable", "base_executable"):
            self.assertTrue(Path(result[key]).resolve().is_relative_to(runtime), result)
        self.assertEqual(result["pointer_bytes"], 8)
        self.assertEqual((result["isolated"], result["ignore_environment"], result["no_user_site"]), (1, 1, 1))
        manifest = json.loads((RUNTIME / "PORTABLE-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(result["version"], manifest["python"])
        for name, filename in result["modules"].items():
            path = Path(filename)
            self.assertTrue(path.is_relative_to(ROOT if name.startswith("ariadne.") else runtime), (name, filename))
            self.assertNotIn(".venv", path.parts)
        for value in result["path"]:
            path = Path(value).resolve()
            self.assertTrue(path == ROOT or path.is_relative_to(runtime), value)

    def test_current_runtime_prefix_stdlib_and_core_native_imports(self):
        with tempfile.TemporaryDirectory(prefix=".portable-probe-", dir=ROOT / "staging") as directory:
            result = probe(RUNTIME, Path(directory))
        self.assert_self_contained(result, RUNTIME)
        manifest = json.loads((RUNTIME / "PORTABLE-MANIFEST.json").read_text(encoding="utf-8"))
        for name in ("python.exe", "python313.dll", "Lib/os.py", "DLLs/_ssl.pyd", "DLLs/_sqlite3.pyd"):
            self.assertEqual(hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest(), manifest["sha256"][name])

    @unittest.skipUnless(os.environ.get("ARIADNE_TEST_RELOCATION") == "1", "Set ARIADNE_TEST_RELOCATION=1 for the full runtime-copy smoke test")
    def test_relocated_runtime_with_spaces_is_self_contained(self):
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=".portable-relocation-", dir=ROOT / "staging") as directory:
            temporary = Path(directory)
            relocated = temporary / "another PC with spaces" / "runtime" / "python"
            files, size = copy_checked_tree(RUNTIME, relocated)
            copied = time.perf_counter()
            result = probe(relocated, temporary)
            self.assert_self_contained(result, relocated)
            receipt = {"qualified": True, "scope": "Runtime-only relocation; app modules intentionally imported from the project source.",
                       "copied_files": files, "copied_bytes": size, "copy_seconds": round(copied - started, 3),
                       "probe_seconds": round(time.perf_counter() - copied, 3), "identity": result,
                       "entrypoint": "python.exe -I -m / fixed script; generated Scripts console launchers are not relocation entrypoints."}
            RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
