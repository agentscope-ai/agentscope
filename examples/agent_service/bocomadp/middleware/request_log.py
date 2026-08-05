"""Access log middleware — one structured line per HTTP request.

Pure ASGI. Depends on the trace ContextVar set by :class:`TraceMiddleware`,
so it MUST be registered AFTER TraceMiddleware (outermost wrapper wins
in Starlette's middleware stack — the last-added middleware runs first).

Output goes through the standard ``logging`` module so the trace filter
and formatter installed by :func:`configure_logging` apply automatically.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from ..logging.trace_context import get_current_trace_id

logger = logging.getLogger("bocomadp.access")

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class AccessLogMiddleware:
    """Log method, path, status, duration and trace id for every request."""

    def __init__(self, app: ASGIApp, *, skip_paths: tuple[str, ...] = ("/healthz",)) -> None:
        self.app = app
        self.skip_paths = skip_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.skip_paths:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        start = time.perf_counter()
        status_code: int | None = None

        async def send_and_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_and_capture)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            trace_id = get_current_trace_id() or "-"
            logger.info(
                "%s %s -> %s in %.1fms trace_id=%s",
                method,
                path,
                status_code,
                duration_ms,
                trace_id,
            )
