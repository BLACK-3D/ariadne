"""Provision the pinned official Windows runtime and a model on the project disk.

Network is needed once. Does not install a service, alter PATH, or touch E:.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path, local_service_port
VERSION = "0.40.12"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip"
SHA256 = "21814944f3e50d3f07b6bb3af75baddb96f59e91353c332ac9c5063b3bfd1041"
MODEL_PINS = {"qwen3.5:9b": "56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90"}


def download_runtime():
    if os.name != "nt":
        raise SystemExit("This provisioner is for Windows x64. Use Compose or the official Ollama installer on other platforms.")
    runtime = _allowed_path(ROOT / "runtime" / "ollama")
    archive = _allowed_path(ROOT / "runtime" / "downloads" / f"ollama-{VERSION}.zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        partial = archive.with_suffix(".part")
        print(f"Downloading official Ollama {VERSION} (~1.49 GB) to {archive.parent}", flush=True)
        request = urllib.request.Request(URL, headers={"User-Agent": "Ariadne-Provision/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            partial.replace(archive)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    with archive.open("rb") as downloaded:
        digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
    if digest != SHA256:
        raise SystemExit("Runtime checksum mismatch. Refusing to extract or execute it.")
    marker = runtime / ".archive-sha256"
    if marker.exists() and marker.read_text().strip() == SHA256 and (runtime / "ollama.exe").is_file():
        return runtime / "ollama.exe"
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if sum(item.file_size for item in bundle.infolist()) > 15_000_000_000:
            raise SystemExit("Unexpected runtime archive size")
        for item in bundle.infolist():
            target = (runtime / item.filename).resolve()
            if not target.is_relative_to(runtime.resolve()) or "\\" in item.filename or ":" in item.filename:
                raise SystemExit("Unsafe archive member")
        bundle.extractall(runtime)
    marker.write_text(SHA256, encoding="ascii")
    print("Runtime archive checksum verified and extracted.", flush=True)
    return runtime / "ollama.exe"


def local_env():
    env = os.environ.copy()
    data = _allowed_path(ROOT / "data")
    _allowed_path(data / "models")
    _allowed_path(data / "tmp")
    (data / "models").mkdir(parents=True, exist_ok=True)
    (data / "tmp").mkdir(parents=True, exist_ok=True)
    profile = _allowed_path(data / "ollama-home")
    profile.mkdir(parents=True, exist_ok=True)
    env.update({"OLLAMA_MODELS": str(data / "models"), "OLLAMA_TMPDIR": str(data / "tmp"),
                "TMP": str(data / "tmp"), "TEMP": str(data / "tmp"), "OLLAMA_NO_CLOUD": "1",
                "OLLAMA_HOST": f"127.0.0.1:{local_service_port('ARIADNE_MODEL_PORT', 11434)}", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
                "HOME": str(profile), "USERPROFILE": str(profile), "APPDATA": str(profile), "LOCALAPPDATA": str(profile)})
    return env


def api(path):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(f"http://127.0.0.1:{local_service_port('ARIADNE_MODEL_PORT', 11434)}" + path, timeout=3) as response:
        return json.load(response)


def owned_model_server(executable):
    """Check the recorded process's executable, not merely an open API port."""
    pidfile = _allowed_path(ROOT / "data" / "ollama.pid")
    try:
        pid = int(pidfile.read_text().strip())
        if os.name != "nt":
            return False
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            return bool(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length))) and Path(buffer.value).resolve() == executable.resolve()
        finally:
            kernel.CloseHandle(handle)
    except (OSError, ValueError):
        return False


def start_model_server(executable, env, allow_owned=False, on_process=None, create_process=None):
    port = local_service_port("ARIADNE_MODEL_PORT", 11434)
    env = {**env, "OLLAMA_HOST": f"127.0.0.1:{port}"}
    try:
        api("/api/version")
    except Exception:
        log = _allowed_path(ROOT / "data" / "ollama.log")
        with log.open("ab") as output:
            process = (create_process or subprocess.Popen)([str(executable), "serve"], env=env, cwd=ROOT, stdout=output, stderr=output,
                                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if on_process:
            on_process(process)
        for _ in range(60):
            try:
                api("/api/version")
                if process.poll() is not None:
                    raise SystemExit("The new Ollama process exited; the previous PID record was preserved.")
                (ROOT / "data" / "ollama.pid").write_text(str(process.pid), encoding="ascii")
                print(f"Local-only Ollama service ready on 127.0.0.1:{port}", flush=True)
                return
            except Exception:
                if process.poll() is not None:
                    raise SystemExit("Ollama exited. Inspect data/ollama.log.")
                time.sleep(1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise SystemExit("Ollama did not become ready in 60 seconds. Previous PID record preserved; inspect data/ollama.log.")
    else:
        # The legacy foreground PID file does not identify a TCP listener's
        # port. Never use that weaker record to adopt an explicitly selected
        # alternate port. Independent hosts already prohibit all adoption.
        if allow_owned and "ARIADNE_MODEL_PORT" not in os.environ and owned_model_server(executable):
            return
        raise SystemExit(f"Port {port} already has a model server. Refusing to change its storage or offline policy. Stop it or use that server explicitly.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    if not args.model or args.model.startswith("-") or len(args.model) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._-/" for c in args.model):
        raise SystemExit("Invalid model name")
    executable = download_runtime()
    if args.runtime_only:
        return
    env = local_env()
    start_model_server(executable, env, allow_owned=True)
    print(f"Provisioning model {args.model} into {env['OLLAMA_MODELS']}", flush=True)
    with (ROOT / "data" / "model-pull.log").open("wb") as log:
        subprocess.run([str(executable), "pull", args.model], env=env, cwd=ROOT, stdout=log, stderr=log, check=True)
    models = api("/api/tags").get("models", [])
    if args.model in MODEL_PINS:
        selected = [model for model in models if model.get("name") == args.model]
        if len(selected) != 1 or selected[0].get("digest") != MODEL_PINS[args.model]:
            raise SystemExit("Installed model tag differs from the qualified digest. It was not selected; review and qualify the new upstream model before updating this pin.")
    inventory = {"runtime_version": VERSION, "runtime_archive_sha256": SHA256, "models": models,
                 "model_directory": env["OLLAMA_MODELS"], "offline_policy": "OLLAMA_NO_CLOUD=1"}
    (ROOT / "data" / "provision-manifest.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print("Provisioning complete. Start Ariadne with start.ps1. No internet is needed for local inference.", flush=True)


if __name__ == "__main__":
    main()
