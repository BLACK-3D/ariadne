"""Small, dependency-free configuration for a local Ariadne installation."""

from __future__ import annotations

import os
import json
import secrets
import stat
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit


def local_service_port(name: str, default: int, *, environ=None) -> int:
    """Select a port for an owned numeric-loopback service, never a host/URL."""
    raw = (os.environ if environ is None else environ).get(name, str(default))
    if type(raw) is not str or not re.fullmatch(r"[1-9][0-9]{0,4}", raw) or not 1 <= int(raw) <= 65535:
        raise ValueError(name + " must be a decimal port from 1 to 65535.")
    return int(raw)


def _allowed_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    # This installation's owner explicitly reserves E: for unrelated data.
    if PureWindowsPath(str(path)).drive.lower() == "e:":
        raise ValueError("The E: drive is excluded from this installation.")
    path = path.absolute()
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Configured paths must not contain symbolic links or reparse points.")
    resolved = path.resolve(strict=False)
    if PureWindowsPath(str(resolved)).drive.lower() == "e:":
        raise ValueError("The E: drive is excluded from this installation.")
    return resolved


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    workspace: Path | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b"
    host: str = "127.0.0.1"
    port: int = 8080
    api_token: str = ""
    max_steps: int = 12
    model_timeout: float = 180
    enable_docker: bool = False
    docker_image: str = "python:3.13-slim"
    connectors_path: Path | None = None
    whisper_bin: str | None = None
    whisper_model: str | None = None
    piper_bin: str | None = None
    piper_model: str | None = None

    def __post_init__(self) -> None:
        data_dir = _allowed_path(self.data_dir)
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "workspace", _allowed_path(self.workspace or data_dir / "workspace"))
        object.__setattr__(self, "connectors_path", _allowed_path(self.connectors_path or data_dir / "connectors.json"))
        for name in ("whisper_bin", "whisper_model", "piper_bin", "piper_model"):
            value = getattr(self, name)
            if value:
                object.__setattr__(self, name, str(_allowed_path(value)))
        if not 1 <= self.port <= 65535:
            raise ValueError("ARIADNE_PORT must be between 1 and 65535.")
        object.__setattr__(self, "max_steps", min(32, max(1, int(self.max_steps))))
        if not 1 <= self.model_timeout <= 900:
            raise ValueError("ARIADNE_MODEL_TIMEOUT must be between 1 and 900 seconds.")
        parsed = urlsplit(self.ollama_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ARIADNE_OLLAMA_URL must be an HTTP(S) URL without credentials, query, or fragment.")
        if not self.model.strip() or not self.host.strip():
            raise ValueError("Model and host must not be empty.")


def _persistent_token(data_dir: Path) -> str:
    data_dir = _allowed_path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = _allowed_path(data_dir / "server.token")
    from .atomic import publish_bytes
    from .tools import ToolRegistry
    guard = object.__new__(ToolRegistry)
    with guard._directory(data_dir) as directory_fd:
        if not token_path.exists():
            token = secrets.token_urlsafe(32)
            try:
                publish_bytes(token_path, (token + "\n").encode("ascii"))
            except FileExistsError:
                pass  # A concurrent first launch published its complete key first.
        with os.fdopen(guard._open_read(token_path, directory_fd), "r", encoding="ascii") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256:
                raise ValueError("server.token is not a valid token file.")
            token = source.read(257).strip()
        if not 40 <= len(token) <= 128 or not all(c.isalnum() or c in "-_" for c in token):
            raise ValueError("server.token is invalid; restore a valid token or remove it to generate a new one.")
        return token


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def load_settings() -> Settings:
    """Load environment settings and create local state; never fetch models."""
    data_dir = _allowed_path(Path(os.environ.get("ARIADNE_DATA_DIR", "data")))
    voice_path = _allowed_path(data_dir / "voice-config.json")
    voice_config = {}
    if voice_path.exists():
        info = voice_path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16_384:
            raise ValueError("voice-config.json must be a regular, unlinked JSON file of at most 16 KiB.")
        try:
            voice_config = json.loads(voice_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("voice-config.json must contain valid UTF-8 JSON.") from None
        if (not isinstance(voice_config, dict)
                or set(voice_config) - {"whisper_bin", "whisper_model", "piper_bin", "piper_model"}
                or any(not isinstance(value, str) or not value for value in voice_config.values())):
            raise ValueError("voice-config.json contains unsupported voice settings.")

    def voice_setting(name: str) -> str | None:
        return os.environ.get("ARIADNE_" + name.upper(), voice_config.get(name)) or None

    settings = Settings(
        data_dir=data_dir,
        ollama_url=(os.environ.get("ARIADNE_OLLAMA_URL") or
                    f"http://127.0.0.1:{local_service_port('ARIADNE_MODEL_PORT', 11434)}").rstrip("/"),
        model=os.environ.get("ARIADNE_MODEL", "qwen3:4b"),
        host=os.environ.get("ARIADNE_HOST", "127.0.0.1"),
        port=int(os.environ.get("ARIADNE_PORT", "8080")),
        max_steps=int(os.environ.get("ARIADNE_MAX_STEPS", "12")),
        model_timeout=float(os.environ.get("ARIADNE_MODEL_TIMEOUT", "180")),
        enable_docker=_boolean("ARIADNE_ENABLE_DOCKER"),
        docker_image=os.environ.get("ARIADNE_DOCKER_IMAGE", "python:3.13-slim"),
        connectors_path=Path(os.environ["ARIADNE_CONNECTORS_PATH"]) if os.environ.get("ARIADNE_CONNECTORS_PATH") else None,
        whisper_bin=voice_setting("whisper_bin"),
        whisper_model=voice_setting("whisper_model"),
        piper_bin=voice_setting("piper_bin"),
        piper_model=voice_setting("piper_model"),
    )
    token = _persistent_token(settings.data_dir)
    settings.workspace.mkdir(parents=True, exist_ok=True)
    from dataclasses import replace

    return replace(settings, api_token=token)
