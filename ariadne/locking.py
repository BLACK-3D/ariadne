"""Cross-platform single-worker ownership for one state directory."""
import os
import stat

from .config import _allowed_path


class InstanceLock:
    def __init__(self, path):
        self.path, self.file, self.directory = _allowed_path(path), None, None

    def acquire(self):
        if self.file:
            raise ValueError("This lock instance is already held")
        from .tools import ToolRegistry, _linked
        paths = object.__new__(ToolRegistry)
        self.directory = paths._directory(self.path.parent)
        try:
            directory_fd = self.directory.__enter__()
            _allowed_path(self.path)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(self.path if directory_fd is None else self.path.name, flags, 0o600, dir_fd=directory_fd)
            self.file = os.fdopen(fd, "r+b")
            info = os.fstat(self.file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _linked(info) or _linked(self.path.lstat()):
                raise ValueError("Ariadne lock must be a regular file without links or reparse points")
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                get_final = kernel.GetFinalPathNameByHandleW
                get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
                get_final.restype = wintypes.DWORD
                buffer = ctypes.create_unicode_buffer(32768)
                count = get_final(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
                actual = buffer.value.removeprefix("\\\\?\\")
                if not count or count >= len(buffer) or os.path.normcase(actual) != os.path.normcase(str(self.path)):
                    raise ValueError("Lock path changed while it was being opened")
            else:
                current = self.path.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("Lock path changed while it was being opened")
            # Validate the held file before any write; do not truncate another owner's lock.
            if info.st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.release()
            raise ValueError("Another Ariadne worker already owns this data directory") from None
        except BaseException:
            self.release()
            raise

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
        if self.directory:
            self.directory.__exit__(None, None, None)
            self.directory = None
