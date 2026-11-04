from fastapi import APIRouter

from app.api import files, health, inspect, integrity, planned, storage, system

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(planned.router)
api_router.include_router(files.router)
api_router.include_router(storage.router)
api_router.include_router(integrity.router)
api_router.include_router(inspect.router)
api_router.include_router(system.router)
