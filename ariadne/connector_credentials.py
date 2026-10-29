"""Load only owner-configured connector secrets after detached-host isolation.

This is private deployment state, never a source/factory configuration. Names
cannot change Python, shell, proxy, runtime or operating-system settings.
"""
from __future__ import annotations

import json
import os
import re

from .config import _allowed_path

_NAME = re.compile(r"ARIADNE_CONNECTOR_[A-Z][A-Z0-9_]{0,63}\Z")
MAX_BYTES = 65_536


def load_connector_credentials(registry):
    path = _allowed_path(registry.settings.data_dir / "connector-secrets.json")
    if not path.exists():
        return {"configured": False, "loaded": 0}
    with registry._directory(path.parent) as directory:
        with os.fdopen(registry._open_read(path, directory), "rb") as source:
            if os.fstat(source.fileno()).st_size > MAX_BYTES:
                raise ValueError("Private connector credentials exceed their size limit.")
            raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Private connector credentials exceed their size limit.")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Private connector credentials contain duplicate fields.")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Private connector credentials must be bounded UTF-8 JSON.") from None
    if (type(value) is not dict or set(value) != {"schema", "environment"} or type(value["schema"]) is not int
            or value["schema"] != 1 or type(value["environment"]) is not dict or len(value["environment"]) > 32):
        raise ValueError("Private connector credential configuration is invalid.")
    referenced = {entry["env"] for connector in registry._connectors.values()
                  for entry in connector["headers"].values() if isinstance(entry, dict)}
    prepared = {}
    for name, secret in value["environment"].items():
        if (type(name) is not str or not _NAME.fullmatch(name) or name not in referenced
                or type(secret) is not str or not 1 <= len(secret) <= 4096
                or any(ord(char) < 32 or ord(char) > 126 for char in secret)):
            raise ValueError("Private connector credentials contain an unsupported name or header value.")
        prepared[name] = secret
    # Validate the whole file before adding anything to the process environment.
    loaded = 0
    for name, secret in prepared.items():
        if not os.environ.get(name):
            os.environ[name] = secret
            loaded += 1
    return {"configured": True, "loaded": loaded}
