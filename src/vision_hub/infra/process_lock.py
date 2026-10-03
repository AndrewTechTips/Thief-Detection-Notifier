"""Exclusive lock file guaranteeing a single process owns the cameras (AD-8).

Uses ``flock``, which the OS releases automatically when the process exits, even after a crash,
so a stale lock file can never block a restart.
"""

import fcntl
import os
from pathlib import Path
from types import TracebackType


class ProcessLockError(Exception):
    """Another process already holds the lock."""


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            msg = (
                f"another process holds {self._path}: only one Vision Hub process may run the "
                "cameras (do not start uvicorn with more than one worker)"
            )
            raise ProcessLockError(msg) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
