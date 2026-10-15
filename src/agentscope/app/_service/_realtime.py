# -*- coding: utf-8 -*-
"""Runtime assembly for browser realtime agents."""

import inspect
from typing import Literal

from ..._logging import logger
from ...agent import Agent, ModelConfig, RealtimeAgent
from ...middleware import MiddlewareBase, RAGMiddleware
from ...permission import AdditionalWorkingDirectory
from ...rag import KnowledgeBase
from ...realtime import RealtimeModelBase
from ...state import AgentState
from .._manager import BackgroundTaskManager, SchedulerManager
from .._types import (
    AgentMiddlewareFactory,
    AgentToolFactory,
    SubAgentTemplate,
)
from ...tool import ToolBase
from ...workspace import WorkspaceBase
from ..channel import ChannelBase, ChannelClients, ChatKind
from ..message_bus import MessageBus
from ..rag.knowledge_base_manager import KnowledgeBaseManagerBase
from ..storage import ChannelOrigin, SessionRecord, StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ._access import ResourceAccessService
from ._model import get_model
from ._toolkit import get_toolkit


class RealtimeService:
    """Assemble a persisted session as a toolkit-enabled realtime agent."""

    def __init__(
        self,
        *,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        scheduler_manager: SchedulerManager,
        background_task_manager: BackgroundTaskManager,
        message_bus: MessageBus,
        resource_access_service: ResourceAccessService,
        knowledge_base_manager: KnowledgeBaseManagerBase | None = None,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        custom_agent_cls: type[Agent] | None = None,
        channel_clients: ChannelClients | None = None,
    ) -> None:
        """Initialize the application-wide realtime Agent assembler.

        This service owns no running connection. A :class:`WebRTCSession`
        calls :meth:`create_agent` for each negotiated voice session.

        Args:
            storage (`StorageBase`):
                Persistent session and agent storage.
            workspace_manager (`WorkspaceManagerBase`):
                Resolves the workspace bound to each session.
            scheduler_manager (`SchedulerManager`):
                Provides schedule tools to assembled toolkits.
            background_task_manager (`BackgroundTaskManager`):
                Provides background-task tools and runtime state.
            message_bus (`MessageBus`):
                Shared bus used by session and team tools.
            resource_access_service (`ResourceAccessService`):
                Resolves caller-visible agents, credentials, and knowledge.
            knowledge_base_manager (`KnowledgeBaseManagerBase | None`,
            optional):
                Resolves knowledge handles for delegated Chat Agents.
            extra_agent_middlewares (`AgentMiddlewareFactory | None`,
            optional):
                Builds application-provided middlewares for delegation.
            extra_agent_tools (`AgentToolFactory | None`, optional):
                Builds application-provided tools for the session toolkit.
            custom_subagent_templates (`dict[str, SubAgentTemplate] | None`,
            optional):
                Sub-agent templates exposed through Agent creation tools.
            custom_agent_cls (`type[Agent] | None`, optional):
                Agent implementation used for delegated Chat tasks.
            channel_clients (`ChannelClients | None`, optional):
                Resolves tools and identity for channel-bound sessions.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._access = resource_access_service
        self._knowledge_base_manager = knowledge_base_manager
        self._extra_agent_middlewares = extra_agent_middlewares
        self._middlewares_take_workspace = False
        if extra_agent_middlewares is not None:
            try:
                inspect.signature(extra_agent_middlewares).bind(
                    "",
                    "",
                    "",
                    None,
                )
                self._middlewares_take_workspace = True
            except (TypeError, ValueError):
                pass
        self._extra_agent_tools = extra_agent_tools
        self._subagent_templates = custom_subagent_templates or {}
        self._agent_cls = custom_agent_cls or Agent
        self._channel_clients = channel_clients

    async def create_agent(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        model: RealtimeModelBase,
    ) -> RealtimeAgent:
        """Load a locked session and assemble its realtime runtime.

        The caller must hold the distributed session lock before invoking
        this method so the loaded state cannot race another Chat or voice
        run. The returned Agent is not connected until its async context is
        entered.

        Args:
            user_id (`str`):
                Owner of the session being opened.
            agent_id (`str`):
                Agent configuration used by the session.
            session_id (`str`):
                Persisted session whose state the runtime will share.
            model (`RealtimeModelBase`):
                Provider adapter already resolved from the session config.

        Returns:
            `RealtimeAgent`:
                An assembled but disconnected realtime Agent.

        Raises:
            RuntimeError:
                If the session no longer exists.
            ValueError:
                If a client-delegating model has no Chat model configured.
        """
        agent_record = await self._access.resolve_agent(user_id, agent_id)
        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None:
            raise RuntimeError(f"Session {session_id!r} no longer exists.")

        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session.config.workspace_id,
        )
        working_dirs = session.state.permission_context.working_directories
        if workspace.workdir not in working_dirs:
            working_dirs[workspace.workdir] = AdditionalWorkingDirectory(
                path=workspace.workdir,
                source="session",
            )

        team_role = await self._resolve_team_role(user_id, session)
        channel_origin = (
            session.origin
            if isinstance(session.origin, ChannelOrigin)
            else None
        )
        channel = (
            await self._channel_clients.get(channel_origin.channel_id)
            if channel_origin is not None and self._channel_clients is not None
            else None
        )
        chat_kind = (
            await channel.chat_kind(channel_origin.chat_id)
            if channel is not None and channel_origin is not None
            else None
        )
        channel_tools = (
            await channel.list_tools(
                workspace,
                channel_origin.channel_user_id
                if chat_kind is ChatKind.PRIVATE
                else None,
            )
            if channel is not None and channel_origin is not None
            else []
        )
        card = getattr(model, "card", None)
        uses_session_chat = (
            getattr(card, "delegation_backend", "none") == "session_chat"
        )
        middlewares = (
            await self._build_delegation_middlewares(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                session=session,
                workspace=workspace,
            )
            if uses_session_chat
            else []
        )
        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            workspace_manager=self._workspace_manager,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            middlewares=middlewares,
            user_id=user_id,
            agent_record=agent_record,
            session_record=session,
            resource_access_service=self._access,
            extra_factory=self._extra_agent_tools,
            sub_agent_templates=self._subagent_templates,
            team_role=team_role,
            channel_tools=channel_tools,
        )

        session.state.session_id = session_id
        system_prompt = await self._build_system_prompt(
            agent_record.data.system_prompt,
            session,
            channel,
            channel_tools,
            chat_kind,
        )
        delegation_agent_factory = None
        if uses_session_chat:
            chat_config = session.config.chat_model_config
            if chat_config is None:
                raise ValueError(
                    "Configure a chat model before starting GPT Live.",
                )
            chat_model = await get_model(user_id, chat_config, self._access)
            fallback_config = session.config.fallback_chat_model_config
            fallback_model = (
                await get_model(user_id, fallback_config, self._access)
                if fallback_config is not None
                else None
            )

            def _create_delegation_agent(state: AgentState) -> Agent:
                """Build an isolated task agent from current session state."""
                config = agent_record.data.chat_config
                return self._agent_cls(
                    name=agent_record.data.name,
                    system_prompt=system_prompt,
                    model=chat_model,
                    toolkit=toolkit,
                    model_config=ModelConfig(
                        fallback_model=fallback_model,
                    ),
                    context_config=config.context_config,
                    react_config=config.react_config,
                    state=state,
                    middlewares=middlewares,
                    offloader=workspace,
                )

            delegation_agent_factory = _create_delegation_agent

        return RealtimeAgent(
            name=agent_record.data.name,
            system_prompt=system_prompt,
            model=model,
            toolkit=toolkit,
            state=session.state,
            delegation_agent_factory=delegation_agent_factory,
        )

    async def _build_delegation_middlewares(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        session: SessionRecord,
        workspace: WorkspaceBase,
    ) -> list[MiddlewareBase]:
        """Build Chat middlewares that are safe in a Live task backend."""
        middlewares: list[MiddlewareBase] = []
        if self._extra_agent_middlewares is not None:
            factory_args: tuple = (user_id, agent_id, session_id)
            if self._middlewares_take_workspace:
                factory_args += (workspace,)
            middlewares.extend(
                await self._extra_agent_middlewares(*factory_args),
            )

        config = session.config.knowledge_config
        if (
            config is None
            or not config.knowledge_base_ids
            or self._knowledge_base_manager is None
        ):
            return middlewares

        knowledges: list[KnowledgeBase] = []
        for knowledge_id in config.knowledge_base_ids:
            try:
                record = await self._access.resolve_knowledge_base(
                    user_id,
                    knowledge_id,
                )
                knowledge = await self._knowledge_base_manager.get_knowledge(
                    record.user_id,
                    knowledge_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Skipping knowledge base %r for session %r: failed "
                    "to resolve runtime handle.",
                    knowledge_id,
                    session_id,
                )
                continue
            knowledges.append(knowledge)
        if knowledges:
            middlewares.append(
                RAGMiddleware(
                    knowledge_bases=knowledges,
                    parameters=RAGMiddleware.Parameters(
                        **(config.parameters or {}),
                    ),
                ),
            )
        return middlewares

    async def _resolve_team_role(
        self,
        user_id: str,
        session: SessionRecord,
    ) -> Literal["leader", "worker"] | None:
        """Resolve the toolkit's team role from the current team record."""
        if session.team_id is None:
            return None
        team = await self._storage.get_team(user_id, session.team_id)
        if team is None:
            logger.warning(
                "Session %r points at missing team %r; assembling realtime "
                "tools without a team role.",
                session.id,
                session.team_id,
            )
            return None
        return "leader" if team.session_id == session.id else "worker"

    @staticmethod
    async def _build_system_prompt(
        base_prompt: str,
        session: SessionRecord,
        channel: ChannelBase | None,
        channel_tools: list[ToolBase],
        chat_kind: ChatKind | None,
    ) -> str:
        """Attach the session and optional channel identity to the prompt."""
        attachment = f"You're within a session (id={session.id})."
        if channel is not None and isinstance(session.origin, ChannelOrigin):
            tools = ", ".join(tool.name for tool in channel_tools)
            chat_id = session.origin.chat_id
            name = session.origin.chat_name or await channel.chat_name(chat_id)
            where = f' named "{name}"' if name else ""
            attachment += (
                f" This session is bound to a chat{where} (id "
                f"{chat_id!r}) on the {channel.display_name} platform."
            )
            if chat_kind is ChatKind.GROUP:
                attachment += " It is a group chat with multiple people."
            elif chat_kind is ChatKind.PRIVATE:
                attachment += " It is a one-to-one private chat."
            if tools:
                attachment += (
                    f" These {channel.display_name} tools are available: "
                    f"{tools}. Use this chat's id as their target."
                )
        return (
            f"{base_prompt}\n\n"
            f"<system-notification>{attachment}</system-notification>"
        )
