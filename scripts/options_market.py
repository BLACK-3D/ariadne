"""Offline options-file validation/conversion; never a broker or downloader.

All paths are relative to an existing workspace inside this box. Conversion
publishes a new canonical file exclusively and never replaces an inbox file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("validate", "convert-cboe"))
    result.add_argument("--workspace", default="data/workspace", help="Existing box-relative workspace folder.")
    result.add_argument("--input", required=True, help="Workspace-relative canonical JSON or Cboe interval CSV.")
    result.add_argument("--metadata", help="Workspace-relative source-provenance JSON; required for Cboe CSV.")
    result.add_argument("--output", help="New workspace-relative canonical JSON path; required for conversion.")
    return result


def guard_for(workspace, *, root=ROOT):
    from ariadne.config import _allowed_path
    from ariadne.tools import ToolRegistry
    root = _allowed_path(root)
    box = object.__new__(ToolRegistry)
    box.root = root
    workspace_path = box._path(workspace)
    if not workspace_path.is_dir():
        raise ValueError("The options workspace must already exist inside this box.")
    guard = object.__new__(ToolRegistry)
    guard.root = workspace_path
    return guard


def read(guard, relative, limit):
    path = guard._path(relative)
    with guard._directory(path.parent) as directory:
        with os.fdopen(guard._open_read(path, directory), "rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
    identity = lambda info:(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(raw) > limit or identity(before) != identity(after) or len(raw) != before.st_size:
        raise ValueError("The options input exceeded its bound or changed while being read.")
    return raw


def metadata(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate source metadata keys are rejected.")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite metadata is rejected.")))


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_data import MAX_DATA, canonical, clock, load_dataset, parse_cboe_csv, validate_dataset
    current = clock(now)
    guard = guard_for(args.workspace, root=root)
    raw = read(guard, args.input, MAX_DATA)
    if args.action == "validate":
        if args.metadata or args.output:
            raise ValueError("Validation does not write output or consume metadata.")
        dataset = load_dataset(raw, current)
    elif args.action == "convert-cboe":
        if not args.metadata or not args.output:
            raise ValueError("Cboe conversion needs --metadata and a new --output path.")
        source = metadata(read(guard, args.metadata, 8192))
        dataset = parse_cboe_csv(raw, source=source, as_of=current)
    else:
        raise ValueError("Unsupported offline options action.")
    if dataset["evidence_kind"] != "historical_quotes":
        raise ValueError("Synthetic fixtures cannot be imported as owner market evidence.")
    quality = validate_dataset(dataset, current)
    receipt = {"action":args.action, "input_sha256":hashlib.sha256(raw).hexdigest(), "input_bytes":len(raw),
               "quality":quality, "network_requests":0, "orders":False, "source_authenticity_established":False}
    if args.action == "convert-cboe":
        output = canonical(dataset)
        path = guard._path(args.output)
        with guard._directory(path.parent, create=True):
            publish_bytes(path, output)
        receipt.update(output=args.output, output_bytes=len(output), output_sha256=hashlib.sha256(output).hexdigest())
    return receipt


def main(argv=None):
    # Process-local only: override no user setting or persistent environment.
    os.environ["ARIADNE_OFFLINE"] = "1"
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    args = parser().parse_args(argv)
    try:
        receipt = run(args)
    except (ValueError, OSError, UnicodeError, RecursionError, OverflowError) as error:
        print("Options import rejected: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
