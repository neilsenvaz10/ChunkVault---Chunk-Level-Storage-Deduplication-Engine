from __future__ import annotations

import sqlite3

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import get_db, get_services
from app.core.services import Services
from app.core.errors import NotFoundError
from app.database.states import ChunkState
from app.schemas.common import ErrorResponse
from app.schemas.inspect import ChunkDetailResponse, ChunkReference
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


_MAX_REFS = 500


@router.get("/chunks/{chunk_hash}", response_model=ChunkDetailResponse, responses={404: {"model": ErrorResponse}},
            summary="One chunk and every manifest row that references it")
def chunk_detail(chunk_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
                 svc: Services = Depends(get_services), conn: sqlite3.Connection = Depends(get_db)):
    c = svc.repo.get_chunk(conn, chunk_hash)
    if c is None:
        raise NotFoundError("chunk not found")
    actual = conn.execute("SELECT COUNT(*) FROM file_chunks WHERE chunk_hash=?", (chunk_hash,)).fetchone()[0]
    refs = conn.execute(
        "SELECT fc.file_id, f.filename, f.status, fc.chunk_index FROM file_chunks fc "
        "JOIN files f USING(file_id) WHERE fc.chunk_hash=? ORDER BY fc.file_id, fc.chunk_index LIMIT ?",
        (chunk_hash, _MAX_REFS)).fetchall()
    return ChunkDetailResponse(
        hash=c.hash, size_bytes=c.size_bytes, ref_count=c.ref_count, state=c.state.value, created_at=c.created_at,
        actual_references=actual, references_truncated=actual > len(refs),
        references=[ChunkReference(file_id=r[0], filename=r[1], file_status=r[2], chunk_index=r[3]) for r in refs])
