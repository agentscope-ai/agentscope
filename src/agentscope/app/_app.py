# -*- coding: utf-8 -*-
"""AgentScope app factory."""
import secrets
from typing import Type, TYPE_CHECKING, Any

from ._lifespan import lifespan
from .access import DenyAllResourceAccessPolicy, ResourceAccessPolicyBase
from .hub import HubBase, HubError, MCPHubBase, SkillHubBase
from .rag.blob_store import BlobStoreBase, LocalBlobStore
from .rag.knowledge_base_manager import KnowledgeBaseManagerBase
from .workspace_manager import WorkspaceManagerBase
from ._router import (
    agent_router,
    channel_router,
    chat_router,
    credential_router,
    health_router,
    hub_router,
    knowledge_base_router,
    embedding_model_router,
    mcp_router,
    model_router,
    tts_model_router,
    schedule_router,
    session_router,
    skill_router,
    workspace_router,
)
from ._types import AgentMiddlewareFactory, AgentToolFactory, SubAgentTemplate
from .channel import ChannelBase, ChannelTypeRegistry
from .message_bus import MessageBus
from .storage import StorageBase
from ..agent import Agent
from ..credential import CredentialFactory, CredentialBase
from ..rag import ApproxTokenChunker, ChunkerBase, ParserBase, TextParser

from .._logging import logger
from .._version import __version__


if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.middleware import Middleware as FastAPIMiddleware
else:
    FastAPI = Any
    FastAPIMiddleware = Any


def _index_hubs(hubs: list | None, kind: str) -> dict:
    """Key the hubs by id, rejecting duplicates.

    Args:
        hubs (`list | None`):
            The hubs passed to :func:`create_app`.
        kind (`str`):
            The hub kind, used in the error message.

    Returns:
        `dict`:
            The hubs keyed by :attr:`HubBase.hub_id`.

    Raises:
        `ValueError`:
            When two hubs of the same kind share an id, which would make
            them indistinguishable in the routes.
    """
    indexed: dict[str, HubBase] = {}
    for hub in hubs or []:
        if hub.hub_id in indexed:
            raise ValueError(
                f"Duplicate {kind} hub id {hub.hub_id!r}: hub ids must be "
                f"unique so routes address exactly one hub.",
            )
        indexed[hub.hub_id] = hub
    return indexed


def create_app(
    storage: StorageBase,
    message_bus: MessageBus,
    workspace_manager: WorkspaceManagerBase,
    knowledge_base_manager: KnowledgeBaseManagerBase | None = None,
    knowledge_parsers: list[ParserBase] | dict[str, ParserBase] | None = None,
    knowledge_chunkers: list[Type[ChunkerBase]] | None = None,
    blob_store: BlobStoreBase | None = None,
    enable_index_worker: bool = True,
    mcp_hubs: list[MCPHubBase] | None = None,
    skill_hubs: list[SkillHubBase] | None = None,
    *,
    enable_channel_worker: bool = True,
    extra_credentials: list[Type[CredentialBase]] | None = None,
    extra_middlewares: list[FastAPIMiddleware] | None = None,
    extra_agent_middlewares: AgentMiddlewareFactory | None = None,
    extra_agent_tools: AgentToolFactory | None = None,
    custom_subagent_templates: list[SubAgentTemplate] | None = None,
    custom_agent_cls: Type[Agent] | None = None,
    resource_access_policy: ResourceAccessPolicyBase | None = None,
    channels: list[Type[ChannelBase]] | None = None,
    download_secret: str | None = None,
    title: str = "AgentScope",
    version: str = __version__,
    **kwargs: Any,
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
        knowledge_chunkers (`list[Type[ChunkerBase]] | None`, optional):
            The chunker classes users can choose from when creating a
            knowledge base.  The chunker type and parameters are pinned
            on the knowledge base record and reconstructed by the index
            worker.  Defaults to ``[ApproxTokenChunker]`` when
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
        mcp_hubs (`list[MCPHubBase] | None`, optional):
            The MCP hubs that provide MCPs.
        skill_hubs (`list[SkillHubBase] | None`, optional):
            The SkillHubs that provide skills.
        enable_channel_worker (`bool`, defaults to ``True``):
            Whether this process holds the channels' long connections.
            ``True`` (embedded deployment) suits a desktop build or a
            single API process. Set ``False`` when running dedicated
            channel workers: a platform gives one bot's events to one
            connection, so every replica connecting would either waste
            connections or duplicate messages. The channel API, the
            client factory and webhook delivery stay available either
            way — only the connections move.
        extra_credentials (`list[Type[CredentialBase]] | None`, optional):
            Additional :class:`~agentscope.credential.CredentialBase`
            subclasses to register before the app starts.  Equivalent to
            calling :func:`~agentscope.credential.CredentialFactory.
            register_credential` for each class.
        extra_middlewares (`list[Middleware] | None`, optional):
            Additional ASGI middlewares to add to the application.
        extra_agent_middlewares (`AgentMiddlewareFactory | None`, optional):
            An async factory ``(user_id, agent_id, session_id, workspace) ->
            awaitable of list[MiddlewareBase]`` that produces extra
            :class:`~agentscope.middleware.MiddlewareBase` instances to
            attach to the agent on each invocation.  Called once per agent
            assembly (i.e. per chat turn / scheduled trigger), so it can
            return user/session-specific middleware (auth, audit logging,
            tenant isolation, etc.).  ``workspace`` is the session's
            resolved :class:`~agentscope.workspace.WorkspaceBase`, exposing
            ``workdir`` and ``get_backend()`` for filesystem-backed
            middleware such as
            :class:`~agentscope.middleware.AgenticMemoryMiddleware`.
            Factories written against the older three-argument signature
            keep working — the fourth argument is only passed to factories
            that accept it.  The returned middlewares are appended to the
            framework-supplied ones (e.g. ``ToolOffloadMiddleware``).
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
        channels (`list[Type[ChannelBase]] | None`, optional):
            Channel adapter classes this service allows (e.g.
            ``[DingTalkChannel, FeishuChannel, DiscordChannel]``).  Each class
            self-describes its ``channel_type``, credentials and config,
            so the service registers it without a separate table; pass a
            custom :class:`~agentscope.app.channel.ChannelBase` subclass
            to add a platform.  When ``None`` (default), no channel types
            are registered and the channel feature stays off until the
            caller opts in by passing at least one adapter class.
        download_secret (`str | None`, optional):
            Signs the short-lived tokens that let a browser download a
            workspace file by navigation. Defaults to a value generated
            per process, which is fine for a single instance but **must
            be set explicitly behind a load balancer** — otherwise a
            token minted by one replica is rejected by the next, and
            downloads fail at random.
        title (`str`, defaults to ``"AgentScope"``):
            OpenAPI title shown in the docs UI.
        version (`str`, defaults to the package version):
            API version shown in the docs UI.

    Returns:
        `FastAPI`: A fully configured application ready to serve requests.
    """
    from fastapi import FastAPI, Request, status
    from fastapi.responses import JSONResponse

    # Register any user-supplied credential types before the app starts
    for cls in extra_credentials or []:
        CredentialFactory.register_credential(cls)

    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # Attach shared state that lifespan and dependencies read from app.state
    app.state.storage = storage
    app.state.message_bus = message_bus
    workspace_manager.bind_storage(storage)
    app.state.workspace_manager = workspace_manager
    app.state.knowledge_base_manager = knowledge_base_manager
    app.state.extra_agent_middlewares = extra_agent_middlewares
    app.state.extra_agent_tools = extra_agent_tools
    app.state.custom_agent_cls = custom_agent_cls
    app.state.resource_access_policy = (
        resource_access_policy or DenyAllResourceAccessPolicy()
    )
    # Channel types this service allows. A channel class self-describes
    # its credentials / config, so the registry is built straight from
    # the list — it has no lifecycle, so it lives on app.state directly
    # rather than being created in the lifespan. Empty by default: the
    # channel feature is off until the caller passes at least one class.
    app.state.channel_type_registry = ChannelTypeRegistry(channels or [])
    app.state.mcp_hubs = _index_hubs(mcp_hubs, "MCP")
    app.state.skill_hubs = _index_hubs(skill_hubs, "skill")
    app.state.download_secret = download_secret or secrets.token_urlsafe(32)

    # Parser / chunker / blob-store defaults only make sense when the
    # KB feature is actually enabled.  When ``knowledge_base_manager`` is
    # ``None`` every KB endpoint is disabled, so leaving these as ``None``
    # avoids unused imports being eagerly constructed at app startup.
    unknown_kwargs = set(kwargs) - {"knowledge_chunker"}
    if unknown_kwargs:
        logger.warning(
            "Ignoring unknown create_app() arguments: %s",
            sorted(unknown_kwargs),
        )

    if knowledge_base_manager is not None:
        app.state.knowledge_parsers = (
            knowledge_parsers
            if knowledge_parsers is not None
            else [TextParser()]
        )
        chunker_classes = list(
            knowledge_chunkers
            if knowledge_chunkers is not None
            else [ApproxTokenChunker],
        )
        # Backward compatibility: the deprecated ``knowledge_chunker``
        # instance is only used for its class.
        if "knowledge_chunker" in kwargs:
            logger.warning(
                "The `knowledge_chunker` argument of create_app() is "
                "deprecated, use `knowledge_chunkers` instead.",
            )
            legacy_cls = type(kwargs["knowledge_chunker"])
            if legacy_cls not in chunker_classes:
                chunker_classes.append(legacy_cls)
        seen_chunker_types: dict[str, Type[ChunkerBase]] = {}
        for cls in chunker_classes:
            if cls.chunker_type in seen_chunker_types:
                raise ValueError(
                    f"Duplicate chunker_type {cls.chunker_type!r}: "
                    f"{seen_chunker_types[cls.chunker_type].__name__} and "
                    f"{cls.__name__}.",
                )
            seen_chunker_types[cls.chunker_type] = cls
        app.state.knowledge_chunkers = chunker_classes
        app.state.blob_store = (
            blob_store
            if blob_store is not None
            else LocalBlobStore(root_dir="./blobs")
        )
    else:
        app.state.knowledge_parsers = knowledge_parsers
        app.state.knowledge_chunkers = knowledge_chunkers
        app.state.blob_store = blob_store
    app.state.enable_index_worker = (
        enable_index_worker and knowledge_base_manager is not None
    )
    app.state.enable_channel_worker = enable_channel_worker

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
        health_router,
        hub_router,
        knowledge_base_router,
        mcp_router,
        schedule_router,
        session_router,
        skill_router,
        workspace_router,
        model_router,
        tts_model_router,
        embedding_model_router,
        channel_router,
    ):
        app.include_router(router)

    @app.exception_handler(HubError)
    async def _on_hub_error(_: Request, exc: HubError) -> JSONResponse:
        """Report an upstream registry failure as a gateway error.

        A hub is a third party we proxy, so its 429 or 500 is not this
        service's fault and must not read as one — a 500 here would send
        the user hunting for a bug on our side.
        """
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.status_code == 429
            else status.HTTP_502_BAD_GATEWAY
        )
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc)},
        )

    # Optional extra middlewares
    for middleware in extra_middlewares or []:
        app.add_middleware(middleware.cls, **middleware.kwargs)

    return app
