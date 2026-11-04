"""Pure briefing preflight and bounded, message-free failure diagnostics.

No services, databases, collectors or models are constructed by this module.
Private input artifacts are published separately by the caller, never replayed.
"""
from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

_PHASES = frozenset({"collection", "cache", "repository", "activity", "snapshots",
                     "venture", "render", "validate", "publish", "receipt", "input"})
_ROOT = Path(__file__).absolute().parents[1]


def failure_detail(error, *, phase):
    """Keep useful application frames, never exception text, locals or causes."""
    cls = type(error)
    name = cls.__name__
    trusted_type = cls.__module__ == "builtins" and getattr(builtins, name, None) is cls
    frames = []
    current = error.__traceback__
    while current is not None:
        code = current.tb_frame.f_code
        # Pure lexical comparison: diagnostics never open traceback paths.
        filename = Path(code.co_filename)
        if filename.is_absolute() and filename.is_relative_to(_ROOT):
            relative = filename.relative_to(_ROOT)
            safe_path = relative.as_posix()
            if (relative.parts[0] in {"ariadne", "scripts"} and ".." not in relative.parts
                    and len(safe_path) <= 240 and re.fullmatch(r"[A-Za-z0-9_./-]+", safe_path)):
                function = code.co_name
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}|<module>", function):
                    function = "function"
                frames.append({"file": relative.as_posix(), "function": function,
                               "line": current.tb_lineno})
                frames = frames[-8:]
        current = current.tb_next
    return {"schema": 1, "phase": phase if phase in _PHASES else "unknown",
            "exception_type": name if trusted_type else "Error", "frames": frames,
            "scope": "Application frames only; exception text, arguments, locals and causes are excluded."}


def render_preflight(report):
    """Render one saved report in memory. Never collect, claim or advance work.

    The receipt describes a preview, not a completed scheduled daily run.
    Publication is the CLI's responsibility, to a new exclusive folder.
    """
    from .briefing import BriefingService, MAX_BYTES, _json
    receipt = {"schema": 1, "kind": "offline_briefing_preflight", "ok": False,
               "status": "failed", "model_calls": 0, "network_calls": 0,
               "workflow_calls": 0, "artifacts": [], "input_sha256": None}
    phase = "input"
    try:
        if type(report) is not dict:
            raise ValueError("A saved report object is required.")
        saved = _json(report)
        if len(saved) > MAX_BYTES:
            raise ValueError("Saved input exceeds the report limit.")
        receipt["input_sha256"] = hashlib.sha256(saved).hexdigest()
        # Detach the renderer's input so a future renderer cannot mutate its caller.
        phase = "render"
        outputs = BriefingService._render(json.loads(saved))
        phase = "validate"
        if type(outputs) is not dict or set(outputs) != {"brief.json", "brief.md", "brief.svg"}:
            raise ValueError("Three complete report formats are required.")
        if any(type(value) is not bytes or not value or len(value) > MAX_BYTES for value in outputs.values()):
            raise ValueError("Invalid bounded report artifacts.")
        if json.loads(outputs["brief.json"]) != report:
            raise ValueError("Rendered JSON must preserve the saved facts.")
        outputs["brief.md"].decode("utf-8", errors="strict")
        svg = ET.fromstring(outputs["brief.svg"])
        from .brief_poster import MAX_SVG_BYTES
        if svg.tag != "{http://www.w3.org/2000/svg}svg" or len(outputs["brief.svg"]) > MAX_SVG_BYTES:
            raise ValueError("Invalid bounded SVG poster.")
        receipt["artifacts"] = [{"path": name, "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, data in outputs.items()]
        receipt.update(ok=True, status="completed")
        return outputs, receipt
    except Exception as error:
        receipt["failure_detail"] = failure_detail(error, phase=phase)
        return {}, receipt
