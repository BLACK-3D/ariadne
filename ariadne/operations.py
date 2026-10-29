"""Owner-facing fixed local operations; no generic tool or device-action bypass."""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading

from .config import _allowed_path

_ACTIONS = {
    "business_ops": frozenset({"snapshot", "audit_erp", "draft_quote"}),
    "market_research": frozenset({"audit", "study"}),
    "home_control": frozenset({"catalog", "status", "import_snapshot", "propose", "receipt"}),
}
_EXAMPLES = {
    "market": ("market_research", {"action": "study"}, ("market-demo.csv", "market-demo.json")),
    "business": ("business_ops", {"action": "audit_erp"}, ("business-erpnext-demo.json",)),
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class OperationsDesk:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.lock = threading.Lock()

    def catalog(self):
        return {"offline": True, "model_calls": 0,
                "tools": [{"id": name, "actions": sorted(actions)} for name, actions in _ACTIONS.items()],
                "examples": sorted(_EXAMPLES),
                "notice": "Local reports and saved telemetry. Device changes use the mission approval flow. Example data is synthetic."}

    def _cancelled(self):
        return bool(self.store.setting("paused", False))

    def run(self, value):
        if type(value) is not dict:
            raise ValueError("An operation must be an object.")
        if self._cancelled():
            raise ValueError("Autonomy is paused; local operations are held.")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Another desk operation is active; wait for its receipt.")
        try:
            if "example" in value:
                return self._example(value)
            if (set(value) != {"tool", "arguments"} or type(value["tool"]) is not str
                    or value["tool"] not in _ACTIONS or type(value["arguments"]) is not dict):
                raise ValueError("Select a supported local operation.")
            name, args = value["tool"], value["arguments"]
            if type(args.get("action")) is not str or args["action"] not in _ACTIONS[name] or self.registry.policy(name, args) != "auto":
                raise ValueError("This action requires the mission approval flow.")
            return self.registry.execute(name, args, cancelled=self._cancelled)
        finally:
            self.lock.release()

    def _example(self, value):
        if set(value) != {"example", "request_id"} or type(value["example"]) is not str or value["example"] not in _EXAMPLES:
            raise ValueError("Choose a supported illustrative example.")
        key = value["request_id"]
        if type(key) is not str or not _ID.fullmatch(key):
            raise ValueError("Example request ID must use 1–64 letters, digits, underscores or hyphens.")
        tool, parameters, names = _EXAMPLES[value["example"]]
        base = "operation-examples/" + key
        folder = self.registry._path(base)
        if folder.exists():
            raise ValueError("This example ID already exists; inspect the earlier output instead of retrying it.")
        # Read fixed public fixtures, never paths supplied by model or browser.
        source = _allowed_path(Path(__file__).resolve().parent / "resources/operations")
        contents = {}
        with self.registry._directory(source) as directory:
            for name in names:
                with os.fdopen(self.registry._open_read(source / name, directory), "r", encoding="utf-8") as stream:
                    text = stream.read(100_001)
                if len(text) > 100_000:
                    raise ValueError("Illustrative input exceeds its fixed size limit.")
                contents[name] = text
        if self._cancelled():
            raise ValueError("Operation cancelled before creating example inputs.")
        for name, text in contents.items():
            self.registry._file_write({"path": base + "/" + name, "content": text})
        args = {**parameters, "request_id": key}
        if value["example"] == "market":
            args.update(path=base + "/" + names[0], metadata_path=base + "/" + names[1])
        else:
            args.update(path=base + "/" + names[0], as_of="2026-10-29")
        result = self.registry.execute(tool, args, cancelled=self._cancelled)
        return {**result, "example": True, "example_input_folder": base,
                "example_notice": "Synthetic demonstration only; not owner records or market performance evidence."}
