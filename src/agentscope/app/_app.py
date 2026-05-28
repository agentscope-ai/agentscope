# -*- coding: utf-8 -*-
"""AgentScope app factory."""
from typing import Type, TYPE_CHECKING, Any

from ._lifespan import lifespan
from ._manager import WorkspaceManagerBase
from ._router import (
    agent_router,
    background_task_router,
    chat_router,
    credential_router,
    model_router,
    schedule_router,
    session_router,
    workspace_router,
)
from ._types import AgentMiddlewareFactory, AgentToolFactory
from .storage import StorageBase
from ..credential import CredentialFactory, CredentialBase

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.middleware import Middleware as FastAPIMiddleware
else:
    FastAPI = Any
    FastAPIMiddleware = Any


def create_app(
    storage: StorageBase,
    *,
    workspace_manager: WorkspaceManagerBase | None = None,
    extra_credentials: list[Type[CredentialBase]] | None = None,
    extra_middlewares: list[FastAPIMiddleware] | None = None,
    extra_agent_middlewares: AgentMiddlewareFactory | None = None,
    extra_agent_tools: AgentToolFactory | None = None,
    title: str = "AgentScope",
    version: str = "2.0.0",
) -> FastAPI:
    """Create and configure a FastAPI application.

    This is the primary entry point for embedding AgentScope into an existing
    service or running it standalone.  All built-in routers are registered
    automatically; pass ``extra_middlewares`` to add your own.

    Usage — standalone::

        app = create_app(storage=RedisStorage())
        uvicorn.run(app, host="0.0.0.0", port=8000)

    Usage — mount onto an existing app::

        root = FastAPI()
        agentscope_app = create_app(storage=RedisStorage())
        root.mount("/agentscope", agentscope_app)

    Args:
        storage (`StorageBase`):
            The storage backend.  Its lifecycle (``__aenter__`` /
            ``__aexit__``) is managed by the app lifespan.
        workspace_manager (`WorkspaceManagerBase | None`, optional):
            The workspace manager.  When provided, its ``close_all`` is called
            on shutdown.  Pass a :class:`~agentscope.app._manager.
            LocalWorkspaceManager`
            for local-directory workspaces.
        extra_credentials (`list[Type[CredentialBase]] | None`, optional):
            Additional :class:`~agentscope.credential.CredentialBase`
            subclasses to register before the app starts.  Equivalent to
            calling :func:`~agentscope.credential.CredentialFactory.
            register_credential` for each class.
        extra_middlewares (`list[Middleware] | None`, optional):
            Additional ASGI middlewares to add to the application.
        extra_agent_middlewares (`AgentMiddlewareFactory | None`, optional):
            An async factory ``(user_id, agent_id, session_id) -> awaitable
            of list[MiddlewareBase]`` that produces extra
            :class:`~agentscope.middleware.MiddlewareBase` instances to
            attach to the agent on each invocation.  Called once per agent
            assembly (i.e. per chat turn / scheduled trigger), so it can
            return user/session-specific middleware (auth, audit logging,
            tenant isolation, etc.).  The returned middlewares are appended
            to the framework-supplied ones (e.g. ``ToolOffloadMiddleware``).
        extra_agent_tools (`AgentToolFactory | None`, optional):
            An async factory ``(user_id, agent_id, session_id) -> awaitable
            of list[ToolBase]`` that produces extra
            :class:`~agentscope.tool.ToolBase` instances to register in the
            agent's toolkit on each invocation.  Useful when tool
            availability depends on the caller (per-tenant integrations,
            user-specific credentials).  The returned tools are added to
            the workspace-derived tools in the toolkit's ``"basic"`` group.
        title (`str`, defaults to ``"AgentScope"``):
            OpenAPI title shown in the docs UI.
        version (`str`, defaults to ``"2.0.0"``):
            API version shown in the docs UI.

    Returns:
        `FastAPI`: A fully configured application ready to serve requests.
    """
    from fastapi import FastAPI

    # Register any user-supplied credential types before the app starts
    for cls in extra_credentials or []:
        CredentialFactory.register_credential(cls)

    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # Attach shared state that lifespan and dependencies read from app.state
    app.state.storage = storage
    app.state.workspace_manager = workspace_manager
    app.state.extra_agent_middlewares = extra_agent_middlewares
    app.state.extra_agent_tools = extra_agent_tools

    # Built-in routers
    for router in (
        agent_router,
        background_task_router,
        chat_router,
        credential_router,
        schedule_router,
        session_router,
        workspace_router,
        model_router,
    ):
        app.include_router(router)

    # Optional extra middlewares
    for middleware in extra_middlewares or []:
        app.add_middleware(middleware.cls, **middleware.kwargs)

    return app
