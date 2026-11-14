from fastapi import APIRouter, Depends

from app.api import benchmarks, files, health, inspect, integrity, planned, reliability, simulations, storage, system
from app.api.auth import (
    admin_for_mutations,
    demo_for_mutations,
    demo_for_mutations_and_downloads,
    require_admin,
)
from app.api.ratelimit import files_limits, maintenance_limits, simulation_limits

# Role checks and abuse limits are no-ops with the local backend. With the cloud backend: reads are public, upload/delete/download/
# simulation need the demo token, audits/reconciliation/benchmarks/fault lab need the admin token (see app/api/auth.py); expensive
# routes are throttled per client address and concurrency-gated (see app/api/ratelimit.py). Authentication runs first, so a refused
# token never takes a concurrency slot.
api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(planned.router)
api_router.include_router(files.router, dependencies=[Depends(demo_for_mutations_and_downloads), Depends(files_limits)])
api_router.include_router(storage.router)
api_router.include_router(integrity.router, dependencies=[Depends(require_admin), Depends(maintenance_limits)])
api_router.include_router(inspect.router)
api_router.include_router(system.router)
api_router.include_router(reliability.router, dependencies=[Depends(admin_for_mutations), Depends(maintenance_limits)])
api_router.include_router(benchmarks.router, dependencies=[Depends(admin_for_mutations), Depends(maintenance_limits)])
api_router.include_router(simulations.router, dependencies=[Depends(demo_for_mutations), Depends(simulation_limits)])
