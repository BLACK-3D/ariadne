"""Install a verified offline vision add-on against this starter's shipped seal.

Validate the complete archive before writes. Publish runtime files exclusively,
with the readiness manifest last. Never install incoming source, owner data or
unknown files. An interrupted partial installation remains unavailable for review.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import __version__
from ariadne.atomic import commit_staged
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry
from scripts.export_vision import PUBLIC_SOURCE_FILES
from scripts.provision_vision import catalog, assets, local, digest, SEALED, exact_runtime_inventory
from scripts.refresh_offline_source import member_name


def expected_files(root):
    value = catalog(root)
    source_path = local(root, SEALED)
    raw = source_path.read_bytes()
    source = json.loads(raw)
    result = {"runtime/vision/" + item["path"]: {"bytes": item["bytes"], "sha256": item["sha256"]}
        for item in source["runtime"]["files"]}
    result.update({item["path"]: {"bytes": item["bytes"], "sha256": item["sha256"]} for item in assets(value)})
    result["runtime/vision/manifest.json"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    for name in PUBLIC_SOURCE_FILES:
        path = local(root, name)
        result[name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return result


def install(archive_path, sha256, *, root=ROOT):
    root = _allowed_path(root)
    path = _allowed_path(archive_path)
    if type(sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Supply the published SHA-256 as 64 lowercase hexadecimal characters")
    if __version__ != "0.40.0":
        raise ValueError("This vision pack requires its matching Ariadne 0.40.0 starter")
    expected = expected_files(root)
    guard = object.__new__(ToolRegistry)
    with guard._directory(path.parent) as directory:
        with os.fdopen(guard._open_read(path, directory), "rb") as raw:
            if hashlib.file_digest(raw, "sha256").hexdigest() != sha256:
                raise ValueError("Vision archive does not match the supplied SHA-256")
            raw.seek(0)
            with zipfile.ZipFile(raw) as archive:
                infos = archive.infolist()
                if len(infos) != len(expected) + 1 or len(infos) > 2500:
                    raise ValueError("Vision pack contains missing or extra members")
                seen = set()
                index = {}
                for item in infos:
                    name = member_name(item.filename)
                    if (name.casefold() in seen or item.orig_filename != item.filename or item.flag_bits & 1
                            or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
                            or item.external_attr & 0x10 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                        raise ValueError("Vision pack has duplicate, linked, encrypted or unsupported members")
                    seen.add(name.casefold())
                    index[name] = item
                    if name == "VISION-PACK-MANIFEST.json":
                        if item.file_size > 2 * 1024**2:
                            raise ValueError("Vision pack metadata exceeds its bound")
                        continue
                    if name not in expected or item.file_size != expected[name]["bytes"]:
                        raise ValueError("Vision member differs from the shipped inventory: " + name)
                    with archive.open(item) as source:
                        if hashlib.file_digest(source, "sha256").hexdigest() != expected[name]["sha256"]:
                            raise ValueError("Vision member differs from the shipped hash seal: " + name)
                if set(index) != set(expected) | {"VISION-PACK-MANIFEST.json"}:
                    raise ValueError("Vision pack has private, extra or missing paths")
                manifest = json.loads(archive.read("VISION-PACK-MANIFEST.json"))
                declared = {item["path"]: {"bytes": item["bytes"], "sha256": item["sha256"]} for item in manifest["files"]}
                if (manifest.get("schema") != 1 or manifest.get("profile") != "ariadne-offline-vision-v1"
                        or manifest.get("application_version") != __version__ or manifest.get("private_state_included") is not False
                        or declared != expected or len(manifest["files"]) != len(expected)):
                    raise ValueError("Vision pack metadata differs from the shipped seal")
                names = [name for name in expected if name.startswith("runtime/vision/")]
                # Validate all existing destinations before creating any directory or file.
                exact_runtime_inventory(root, expected)
                for name in names:
                    target = local(root, name)
                    if target.exists() and (target.stat().st_size != expected[name]["bytes"] or digest(target) != expected[name]["sha256"]):
                        raise FileExistsError("An uncertain vision asset exists; inspect it before installation: " + name)
                names.sort(key=lambda name: (name == "runtime/vision/manifest.json", name))
                installed = 0
                for name in names:
                    target = local(root, name)
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = local(root, name + ".install-" + secrets.token_hex(8) + ".partial")
                    sha = hashlib.sha256()
                    with guard._directory(target.parent):
                        with temporary.open("xb") as output, archive.open(index[name]) as source:
                            while block := source.read(1024 * 1024):
                                sha.update(block)
                                output.write(block)
                            output.flush()
                            os.fsync(output.fileno())
                        if sha.hexdigest() != expected[name]["sha256"]:
                            raise ValueError("Archive changed during installation; inspect the partial file")
                        commit_staged(temporary, local(root, name))
                    installed += 1
    return {"installed": True, "files_written": installed, "private_state_included": False,
        "camera_opened": False, "network_used": False, "owner_settings_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.archive, args.sha256), indent=2))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        parser.exit(1, "Vision installation stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
