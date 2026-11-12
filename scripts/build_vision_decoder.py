"""Explicitly build the small offline FFmpeg adapter inside this installation.

Requires only this box's CPython 3.13; the pinned portable compiler includes make
and a POSIX shell. --download authorizes fetching the two pinned upstream inputs.
Compiler/build caches are not included in portable exports. Matching unmodified
FFmpeg source and the complete build recipe are retained in the vision pack.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.provision_vision import catalog, local, digest, fetch, verify_asset, publish
from ariadne.config import _allowed_path


def safe_name(name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\\" in name or any(p in {".", ".."} or ":" in p for p in parts):
        raise ValueError("Unsafe decoder build archive member")


def install_files(root, work, value):
    source = work / "source/ffmpeg-9.0.2"
    compiler = work / "toolchain/llvm-mingw-20261104-ucrt-x86_64"
    originals = {"ffmpeg/ffmpeg.exe": source / "ffmpeg.exe", "ffmpeg/COPYING.LGPLv2.1": source / "COPYING.LGPLv2.1",
        "ffmpeg/LICENSE.md": source / "LICENSE.md", "ffmpeg/LICENSE.LLVM.txt": compiler / "LICENSE.TXT",
        "ffmpeg/COPYING.MinGW-w64-runtime.txt": compiler / "x86_64-w64-mingw32/share/mingw32/COPYING.MinGW-w64-runtime.txt"}
    originals["ffmpeg/SOURCE.txt"] = value["decoder_build"]["source_notice"].encode()
    for item in value["native_files"]:
        raw = originals[item["path"]]
        payload = raw if type(raw) is bytes else _allowed_path(raw).read_bytes()
        import hashlib
        if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("Rebuilt decoder or notice differs from the reviewed seal: " + item["path"])
        target = local(root, "runtime/vision/" + item["path"])
        if target.exists():
            if digest(target) != item["sha256"]:
                raise FileExistsError("A different adapter or notice exists; inspect it before replacing")
        else:
            publish(target, payload)


def build(*, root=ROOT, download=False):
    root = _allowed_path(root)
    if os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info[:2] != (3, 13):
        raise ValueError("Use Windows x64 CPython 3.13 inside this Ariadne installation")
    value = catalog(root)
    inputs = value["decoder_build"]["inputs"]
    paths = {item["role"]: (fetch if download else verify_asset)(root, item) for item in inputs}
    work = local(root, "runtime/vision/build")
    if work.exists():
        raise FileExistsError("Decoder build directory exists; inspect it before retrying or repairing")
    work.mkdir(parents=True)
    with zipfile.ZipFile(paths["compiler"]) as archive:
        total = 0
        for member in archive.infolist():
            safe_name(member.filename)
            if stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("Unsupported compiler archive member")
            total += member.file_size
            if total > 2 * 1024**3:
                raise ValueError("Compiler archive exceeds its bound")
        archive.extractall(local(root, "runtime/vision/build/toolchain"))
    with tarfile.open(paths["source"]) as archive:
        members = archive.getmembers()
        if sum(item.size for item in members) > 512 * 1024**2:
            raise ValueError("FFmpeg source exceeds its bound")
        for member in members:
            safe_name(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError("FFmpeg source contains an unsupported link or device")
        archive.extractall(local(root, "runtime/vision/build/source"), filter="data")
    toolchain = work / "toolchain/llvm-mingw-20261104-ucrt-x86_64"
    shell = _allowed_path(toolchain / "busybox/bin/sh.exe")
    # The script accepts a single ordinary path; no shell interpolation is used.
    script = local(root, "scripts/build_vision_decoder.sh")
    env = dict(os.environ)
    temporary = local(root, "runtime/vision/build/tmp")
    temporary.mkdir()
    env.update(TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    log = local(root, "runtime/vision/build/build.log")
    with log.open("xb") as output:
        result = subprocess.run([str(shell), str(script).replace("\\", "/"), str(work).replace("\\", "/")],
            cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Decoder build failed; inspect runtime/vision/build/build.log. No automatic retry.")
    binary = _allowed_path(work / "source/ffmpeg-9.0.2/ffmpeg.exe")
    expected = value["decoder"]
    if binary.stat().st_size != expected["bytes"] or digest(binary) != expected["sha256"]:
        raise ValueError("Built decoder differs from the reviewed binary; independent qualification is required before use")
    install_files(root, work, value)
    target = local(root, "runtime/vision/" + expected["path"])
    return {"built": True, "path": str(target), "sha256": expected["sha256"], "bytes": expected["bytes"], "network_used": download}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(build(download=args.download), indent=2))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        parser.exit(1, "Decoder build stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
