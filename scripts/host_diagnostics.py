"""Bounded startup evidence without exception messages or ambient private data."""
from __future__ import annotations

import builtins
from pathlib import Path
import re

_BUILTIN_ERRORS = frozenset(value for value in vars(builtins).values()
    if isinstance(value, type) and issubclass(value, BaseException))
_OS_ERRORS = frozenset(value for value in _BUILTIN_ERRORS if issubclass(value, OSError))

STAGES = frozenset({"initialization", "stop_watcher", "request", "environment", "lock", "identity", "interpreter", "independence",
    "stop_event", "service_job", "status", "log", "application", "shutdown", "cleanup"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
_SOURCE = re.compile(r"(?:ariadne|scripts)/[A-Za-z0-9_./-]{1,180}\.py\Z")


def failure_detail(error, stage, *, root):
    """Return safe structural evidence only; never stringify an exception.

    Frames are restricted to ASCII application-relative filenames. We do not
    load source, capture locals or inspect exception arguments, chained errors,
    environment, command lines, identity fields or external-library paths.
    """
    kind = type(error)
    name = kind.__name__ if kind in _BUILTIN_ERRORS else "Exception"
    result = {"schema": 1, "stage": stage if type(stage) is str and stage in STAGES else "unknown",
              "error_type": name,
              "action": "Inspect this private attempt; no automatic restart was attempted."}
    for field in ("errno", "winerror"):
        value = getattr(error, field, None) if kind in _OS_ERRORS else None
        if type(value) is int and -(2**31) <= value < 2**31:
            result[field] = value
    frames = []
    trace = error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        try:
            filename = Path(trace.tb_frame.f_code.co_filename).absolute().relative_to(root).as_posix()
        except (ValueError, OSError):
            filename = ""
        if _SOURCE.fullmatch(filename) and ".." not in Path(filename).parts:
            function = trace.tb_frame.f_code.co_name
            frames.append({"file": filename, "line": trace.tb_lineno,
                           "function": function if _IDENTIFIER.fullmatch(function) else "<frame>"})
        trace = trace.tb_next
    result["application_frames"] = frames[-8:]
    result["trace_scan_truncated"] = trace is not None
    return result
