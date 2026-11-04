"""Narrow, read-only, paginated inspection of the metadata tables.

Only the four whitelisted tables are reachable; table names come from an enum,
never from free text, and no endpoint accepts SQL or filter expressions.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.database.init import get_schema_version
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

_ORDER = {
    TableName.FILES: "created_at DESC, file_id",
    TableName.CHUNKS: "hash",
    TableName.FILE_CHUNKS: "file_id, chunk_index",
    TableName.OPERATIONS: "operation_id DESC",
}

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
def schema(conn: sqlite3.Connection = Depends(get_db)):
    tables = []
    for t in TableName:
        fks = {r["from"]: ForeignKeyInfo(table=r["table"], column=r["to"], on_delete=r["on_delete"])
               for r in conn.execute(f"PRAGMA foreign_key_list({t.value})")}  # t is a whitelisted enum value
        cols = [ColumnInfo(
            name=r["name"], type=r["type"], not_null=bool(r["notnull"]), primary_key_position=r["pk"],
            default=r["dflt_value"], foreign_key=fks.get(r["name"]),
            description=_COLUMN_DOC[t.value].get(r["name"], ""))
            for r in conn.execute(f"PRAGMA table_info({t.value})")]
        count = conn.execute(f"SELECT COUNT(*) FROM {t.value}").fetchone()[0]
        tables.append(TableInfo(name=t, description=_TABLE_DOC[t], row_count=count, columns=cols))
    mismatches = conn.execute("SELECT COUNT(*) FROM v_ref_count_mismatch").fetchone()[0]
    checked = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return SchemaResponse(
        schema_version=get_schema_version(conn), tables=tables,
        ref_count_invariant=RefCountInvariant(statement=_INVARIANT, chunks_checked=checked, mismatches=mismatches))


@router.get("/tables/{table}/rows", response_model=TableRowsResponse, responses={422: {"model": ErrorResponse}},
            summary="Paginated rows of one whitelisted table (read-only)")
def table_rows(table: TableName, limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
               conn: sqlite3.Connection = Depends(get_db)):
    total = conn.execute(f"SELECT COUNT(*) FROM {table.value}").fetchone()[0]
    cur = conn.execute(f"SELECT * FROM {table.value} ORDER BY {_ORDER[table]} LIMIT ? OFFSET ?", (limit, offset))
    columns = [d[0] for d in cur.description]
    return TableRowsResponse(table=table, columns=columns, rows=[dict(zip(columns, tuple(r))) for r in cur.fetchall()],
                             total=total, limit=limit, offset=offset)
