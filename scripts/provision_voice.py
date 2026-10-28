"""Install pinned offline Windows speech recognition without recording audio.

Downloads come from whisper.cpp's release and its officially linked model repo.
Both files must match pinned SHA-256 digests before anything is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from ariadne.config import _allowed_path


WHISPER_VERSION = "b5454"
WHISPER_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip"
WHISPER_SHA256 = "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.en.bin"
MODEL_SHA256 = "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"


def _official_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and (hostname in {"github.com", "huggingface.co", "hf.co"}
                 or hostname.endswith((".githubusercontent.com", ".huggingface.co", ".hf.co"))))


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not _official_url(new_url):
            raise ValueError("Download redirected outside the official HTTPS asset hosts.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _regular(path: Path) -> bool:
    path = _allowed_path(path)
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Provisioned files must be regular files with a single link.")
    return True


def _digest(path: Path) -> str:
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(url: str, destination: Path, expected_hash: str, max_bytes: int) -> Path:
    destination = _allowed_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _regular(destination) and _digest(destination) == expected_hash:
        print(f"Verified cached {destination.name}", flush=True)
        return destination
    if not _official_url(url):
        raise ValueError("Downloads must originate from an official HTTPS asset host.")
    temporary = _allowed_path(destination.with_name(destination.name + ".part-" + secrets.token_hex(8)))
    digest = hashlib.sha256()
    received = 0
    opener = urllib.request.build_opener(OfficialRedirects())
    print(f"Downloading {destination.name} (maximum {max_bytes // 1_000_000} MB)...", flush=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Ariadne-local-provisioner/1"})
        with opener.open(request, timeout=60) as response, open(temporary, "xb") as target:
            if not _official_url(response.url):
                raise ValueError("Unexpected download origin.")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError("Download exceeded its fixed size limit.")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"SHA-256 verification failed for {destination.name}; download was not installed.")
        _allowed_path(destination)
        os.replace(temporary, destination)
        print(f"Verified {destination.name}: {received:,} bytes, SHA-256 {expected_hash}", flush=True)
        return destination
    finally:
        if temporary.exists():
            _allowed_path(temporary).unlink()


def _member_path(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or PureWindowsPath(name).drive or ":" in name:
        raise ValueError("Archive contains an unsafe path.")
    parts = PurePosixPath(name).parts
    if not parts or any(part in {".", ".."} or part.endswith((".", " ")) for part in parts):
        raise ValueError("Archive contains an ambiguous or escaping path.")
    for part in parts:
        base = part.split(".", 1)[0].upper()
        if (any(ord(char) < 32 or char in '<>"|?*' for char in part)
                or base in {"CON", "PRN", "AUX", "NUL"}
                or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", base)):
            raise ValueError("Archive contains an unsupported Windows path.")
    return parts


def extract_verified(archive_path: Path, destination: Path) -> Path:
    archive_path, destination = _allowed_path(archive_path), _allowed_path(destination)
    if not _regular(archive_path) or _digest(archive_path) != WHISPER_SHA256:
        raise ValueError("The whisper.cpp archive must match the pinned digest before extraction.")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(info.file_size for info in entries) > 200_000_000:
            raise ValueError("Archive exceeds the extraction limits.")
        checked = []
        names = set()
        for info in entries:
            parts = _member_path(info.filename)
            normalized = "/".join(parts).casefold()
            if normalized in names:
                raise ValueError("Archive contains duplicate paths.")
            names.add(normalized)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1 or info.file_size > 80_000_000:
                raise ValueError("Archive contains an unsupported member or oversized file.")
            target = _allowed_path(destination.joinpath(*parts))
            target.relative_to(destination)
            checked.append((info, target))
        expected_files = {target.relative_to(destination).as_posix().casefold() for info, target in checked if not info.is_dir()}
        # Extra executable/DLL files from an unrelated installation must not be
        # available for Windows DLL search when the verified executable starts.
        for existing in destination.rglob("*"):
            existing = _allowed_path(existing)
            if existing.is_file() and existing.relative_to(destination).as_posix().casefold() not in expected_files:
                raise ValueError("The versioned whisper directory contains unexpected files; choose a clean project runtime directory.")
        for info, target in checked:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _regular(target)
            temporary = _allowed_path(target.with_name(target.name + ".part-" + secrets.token_hex(8)))
            try:
                with archive.open(info) as source, open(temporary, "xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if temporary.stat().st_size != info.file_size:
                    raise ValueError("Extracted archive member has an unexpected size.")
                _allowed_path(target)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    _allowed_path(temporary).unlink()
    candidates = [path for path in destination.rglob("whisper-cli.exe") if _regular(path)]
    if len(candidates) != 1:
        raise ValueError("The verified archive does not contain exactly one whisper-cli.exe.")
    return _allowed_path(candidates[0])


def provision(root: Path) -> dict:
    root = _allowed_path(root)
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("This pinned provisioner supports Windows x64; other platforms can configure whisper.cpp manually.")
    runtime = _allowed_path(root / "runtime")
    data = _allowed_path(os.environ.get("ARIADNE_DATA_DIR", str(root / "data")))
    downloads = _allowed_path(runtime / "downloads")
    binary_root = _allowed_path(runtime / "whisper" / WHISPER_VERSION)
    model_path = _allowed_path(data / "voice" / "ggml-base.en.bin")
    config_path = _allowed_path(data / "voice-config.json")
    archive = download(WHISPER_URL, downloads / "whisper-bin-x64-b5454.zip", WHISPER_SHA256, 15_000_000)
    binary = extract_verified(archive, binary_root)
    model = download(MODEL_URL, model_path, MODEL_SHA256, 180_000_000)
    print("Checking the verified whisper-cli.exe --help (no audio recording)...", flush=True)
    result = subprocess.run([str(binary), "--help"], cwd=binary.parent, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False, timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 or b"usage" not in result.stdout.lower():
        raise ValueError("The verified whisper.cpp executable failed its --help check.")
    config = {"whisper_bin": str(binary), "whisper_model": str(model)}
    if _regular(config_path):
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or set(existing) - {"whisper_bin", "whisper_model", "piper_bin", "piper_model"}:
            raise ValueError("Existing voice-config.json has an unsupported format.")
        config = {**existing, **config}
    temporary = _allowed_path(config_path.with_name("voice-config.json.part-" + secrets.token_hex(8)))
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as output:
            json.dump(config, output, indent=2)
            output.write("\n")
        _allowed_path(config_path)
        os.replace(temporary, config_path)
    finally:
        if temporary.exists():
            _allowed_path(temporary).unlink()
    print(json.dumps({"ok": True, "config": str(config_path), **config, "recording_started": False}, indent=2), flush=True)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision verified offline whisper.cpp speech recognition for Windows x64.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Ariadne project directory; E: is prohibited.")
    args = parser.parse_args()
    try:
        provision(args.root)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        print(f"Voice provisioning failed: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
