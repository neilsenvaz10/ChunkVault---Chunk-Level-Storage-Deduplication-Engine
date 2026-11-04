"""Registry of planned (not yet implemented) API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PlannedEndpoint(BaseModel):
    method: Literal["GET", "POST", "DELETE"]
    path: str
    summary: str
    request_schema: str | None
    response_schema: str | None
    phase: int
    status: Literal["planned"] = "planned"


class PlannedEndpointsResponse(BaseModel):
    endpoints: list[PlannedEndpoint]
