"""Cloud-only routes (registered only when ``CHUNKVAULT_BACKEND=cloud``): the quota view and explicit garbage collection."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.api.ratelimit import maintenance_limits
from app.api.deps import get_db, get_services
from app.core.services import Services
from app.database.interfaces import Conn
from app.maintenance.gc import MAX_LIMIT
from app.schemas.common import ErrorResponse

router = APIRouter(tags=["cloud"])


class QuotaResponse(BaseModel):
    """GET /api/storage/quota. The application's own accounting; not the cloud provider's project-wide limits."""

    quota_bytes: int
    used_bytes: int = Field(description="Sum of size_bytes over every chunk row: unique objects, never counting duplicates twice.")
    available_bytes: int
    referenced_bytes: int = Field(description="Part of used_bytes that complete files still reference.")
    reclaimable_bytes: int = Field(description="Part of used_bytes with no reference (waiting for garbage collection).")
    accounting: str


class GcRequest(BaseModel):
    dry_run: bool = Field(default=True, description="Default true: report what would be reclaimed and change nothing.")
    limit: int = Field(default=200, ge=1, le=MAX_LIMIT, description="At most this many chunks per call.")


class GcResponse(BaseModel):
    dry_run: bool
    grace_seconds: float
    candidates: int
    candidate_bytes: int
    rows_removed: int
    objects_removed: int
    objects_already_absent: int
    bytes_reclaimed: int
    failed: list[str]
    more_available: bool


@router.get("/storage/quota", response_model=QuotaResponse, summary="Application storage quota and usage")
def quota(svc: Services = Depends(get_services), conn: Conn = Depends(get_db)):
    cap = svc.settings.cloud.stored_bytes_cap
    used = svc.repo.chunk_bytes_total(conn)
    totals = svc.repo.storage_totals(conn)
    return QuotaResponse(
        quota_bytes=cap, used_bytes=used, available_bytes=max(0, cap - used),
        referenced_bytes=max(0, used - totals.unreferenced_chunk_bytes), reclaimable_bytes=totals.unreferenced_chunk_bytes,
        accounting="unique chunk bytes: referenced + pending + awaiting garbage collection")


@router.post("/admin/gc", response_model=GcResponse, dependencies=[Depends(require_admin), Depends(maintenance_limits)],
             responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
             summary="Explicit garbage collection of unreferenced chunks (admin; dry run by default)")
def gc(body: GcRequest | None = None, svc: Services = Depends(get_services)):
    body = body or GcRequest()
    return GcResponse(**svc.gc.run(dry_run=body.dry_run, limit=body.limit).__dict__)
