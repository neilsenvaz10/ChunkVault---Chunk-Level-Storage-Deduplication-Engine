"""Read-only listing of planned (not yet implemented) API endpoints.

Planned endpoints are deliberately NOT registered: a request to them returns
the standard 404. Every entry here is marked ``status: "planned"``. Endpoints
leave this registry when they become functional.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.planned import PlannedEndpoint, PlannedEndpointsResponse

router = APIRouter(tags=["meta"])

PLANNED_ENDPOINTS: tuple[PlannedEndpoint, ...] = (
    PlannedEndpoint(method="POST", path="/api/benchmarks", summary="Run a local benchmark",
                    request_schema="BenchmarkRequest", response_schema="BenchmarkResponse", phase=6),
    PlannedEndpoint(method="POST", path="/api/simulations", summary="Run the analytical capacity model",
                    request_schema="SimulationRequest", response_schema="SimulationResponse", phase=7),
)


@router.get(
    "/planned-endpoints",
    response_model=PlannedEndpointsResponse,
    summary="Endpoints planned but NOT yet implemented",
)
def planned_endpoints() -> PlannedEndpointsResponse:
    return PlannedEndpointsResponse(endpoints=list(PLANNED_ENDPOINTS))
