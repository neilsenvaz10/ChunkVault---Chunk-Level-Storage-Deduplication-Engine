"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from app.database.interfaces import Conn

from app.core.config import Settings
from app.core.services import Services


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_services(request: Request) -> Services:
    request.app.state.readiness.ensure_ready()  # 503 at once while the database is unavailable; re-checks it at most every few seconds
    return request.app.state.services


def get_db(request: Request) -> Iterator[Conn]:
    """One connection per request, always closed. Callers that write must use
    ``database.connection.transaction`` for explicit transaction boundaries."""
    request.app.state.readiness.ensure_ready()
    with request.app.state.services.db.connection() as conn:
        yield conn
