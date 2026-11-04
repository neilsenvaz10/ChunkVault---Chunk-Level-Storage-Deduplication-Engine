"""Read-only listing of planned (not yet implemented) API endpoints.

Planned endpoints are deliberately NOT registered: a request to them returns
the standard 404. Every entry here is marked ``status: "planned"``. Endpoints
leave this registry when they become functional.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.planned import PlannedEndpoint, PlannedEndpointsResponse

router = APIRouter(tags=["meta"])

# Nothing is planned any more: benchmark and simulation endpoints are implemented. The endpoint
# stays so clients can still ask, and the registry stays the single place new plans would be listed.
PLANNED_ENDPOINTS: tuple[PlannedEndpoint, ...] = ()


@router.get(
    "/planned-endpoints",
    response_model=PlannedEndpointsResponse,
    summary="Endpoints planned but NOT yet implemented",
)
def planned_endpoints() -> PlannedEndpointsResponse:
    return PlannedEndpointsResponse(endpoints=list(PLANNED_ENDPOINTS))
