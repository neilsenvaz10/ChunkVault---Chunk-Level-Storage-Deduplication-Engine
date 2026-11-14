"""Authentication for the cloud backend: a demo role and an admin role, both server-validated bearer tokens.

* ``local`` backend: no authentication at all (unchanged behaviour; the app is for the owner's machine).
* ``cloud`` backend: reads are public; mutations and anything expensive need a token.
    - demo  : upload, delete, download/restore, simulations
    - admin : everything demo can do, plus integrity audits, reconciliation, garbage collection, benchmarks, the fault lab

Rules: tokens are compared in constant time against values that exist only in the backend's environment; they are never logged,
echoed, or placed in an error message; the admin token is never sent to, or accepted from, anything but the ``Authorization``
header. This is access control by a shared secret, not user accounts, and it is independent of CORS or anything the browser holds.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from app.api.ratelimit import limits_of
from app.core.errors import ChunkVaultError, TooManyRequestsError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthenticationError(ChunkVaultError):
    code = "unauthorized"
    status_code = 401


class ForbiddenError(ChunkVaultError):
    code = "forbidden"
    status_code = 403


def _presented(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _role(request: Request) -> str | None:
    """'admin', 'demo' or None. Both comparisons always run, so timing does not reveal which token matched."""
    cloud = request.app.state.settings.cloud
    token = _presented(request)
    if cloud is None or token is None:
        return None
    given = token.encode("utf-8")
    is_admin = hmac.compare_digest(given, cloud.admin_token.encode("utf-8"))
    is_demo = hmac.compare_digest(given, cloud.demo_token.encode("utf-8"))
    return "admin" if is_admin else "demo" if is_demo else None


def _enforced(request: Request) -> bool:
    return request.app.state.settings.cloud is not None


def _checked_role(request: Request) -> str | None:
    """Role of the presented token, with brute-force protection: an address that has failed too often is refused (429) before its
    token is even compared; an INVALID token (one was presented and matched nothing) counts as a failure; a missing token does not."""
    lim = limits_of(request)
    key = lim.address(request) if lim is not None else None
    if lim is not None:
        wait = lim.auth_failures.retry_after(key)
        if wait:
            raise TooManyRequestsError("too many failed authentication attempts from this address; try again later", retry_after=wait)
    role = _role(request)
    if role is None and lim is not None and _presented(request) is not None:
        lim.auth_failures.record(key)
    return role


def require_demo(request: Request) -> None:
    if not _enforced(request):
        return
    role = _checked_role(request)
    if role is None:
        raise AuthenticationError("a valid access token is required for this operation")
    # admin satisfies demo


def require_admin(request: Request) -> None:
    if not _enforced(request):
        return
    role = _checked_role(request)
    if role is None:
        raise AuthenticationError("a valid access token is required for this operation")
    if role != "admin":
        raise ForbiddenError("this operation requires administrator access")


def demo_for_mutations_and_downloads(request: Request) -> None:
    """Files router: listing and inspection are public; upload, delete and download (restore) need the demo token."""
    if request.method not in SAFE_METHODS or request.url.path.endswith("/download"):
        require_demo(request)


def admin_for_mutations(request: Request) -> None:
    """Benchmarks / fault lab: reading results is public, starting or cancelling anything needs the admin token."""
    if request.method not in SAFE_METHODS:
        require_admin(request)


def demo_for_mutations(request: Request) -> None:
    if request.method not in SAFE_METHODS:
        require_demo(request)
