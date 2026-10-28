"""Owner-only MCP registration. Discovery evidence never grants tool authority."""
from __future__ import annotations

import hashlib
import re
from functools import wraps

from .mcp_tools import canonical, read_json, write_json, validate_tool, validate_catalog, _server, MAX_SELECTED


def _mutating(operation):
    @wraps(operation)
    def guarded(self, *args, **kwargs):
        with self.registry.mutation():
            return operation(self, *args, **kwargs)
    return guarded


class MCPAdmin:
    def __init__(self, registry):
        self.registry = registry

    def overview(self):
        return {"status": self.registry.status(), "servers": self.registry.config()["servers"],
                "tools": self.registry.descriptions()}

    @_mutating
    def add(self, data):
        if type(data) is not dict or set(data) - {"id", "url", "mode", "headers"}:
            raise ValueError("Use a server id, loopback HTTP URL, explicit protocol mode and optional credential references.")
        proposed = {**data, "tools": []}
        _server(proposed)
        config = self.registry.config()
        if len(config["servers"]) >= 4 or any(s["id"] == proposed["id"] for s in config["servers"]):
            raise ValueError("Choose a unique id; at most four local MCP servers may be registered.")
        config["servers"].append(proposed)
        validate_catalog(config)
        write_json(self.registry.path, config)
        return {"ok": True}

    @_mutating
    def remove(self, server_id):
        config = self.registry.config()
        if not any(s["id"] == server_id for s in config["servers"]):
            raise KeyError("Unknown MCP server")
        config["servers"] = [s for s in config["servers"] if s["id"] != server_id]
        write_json(self.registry.path, config)
        return {"ok": True}

    def _snapshot_path(self, server_id):
        if not isinstance(server_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", server_id):
            raise ValueError("Invalid server id.")
        return self.registry.settings.data_dir / "mcp-discovery" / (server_id + ".json")

    @_mutating
    def discover(self, server_id):
        snapshot = self.registry.discover(server_id)
        raw = write_json(self._snapshot_path(server_id), snapshot)
        tools = []
        for metadata in snapshot["tools"]:
            try:
                validate_tool(metadata)
                supported, reason = True, "Supported bounded schema"
            except ValueError:
                supported, reason = False, "Tool metadata/schema is outside Ariadne's supported subset"
            tools.append({"metadata": metadata, "supported": supported, "reason": reason})
        return {"snapshot_sha256": hashlib.sha256(raw).hexdigest(), "identity": snapshot["identity"], "tools": tools}

    @_mutating
    def select(self, server_id, data):
        if type(data) is not dict or set(data) != {"snapshot_sha256", "selections"}:
            raise ValueError("Select tools from a reviewed discovery fingerprint.")
        selections = data["selections"]
        if type(selections) is not list or len(selections) > MAX_SELECTED:
            raise ValueError("Selected tool count exceeds its limit.")
        snapshot, raw = read_json(self._snapshot_path(server_id))
        if data["snapshot_sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("Discovery evidence changed. Discover and review again.")
        config = self.registry.config()
        server = next((s for s in config["servers"] if s["id"] == server_id), None)
        if server is None:
            raise KeyError("Unknown MCP server")
        if canonical(snapshot.get("server")) != canonical({k: v for k, v in server.items() if k not in {"tools", "identity"}}):
            raise ValueError("Server configuration changed. Discover and review again.")
        selected, names = [], set()
        for selection in selections:
            if type(selection) is not dict or set(selection) != {"name", "read_only"} or type(selection["read_only"]) is not bool:
                raise ValueError("Each selected tool needs an explicit owner read-only or approval policy.")
            name = selection["name"]
            if type(name) is not str or name in names:
                raise ValueError("Invalid or duplicate selected tool.")
            metadata = next((t for t in snapshot["tools"] if t.get("name") == name), None)
            validate_tool(metadata)
            names.add(name)
            selected.append({"metadata": metadata, "read_only": selection["read_only"]})
        server["identity"], server["tools"] = snapshot["identity"], selected
        _server(server)
        if sum(len(s.get("tools", [])) for s in config["servers"]) > MAX_SELECTED:
            raise ValueError("Selected tool count exceeds its total limit.")
        validate_catalog(config)
        write_json(self.registry.path, config)
        return {"ok": True, "selected_tools": len(selected)}
