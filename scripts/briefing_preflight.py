"""Render saved briefing JSON offline into a new private staging folder.

Uses no owner database, workflow, collector or model. Source hash is mandatory.
A successful preview does not complete or replay the scheduled daily receipt.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))

from ariadne.atomic import publish_bytes
from ariadne.briefing import MAX_BYTES, _json
from ariadne.briefing_diagnostics import render_preflight
from ariadne.config import _allowed_path
from ariadne.network_policy import install_network_policy
from ariadne.tools import ToolRegistry


def _inside(value, *, staging=False):
    relative = Path(value)
    if relative.is_absolute() or PureWindowsPath(value).drive or ".." in relative.parts:
        raise ValueError("Use a project-relative path without parent traversal.")
    if staging and (not relative.parts or relative.parts[0] != "staging" or len(relative.parts) < 2):
        raise ValueError("Preflight output must be a new folder under staging.")
    path = _allowed_path(ROOT / relative)
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError("Preflight paths must stay inside the project.")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    os.environ["ARIADNE_OFFLINE"] = "1"
    install_network_policy()
    phase, destination, receipt = "input", None, None
    claimed = False
    published = []
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", args.input_sha256):
            raise ValueError("A lowercase SHA-256 input hash is required.")
        source, destination = _inside(args.input), _inside(args.output, staging=True)
        guard = object.__new__(ToolRegistry)  # Directory primitives only; no registry or DB initialization.
        with guard._directory(source.parent) as descriptor:
            with os.fdopen(guard._open_read(source, descriptor), "rb") as stream:
                payload = stream.read(MAX_BYTES+1)
        if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != args.input_sha256:
            raise ValueError("Saved input does not match the bounded approved bytes.")
        report = json.loads(payload)
        # Claim the destination first. An interrupted attempt is never overwritten.
        with guard._directory(destination.parent, create=True):
            destination.mkdir(mode=0o700)
            claimed = True
        phase = "render"
        outputs, receipt = render_preflight(report)
        receipt["source_sha256"] = args.input_sha256
        phase = "publish"
        expected = {row["path"]: row for row in receipt["artifacts"]}
        with guard._directory(destination):
            for name, data in outputs.items():
                publish_bytes(destination/name, data)
                published.append(expected[name])
            publish_bytes(destination/"receipt.json", _json(receipt))
        print(json.dumps(receipt, ensure_ascii=True, indent=2))
        return 0 if receipt["ok"] else 2
    except (ValueError, OSError, RecursionError) as error:
        # Even validation/publish errors must not disclose local paths or input text.
        from ariadne.briefing_diagnostics import failure_detail
        receipt = {**(receipt or {}), "ok": False, "status": "failed", "artifacts": published,
                   "failure_detail": failure_detail(error, phase=phase)}
        if claimed:
            try:
                with guard._directory(destination):
                    publish_bytes(destination/"receipt.json", _json(receipt))
            except (ValueError, OSError):
                pass  # Disk failure may prevent a durable receipt; the claimed folder still blocks replay.
        print(json.dumps(receipt, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
