from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends

from app.api.deps import get_services
from app.core.services import Services
from app.reliability.interfaces import AuditReport
from app.schemas.common import ErrorResponse
from app.schemas.operations import (
    IntegrityCheckRequest,
    IntegrityCheckResponse,
    IntegrityFinding,
    ReconcileResponse,
    RepairActionModel,
)

router = APIRouter(prefix="/integrity", tags=["integrity"])


def _to_response(report: AuditReport) -> IntegrityCheckResponse:
    return IntegrityCheckResponse(
        healthy=report.healthy, chunks_checked=report.chunks_checked, files_checked=report.files_checked,
        findings=[IntegrityFinding(kind=f.kind, subject=f.subject, detail=f.detail) for f in report.findings],
        counts_by_kind=dict(Counter(f.kind for f in report.findings)))


@router.post("/audit", response_model=IntegrityCheckResponse, responses={404: {"model": ErrorResponse}},
             summary="Read-only consistency audit (changes nothing)")
def audit(body: IntegrityCheckRequest | None = None, svc: Services = Depends(get_services)):
    body = body or IntegrityCheckRequest()
    report = (svc.auditor.verify_file(body.file_id) if body.file_id
              else svc.auditor.verify_store(body.verify_chunk_bytes))
    return _to_response(report)


@router.post("/reconcile", response_model=ReconcileResponse, responses={409: {"model": ErrorResponse}},
             summary="Explicit, reported cleanup of crash leftovers (never touches referenced chunks)")
def reconcile(svc: Services = Depends(get_services)):
    report = svc.reconciler.reconcile()
    return ReconcileResponse(
        actions=[RepairActionModel(action=a.action, subject=a.subject, detail=a.detail) for a in report.actions],
        remaining=_to_response(report.remaining))
