"""Stage only the accepted tiny public notices; no downloads or binary changes.

Run after provisioning native runtimes and before a factory export. Existing
different files are refused, so this command cannot overwrite an owner file.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.atomic import publish_bytes
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry
from scripts.export_offline import _small, _source_notice_files, _runtime_notice_files


def stage(root=ROOT):
    root = _allowed_path(root)
    catalog, _, _ = _source_notice_files(root)
    payloads = {"runtime/" + item["path"]: _small(root / "config/runtime-notices" / item["path"])
                for item in catalog["assets"]}
    for scope in ("ollama", "whisper"):
        value = {"schema": 1, "assets": [item for item in catalog["assets"] if item["path"].startswith(scope + "/")]}
        relative = "runtime/" + scope + "/NOTICE-PROVENANCE.json"
        target = _allowed_path(root / relative)
        # Harmless JSON formatting differences do not require a rewrite.
        payloads[relative] = (_small(target) if target.exists() and json.loads(_small(target)) == value
                             else (json.dumps(value, indent=2) + "\n").encode("utf-8"))
    for name, payload in payloads.items():
        target = _allowed_path(root / name)
        if target.exists() and _small(target) != payload:
            raise ValueError("An existing runtime notice differs; no files were replaced.")
    created = []
    guard = object.__new__(ToolRegistry)
    for name, payload in payloads.items():
        target = _allowed_path(root / name)
        if not target.exists():
            with guard._directory(target.parent, create=True):
                publish_bytes(target, payload)
            created.append(name)
    _runtime_notice_files(root)
    return {"created": created, "verified_notice_files": len(catalog["assets"]), "network_used": False,
            "native_binaries_changed": False}


if __name__ == "__main__":
    print(json.dumps(stage(), indent=2))
