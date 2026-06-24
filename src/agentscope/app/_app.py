# -*- coding: utf-8 -*-
"""AgentScope app factory."""
from typing import Type, TYPE_CHECKING, Any

from ._lifespan import lifespan
from .workspace_manager import WorkspaceManagerBase
from ._router import (
    agent_router,
    chat_router,
    credential_router,
    model_router,
    realtime_model_router,
    realtime_router,
    tts_model_router,
    schedule_router,
    session_router,
    workspace_router,
)
from ._types import AgentMiddlewareFactory, AgentToolFactory, SubAgentTemplate
from .message_bus import MessageBus
from .storage import StorageBase
from ..agent import Agent
from ..credential import CredentialFactory, CredentialBase
from .._version import __version__


if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.middleware import Middleware as FastAPIMiddleware
else:
    FastAPI = Any
    FastAPIMiddleware = Any


def create_app(
    storage: StorageBase,
    message_bus: MessageBus,
    workspace_manager: WorkspaceManagerBase,
    *,
    extra_credentials: list[Type[CredentialBase]] | None = None,
    extra_middlewares: list[FastAPIMiddleware] | None = None,
    extra_agent_middlewares: AgentMiddlewareFactory | None = None,
    extra_agent_tools: AgentToolFactory | None = None,
    custom_subagent_templates: list[SubAgentTemplate] | None = None,
    custom_agent_cls: Type[Agent] | None = None,
    title: str = "AgentScope",
    version: str = __version__,
) -> FastAPI:
    """Create and configure a FastAPI application.

    This is the primary entry point for embedding AgentScope into an existing
    service or running it standalone.  All built-in routers are registered
    automatically; pass ``extra_middlewares`` to add your own.

    Usage — standalone::

        app = create_app(
            storage=RedisStorage(),
            message_bus=RedisMessageBus(),
            workspace_manager=LocalWorkspaceManager(),
        )
        uvicorn.run(app, host="0.0.0.0", port=8000)

    Usage — mount onto an existing app::

        root = FastAPI()
        agentscope_app = create_app(
            storage=RedisStorage(),
            message_bus=RedisMessageBus(),
            workspace_manager=LocalWorkspaceManager(),
        )
        root.mount("/agentscope", agentscope_app)

    Args:
        storage (`StorageBase`):
            The storage backend.  Its lifecycle (``__aenter__`` /
            ``__aexit__``) is managed by the app lifespan.
        message_bus (`MessageBus`):
            The live message bus used for cross-session inbox delivery
            and idle-session triggers. Required — the bus is intentionally
            decoupled from ``storage`` so the persistence backend (e.g.
            SQL) can differ from the transport backend (Redis). Its
            lifecycle is also managed by the app lifespan.
        workspace_manager (`WorkspaceManagerBase`):
            The workspace manager. Required — every chat run and every
            ``/workspace`` endpoint depends on it. Its lifecycle (
            ``__aenter__`` / ``__aexit__``) is managed by the app
            lifespan. Pass a :class:`~agentscope.app._manager.
            LocalWorkspaceManager` for local-directory workspaces.
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
        custom_subagent_templates (`list[SubAgentTemplate] | None`, optional):
            Reusable blueprints for sub-agent creation within teams.
            Each template defines a sub-agent *type* (e.g. ``"researcher"``,
            ``"coder"``) with pre-configured system prompt, context config,
            ReAct config, permission context, and task context. When
            registered, the ``AgentCreate`` tool exposes a
            ``subagent_type`` parameter so the leader agent can route to
            the appropriate template.  See
            :class:`~agentscope.app._types.SubAgentTemplate` for details.
        custom_agent_cls (`Type[Agent] | None`, optional):
            A custom :class:`~agentscope.agent.Agent` subclass to use
            when assembling agents.  When ``None`` (default), the
            built-in :class:`~agentscope.agent.Agent` is used.
        title (`str`, defaults to ``"AgentScope"``):
            OpenAPI title shown in the docs UI.
        version (`str`, defaults to the package version):
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
    app.state.message_bus = message_bus
    app.state.workspace_manager = workspace_manager
    app.state.extra_agent_middlewares = extra_agent_middlewares
    app.state.extra_agent_tools = extra_agent_tools
    app.state.custom_agent_cls = custom_agent_cls

    # Validate custom sub-agent templates for duplicate types and store in
    #  app.state
    templates = custom_subagent_templates or []
    seen_types: set[str] = set()
    duplicates: set[str] = set()
    for t in templates:
        if t.type in seen_types:
            duplicates.add(t.type)
        seen_types.add(t.type)
    if duplicates:
        raise ValueError(
            f"Duplicate sub_agent_template type(s): {duplicates}",
        )
    app.state.custom_subagent_templates = {t.type: t for t in templates}

    # Built-in routers
    for router in (
        agent_router,
        chat_router,
        credential_router,
        realtime_router,
        schedule_router,
        session_router,
        workspace_router,
        model_router,
        tts_model_router,
        realtime_model_router,
    ):
        app.include_router(router)

    # Optional extra middlewares
    for middleware in extra_middlewares or []:
        app.add_middleware(middleware.cls, **middleware.kwargs)

    return app
