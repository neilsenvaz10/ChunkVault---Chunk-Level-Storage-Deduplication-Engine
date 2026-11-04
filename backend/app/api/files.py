from __future__ import annotations

import sqlite3
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import get_db, get_services
from app.core.services import Services
from app.database.repository import FileNotFoundInMetadata
from app.deduplication.interfaces import UploadTooLargeError
from app.schemas.common import ErrorResponse
from app.schemas.files import (
    DeleteFileResponse,
    FileChunkInfo,
    FileDetailResponse,
    FileListResponse,
    FileSummary,
    UploadChunkOutcome,
    UploadResponse,
)

router = APIRouter(prefix="/files", tags=["files"])

FileId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="File identifier returned by upload.")]
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}
_MULTIPART_SLACK = 1024 * 1024


def _summary(f, chunk_count: int) -> FileSummary:
    return FileSummary(file_id=f.file_id, filename=f.filename, original_size=f.original_size,
                       sha256=f.sha256, status=f.status.value, created_at=f.created_at, chunk_count=chunk_count)


@router.post("", response_model=UploadResponse, status_code=201,
             responses={413: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
             summary="Upload a file (chunk, hash, deduplicate, store)")
def upload_file(request: Request, file: UploadFile = File(...), svc: Services = Depends(get_services)):
    declared = request.headers.get("content-length")
    limit = svc.settings.max_upload_bytes
    if declared and declared.isdigit() and int(declared) > limit + _MULTIPART_SLACK:
        raise UploadTooLargeError(f"upload exceeds the {limit}-byte limit")
    result = svc.uploader.upload(file.filename or "", file.file)
    return UploadResponse(**{**result.__dict__, "chunk_outcomes": [UploadChunkOutcome(**vars(o)) for o in result.chunk_outcomes]})


@router.get("", response_model=FileListResponse, summary="List files (newest first)")
def list_files(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               svc: Services = Depends(get_services), conn: sqlite3.Connection = Depends(get_db)):
    files = svc.repo.list_files(conn, limit, offset)
    counts = {}
    if files:
        marks = ",".join("?" * len(files))
        counts = dict(conn.execute(
            f"SELECT file_id, COUNT(*) FROM file_chunks WHERE file_id IN ({marks}) GROUP BY file_id",
            [f.file_id for f in files]).fetchall())
    return FileListResponse(items=[_summary(f, counts.get(f.file_id, 0)) for f in files],
                            total=svc.repo.count_files(conn), limit=limit, offset=offset)


@router.get("/{file_id}", response_model=FileDetailResponse, responses=_ERR,
            summary="File metadata and ordered chunk manifest")
def get_file(file_id: FileId, svc: Services = Depends(get_services),
             conn: sqlite3.Connection = Depends(get_db)):
    f = svc.repo.get_file(conn, file_id)
    if f is None:
        raise FileNotFoundInMetadata("file not found")
    manifest = svc.repo.get_file_chunks(conn, file_id)
    refs: dict[str, int] = {}
    for m in manifest:
        if m.chunk_hash not in refs:
            refs[m.chunk_hash] = svc.repo.get_chunk(conn, m.chunk_hash).ref_count
    return FileDetailResponse(
        **_summary(f, len(manifest)).model_dump(), unique_chunk_count=len(refs),
        chunks=[FileChunkInfo(chunk_index=m.chunk_index, chunk_hash=m.chunk_hash, chunk_size=m.chunk_size,
                              ref_count=refs[m.chunk_hash]) for m in manifest])


@router.get("/{file_id}/download", responses={**_ERR, 500: {"model": ErrorResponse}},
            response_class=FileResponse, summary="Restore and download a file (fully verified before sending)")
def download_file(file_id: FileId, svc: Services = Depends(get_services)):
    tmp = svc.settings.staging_dir / f"restore-{uuid.uuid4().hex}.tmp"
    result = svc.restorer.restore_to_file(file_id, tmp)  # removes tmp itself on failure
    return FileResponse(
        result.path, media_type="application/octet-stream", filename=result.filename,
        headers={"X-ChunkVault-SHA256": result.sha256},
        background=BackgroundTask(lambda: result.path.unlink(missing_ok=True)),
    )


@router.delete("/{file_id}", response_model=DeleteFileResponse, responses=_ERR,
               summary="Delete a logical file; shared chunks are kept")
def delete_file(file_id: FileId, svc: Services = Depends(get_services)):
    return DeleteFileResponse(**svc.deleter.delete(file_id).__dict__)
