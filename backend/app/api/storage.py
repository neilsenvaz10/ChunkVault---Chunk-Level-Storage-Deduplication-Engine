from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, get_services
from app.core.services import Services
from app.database.states import ChunkState
from app.schemas.files import ChunkInfo, ChunkListResponse, StorageStatsResponse
from app.storage.stats import compute_stats

router = APIRouter(tags=["storage"])


@router.get("/chunks", response_model=ChunkListResponse, summary="Inspect stored chunk metadata")
def list_chunks(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                state: ChunkState | None = None,
                svc: Services = Depends(get_services), conn: sqlite3.Connection = Depends(get_db)):
    items = [ChunkInfo(hash=c.hash, size_bytes=c.size_bytes, ref_count=c.ref_count, state=c.state.value,
                       created_at=c.created_at) for c in svc.repo.list_chunks(conn, limit, offset, state)]
    return ChunkListResponse(items=items, total=svc.repo.count_chunks(conn, state), limit=limit, offset=offset)


@router.get("/storage/stats", response_model=StorageStatsResponse,
            summary="Storage statistics computed from real metadata and files")
def storage_stats(svc: Services = Depends(get_services)):
    return StorageStatsResponse(**compute_stats(svc.settings, svc.store).__dict__)
