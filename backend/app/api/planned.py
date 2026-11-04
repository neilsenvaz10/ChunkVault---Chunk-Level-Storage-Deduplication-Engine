"""Read-only listing of planned endpoints.

The planned endpoints themselves are deliberately NOT registered: a request to
them returns the standard 404. This listing is the only place they appear, and
every entry is marked ``status: "planned"``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.planned import PlannedEndpoint, PlannedEndpointsResponse

router = APIRouter(tags=["meta"])

PLANNED_ENDPOINTS: tuple[PlannedEndpoint, ...] = (
    PlannedEndpoint(method="POST", path="/api/files", summary="Upload a file (chunk, hash, deduplicate, store)",
                    request_schema="multipart/form-data", response_schema="UploadResponse", phase=3),
    PlannedEndpoint(method="GET", path="/api/files/{file_id}/content", summary="Restore a file (streamed, hash-verified)",
                    request_schema=None, response_schema="application/octet-stream", phase=4),
    PlannedEndpoint(method="DELETE", path="/api/files/{file_id}", summary="Delete a file and release its chunks",
                    request_schema=None, response_schema="DeleteFileResponse", phase=4),
    PlannedEndpoint(method="GET", path="/api/files", summary="List stored files",
                    request_schema=None, response_schema="FileListResponse", phase=3),
    PlannedEndpoint(method="GET", path="/api/files/{file_id}", summary="Inspect a file and its chunk list",
                    request_schema=None, response_schema="FileDetailResponse", phase=3),
    PlannedEndpoint(method="POST", path="/api/integrity/checks", summary="Run an integrity audit",
                    request_schema="IntegrityCheckRequest", response_schema="IntegrityCheckResponse", phase=5),
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
