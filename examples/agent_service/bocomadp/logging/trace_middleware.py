"""Gateway request trace middleware (ASGI).

Adapted from deer-flow-2.0's trace_middleware.py. Pure ASGI, no Starlette
import required at runtime — works with any ASGI server (uvicorn / daphne).

Bound to the app via ``create_app(extra_middlewares=[Middleware(...)])``.
For every HTTP request it:

1. Reads the inbound ``X-Trace-Id`` header (if any).
2. Normalizes it (falls back to a fresh uuid4 hex).
3. Binds it to the request's ContextVar via :func:`request_trace_context`.
4. Writes the same trace id back to the response ``X-Trace-Id`` header.

The ``enabled`` flag is a startup snapshot (see deer-flow's rationale):
``configure_logging()`` installs the trace filter + formatter once at
startup, so live-reading ``enhance.enabled`` here would let the response
header appear while the log formatter stays on its startup value.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .trace_context import TRACE_ID_HEADER, request_trace_context

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TraceMiddleware:
    """Bind a request-level trace id and write it to HTTP response headers."""

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = bool(enabled)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        # Inbound X-Trace-Id (may be missing — generate one).
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        incoming_trace_id: str | None = None
        header_key = TRACE_ID_HEADER.lower().encode("latin-1")
        for raw_key, raw_value in headers:
            if raw_key == header_key:
                try:
                    incoming_trace_id = raw_value.decode("latin-1")
                except Exception:
                    incoming_trace_id = None
                break

        with request_trace_context(incoming_trace_id) as trace_id:

            async def send_with_trace(message: Message) -> None:
                if message["type"] == "http.response.start":
                    # Mutate the mutable headers list in place.
                    response_headers: list[tuple[bytes, bytes]] = message.get(
                        "headers", [],
                    )
                    # Replace any existing X-Trace-Id to avoid duplicates.
                    response_headers = [
                        (k, v)
                        for (k, v) in response_headers
                        if k != header_key
                    ]
                    response_headers.append(
                        (header_key, trace_id.encode("latin-1")),
                    )
                    message["headers"] = response_headers
                await send(message)

            await self.app(scope, receive, send_with_trace)
