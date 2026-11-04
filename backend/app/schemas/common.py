"""Shared schemas: the structured error envelope and health response."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code, e.g. 'validation_error'.")
    message: str = Field(description="Human-readable summary.")
    details: Any = Field(default=None, description="Optional structured detail (e.g. field errors).")


class ErrorResponse(BaseModel):
    """Every non-2xx response from the API uses this envelope."""

    error: ErrorBody


class DatabaseHealth(BaseModel):
    connected: bool
    foreign_keys_enforced: bool
    schema_version: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    database: DatabaseHealth
    max_upload_bytes: int = Field(description="Configured upload size limit, so clients can validate before sending.")
