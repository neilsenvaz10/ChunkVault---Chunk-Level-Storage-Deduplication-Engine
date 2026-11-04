"""Single-writer guard.

All mutating storage operations (upload, delete, reconcile) run under this
lock. It combines an in-process ``threading.Lock`` with an OS-level exclusive
file lock on ``data/writer.lock``, so a second server *process* (e.g. started
with ``--workers 2``) cannot interleave mutations with the first.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import ChunkVaultError

if os.name == "nt":
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class WriterBusyError(ChunkVaultError):
    code = "writer_busy"
    status_code = 409


class WriterLock:
    def __init__(self, path: Path, timeout_seconds: float = 30.0) -> None:
        self._path = Path(path)
        self._timeout = timeout_seconds
        self._thread_lock = threading.Lock()

    @contextmanager
    def acquire(self, timeout_seconds: float | None = None) -> Iterator[None]:
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout
        if not self._thread_lock.acquire(timeout=max(timeout, 0)):
            raise WriterBusyError("another storage operation is in progress")
        fh = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self._path, "a+b")
            if fh.seek(0, os.SEEK_END) == 0:
                fh.write(b"\0")
                fh.flush()
            while not _try_lock(fh):
                if time.monotonic() >= deadline:
                    raise WriterBusyError("another storage operation is in progress")
                time.sleep(0.05)
            try:
                yield
            finally:
                _unlock(fh)
        finally:
            if fh is not None:
                fh.close()
            self._thread_lock.release()

    def is_free(self) -> bool:
        """True if no writer currently holds the lock (momentary check)."""
        try:
            with self.acquire(timeout_seconds=0):
                return True
        except WriterBusyError:
            return False
