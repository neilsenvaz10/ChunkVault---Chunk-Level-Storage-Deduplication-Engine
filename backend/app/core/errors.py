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
