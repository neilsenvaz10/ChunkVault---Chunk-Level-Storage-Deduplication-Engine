from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.database.interfaces import Conn
from app.api.deps import get_db, get_services, get_settings
from app.core.config import Settings
from app.core.services import Services
from app.schemas.common import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


@router.get("/health/live", response_model=LivenessResponse, summary="Liveness: the process is up (never touches the database)")
def liveness(settings: Settings = Depends(get_settings)) -> LivenessResponse:
    """For the platform's health check. Constant-time and dependency-free on purpose: a database outage or an exhausted connection
    pool must not make the platform believe the process is dead and restart it. Use /health/ready for database health."""
    return LivenessResponse(service=settings.app_name, version=settings.app_version)


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Database check failed."}},
    summary="Readiness: service and database health",
)
@router.get(
    "/health/ready",
    response_model=HealthResponse,
    include_in_schema=False,  # an alias of /health with the name the platform documentation uses
)
def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    svc: Services = Depends(get_services),
    conn: Conn = Depends(get_db),
) -> HealthResponse:
    h = svc.db.check(conn)  # never raises a driver error; reports connected=False instead
    db = DatabaseHealth(connected=h.connected, foreign_keys_enforced=h.foreign_keys_enforced,
                        schema_version=int(h.schema_version) if h.schema_version is not None else None)
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
