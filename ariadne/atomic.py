"""Flush a complete private file before publishing its final name exclusively.

Callers must validate their destination and hold any required parent-directory guard.
The final name is never replaced. A killed process may leave a harmless staging file.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def sync_directory(path):
    """Persist directory changes where the platform exposes directory fsync."""
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def commit_staged(stage, destination):
    """Atomically publish an already flushed same-directory file, without replacement."""
    stage, destination = Path(stage), Path(destination)
    if stage.parent != destination.parent:
        raise ValueError("Atomic publication requires the same parent directory.")
    if os.name == "nt":
        import ctypes
        function = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        function.restype = ctypes.c_int
        # MOVEFILE_WRITE_THROUGH; deliberately omit MOVEFILE_REPLACE_EXISTING.
        if not function(str(stage), str(destination), 0x8):
            error = ctypes.get_last_error()
            if error in (80, 183):
                raise FileExistsError(error, "Destination already exists", str(destination))
            raise ctypes.WinError(error)
    else:
        os.link(stage, destination, follow_symlinks=False)
        stage.unlink()
    sync_directory(destination.parent)


def publish_bytes(destination, payload, *, checkpoint=None):
    """Create a durable complete file or raise FileExistsError; never expose partial bytes.

    ``checkpoint`` is an optional test seam called after staging fsync, before commit.
    """
    destination = Path(destination)
    if not isinstance(payload, bytes):
        raise TypeError("Atomic file payload must be bytes.")
    stage = destination.with_name("." + destination.name + ".box-" + secrets.token_hex(12) + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    identity = None
    committed = False
    try:
        descriptor = os.open(stage, flags, 0o600)
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        if checkpoint:
            checkpoint()
        commit_staged(stage, destination)
        committed = True
    finally:
        if identity is not None and not committed:
            try:
                info = stage.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    stage.unlink()
            except FileNotFoundError:
                pass
