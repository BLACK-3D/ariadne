"""Small Win32 primitives for the fixed Ariadne background host.

Only the trusted host is detached. Capability-worker resource jobs are unchanged.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess


def _kernel():
    if os.name != "nt":
        raise OSError("The independent Windows host requires Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def process_identity(pid):
    """Read immutable identity from an opened process, never a command string."""
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        raise ProcessLookupError("Process is unavailable.")
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0x102:
            raise ProcessLookupError("Process is no longer running.")
        name, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)) or not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            raise OSError("Could not verify process identity.")
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        token, size = wintypes.HANDLE(), wintypes.DWORD()
        if not advapi.OpenProcessToken(handle, 8, ctypes.byref(token)):
            raise OSError("Could not verify process owner.")
        try:
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value < 65536:
                raise OSError("Invalid process owner metadata.")
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(size)):
                raise OSError("Could not verify process owner.")
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            sid = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid)):
                raise OSError("Could not verify process owner.")
            try:
                owner = sid.value
            finally:
                kernel.LocalFree.argtypes = [wintypes.HLOCAL]
                kernel.LocalFree(ctypes.cast(sid, wintypes.HLOCAL))
        finally:
            kernel.CloseHandle(token)
        created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
        return {"pid": pid, "executable": str(Path(name.value).resolve()), "created_filetime": created, "owner_sid": owner}
    finally:
        kernel.CloseHandle(handle)


def same_process(expected):
    try:
        return process_identity(expected["pid"]) == expected
    except (OSError, KeyError, TypeError, ValueError):
        return False


class StopEvent:
    """Named, random, same-user control channel; no HTTP token is transmitted."""
    def __init__(self, name, *, create=False):
        import re
        if not isinstance(name, str) or not re.fullmatch(r"Local\\Ariadne-[a-f0-9]{48}", name):
            raise ValueError("Invalid host control event.")
        self.kernel = _kernel()
        self.kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.OpenEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.handle = (self.kernel.CreateEventW(None, True, False, name) if create else
                       self.kernel.OpenEventW(2 | 0x100000, False, name))
        if not self.handle:
            raise OSError("Host control channel is unavailable.")
        if create and ctypes.get_last_error() == 183:
            self.close()
            raise OSError("Host control event already exists.")

    def wait(self, milliseconds=500):
        result = self.kernel.WaitForSingleObject(self.handle, milliseconds)
        if result not in (0, 0x102):
            raise OSError("Host control wait failed.")
        return result == 0

    def signal(self):
        if not self.kernel.SetEvent(self.handle):
            raise OSError("Host stop request failed.")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ServiceJob:
    """Kill only newly created owned service trees when their host closes."""
    def __init__(self, *, allow_breakaway=False):
        self.processes = []
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                        ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = _kernel()
        self.kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000 | (0x800 if allow_breakaway else 0)
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Could not establish owned service lifecycle job.")

    def assign_handle(self, handle):
        if not self.kernel.AssignProcessToJobObject(self.handle, handle):
            raise OSError("Could not contain the owned service lifecycle.")

    def popen(self, command, *, cwd, env, stdout=None, stderr=None, stdin=None, creationflags=0):
        """Create a service atomically associated with this lifetime job.

        PROC_THREAD_ATTRIBUTE_JOB_LIST applies before the initial thread runs.
        A host crash cannot leave a runnable service in an assignment gap.
        HANDLE_LIST permits only the three duplicated standard handles.
        """
        if not self.handle:
            raise OSError("Owned service job is closed.")
        process = _job_process(self, command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                               stdin=stdin, creationflags=creationflags)
        self.processes.append(process)
        return process

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        errors = []
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(error)
            finally:
                process.close()
        self.processes = []
        if errors:
            raise OSError("An owned service did not confirm exit after job closure.")


class _OwnedProcess:
    """The small Popen contract used by the fixed model and flow launchers."""
    def __init__(self, kernel, handle, pid, command):
        self.kernel, self._handle, self.pid = kernel, handle, pid
        self.args, self.returncode = list(command), None
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    def poll(self):
        result = self.kernel.WaitForSingleObject(self._handle, 0) if self.returncode is None else 0
        if result not in (0, 0x102):
            raise OSError("Could not inspect the owned service process.")
        if self.returncode is None and result == 0:
            code = wintypes.DWORD()
            if not self.kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
                raise OSError("Could not read owned service exit status.")
            self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        milliseconds = 0xFFFFFFFF if timeout is None else max(0, min(0xFFFFFFFE, int(timeout * 1000)))
        result = self.kernel.WaitForSingleObject(self._handle, milliseconds)
        if result == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != 0:
            raise OSError("Could not wait for owned service exit.")
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self.kernel.TerminateProcess(self._handle, 1):
            raise OSError("Could not terminate the owned service.")

    kill = terminate

    def close(self):
        if self._handle:
            self.kernel.CloseHandle(self._handle)
            self._handle = None


def _job_process(job, command, *, cwd, env, stdout, stderr, stdin, creationflags):
    import msvcrt
    from ariadne.config import _allowed_path
    if (not isinstance(command, (list, tuple)) or not command or
            any(type(value) is not str or "\x00" in value for value in command) or
            type(creationflags) is not int or creationflags not in (0, 0x08000000) or
            stdin not in (None, subprocess.DEVNULL)):
        raise ValueError("Unsupported fixed-service creation parameters.")
    executable = _allowed_path(command[0])
    directory = _allowed_path(cwd)
    if not executable.is_file() or not directory.is_dir():
        raise ValueError("Fixed service executable or directory is missing.")
    if type(env) is not dict or any(type(key) is not str or not key or "=" in key or "\x00" in key or
                                  type(value) is not str or "\x00" in value for key, value in env.items()):
        raise ValueError("Invalid fixed-service environment.")
    environment = "\0".join(key + "=" + value for key, value in sorted(env.items(), key=lambda row: row[0].casefold())) + "\0\0"
    if len(environment) > 1024 * 1024:
        raise ValueError("Fixed-service environment exceeds its bound.")

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
                    ("title", wintypes.LPWSTR)] + [(name, wintypes.DWORD) for name in
                    ("x", "y", "x_size", "y_size", "x_count", "y_count", "fill", "flags")] + [
                    ("show", wintypes.WORD), ("reserved_size", wintypes.WORD), ("reserved_bytes", wintypes.LPVOID),
                    ("input", wintypes.HANDLE), ("output", wintypes.HANDLE), ("error", wintypes.HANDLE)]
    class ExtendedStartupInfo(ctypes.Structure):
        _fields_ = [("startup", StartupInfo), ("attributes", wintypes.LPVOID)]
    class ProcessInformation(ctypes.Structure):
        _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE), ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]
    kernel = job.kernel
    kernel.InitializeProcThreadAttributeList.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.UpdateProcThreadAttribute.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID]
    kernel.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID, ctypes.POINTER(ProcessInformation)]
    duplicates, files = [], []
    initialized = False
    attributes = None
    try:
        def standard_handle(source, mode):
            if source is None or source == subprocess.DEVNULL:
                source = open(os.devnull, mode)
                files.append(source)
            handle = wintypes.HANDLE()
            current = kernel.GetCurrentProcess()
            if not kernel.DuplicateHandle(current, msvcrt.get_osfhandle(source.fileno()), current, ctypes.byref(handle), 0, True, 2):
                raise OSError("Could not prepare owned service standard handles.")
            duplicates.append(handle)
            return handle
        info = ExtendedStartupInfo()
        info.startup.cb = ctypes.sizeof(info)
        info.startup.flags = 0x100 | 1
        info.startup.input = standard_handle(None, "rb")
        info.startup.output = standard_handle(stdout, "ab")
        info.startup.error = standard_handle(stderr, "ab")
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        if not 0 < size.value < 1024 * 1024:
            raise OSError("Could not size atomic service attributes.")
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            raise OSError("Could not initialize atomic service attributes.")
        initialized = True
        handles = (wintypes.HANDLE * len(duplicates))(*[value.value for value in duplicates])
        jobs = (wintypes.HANDLE * 1)(job.handle)
        if (not kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020002, handles, ctypes.sizeof(handles), None, None) or
                not kernel.UpdateProcThreadAttribute(attributes, 0, 0x0002000D, jobs, ctypes.sizeof(jobs), None, None)):
            raise OSError("Could not establish atomic service containment.")
        info.attributes = ctypes.cast(attributes, wintypes.LPVOID)
        process = ProcessInformation()
        arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        env_buffer = ctypes.create_unicode_buffer(environment)
        flags = creationflags | 0x00080000 | 0x00000400
        if not kernel.CreateProcessW(str(executable), arguments, None, None, True, flags,
                                     env_buffer, str(directory), ctypes.byref(info), ctypes.byref(process)):
            raise OSError("Atomic owned-service process creation failed.")
        kernel.CloseHandle(process.thread)
        return _OwnedProcess(kernel, process.process, process.pid, command)
    finally:
        if initialized:
            kernel.DeleteProcThreadAttributeList(attributes)
        for handle in duplicates:
            kernel.CloseHandle(handle)
        for source in files:
            source.close()


def detach(command, cwd, environment):
    """Try explicit job breakaway, then same-user WMI process creation.

    A denied breakaway never falls back to an ordinary inherited child. WMI's
    creator is the Windows provider, so the caller's kill-on-close job does not
    own the host. The caller must verify resulting PID/exe/creation/owner and
    wait for the host's nonce-bound readiness receipt before claiming success.
    """
    flags = 0x01000000 | 0x00000008 | 0x00000200
    try:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   close_fds=True, creationflags=flags)
        return process.pid, "win32_breakaway"
    except OSError as error:
        if error.winerror not in (5, 50, 87):
            raise
    # Fixed PowerShell source; command/path arrive as JSON stdin, never shell code.
    script = "$ErrorActionPreference='Stop';$p=[Console]::In.ReadToEnd()|ConvertFrom-Json;$s=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0;CreateFlags=[uint32]0x01000208};$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=[string]$p.command;CurrentDirectory=[string]$p.cwd;ProcessStartupInformation=$s};if($r.ReturnValue -ne 0){throw 'Independent host creation failed'};[Console]::Write([string]$r.ProcessId)"
    executable = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                            input=json.dumps({"command": subprocess.list2cmdline(command), "cwd": str(cwd)}),
                            capture_output=True, text=True, timeout=25, env=environment, creationflags=0x08000000)
    if result.returncode or not result.stdout.strip().isdigit():
        raise OSError("Independent host creation was denied; no inherited fallback was started.")
    return int(result.stdout.strip()), "wmi_same_user"
