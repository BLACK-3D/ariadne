"""Bounded execution for fixed, optional local capability workers.

These are resource limits, not a filesystem or network sandbox. Each worker
must separately restrict its inputs, destinations and native library features.
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import _allowed_path

_SLOTS = threading.BoundedSemaphore(2)
_ALLOWED = {"_mcp_worker.py", "_ocr_worker.py", "_analytics_worker.py",
            "_production_worker.py", "_knowledge_worker.py", "_media_worker.py", "_automation_worker.py"}
_CHILD_WORKERS = {"_production_worker.py", "_knowledge_worker.py", "_media_worker.py", "_automation_worker.py"}
_LIMITS = None


class WorkerError(ValueError):
    """A bounded worker could not complete; its raw diagnostics stay private."""


def apply_limits(memory_bytes: int, cpu_seconds: int, process_limit: int = 1) -> None:
    global _LIMITS
    if _LIMITS is not None:
        if _LIMITS != (memory_bytes, cpu_seconds, process_limit):
            raise WorkerError("Worker resource limits cannot change during execution.")
        return
    if type(memory_bytes) is not int or not 64 * 1024**2 <= memory_bytes <= 32 * 1024**3:
        raise WorkerError("Invalid worker memory limit.")
    if type(cpu_seconds) is not int or not 1 <= cpu_seconds <= 300:
        raise WorkerError("Invalid worker CPU limit.")
    if type(process_limit) is not int or not 1 <= process_limit <= 8:
        raise WorkerError("Invalid worker process limit.")
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("counters", Counters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                        ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes, kernel.SetInformationJobObject.restype = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes, kernel.AssignProcessToJobObject.restype = [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x100 | 0x200 | 0x2000 | 0x8 | 0x2 | 0x4
        limits.basic.active_processes = process_limit
        limits.basic.process_time = cpu_seconds * 10_000_000
        limits.basic.job_time = cpu_seconds * 10_000_000
        limits.process_memory = memory_bytes
        limits.job_memory = memory_bytes
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            if handle:
                kernel.CloseHandle(handle)
            raise WorkerError("Could not establish operating-system worker limits.")
        globals()["_JOB_HANDLE"] = handle  # Keep kill-on-close job alive for this process.
    else:
        raise WorkerError("Operating-system worker limits are unsupported.")
    _LIMITS = (memory_bytes, cpu_seconds, process_limit)


def _environment(env):
    root = _allowed_path(Path(__file__).resolve().parents[1])
    temp = _allowed_path(root / "data" / "worker-tmp")
    temp.mkdir(parents=True, exist_ok=True)
    result = {}
    for key in ("SystemRoot", "WINDIR", "COMSPEC", "LANG", "LC_ALL"):
        if key in os.environ:
            result[key] = os.environ[key]
    # Do not inherit owner credentials, proxies, Python hooks or executable paths.
    result.update({"PATH": str(Path(sys.executable).parent) + os.pathsep + str(Path(os.environ.get("SystemRoot", "/")) / "System32"),
                   "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
                   "HOME": str(temp), "USERPROFILE": str(temp), "XDG_CACHE_HOME": str(temp),
                   "LOCALAPPDATA": str(temp), "APPDATA": str(temp), "PYTHONDONTWRITEBYTECODE": "1",
                   "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
                   "NUMEXPR_NUM_THREADS": "2", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    if env:
        # Explicit values are never inherited wholesale. Keep process controls fixed.
        if any(key in result or key.upper().startswith(("PYTHON", "LD_", "DYLD_")) for key in env):
            raise WorkerError("Worker environment cannot override process controls.")
        result.update(env)
    return result


def run_worker(script: Path, payload: bytes, *, timeout: float, memory_bytes: int,
               cpu_seconds: int, max_output_bytes: int, max_input_bytes: int = 16 * 1024**2,
               env: dict | None = None, cancelled=None, process_limit: int = 1) -> bytes:
    script = _allowed_path(script)
    if script.parent != Path(__file__).resolve().parent or script.name not in _ALLOWED:
        raise WorkerError("Only fixed internal capability workers may execute.")
    if type(process_limit) is not int or not 1 <= process_limit <= (8 if script.name in _CHILD_WORKERS else 1):
        raise WorkerError("This worker cannot launch the requested number of processes.")
    info = script.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkerError("Capability worker must be an unlinked regular file.")
    if type(payload) is not bytes or len(payload) > max_input_bytes:
        raise WorkerError("Worker input exceeds its byte limit.")
    if not 0 < timeout <= 300 or not 0 < max_output_bytes <= 4 * 1024**2:
        raise WorkerError("Invalid worker output or wall-time limit.")
    if not _SLOTS.acquire(blocking=False):
        raise WorkerError("Both local capability workers are busy; try again after a current job finishes.")
    process = None
    readers = []
    directory_guard = None
    try:
        if cancelled and cancelled():
            raise WorkerError("Capability operation was cancelled.")
        child_env = _environment(env)
        from .tools import ToolRegistry
        directory_guard = object.__new__(ToolRegistry)._directory(Path(child_env["TEMP"]))
        directory_guard.__enter__()  # Pin temp/cache ancestors for the complete child lifetime.
        process = subprocess.Popen([sys.executable, "-I", "-B", str(script.parent / "_worker_bootstrap.py"),
                                    script.name, str(memory_bytes), str(cpu_seconds), str(process_limit)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=str(script.parent.parent), env=child_env,
                                   start_new_session=os.name == "posix",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = bytearray()
        overflow = threading.Event()

        def read():
            try:
                while True:
                    chunk = process.stdout.read(16_384)
                    if not chunk:
                        break
                    if len(output) + len(chunk) > max_output_bytes:
                        overflow.set()
                        _terminate(process)
                        break
                    output.extend(chunk)
            except (OSError, ValueError):
                pass

        def write():
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        for operation in (read, write):
            thread = threading.Thread(target=operation, daemon=True)
            thread.start()
            readers.append(thread)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancelled and cancelled():
                raise WorkerError("Capability operation was cancelled.")
            if overflow.is_set():
                raise WorkerError("Worker output exceeds its byte limit.")
            if time.monotonic() >= deadline:
                raise WorkerError("Capability operation exceeded its wall-time limit.")
            time.sleep(0.05)
        for thread in readers:
            thread.join(timeout=2)
        if overflow.is_set():
            raise WorkerError("Worker output exceeds its byte limit.")
        if process.returncode != 0:
            raise WorkerError("Capability worker failed or exceeded its operating-system limits.")
        if cancelled and cancelled():
            raise WorkerError("Capability operation was cancelled.")
        return bytes(output)
    finally:
        if process is not None:
            if os.name == "posix" or process.poll() is None:
                _terminate(process)
            process.wait(timeout=5)
            for thread in readers:
                thread.join(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        if directory_guard:
            directory_guard.__exit__(None, None, None)
        _SLOTS.release()


def _terminate(process):
    """End the entire worker group; Windows descendants belong to its Job."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()
