from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.database.init import get_schema_version
from app.schemas.common import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Database check failed."}},
    summary="Service and database health",
)
def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> HealthResponse:
    try:
        conn.execute("SELECT 1").fetchone()
        db = DatabaseHealth(
            connected=True,
            foreign_keys_enforced=conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
            schema_version=get_schema_version(conn),
        )
    except sqlite3.Error:
        db = DatabaseHealth(connected=False, foreign_keys_enforced=False)
    ok = db.connected and db.foreign_keys_enforced
    if not ok:
        response.status_code = 503
    return HealthResponse(
        status="ok" if ok else "degraded",
        service=settings.app_name,
        version=settings.app_version,
        database=db,
        max_upload_bytes=settings.max_upload_bytes,
    )
