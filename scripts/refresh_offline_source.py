"""Refresh audited source in a complete verified appliance ZIP, without extraction.

This is a full ZIP rewrite, never a delta. Every old member, including removed
or replaced source, is rehashed and CRC checked. Native/model bytes are copied
exactly. Runtime recipes must remain identical; reprovision instead if they
change. No network, runtime traversal, service launch or owner-state migration.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import time
import tomllib
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.atomic import commit_staged, publish_bytes
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry
from scripts.export_offline import (SOURCE_FILES, RUNTIME_DIRS, RUNTIME_MANIFESTS,
    OPTIONAL_PACKAGE_BINS, PUBLIC_MODEL_TAGS, NOTICE_ASSETS, EXCLUDED)

MAX_MEMBERS = 500_000
MAX_PAYLOAD = 512 * 1024**3
MAX_MANIFEST = 128 * 1024**2
MAX_SOURCE_FILE = 64 * 1024**2
MAX_SOURCE_PAYLOAD = 512 * 1024**2
PUBLIC_CATALOG_SHA256 = "dd3db7b191f4f49edfe7a5452e4b1abc44fc6f4717552ebf0573519e408eb00a"
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SOURCE_ROOTS = ("ariadne/", "scripts/", "tests/", "docs/", "config/")
_ROOT_FILES = frozenset(SOURCE_FILES)


def require(value, message):
    if not value:
        raise ValueError(message)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key.")
        result[key] = value
    return result


def _json(raw):
    def reject(value):
        raise ValueError("Nonfinite JSON metadata.")
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=reject)


def member_name(name):
    require(type(name) is str and 0 < len(name) <= 4096 and not PureWindowsPath(name).drive
            and not name.startswith("/") and "\\" not in name, "Unsafe ZIP member path.")
    parts = name.split("/")
    for part in parts:
        require(part not in {"", ".", ".."} and not part.endswith((" ", ".")), "Unsafe Windows path component.")
        require(len(part.encode("utf-16-le")) // 2 <= 255, "Windows path component exceeds limit.")
        require(not any(ord(char) < 32 or char in '<>:"|?*' for char in part), "Invalid Windows filename.")
        stem = part.split(".", 1)[0].rstrip(" ")
        require(not re.fullmatch(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])", stem, re.I),
                "Reserved Windows filename.")
    return name


def _key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _names(infos, *, source):
    require(1 < len(infos) <= MAX_MEMBERS, "ZIP member-count limit exceeded.")
    names, canonical = {}, set()
    for info in infos:
        require(info.orig_filename == info.filename, "ZIP filename was truncated or normalized.")
        name = member_name(info.filename)
        key = _key(name)
        require(key not in canonical, "Duplicate or case-colliding ZIP member.")
        canonical.add(key)
        require(not info.flag_bits & 1 and info.compress_type in
                ({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} if source else {zipfile.ZIP_STORED}),
                "Encrypted or unsupported ZIP compression.")
        require(stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG} and not info.external_attr & 0x10,
                "ZIP members must be regular files.")
        require(type(info.file_size) is int and 0 <= info.file_size <= (MAX_SOURCE_FILE if source else MAX_PAYLOAD),
                "ZIP member size exceeds limit.")
        names[name] = info
    for name in names:
        parts = name.split("/")
        for length in range(1, len(parts)):
            require(_key("/".join(parts[:length])) not in canonical, "File/directory ZIP prefix collision.")
    return names


def _source_member(name):
    admitted = name.startswith(_SOURCE_ROOTS) or name in _ROOT_FILES or bool(re.fullmatch(r"requirements[^/]*\.txt", name))
    return admitted and not name.casefold().endswith((".exe", ".dll", ".pyd", ".so", ".dylib", ".whl", ".zip", ".7z",
        ".gguf", ".safetensors", ".onnx", ".bin", ".pem", ".key", ".db", ".sqlite", ".sqlite3"))


def _private(name):
    parts = name.casefold().split("/")
    return (any(part in {".git", ".venv", "__pycache__", ".cache", "backups", "browser-profile",
                         "browser-profiles", "user-data", ".env", ".netrc", ".npmrc.private"} for part in parts)
            or name.casefold().endswith((".token", ".env", ".pyc", ".pyo", ".partial", ".part", ".log")))


def _runtime_member(name):
    if name == "data/voice/ggml-base.en.bin" or name.startswith("data/models/"):
        return True
    if not name.startswith("runtime/") or _private(name):
        return False
    if name.startswith("runtime/automation/node-red/"):
        return name.startswith("runtime/automation/node-red/node_modules/") or name in {
            "runtime/automation/node-red/package.json", "runtime/automation/node-red/package-lock.json"}
    for directory in OPTIONAL_PACKAGE_BINS:
        if name.startswith(directory + "/"):
            return name == "runtime/knowledge/python/bin/monty.exe"
    if name.endswith("/direct_url.json") and name.startswith(("runtime/automation/python/", "runtime/production/python/", "runtime/production/blender-python/")):
        return False
    if any(name.startswith(directory + "/") for directory in RUNTIME_DIRS if directory.startswith("runtime/")):
        return True
    if re.match(r"runtime/automation/node-v[0-9][^/]*/", name):
        return True
    return any(name == directory + "/" + leaf for directory, leaves in RUNTIME_MANIFESTS.items() for leaf in leaves)


def _compatible_recipe(name):
    # Be conservative: new/changed utility scripts need an explicit audit unless
    # they are QA or this source-only refresh helper itself.
    if name.startswith("scripts/"):
        return not (name.startswith("scripts/check_") or name == "scripts/refresh_offline_source.py")
    return (name.startswith("config/") or bool(re.fullmatch(r"requirements[^/]*\.txt", name))
            or name in _ROOT_FILES - {"README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml"})


def _small(archive, infos, name, cap=MAX_MANIFEST):
    require(name in infos and infos[name].file_size <= cap, "Required bounded metadata is missing or oversized.")
    with archive.open(infos[name]) as stream:
        raw = stream.read(cap + 1)
    require(len(raw) == infos[name].file_size and len(raw) <= cap, "Bounded metadata size differs.")
    return raw


def _whole(stream):
    stream.seek(0)
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
    stream.seek(0)
    return digest


def _identity(stream):
    info = os.fstat(stream.fileno())
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _confined(value, root):
    path = _allowed_path(value)
    require(path.is_relative_to(root), "All inputs and outputs must remain inside the workspace.")
    member_name(path.relative_to(root).as_posix())
    return path


def _open(stack, path, root, guard):
    path = _confined(path, root)
    parent = stack.enter_context(guard._directory(path.parent))
    stream = stack.enter_context(os.fdopen(guard._open_read(path, parent), "rb"))
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Inputs must be unlinked regular files.")
    return stream


def _sidecar(stack, path, root, guard, cap):
    stream = _open(stack, path, root, guard)
    raw = stream.read(cap + 1)
    require(len(raw) <= cap, "Archive sidecar exceeds its bound.")
    return raw


def _archive_inputs(stack, path, root, guard, *, receipt):
    path = _confined(path, root)
    require(path.suffix.lower() == ".zip" and ".partial" not in path.name.casefold(), "A complete ZIP is required, never a partial.")
    require(path.name.isascii(), "ASCII archive names are required by the checksum sidecar format.")
    stream = _open(stack, path, root, guard)
    before = _identity(stream)
    expected = _sidecar(stack, path.with_suffix(".zip.sha256"), root, guard, 4096).decode("ascii").strip()
    require(re.fullmatch(r"[a-f0-9]{64}  " + re.escape(path.name), expected) is not None, "Invalid archive checksum sidecar.")
    digest = _whole(stream)
    require(expected == digest + "  " + path.name, "Whole-archive SHA256 differs from sidecar.")
    saved = _json(_sidecar(stack, path.with_suffix(".zip.receipt.json"), root, guard, 64_000)) if receipt else None
    if saved is not None:
        require(type(saved) is dict and saved.get("path") == path.name and saved.get("sha256") == digest
                and type(saved.get("bytes")) is int and saved["bytes"] == before[2], "Archive receipt SHA256/size differs.")
    archive = stack.enter_context(zipfile.ZipFile(stream))
    infos = _names(archive.infolist(), source=not receipt)
    return archive, infos, saved, digest, stream, before


def _box(archive, infos, receipt):
    manifest = _json(_small(archive, infos, "BOX-MANIFEST.json"))
    keys = {"schema", "application_version", "created_utc", "format", "payload_bytes", "default_model",
            "public_model_tags", "private_state_included", "excluded_categories", "files", "source_refresh"}
    require(type(manifest) is dict and type(manifest.get("schema")) is int and manifest["schema"] == 1
            and manifest.get("private_state_included") is False and manifest.get("default_model") == "qwen3.5:9b"
            and manifest.get("format") == "ZIP64/ZIP_STORED" and not set(manifest) - keys
            and type(manifest.get("public_model_tags")) is list and len(manifest["public_model_tags"]) == 3
            and set(manifest["public_model_tags"]) == PUBLIC_MODEL_TAGS, "Unknown/private appliance schema.")
    rows = manifest.get("files")
    require(type(rows) is list and len(rows) + 1 == len(infos), "BOX manifest count differs.")
    expected = {}
    for row in rows:
        require(type(row) is dict and set(row) == {"path", "bytes", "sha256"}, "Malformed BOX member pin.")
        name = member_name(row["path"])
        require(name not in expected and name != "BOX-MANIFEST.json" and type(row["bytes"]) is int
                and 0 <= row["bytes"] <= MAX_PAYLOAD and type(row["sha256"]) is str and _HASH.fullmatch(row["sha256"]),
                "Invalid/duplicate BOX member pin.")
        require((_source_member(name) and not _private(name)) or _runtime_member(name), "Unknown or private appliance member.")
        expected[name] = row
    require(set(infos) == set(expected) | {"BOX-MANIFEST.json"}, "BOX member set differs.")
    payload = sum(row["bytes"] for row in rows)
    require(type(manifest.get("payload_bytes")) is int and manifest["payload_bytes"] == payload <= MAX_PAYLOAD,
            "BOX payload size differs or exceeds limit.")
    require(type(receipt.get("files")) is int and receipt["files"] == len(infos)
            and type(receipt.get("payload_bytes")) is int and receipt["payload_bytes"] == payload
            and receipt.get("manifest") == "BOX-MANIFEST.json", "BOX receipt member counts differ.")
    for name, row in expected.items():
        require(infos[name].file_size == row["bytes"], "BOX member byte size differs.")
    return manifest, expected


def _source(archive, infos):
    manifest = _json(_small(archive, infos, "SOURCE-MANIFEST.json"))
    require(type(manifest) is dict and set(manifest) == {"version", "sha256"}
            and type(manifest["version"]) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)?", manifest["version"]),
            "Unknown source manifest/version.")
    pins = manifest["sha256"]
    require(type(pins) is dict and pins and set(infos) == set(pins) | {"SOURCE-MANIFEST.json"}, "Source manifest member set differs.")
    for name, digest in pins.items():
        member_name(name)
        require(_source_member(name) and not _private(name), "Private or non-source replacement member.")
        require(type(digest) is str and _HASH.fullmatch(digest), "Unknown source SHA256 pin.")
    require(sum(infos[name].file_size for name in pins) <= MAX_SOURCE_PAYLOAD, "Source payload limit exceeded.")
    return manifest["version"], {name: {"path": name, "bytes": infos[name].file_size, "sha256": digest} for name, digest in pins.items()}


def _version(archive, infos, expected):
    raw = _small(archive, infos, "ariadne/__init__.py", 16_384).decode("utf-8")
    matches = re.findall(r"^__version__\s*=\s*[\"']([^\"']+)[\"']\s*$", raw, flags=re.M)
    require(matches == [expected], "Packaged application version differs from manifest.")
    project = tomllib.loads(_small(archive, infos, "pyproject.toml", 64_000).decode("utf-8"))
    require(type(project.get("project")) is dict and project["project"].get("version") == expected, "Project version differs from source manifest.")


def _project_recipe(archive, infos):
    project = tomllib.loads(_small(archive, infos, "pyproject.toml", 64_000).decode("utf-8"))
    require(type(project.get("project")) is dict, "Missing project metadata.")
    project["project"].pop("version", None)
    return project


def _runtime_pins(archive, infos, rows):
    raw = _small(archive, infos, "config/public-models.json", 256_000)
    require(hashlib.sha256(raw).hexdigest() == PUBLIC_CATALOG_SHA256, "Unknown public-model catalog pins; reprovision and audit instead.")
    catalog = _json(raw)
    require(type(catalog.get("schema")) is int and catalog["schema"] == 1 and catalog.get("default_model") == "qwen3.5:9b"
            and type(catalog.get("models")) is list and len(catalog["models"]) == 3
            and {item["tag"] for item in catalog["models"]} == PUBLIC_MODEL_TAGS, "Invalid public-model catalog.")
    approved = {}
    for item in catalog["models"]:
        for record in [item["manifest"], *item["metadata"], *({"path": "data/models/blobs/sha256-" + blob["sha256"], **blob} for blob in item["blobs"])]:
            name = member_name(record["path"])
            require(name.startswith("data/models/") and name in rows and type(record["bytes"]) is int
                    and rows[name]["bytes"] == record["bytes"] and rows[name]["sha256"] == record["sha256"], "Missing/changed public model pin.")
            require(name not in approved or approved[name] == record, "Conflicting public model pin.")
            approved[name] = record
    require({name for name in rows if name.startswith("data/models/")} == set(approved), "Unreviewed model member in base archive.")
    notices = _json(_small(archive, infos, "config/runtime-notices/catalog.json", 256_000))
    require(type(notices) is dict and type(notices.get("schema")) is int and notices["schema"] == 1
            and type(notices.get("assets")) is list and len(notices["assets"]) == len(NOTICE_ASSETS)
            and {item["path"] for item in notices["assets"]} == NOTICE_ASSETS, "Missing/unknown runtime notice pins.")
    for item in notices["assets"]:
        for prefix in ("runtime/", "config/runtime-notices/"):
            name = prefix + item["path"]
            require(name in rows and type(item["bytes"]) is int and rows[name]["bytes"] == item["bytes"]
                    and rows[name]["sha256"] == item["sha256"], "Required runtime notice pin is absent or changed.")
    for scope in ("ollama", "whisper"):
        provenance = _json(_small(archive, infos, "runtime/" + scope + "/NOTICE-PROVENANCE.json", 256_000))
        require(provenance == {"schema": 1, "assets": [item for item in notices["assets"] if item["path"].startswith(scope + "/")]},
                "Runtime notice provenance differs from accepted source pins.")
    portable = _json(_small(archive, infos, "runtime/python/PORTABLE-MANIFEST.json"))
    hashes = portable.get("sha256") if type(portable) is dict else None
    require(type(hashes) is dict and hashes, "Missing portable interpreter pins.")
    for name, digest in hashes.items():
        member_name(name)
        target = "runtime/python/" + name
        require(type(digest) is str and _HASH.fullmatch(digest) and target in rows and rows[target]["sha256"] == digest,
                "Portable interpreter pin is absent or changed.")
    for name in ("runtime/python/python.exe", "runtime/ollama/ollama.exe", "runtime/whisper/b5454/Release/whisper-cli.exe",
                 "runtime/knowledge/python/bin/monty.exe", "data/voice/ggml-base.en.bin", "config/box-profiles.json",
                 "runtime/ollama/LICENSE", "runtime/whisper/LICENSE.whisper.cpp"):
        require(name in rows, "Required cached runtime/notices are absent.")
    profile = _json(_small(archive, infos, "config/box-profiles.json", 1_000_000))
    require(type(profile.get("schema")) is int and profile["schema"] == 1 and profile.get("default_model") == "qwen3.5:9b", "Factory profile default/schema differs.")


def _copy(archive, info, expected, target=None):
    digest, count = hashlib.sha256(), 0
    with archive.open(info) as stream:
        while block := stream.read(1024 * 1024):
            count += len(block)
            require(count <= expected["bytes"], "Member exceeded its declared size.")
            digest.update(block)
            if target is not None:
                target.write(block)
    require(count == expected["bytes"] and digest.hexdigest() == expected["sha256"], "Member SHA256/size failed; archive was not published.")


def _entry(name):
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def refresh(base_zip, source_zip, output_zip, *, root=ROOT):
    root = _allowed_path(root)
    require(root.is_relative_to(ROOT), "Refresh workspace must remain inside Yikes.")
    base_zip, source_zip, output_zip = [_confined(path, root) for path in (base_zip, source_zip, output_zip)]
    require(len({_key(str(path)) for path in (base_zip, source_zip, output_zip)}) == 3, "Archive input/output aliases are excluded.")
    require(output_zip.suffix.lower() == ".zip" and ".partial" not in output_zip.name.casefold(), "Output must be a complete .zip name.")
    require(output_zip.name.isascii(), "ASCII archive names are required by the checksum sidecar format.")
    partial = output_zip.with_suffix(".zip.partial")
    sidecars = (output_zip.with_suffix(".zip.sha256"), output_zip.with_suffix(".zip.receipt.json"))
    for path in (output_zip, partial, *sidecars):
        _confined(path, root)
        require(not path.exists(), "Refresh never overwrites an archive, sidecar or prior partial.")
    guard = object.__new__(ToolRegistry)
    with ExitStack() as stack:
        base, base_infos, saved, base_hash, base_stream, base_identity = _archive_inputs(stack, base_zip, root, guard, receipt=True)
        source, source_infos, _, source_hash, source_stream, source_identity = _archive_inputs(stack, source_zip, root, guard, receipt=False)
        manifest, old_rows = _box(base, base_infos, saved)
        version, new_rows = _source(source, source_infos)
        _version(base, base_infos, manifest.get("application_version"))
        require(saved.get("application_version") == manifest.get("application_version"), "Base receipt/application version differs.")
        _version(source, source_infos, version)
        require(_project_recipe(base, base_infos) == _project_recipe(source, source_infos),
                "Project runtime/build requirements changed; reprovision instead of source refresh.")
        _runtime_pins(base, base_infos, old_rows)
        old_recipes = {name: row["sha256"] for name, row in old_rows.items() if _compatible_recipe(name)}
        new_recipes = {name: row["sha256"] for name, row in new_rows.items() if _compatible_recipe(name)}
        require(old_recipes == new_recipes, "Runtime/packaging recipes changed or are absent; reprovision instead of source refresh.")
        retained = {name: row for name, row in old_rows.items() if not _source_member(name)}
        merged = {**retained, **new_rows}
        _names([_entry(name) for name in merged] + [_entry("BOX-MANIFEST.json")], source=False)
        require(sum(row["bytes"] for row in merged.values()) <= MAX_PAYLOAD, "Refreshed payload exceeds limit.")
        with guard._directory(output_zip.parent, create=True) as output_parent:
            with partial.open("xb") as outer, zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as result:
                for name, row in old_rows.items():
                    if name in retained:
                        with result.open(_entry(name), "w", force_zip64=True) as target:
                            _copy(base, base_infos[name], row, target)
                    else:
                        _copy(base, base_infos[name], row)
                for name, row in new_rows.items():
                    with result.open(_entry(name), "w", force_zip64=True) as target:
                        _copy(source, source_infos[name], row, target)
                require(_identity(base_stream) == base_identity and _identity(source_stream) == source_identity,
                        "Input archive changed during refresh.")
                updated = {"schema": 1, "format": "ZIP64/ZIP_STORED", "application_version": version,
                    "default_model": "qwen3.5:9b", "public_model_tags": sorted(PUBLIC_MODEL_TAGS),
                    "private_state_included": False, "excluded_categories": EXCLUDED,
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "payload_bytes": sum(row["bytes"] for row in merged.values()), "files": [merged[name] for name in sorted(merged)],
                    "source_refresh": {"base_archive_sha256": base_hash, "source_archive_sha256": source_hash,
                        "retained_runtime_files": len(retained), "replaced_source_files": len(new_rows), "all_old_members_verified": True}}
                result.writestr(_entry("BOX-MANIFEST.json"), json.dumps(updated, indent=2, ensure_ascii=False))
                result.close()
                outer.flush()
                os.fsync(outer.fileno())
            commit_staged(partial, output_zip)
            with os.fdopen(guard._open_read(output_zip, output_parent), "rb") as stream:
                published_identity = _identity(stream)
                require(os.fstat(stream.fileno()).st_nlink == 1, "Published output gained an unexpected link.")
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                require(_identity(stream) == published_identity, "Output archive changed during hashing.")
            receipt = {"path": output_zip.name, "application_version": version, "bytes": published_identity[2],
                "sha256": digest, "files": len(merged) + 1, "payload_bytes": updated["payload_bytes"], "manifest": "BOX-MANIFEST.json",
                "source_refresh": updated["source_refresh"]}
            for path, payload in ((sidecars[0], (digest + "  " + output_zip.name + "\n").encode("ascii")),
                                  (sidecars[1], (json.dumps(receipt, indent=2) + "\n").encode("utf-8"))):
                publish_bytes(path, payload)
            return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.base, args.source, args.output), indent=2))


if __name__ == "__main__":
    main()
