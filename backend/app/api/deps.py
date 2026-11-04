"""FastAPI dependencies."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from app.core.config import Settings
from app.database.connection import connect


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request, always closed. Callers that write must use
    ``database.connection.transaction`` for explicit transaction boundaries."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
