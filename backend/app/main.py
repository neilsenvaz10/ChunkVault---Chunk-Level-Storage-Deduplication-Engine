"""ChunkVault FastAPI application (single process, modular internals)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.benchmarking.jobs import BenchmarkJobs
from app.api.guard import LocalAccessGuard
from app.api.limits import BodyLimitMiddleware
from app.api.ratelimit import Limits
from app.api.router import api_router
from app.core.config import Settings
from app.core.envfile import load_env_file
from app.core.errors import DatabaseUnavailableError
from app.core.readiness import Readiness
from app.core.services import build_services
from app.schemas.common import ErrorResponse


def _verify_database(db) -> None:
    """A fast reachability probe first (a dead database must not cost a 15 s pool wait per attempt), then the schema verification."""
    reachable = getattr(db, "reachable", None)
    if reachable is not None and not reachable(3.0):
        raise DatabaseUnavailableError("the database is not reachable")
    db.prepare_for_startup()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_env_file()  # local development: backend/.env fills only variables the OS has not set
        settings = Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: build the configured backend (a backend that is unavailable fails here, never silently falls back), then
        # prepare it: local creates directories and the idempotent schema; cloud would only verify the schema version.
        app.state.services = build_services(settings)
        db = app.state.services.db
        # Readiness of the database. Local backend: always ready (a failure here still stops the process, as before). Cloud backend: a
        # TRANSIENT outage keeps the process alive and not-ready, and it recovers by itself; a wrong/missing schema still stops it.
        readiness = Readiness(lambda: _verify_database(db), settings.cloud.startup_retry_seconds if settings.cloud else 0.0)
        app.state.readiness = readiness
        try:
            _verify_database(db)
            readiness.mark_ready()
        except DatabaseUnavailableError as exc:
            if settings.backend != "cloud":
                raise
            logging.getLogger("chunkvault").warning("starting WITHOUT a database connection (%s): liveness is up, readiness will "
                                                    "recover when the database answers", type(exc).__name__)
            readiness.mark_unavailable(type(exc).__name__)
        app.state.benchmarks = BenchmarkJobs(settings.benchmarks_dir, settings.cdc)
        yield
        app.state.benchmarks.shutdown()  # never leave a benchmark worker running
        for closer in (getattr(app.state.services.store, "close", None), getattr(app.state.services.db, "close", None)):
            if closer is not None:
                closer()  # cloud: close the HTTP client and the connection pool
        # Shutdown: connections are per-request and already closed; nothing to release.

    cloud_mode = settings.backend == "cloud"
    app = FastAPI(
        title="ChunkVault API",
        version=settings.app_version,
        description=(
            "Chunk-level storage deduplication engine (hosted demo: single process, one worker; Supabase PostgreSQL + private "
            "Storage). Reads are public; uploads, downloads, deletes and simulations need the demo token; audits, reconciliation, "
            "garbage collection, benchmarks and the fault lab need the admin token."
            if cloud_mode else
            "Chunk-level storage deduplication engine (single process, sequential "
            "writes: run with ONE worker). No authentication: local use only."
        ),
        lifespan=lifespan,
        # under /api so the dev proxy reaches them and the SPA keeps its own /docs route.
        # The interactive Swagger page exists locally only; /api/openapi.json stays in both modes because the in-app API explorer reads it.
        docs_url=None if cloud_mode else "/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    if settings.cloud is not None and settings.cloud.rate_limiting:
        app.state.limits = Limits(settings.cloud)  # absent locally: no limits, behaviour unchanged
    # Added first = innermost of the two, so CORS headers also appear on 413/400 rejections.
    app.add_middleware(BodyLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"] if settings.backend == "cloud" else ["Content-Type"],
        expose_headers=["Content-Disposition", "X-ChunkVault-SHA256", "Retry-After"],
    )
    # Outermost: reject foreign Host headers and cross-site modification requests before anything else runs.
    app.add_middleware(LocalAccessGuard, allowed_hosts=settings.allowed_hosts, allowed_origins=settings.cors_origins)
    register_exception_handlers(app)
    app.include_router(api_router)
    if settings.backend == "cloud":  # quota view and garbage collection exist only where they mean something
        from app.api.admin import router as cloud_router

        app.include_router(cloud_router, prefix="/api")
    return app


app = create_app()
