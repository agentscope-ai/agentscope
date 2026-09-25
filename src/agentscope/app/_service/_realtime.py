# -*- coding: utf-8 -*-
"""Runtime assembly for browser realtime agents."""

from typing import Literal

from ..._logging import logger
from ...agent import RealtimeAgent
from ...permission import AdditionalWorkingDirectory
from ...realtime import RealtimeModelBase
from .._manager import BackgroundTaskManager, SchedulerManager
from .._types import AgentToolFactory, SubAgentTemplate
from ...tool import ToolBase
from ..channel import ChannelBase, ChannelClients, ChatKind
from ..message_bus import MessageBus
from ..storage import ChannelOrigin, SessionRecord, StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ._access import ResourceAccessService
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
        extra_agent_tools: AgentToolFactory | None = None,
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        channel_clients: ChannelClients | None = None,
    ) -> None:
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._access = resource_access_service
        self._extra_agent_tools = extra_agent_tools
        self._subagent_templates = custom_subagent_templates or {}
        self._channel_clients = channel_clients

    async def create_agent(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        model: RealtimeModelBase,
    ) -> RealtimeAgent:
        """Load the locked session and assemble its realtime runtime."""
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
        channel = (
            await self._channel_clients.get(session.origin.channel_id)
            if isinstance(session.origin, ChannelOrigin)
            and self._channel_clients is not None
            else None
        )
        channel_tools = (
            await channel.list_tools(workspace) if channel is not None else []
        )
        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            workspace_manager=self._workspace_manager,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            middlewares=[],
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
        )
        return RealtimeAgent(
            name=agent_record.data.name,
            system_prompt=system_prompt,
            model=model,
            toolkit=toolkit,
            state=session.state,
        )

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
    ) -> str:
        """Attach the session and optional channel identity to the prompt."""
        attachment = f"You're within a session (id={session.id})."
        if channel is not None and isinstance(session.origin, ChannelOrigin):
            tools = ", ".join(tool.name for tool in channel_tools)
            chat_id = session.origin.chat_id
            kind = await channel.chat_kind(chat_id)
            name = session.origin.chat_name or await channel.chat_name(chat_id)
            where = f' named "{name}"' if name else ""
            attachment += (
                f" This session is bound to a chat{where} (id "
                f"{chat_id!r}) on the {channel.display_name} platform."
            )
            if kind is ChatKind.GROUP:
                attachment += " It is a group chat with multiple people."
            elif kind is ChatKind.PRIVATE:
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
