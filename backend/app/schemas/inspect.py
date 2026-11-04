"""Schemas for the read-only metadata inspection endpoints."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TableName(StrEnum):
    """The only tables that can be inspected (a whitelist, never user-supplied SQL)."""

    FILES = "files"
    CHUNKS = "chunks"
    FILE_CHUNKS = "file_chunks"
    OPERATIONS = "operations"


class ForeignKeyInfo(BaseModel):
    table: str
    column: str
    on_delete: str


class ColumnInfo(BaseModel):
    name: str
    type: str
    not_null: bool
    primary_key_position: int = Field(description="0 if not part of the primary key, else 1-based position.")
    default: str | None
    foreign_key: ForeignKeyInfo | None
    description: str


class TableInfo(BaseModel):
    name: TableName
    description: str
    row_count: int
    columns: list[ColumnInfo]


class RefCountInvariant(BaseModel):
    statement: str
    chunks_checked: int
    mismatches: int = Field(description="Chunks whose ref_count differs from their manifest occurrences.")


class SchemaResponse(BaseModel):
    schema_version: int
    tables: list[TableInfo]
    ref_count_invariant: RefCountInvariant


class TableRowsResponse(BaseModel):
    table: TableName
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class ChunkReference(BaseModel):
    file_id: str
    filename: str
    file_status: str
    chunk_index: int


class ChunkDetailResponse(BaseModel):
    """GET /api/chunks/{hash}: a chunk and every manifest row that references it."""

    hash: str
    size_bytes: int
    ref_count: int
    state: str
    created_at: str
    actual_references: int = Field(description="COUNT of manifest rows, computed now (not the stored counter).")
    references: list[ChunkReference]
    references_truncated: bool
