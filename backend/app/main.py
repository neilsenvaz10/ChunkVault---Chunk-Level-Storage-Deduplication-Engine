"""ChunkVault FastAPI application (single process, modular internals)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.benchmarking.jobs import BenchmarkJobs
from app.api.guard import LocalAccessGuard
from app.api.limits import BodyLimitMiddleware
from app.api.router import api_router
from app.core.config import Settings
from app.core.services import build_services
from app.database.init import initialize_database
from app.schemas.common import ErrorResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: create data directories and the schema (idempotent, no data inserted).
        settings.ensure_directories()
        initialize_database(settings.db_path)
        app.state.services = build_services(settings)
        app.state.benchmarks = BenchmarkJobs(settings.benchmarks_dir, settings.cdc)
        yield
        app.state.benchmarks.shutdown()  # never leave a benchmark worker running
        # Shutdown: connections are per-request and already closed; nothing to release.

    app = FastAPI(
        title="ChunkVault API",
        version=settings.app_version,
        description=(
            "Chunk-level storage deduplication engine (single process, sequential "
            "writes: run with ONE worker). No authentication: local use only."
        ),
        lifespan=lifespan,
        # under /api so the dev proxy reaches them and the SPA keeps its own /docs route
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    # Added first = innermost of the two, so CORS headers also appear on 413/400 rejections.
    app.add_middleware(BodyLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition", "X-ChunkVault-SHA256"],
    )
    # Outermost: reject foreign Host headers and cross-site modification requests before anything else runs.
    app.add_middleware(LocalAccessGuard, allowed_hosts=settings.allowed_hosts, allowed_origins=settings.cors_origins)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
