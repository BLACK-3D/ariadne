"""Discover fixed local report procedures without arbitrary code execution."""
from __future__ import annotations

NAMES = {"local_procedure"}


class ProcedureTools:
    def __init__(self, registry):
        self.registry, self.store, self._service = registry, None, None

    def service(self):
        if self.store is None:
            raise ValueError("Local procedure history requires the application store.")
        if self._service is None:
            from .procedures import Procedures
            self._service = Procedures(self.registry, self.store)
        return self._service

    def schemas(self):
        from .tools import _schema
        return [_schema("local_procedure", "Discover or run fixed local report workflows: readiness, stock rotation, power budget, rebuilding projects, reference audit, business snapshot, knowledge inventory and offline checklist. A fixed procedure makes zero model calls, reads local records and writes unique workspace reports. It does not contact services or control hardware. Use catalog for exact IDs and outputs. Reuse the same request_id only to retrieve the same completed invocation, never to repeat uncertain work.", {
            "action": {"type": "string", "enum": ["catalog", "run", "history"]},
            "procedure_id": {"type": "string", "maxLength": 64}, "request_id": {"type": "string", "maxLength": 64}}, ["action"])]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown procedure tool.")
        return "auto"

    def status(self):
        return {"ready": True, "offline": True, "reason": "Fixed local reports with verified artifacts and durable receipts; no model calls inside the procedure."}

    def execute(self, name, args, *, cancelled=None):
        from .tools import _object
        if name not in NAMES:
            raise ValueError("Unknown procedure tool.")
        _object(args, {"action", "procedure_id", "request_id"}, {"action"})
        if cancelled and cancelled():
            raise ValueError("Procedure cancelled before starting.")
        action = args["action"]
        if action == "catalog":
            return {"ok": True, **self.service().catalog()}
        if action == "history":
            return {"ok": True, "runs": self.service().history()["runs"][:10]}
        if action == "run":
            return {"ok": True, "run": self.service().run(args.get("procedure_id"), args.get("request_id"))}
        raise ValueError("Unknown procedure action.")
