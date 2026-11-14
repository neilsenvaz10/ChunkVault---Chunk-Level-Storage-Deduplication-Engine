"""Structured error responses: {"error": {"code", "message", "details"}}."""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ChunkVaultError, DatabaseUnavailableError, is_concurrency_conflict

logger = logging.getLogger("chunkvault")


def error_response(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ChunkVaultError)
    async def _domain_error(request: Request, exc: ChunkVaultError) -> JSONResponse:
        if isinstance(exc, DatabaseUnavailableError):
            readiness = getattr(request.app.state, "readiness", None)
            if readiness is not None:
                readiness.mark_unavailable(type(exc).__name__)  # later requests fail fast instead of each waiting for a pooled connection
            if request.url.path in ("/api/health", "/api/health/ready"):  # readiness reports 'degraded' in its own shape
                st = request.app.state.settings
                return JSONResponse(status_code=503, content={
                    "status": "degraded", "service": st.app_name, "version": st.app_version,
                    "database": {"connected": False, "foreign_keys_enforced": False, "schema_version": None},
                    "max_upload_bytes": st.max_upload_bytes})
        resp = error_response(exc.status_code, exc.code, exc.message, exc.details)
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            resp.headers["Retry-After"] = str(retry_after)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "message": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            code = "not_found"
        elif exc.status_code == 405:
            code = "method_not_allowed"
        else:
            code = f"http_{exc.status_code}"
        try:
            message = HTTPStatus(exc.status_code).phrase
        except ValueError:
            message = "HTTP error"
        if isinstance(exc.detail, str) and exc.detail:
            message = exc.detail
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        if is_concurrency_conflict(exc):
            return error_response(409, "concurrent_conflict", "A concurrent operation conflicted with this request; nothing was changed, retry.")
        logger.exception("unhandled error", exc_info=exc)
        return error_response(500, "internal_error", "An unexpected error occurred.")
