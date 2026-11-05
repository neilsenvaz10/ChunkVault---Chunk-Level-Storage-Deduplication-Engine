"""Recognise "the disk is full" failures, wherever in a chain of exceptions they originate.

Staging and publishing wrap ``OSError`` in ``ChunkWriteError``/``UploadError``; SQLite raises
``sqlite3.OperationalError`` ("database or disk is full") directly. The upload orchestrator uses this to report all of
them as one clear ``insufficient_storage`` error after its normal cleanup has run.
"""

from __future__ import annotations

import errno
import sqlite3

_ERRNOS = {errno.ENOSPC} | ({errno.EDQUOT} if hasattr(errno, "EDQUOT") else set())
_WINERRORS = {39, 112}  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_SQLITE_FULL = 13


def is_disk_full(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, OSError) and (exc.errno in _ERRNOS or getattr(exc, "winerror", None) in _WINERRORS):
            return True
        if isinstance(exc, sqlite3.Error):
            if getattr(exc, "sqlite_errorcode", None) == _SQLITE_FULL or "disk is full" in str(exc).lower():
                return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False
