from fastapi import APIRouter

from app.api import benchmarks, files, health, inspect, integrity, planned, reliability, simulations, storage, system

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(planned.router)
api_router.include_router(files.router)
api_router.include_router(storage.router)
api_router.include_router(integrity.router)
api_router.include_router(inspect.router)
api_router.include_router(system.router)
api_router.include_router(reliability.router)
api_router.include_router(benchmarks.router)
api_router.include_router(simulations.router)
