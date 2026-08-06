"""Global error handler middleware.

Catches unhandled exceptions in the ASGI app and returns a uniform JSON
``{"detail": ..., "trace_id": ...}`` response with status 500. Without
this, uvicorn returns a plain 500 and the trace_id (bound by
TraceMiddleware) never reaches the client, making it hard to correlate
a failure in logs with a failure reported by the frontend.
"""

from __future__ import annotations

import logging
import traceback
from typing import Awaitable, Callable

from ..logging.trace_context import get_current_trace_id

logger = logging.getLogger("bocomadp.errors")

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class ErrorHandlingMiddleware:
    """Catch unhandled exceptions and emit a traceable 500 response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            trace_id = get_current_trace_id() or "-"
            logger.exception("Unhandled exception trace_id=%s: %s", trace_id, exc)
            tb = traceback.format_exc()
            body = (
                b'{"detail": "Internal Server Error", '
                b'"trace_id": "' + trace_id.encode("ascii") + b'"}'
            )
            headers = [
                (b"content-type", b"application/json"),
                (b"x-trace-id", trace_id.encode("latin-1")),
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": headers,
                },
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                },
            )
