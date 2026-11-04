"""Local-access guard: Host and Origin validation.

ChunkVault has no authentication and is meant for localhost use. A web page the user happens to
have open can still make their browser talk to ``localhost``, so two cheap checks matter:

* **Host header allow-list** (defends against DNS rebinding): the request must be addressed to
  one of ``allowed_hosts`` (default localhost, 127.0.0.1, [::1]); anything else is rejected, so an
  attacker-controlled hostname that rebinds to 127.0.0.1 cannot read the API.
* **Origin check on mutations** (defends against cross-site request forgery): a browser always
  sends ``Origin`` on cross-site POST/DELETE; if present it must be one of the configured CORS
  origins. Multipart uploads and body-less POSTs are "simple requests" that browsers send without
  a CORS preflight, so CORS alone does not stop them from executing.

Requests without an Origin header (curl, scripts, the test client) are unaffected.
"""

from __future__ import annotations

import json

_SAFE = {"GET", "HEAD", "OPTIONS"}


def host_name(raw: str) -> str:
    """'LocalHost:8000' -> 'localhost'; '[::1]:8000' -> '[::1]'."""
    raw = raw.strip().lower()
    if raw.startswith("["):
        return raw.split("]")[0] + "]"
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


class LocalAccessGuard:
    def __init__(self, app, allowed_hosts: tuple[str, ...], allowed_origins: tuple[str, ...]) -> None:
        self.app = app
        self._hosts = {host_name(h) for h in allowed_hosts}
        self._origins = {o.strip().rstrip("/").lower() for o in allowed_origins}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if host_name(headers.get(b"host", b"").decode("latin-1")) not in self._hosts:
            await self._reject(send, 400, "host_not_allowed", "The Host header is not an allowed local address.")
            return
        origin = headers.get(b"origin")
        if scope["method"] not in _SAFE and origin is not None:
            if origin.decode("latin-1").strip().rstrip("/").lower() not in self._origins:
                await self._reject(send, 403, "origin_not_allowed", "Cross-origin modification requests are not allowed.")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message, "details": None}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
