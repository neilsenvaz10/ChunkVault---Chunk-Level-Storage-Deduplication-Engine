from fastapi import APIRouter

from app.api import files, health, integrity, planned, storage

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(planned.router)
api_router.include_router(files.router)
api_router.include_router(storage.router)
api_router.include_router(integrity.router)
