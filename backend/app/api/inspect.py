"""Narrow, read-only, paginated inspection of the metadata tables.

Only the four whitelisted tables are reachable; table names come from an enum,
never from free text, and no endpoint accepts SQL or filter expressions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.database.interfaces import Conn
from app.api.deps import get_db, get_services
from app.core.services import Services
from app.schemas.common import ErrorResponse
from app.schemas.inspect import (
    ColumnInfo,
    ForeignKeyInfo,
    RefCountInvariant,
    SchemaResponse,
    TableInfo,
    TableName,
    TableRowsResponse,
)

router = APIRouter(prefix="/inspect", tags=["inspect"])

_TABLE_DOC = {
    TableName.FILES: "One logical file: identity, size, whole-file SHA-256 and lifecycle status.",
    TableName.CHUNKS: "One row per distinct chunk, keyed by its SHA-256. Holds the reference count.",
    TableName.FILE_CHUNKS: "The ordered manifest: which chunk occupies each position of each file.",
    TableName.OPERATIONS: "Audit trail of upload, restore, delete and maintenance operations.",
}

_COLUMN_DOC = {
    "files": {
        "file_id": "Random identifier assigned at upload.",
        "filename": "Original file name (path components stripped).",
        "original_size": "Size in bytes of the uploaded file.",
        "sha256": "Whole-file SHA-256; NULL only while status is 'uploading'.",
        "status": "uploading | complete | failed | deleting. Only 'complete' files are restorable.",
        "created_at": "UTC timestamp when the upload started.",
    },
    "chunks": {
        "hash": "Lowercase hex SHA-256 of the chunk bytes: its identity and storage address.",
        "size_bytes": "Chunk length in bytes.",
        "ref_count": "Number of manifest rows referencing this chunk (maintained by triggers).",
        "state": "pending | stored | corrupt. 'stored' means the bytes were atomically published.",
        "created_at": "UTC timestamp when the chunk row was committed.",
    },
    "file_chunks": {
        "file_id": "The file this manifest row belongs to.",
        "chunk_index": "0-based position of the chunk within the file.",
        "chunk_hash": "The chunk occupying that position (the same hash may appear many times).",
        "chunk_size": "Chunk length in bytes; must equal chunks.size_bytes.",
    },
    "operations": {
        "operation_id": "Auto-incrementing identifier.",
        "file_id": "Related file, or NULL (whole-store operations, or the file was deleted).",
        "operation_type": "upload | restore | delete | integrity_check | reconciliation | benchmark | simulation.",
        "status": "running | succeeded | failed | aborted.",
        "started_at": "UTC start time.",
        "completed_at": "UTC end time; NULL while running.",
        "error_message": "Failure reason; required when status is 'failed'.",
    },
}

_INVARIANT = "chunks.ref_count = COUNT(file_chunks rows whose chunk_hash = chunks.hash)"


@router.get("/schema", response_model=SchemaResponse, summary="Tables, columns, keys and the ref-count invariant")
def schema(svc: Services = Depends(get_services), conn: Conn = Depends(get_db)):
    by_name = {t.name: t for t in svc.db.introspect()}  # catalog introspection lives in the database implementation
    tables = []
    for t in TableName:
        ts = by_name[t.value]
        cols = [ColumnInfo(
            name=c.name, type=c.type, not_null=c.not_null, primary_key_position=c.primary_key_position, default=c.default,
            foreign_key=(ForeignKeyInfo(table=c.foreign_key.table, column=c.foreign_key.column, on_delete=c.foreign_key.on_delete)
                         if c.foreign_key else None),
            description=_COLUMN_DOC[t.value].get(c.name, "")) for c in ts.columns]
        tables.append(TableInfo(name=t, description=_TABLE_DOC[t], row_count=svc.repo.count_rows(conn, t.value), columns=cols))
    mismatches = len(svc.repo.ref_count_mismatches(conn))
    checked = svc.repo.count_chunks(conn)
    return SchemaResponse(
        schema_version=int(svc.db.schema_version() or 0), tables=tables,
        ref_count_invariant=RefCountInvariant(statement=_INVARIANT, chunks_checked=checked, mismatches=mismatches))


@router.get("/tables/{table}/rows", response_model=TableRowsResponse, responses={422: {"model": ErrorResponse}},
            summary="Paginated rows of one whitelisted table (read-only)")
def table_rows(table: TableName, limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
               svc: Services = Depends(get_services), conn: Conn = Depends(get_db)):
    total = svc.repo.count_rows(conn, table.value)
    columns, rows = svc.repo.list_rows(conn, table.value, limit, offset)
    return TableRowsResponse(table=table, columns=columns, rows=[dict(zip(columns, r)) for r in rows],
                             total=total, limit=limit, offset=offset)
