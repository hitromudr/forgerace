"""Кроссплатформенный файловый блокировщик для BenchmarkStore."""

import os
try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

__all__ = ["acquire_file_lock"]

def acquire_file_lock(path: Path, mode: str = "exclusive") -> "contextmanager":
    """Блокирует файл в режиме shared или exclusive.

    Args:
        path: путь к файлу
        mode: "shared" или "exclusive"

    Returns:
        контекст-менеджер, который снимает блокировку при выходе
    """
    class _FileLock:
        def __init__(self, path: Path, mode: str):
            self._path = path
            self._mode = mode
            self._fd = None

        def __enter__(self):
            self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT)
            if os.name == "nt":  # Windows
                if self._mode == "exclusive":
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    msvcrt.locking(self._fd, msvcrt.LK_NBRLCK, 1)
            else:  # Unix
                if self._mode == "exclusive":
                    fcntl.flock(self._fd, fcntl.LOCK_EX)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_SH)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self._fd is not None:
                try:
                    if os.name == "nt":
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._fd)
                    self._fd = None

    return _FileLock(path, mode)
