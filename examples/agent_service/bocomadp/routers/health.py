"""Health-check router.

Mounted on the FastAPI app returned by ``create_app`` via
``app.include_router(health_router)``. Keep this dependency-free so
container orchestrators (k8s liveness/readiness) get a fast 200.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import __version__
from ..logging.trace_context import get_current_trace_id

health_router = APIRouter(tags=["health"])


@health_router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict:
    """Return 200 if the process is alive."""
    return {
        "status": "ok",
        "version": __version__,
        "trace_id": get_current_trace_id(),
    }


@health_router.get("/readyz", summary="Readiness probe")
async def readyz(request: Request) -> dict:
    """Return 200 only if backing services (storage/bus) are reachable.

    The check is intentionally lightweight: it probes ``app.state.storage``
    presence rather than a real round-trip. Replace the body with a real
    ping (``await storage.ping()``) once your storage exposes one.
    """
    storage = getattr(request.app.state, "storage", None)
    message_bus = getattr(request.app.state, "message_bus", None)
    ready = storage is not None and message_bus is not None
    return {
        "ready": ready,
        "storage": type(storage).__name__ if storage else "missing",
        "message_bus": type(message_bus).__name__ if message_bus else "missing",
        "trace_id": get_current_trace_id(),
    }
