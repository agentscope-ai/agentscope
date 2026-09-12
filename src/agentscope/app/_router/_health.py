# -*- coding: utf-8 -*-
"""The health router."""

from fastapi import APIRouter, Depends, Request, Response, status

from ._schema import ComponentStatus, HealthResponse
from ..deps import get_current_user_id

health_router = APIRouter(tags=["health"])

# Attached by ``create_app`` before the server starts serving.
_EAGER_COMPONENTS = ("storage", "message_bus", "workspace_manager")

# Built during the lifespan startup, in the order they are created.
_LIFESPAN_COMPONENTS = (
    "background_task_manager",
    "chat_run_registry",
    "scheduler_manager",
    "resource_access_service",
    "chat_service",
    "session_service",
)

# Optional hubs, attached eagerly — empty means the deployment
# registered none, which is a healthy state, not a failure.
_OPTIONAL_HUBS = ("mcp_hubs", "skill_hubs")


@health_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Report service liveness and per-component readiness",
)
async def get_health(
    request: Request,
    response: Response,
    _: str = Depends(get_current_user_id),
) -> HealthResponse:
    """Report whether the service is ready to serve requests.

    The check is deliberately I/O-free — it only inspects what is
    attached to ``app.state``, so probing it never touches Redis, the
    database or a workspace backend.

    Under a plain ``uvicorn.run(app)`` the ``not_ready`` status is
    effectively unreachable: uvicorn completes the lifespan startup
    before it accepts connections, so anything that reaches this handler
    already has the lifespan components in place.  What it does catch is
    the mount-as-sub-app deployment documented in
    :func:`~agentscope.app.create_app` — Starlette does not run a mounted
    sub-app's lifespan, so every lifespan component stays missing and
    all business endpoints are broken.  Reporting 503 here turns that
    silent misconfiguration into one clear signal.

    Args:
        request (`Request`): The incoming FastAPI request.
        response (`Response`): The outgoing response, whose status code
            is downgraded to ``503`` when the service is not ready.

    Returns:
        `HealthResponse`: The overall status, API version and the
        per-component readiness map.
    """
    state = request.app.state
    components: dict[str, ComponentStatus] = {
        name: "ok" if getattr(state, name, None) is not None else "not_ready"
        for name in _EAGER_COMPONENTS + _LIFESPAN_COMPONENTS
    }
    for name in _OPTIONAL_HUBS:
        components[name] = "ok" if getattr(state, name, None) else "disabled"

    # The KB feature is off when no manager was passed to create_app; only
    # when it is on does a missing service mean the startup fell short.
    if getattr(state, "knowledge_base_manager", None) is None:
        components["knowledge_base"] = "disabled"
    elif getattr(state, "knowledge_base_service", None) is not None:
        components["knowledge_base"] = "ok"
    else:
        components["knowledge_base"] = "not_ready"

    ready = all(value != "not_ready" for value in components.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if ready else "not_ready",
        version=request.app.version,
        components=components,
    )
