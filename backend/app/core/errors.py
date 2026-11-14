"""Application-wide exception hierarchy.

Every domain error derives from ``ChunkVaultError`` so the API layer can turn
it into the structured error response (see ``app/api/errors.py``) without
knowing about individual components.
"""

from __future__ import annotations

from typing import Any


import re

_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[^\\/\s'\"<>|:]+[\\/])+[^\\/\s'\"<>|:]*")


def scrub_paths(text: str) -> str:
    """Replace filesystem paths in an exception message so they never reach an API response."""
    return _PATH.sub("<path>", text)


class ChunkVaultError(Exception):
    """Base class for all expected (non-bug) errors."""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ConfigurationError(ChunkVaultError):
    """Invalid or inconsistent configuration. Raised at startup, never per request."""

    code = "configuration_error"


class DatabaseError(ChunkVaultError):
    """The metadata database is unavailable, corrupt, or at an unsupported version."""

    code = "database_error"
    status_code = 503


class DatabaseUnavailableError(DatabaseError):
    """The database cannot be reached right now (connection refused, timeout, no free pooled connection). Transient by nature:
    the process stays alive, reports itself not ready, and recovers when the database returns. Distinct from a wrong or missing
    schema, which is a deployment error and still stops the process at start-up."""

    code = "database_unavailable"
    status_code = 503


class TooManyRequestsError(ChunkVaultError):
    """A per-client or global limit was reached; the request was refused before doing any work. Carries ``retry_after`` seconds."""

    code = "rate_limited"
    status_code = 429

    def __init__(self, message: str, *, retry_after: float = 1.0, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retry_after = max(1, int(retry_after + 0.999))


class NotFoundError(ChunkVaultError):
    code = "not_found"
    status_code = 404


class ConflictError(ChunkVaultError):
    code = "conflict"
    status_code = 409


class InvalidParametersError(ChunkVaultError):
    """Individually valid parameters that are inconsistent with each other."""

    code = "invalid_parameters"
    status_code = 422


class ConcurrentConflictError(ChunkVaultError):
    """The database aborted this operation because it collided with a concurrent one (deadlock / serialization failure).
    Nothing was committed; the caller can simply retry."""

    code = "concurrent_conflict"
    status_code = 409


_CONFLICT_SQLSTATES = frozenset({"40P01", "40001"})  # deadlock_detected, serialization_failure


def is_concurrency_conflict(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 8:
        if getattr(exc, "sqlstate", None) in _CONFLICT_SQLSTATES:
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False
