"""Example business router — shows how to add product-specific endpoints.

Pattern:

1. Define an :class:`APIRouter` with a prefix.
2. Inject dependencies (storage, bus, current user) via ``FastAPI.Depends``.
3. Read the trace id from the ContextVar when you want to echo it.

This router is illustrative — delete or repurpose it. It demonstrates the
three things you'll usually do in a product router:

- talk to a backing service (``app.state.storage`` via ``Request``),
- emit a structured log line carrying the trace id,
- return the trace id in the JSON body so the client can correlate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..logging.trace_context import get_current_trace_id

logger = logging.getLogger("bocomadp.stats")

stats_router = APIRouter(prefix="/stats", tags=["stats"])


@stats_router.get("/ping", summary="Echo the current trace id")
async def ping(request: Request) -> dict:
    """Trivial endpoint: returns the trace id bound by TraceMiddleware."""
    trace_id = get_current_trace_id()
    logger.info("stats.ping hit trace_id=%s", trace_id)
    return {
        "pong": True,
        "trace_id": trace_id,
    }


@stats_router.get("/storage", summary="Report the storage backend class")
async def storage_info(request: Request) -> dict:
    """Read ``app.state.storage`` to report which backend is wired in.

    Replace this with real queries against your business tables once the
    product grows. The point here is to show how to reach the shared
    state that ``create_app`` puts on ``app.state``.
    """
    storage = getattr(request.app.state, "storage", None)
    return {
        "storage_class": type(storage).__name__ if storage else "missing",
        "trace_id": get_current_trace_id(),
    }
