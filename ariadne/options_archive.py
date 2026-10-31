"""Bounded immutable local quote catalogs, streamed without stitching price blobs.

Hashes establish which supplied bytes were studied, not price authenticity.
Every iterator reopens and verifies the manifest and each chunk before yielding.
No network, database writes, inference, source execution or broker access.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .options_data import (MAX_DATA, MAX_QUOTES, MAX_SNAPSHOTS, OptionsDataError,
                           SYMBOLS, clock, load_dataset, number,
                           _date, _source, _text, research_symbol_set)

MAX_MANIFEST = 256 * 1024
MAX_CHUNKS = 96
MAX_ARCHIVE = 512 * 1024 * 1024
TOP_KEYS = {"schema", "as_of", "evidence_kind", "symbol", "source", "selection", "limitations", "chunks"}
CHUNK_KEYS = {"path", "sha256", "bytes", "snapshots", "quotes", "first_observed_at", "last_observed_at"}
SELECTION_KEYS = {"start", "end", "max_dte", "reason"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise OptionsDataError(f"Archive {label} is outside its bounded range.")


def _digest(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        raise OptionsDataError("Archive SHA-256 must be lowercase hexadecimal.")


def _relative_path(value):
    # Reuse the reviewed workspace path vocabulary without resolving/accessing
    # anything here. ToolRegistry._path also verifies actual ancestors on read.
    from .tools import ToolRegistry
    ToolRegistry._parts(None, value)
    if "\\" in value:
        raise OptionsDataError("Archive paths use canonical workspace-relative forward slashes.")
    return value


def parse_manifest(raw, *, research_symbols=None):
    """Parse a bounded exact contract; never fill, repair or reorder metadata."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MANIFEST:
        raise OptionsDataError("Archive manifests require 1 byte-256 KiB of JSON.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OptionsDataError("Duplicate archive JSON keys are rejected.")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise OptionsDataError("Archive manifest must be bounded JSON with unique keys.") from None
    if type(value) is not dict or set(value) != TOP_KEYS or type(value["schema"]) is not int or value["schema"] != 1:
        raise OptionsDataError("Use the exact schema-1 options archive contract.")
    allowed = research_symbol_set(research_symbols)
    if type(value["symbol"]) is not str or value["symbol"] not in allowed:
        raise OptionsDataError("Archive symbol is not supported.")
    if type(value["evidence_kind"]) is not str or value["evidence_kind"] not in {"historical_quotes", "synthetic_fixture"}:
        raise OptionsDataError("Archive evidence kind must be explicit.")
    now = clock()
    as_of = clock(value["as_of"])
    if as_of > now:
        raise OptionsDataError("Archive cannot be dated in the future.")
    _source(value["source"], now, as_of)
    selection = value["selection"]
    if type(selection) is not dict or set(selection) != SELECTION_KEYS:
        raise OptionsDataError("Archive selection must be predeclared explicitly.")
    start, end = _date(selection["start"]), _date(selection["end"])
    if end < start or (end - start).days > 14000:
        raise OptionsDataError("Archive selection must be a bounded chronological interval.")
    if selection["max_dte"] is not None:
        _integer(selection["max_dte"], 0, 1096, "maximum DTE")
    _text(selection["reason"], "archive selection reason", 2000)
    limitations = value["limitations"]
    if type(limitations) is not list or not 1 <= len(limitations) <= 24:
        raise OptionsDataError("Archive requires bounded explicit limitations.")
    for item in limitations:
        _text(item, "archive limitation", 2000)
    chunks = value["chunks"]
    if type(chunks) is not list or not 1 <= len(chunks) <= MAX_CHUNKS:
        raise OptionsDataError("An archive requires 1-96 chunks.")
    total, seen, previous = 0, set(), None
    for chunk in chunks:
        if type(chunk) is not dict or set(chunk) != CHUNK_KEYS:
            raise OptionsDataError("Archive chunk metadata must match the exact contract.")
        path = _relative_path(chunk["path"])
        # Reject case aliases even when validated on a case-sensitive platform.
        if path.casefold() in seen:
            raise OptionsDataError("Archive chunk paths must be unique.")
        seen.add(path.casefold())
        _digest(chunk["sha256"])
        _integer(chunk["bytes"], 1, MAX_DATA, "chunk bytes")
        _integer(chunk["snapshots"], 1, MAX_SNAPSHOTS, "chunk snapshots")
        _integer(chunk["quotes"], 1, MAX_QUOTES, "chunk quote count")
        first, last = clock(chunk["first_observed_at"]), clock(chunk["last_observed_at"])
        if first > last or previous is not None and first <= previous:
            raise OptionsDataError("Archive chunk intervals must be unique and strictly chronological.")
        previous = last
        total += chunk["bytes"]
    if total > MAX_ARCHIVE:
        raise OptionsDataError("Archive exceeds the 512 MiB total bound.")
    return value


def _read_file(registry, relative, maximum, *, expected_bytes=None, expected_sha256=None):
    path = registry._path(relative)
    with registry._directory(path.parent) as directory_fd:
        fd = registry._open_read(path, directory_fd)
        with os.fdopen(fd, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if not 1 <= size <= maximum or expected_bytes is not None and size != expected_bytes:
                raise OptionsDataError("Archive file size changed or exceeds its bound.")
            raw = stream.read(maximum + 1)
    if len(raw) != size or len(raw) > maximum:
        raise OptionsDataError("Archive file changed while being read.")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise OptionsDataError("Archive file does not match its accepted SHA-256.")
    return raw


def load_manifest(registry, path, *, expected_manifest_sha256=None, research_symbols=None):
    """Return exact supplied bytes and parsed metadata under workspace guards."""
    _relative_path(path)
    if expected_manifest_sha256 is not None:
        _digest(expected_manifest_sha256)
    raw = _read_file(registry, path, MAX_MANIFEST, expected_sha256=expected_manifest_sha256)
    return raw, parse_manifest(raw, research_symbols=research_symbols)


def _datasets(registry, manifest, checkpoint=None, *, research_symbols=None):
    from .qqq_data import exchange_day, session_dates
    expected = session_dates(_date(manifest["selection"]["start"]), _date(manifest["selection"]["end"]))
    if not expected:
        raise OptionsDataError("Archive selection contains no exchange sessions.")
    cursor, last_available = 0, None
    for chunk in manifest["chunks"]:
        if checkpoint is not None:
            checkpoint()
        raw = _read_file(registry, chunk["path"], MAX_DATA,
                         expected_bytes=chunk["bytes"], expected_sha256=chunk["sha256"])
        if checkpoint is not None:
            checkpoint()
        dataset = load_dataset(raw, research_symbols=research_symbols)
        if (dataset["source"] != manifest["source"] or dataset["as_of"] != manifest["as_of"]
                or dataset["evidence_kind"] != manifest["evidence_kind"]):
            raise OptionsDataError("Archive chunks must retain exactly the declared source and evidence basis.")
        snapshots = dataset["snapshots"]
        quote_count = sum(len(snapshot["quotes"]) for snapshot in snapshots)
        if (len(snapshots) != chunk["snapshots"] or quote_count != chunk["quotes"]
                or clock(snapshots[0]["observed_at"]) != clock(chunk["first_observed_at"])
                or clock(snapshots[-1]["observed_at"]) != clock(chunk["last_observed_at"])):
            raise OptionsDataError("Archive chunk content does not match its declared coverage.")
        for snapshot in snapshots:
            day = exchange_day(clock(snapshot["observed_at"]))
            available = clock(snapshot["available_at"])
            if snapshot["underlying_symbol"] != manifest["symbol"] or cursor >= len(expected) or day != expected[cursor]:
                raise OptionsDataError("Archive requires every selected exchange session once, in chronological order.")
            if last_available is not None and available <= last_available:
                raise OptionsDataError("Archive publication times must strictly advance across chunks.")
            maximum = manifest["selection"]["max_dte"]
            if maximum is not None and any((_date(q["expiry"]) - day).days > maximum for q in snapshot["quotes"]):
                raise OptionsDataError("Archive contains contracts outside its predeclared DTE scope.")
            cursor += 1
            last_available = available
        if checkpoint is not None:
            checkpoint()
        yield dataset
    if cursor != len(expected):
        raise OptionsDataError("Archive is missing selected exchange sessions; no gap is silently repaired.")


def iter_archive(registry, path, *, expected_manifest_sha256=None, checkpoint=None, research_symbols=None):
    """Yield one fully checked canonical dataset at a time; no resident merge.

    Consumers must exhaust the iterator successfully before publishing results.
    A later missing/corrupt chunk must invalidate the entire attempted study.
    """
    _, manifest = load_manifest(registry, path, expected_manifest_sha256=expected_manifest_sha256, research_symbols=research_symbols)
    yield from _datasets(registry, manifest, checkpoint, research_symbols=research_symbols)


def inspect_archive(registry, path, *, expected_manifest_sha256=None, checkpoint=None, research_symbols=None):
    """Fully verify supplied bytes once and return compact reproducibility facts."""
    raw, manifest = load_manifest(registry, path, expected_manifest_sha256=expected_manifest_sha256, research_symbols=research_symbols)
    zeros = {"zero_bid_quotes":0, "zero_ask_quotes":0, "missing_bid_size_quotes":0,
             "missing_ask_size_quotes":0, "zero_bid_size_quotes":0, "zero_ask_size_quotes":0}
    snapshots = quotes = 0
    for dataset in _datasets(registry, manifest, checkpoint, research_symbols=research_symbols):
        for snapshot in dataset["snapshots"]:
            snapshots += 1
            for quote in snapshot["quotes"]:
                quotes += 1
                zeros["zero_bid_quotes"] += number(quote["bid"]) == 0
                zeros["zero_ask_quotes"] += number(quote["ask"]) == 0
                for name in ("bid_size", "ask_size"):
                    zeros["missing_" + name + "_quotes"] += quote[name] is None
                    zeros["zero_" + name + "_quotes"] += quote[name] == 0
    return {"schema":1, "manifest_sha256":hashlib.sha256(raw).hexdigest(),
            "manifest_bytes":len(raw), "manifest":manifest, "symbol":manifest["symbol"],
            "chunks":manifest["chunks"], "chunk_count":len(manifest["chunks"]),
            "bytes":sum(chunk["bytes"] for chunk in manifest["chunks"]),
            "snapshots":snapshots, "quotes":quotes,
            "first_observed_at":manifest["chunks"][0]["first_observed_at"],
            "last_observed_at":manifest["chunks"][-1]["last_observed_at"],
            "quote_time_basis":manifest["source"]["quote_time_basis"],
            "evidence_kind":manifest["evidence_kind"], "source_independently_verified":False,
            "source":manifest["source"], "selection":manifest["selection"],
            "limitations":manifest["limitations"], "quote_quality_counts":zeros,
            "session_gaps":0, "corporate_action_coverage":"Standard 100-share contracts only; adjusted deliverables are unsupported; source actions are not independently reconciled.",
            "execution_qualified":False, "forward_eligible":False}
