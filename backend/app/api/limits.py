"""Bounded request bodies, enforced before any body parsing.

Multipart parsing (``UploadFile``) spools the body into a temporary file
BEFORE the endpoint runs, so an endpoint-level size check cannot stop an
oversized request from consuming memory/temporary disk. This pure-ASGI
middleware therefore:

* rejects a declared ``Content-Length`` above the limit immediately (413), reading nothing;
* rejects a malformed ``Content-Length`` (400);
* counts the bytes actually received, so chunked requests or lying clients are
  cut off as soon as the limit is crossed (413), whatever the headers said.

``POST /api/files`` may carry ``max_upload_bytes`` + a small multipart overhead
allowance; every other request body is limited to ``DEFAULT_BODY_LIMIT``.
The exact file-size limit is still enforced while the file is copied into staging.
"""

from __future__ import annotations

import json

DEFAULT_BODY_LIMIT = 1024 * 1024
MULTIPART_OVERHEAD = 1024 * 1024


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app, max_upload_bytes: int) -> None:
        self.app = app
        self._upload_limit = max_upload_bytes + MULTIPART_OVERHEAD

    def _limit_for(self, scope) -> int:
        if scope["method"] == "POST" and scope["path"].rstrip("/") == "/api/files":
            return self._upload_limit
        return DEFAULT_BODY_LIMIT

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                await self._reject(send, 400, "invalid_content_length", "Content-Length header is invalid.")
                return
            if int(declared) > limit:
                await self._reject(send, 413, "upload_too_large", "Request body exceeds the size limit.")
                return

        received = 0
        started = False
        exceeded = False

        async def counting_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if exceeded:
                # FastAPI turns any body-parsing exception into a generic 400; the
                # real cause is the size limit, so replace that response with 413.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await self._reject(send, 413, "upload_too_large", "Request body exceeds the size limit.")
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                return
            await self._reject(send, 413, "upload_too_large", "Request body exceeds the size limit.")

    @staticmethod
    async def _reject(send, status: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message, "details": None}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
