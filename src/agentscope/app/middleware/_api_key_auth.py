"""Opt-in API-key authentication middleware for the AgentScope service.

Addresses #3314 (X-User-ID header accepted as identity without
verification, CWE-306): when ``AGENTSCOPE_API_KEY`` is configured,
every request must present a matching key via ``Authorization: Bearer``
or ``X-API-Key`` or be rejected with 401. When unset, behavior is
unchanged (no breaking change for local deployments).

This is shared-key authentication (one server-wide secret), not
per-user identity verification. User identity remains the
caller-supplied ``X-User-ID`` header; production deployments should
authenticate users at a reverse proxy that injects a verified header
(see #3314 suggested remediation step 2). Per-user key management and
full identity verification (JWT/OAuth) are separate concerns left for
future issues (#1061 is the tracking issue for identity auth).
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

#: Environment variable holding the shared API key. Unset/empty means
#: authentication is disabled (opt-in by design — zero breaking change).
API_KEY_ENV = "AGENTSCOPE_API_KEY"


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that lack the configured shared API key.

    Compares presented credentials against the server-wide key using
    ``hmac.compare_digest`` (constant-time) to avoid timing oracles.
    Accepts the key via ``Authorization: Bearer`` or ``X-API-Key``.
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._is_exempt(request.url.path):
            return await call_next(request)
        key = self._extract_key(request)
        # Encode to bytes before comparing: compare_digest's str mode
        # only accepts ASCII, so a header carrying raw non-ASCII bytes
        # (a hand-crafted request can send them) would raise TypeError
        # and show up as a 500 instead of a 401.
        if key is None or not hmac.compare_digest(
            key.encode("utf-8"), self._api_key.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key."},
            )
        return await call_next(request)

    @staticmethod
    def _extract_key(request: Request) -> str | None:
        """Pull the presented key from either accepted header."""
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return bearer[len("Bearer ") :] or None
        return request.headers.get("X-API-Key") or None

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return path == "/health" or path.startswith("/health/")
