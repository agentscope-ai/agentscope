# -*- coding: utf-8 -*-
"""AgentScope app factory."""
from typing import Type, TYPE_CHECKING, Any

from ._lifespan import lifespan
from .access import DenyAllResourceAccessPolicy, ResourceAccessPolicyBase
from .rag.blob_store import BlobStoreBase, LocalBlobStore
from .rag.knowledge_base_manager import KnowledgeBaseManagerBase
from .workspace_manager import WorkspaceManagerBase
from ._router import (
    agent_router,
    chat_router,
    credential_router,
    knowledge_base_router,
    model_router,
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
from ..rag import (
    ApproxTokenChunker,
    ChunkerBase,
    ParserBase,
    TextParser,
)
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
    knowledge_base_manager: KnowledgeBaseManagerBase | None = None,
    knowledge_parsers: list[ParserBase] | dict[str, ParserBase] | None = None,
    knowledge_chunker: ChunkerBase | None = None,
    blob_store: BlobStoreBase | None = None,
    enable_index_worker: bool = True,
    *,
    extra_credentials: list[Type[CredentialBase]] | None = None,
    extra_middlewares: list[FastAPIMiddleware] | None = None,
    extra_agent_middlewares: AgentMiddlewareFactory | None = None,
    extra_agent_tools: AgentToolFactory | None = None,
    custom_subagent_templates: list[SubAgentTemplate] | None = None,
    custom_agent_cls: Type[Agent] | None = None,
    resource_access_policy: ResourceAccessPolicyBase | None = None,
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
        knowledge_base_manager (`KnowledgeBaseManagerBase | None`, \
         optional):
            The knowledge base manager that owns knowledge base
            lifecycle and serves
            :class:`~agentscope.rag.KnowledgeBase`
            runtime handles to both HTTP service and agent code.
            The manager carries its own vector store instance — its
            ``__aenter__`` / ``__aexit__`` enter and release that
            vector store, so the caller does not pass the vector
            store separately.  ``None`` disables knowledge base
            endpoints entirely.
        knowledge_parsers (`list[ParserBase] | dict[str, ParserBase] | \
         None`, optional):
            Parsers registered for knowledge base document uploads.
            Pass a **list** to have the service route by each parser's
            ``supported_media_types`` (later entries override earlier
            ones for overlapping types, with a warning); pass a
            **dict** ``media_type → parser`` for explicit routing
            (one parser bound to multiple types, type aliases, ...).
            Defaults to ``[TextParser()]`` when
            ``knowledge_base_manager`` is set.
        knowledge_chunker (`ChunkerBase | None`, optional):
            The chunker shared across every knowledge base.  Defaults
            to :class:`~agentscope.rag.ApproxTokenChunker()` when
            ``knowledge_base_manager`` is set.
        blob_store (`BlobStoreBase | None`, optional):
            Backend storing uploaded document bytes between the
            upload endpoint and the indexing worker.  Required when
            ``knowledge_base_manager`` is set; defaults to
            :class:`~agentscope.app.rag.blob_store.LocalBlobStore`
            rooted at ``./blobs``.  Its lifecycle (``__aenter__`` /
            ``__aexit__``) is managed by the app lifespan.
        enable_index_worker (`bool`, defaults to ``True``):
            When ``True`` (embedded deployment) the API process starts
            an :class:`~agentscope.app._service.IndexWorker` and an
            :class:`~agentscope.app._service.IndexSweeper` in its
            lifespan, and dispatches indexing tasks via an
            in-process queue.  When ``False`` (dedicated deployment)
            the API process performs no indexing — a separate worker
            process is expected to consume tasks from the message
            bus.  No effect when ``knowledge_base_manager`` is
            ``None``.
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
        resource_access_policy (`ResourceAccessPolicyBase | None`, optional):
            Policy deciding whether a viewer may access
            credentials / agents / knowledge bases owned by another
            user. When ``None`` (default), a
            :class:`DenyAllResourceAccessPolicy` is installed which
            preserves the historical owner-isolated behavior.
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
    app.state.knowledge_base_manager = knowledge_base_manager
    app.state.extra_agent_middlewares = extra_agent_middlewares
    app.state.extra_agent_tools = extra_agent_tools
    app.state.custom_agent_cls = custom_agent_cls
    app.state.resource_access_policy = (
        resource_access_policy or DenyAllResourceAccessPolicy()
    )

    # Parser / chunker / blob-store defaults only make sense when the
    # KB feature is actually enabled.  When ``knowledge_base_manager`` is
    # ``None`` every KB endpoint is disabled, so leaving these as ``None``
    # avoids unused imports being eagerly constructed at app startup.
    if knowledge_base_manager is not None:
        app.state.knowledge_parsers = (
            knowledge_parsers
            if knowledge_parsers is not None
            else [TextParser()]
        )
        app.state.knowledge_chunker = knowledge_chunker or ApproxTokenChunker()
        app.state.blob_store = (
            blob_store
            if blob_store is not None
            else LocalBlobStore(root_dir="./blobs")
        )
    else:
        app.state.knowledge_parsers = knowledge_parsers
        app.state.knowledge_chunker = knowledge_chunker
        app.state.blob_store = blob_store
    app.state.enable_index_worker = (
        enable_index_worker and knowledge_base_manager is not None
    )

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
        knowledge_base_router,
        schedule_router,
        session_router,
        workspace_router,
        model_router,
        tts_model_router,
    ):
        app.include_router(router)

    # Optional extra middlewares
    for middleware in extra_middlewares or []:
        app.add_middleware(middleware.cls, **middleware.kwargs)

    return app
