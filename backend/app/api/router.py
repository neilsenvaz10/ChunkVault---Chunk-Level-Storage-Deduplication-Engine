from fastapi import APIRouter

from app.api import health, planned

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(planned.router)
