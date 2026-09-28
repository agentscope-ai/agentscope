# -*- coding: utf-8 -*-
"""Tests for realtime service runtime assembly."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentscope.app._service._realtime import RealtimeService
from agentscope.app.channel import ChatKind
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChannelOrigin,
    SessionConfig,
    SessionRecord,
)


class RealtimeServiceTest(unittest.IsolatedAsyncioTestCase):
    """Verify complete toolkit and session-context assembly."""

    async def test_create_agent_assembles_the_session_toolkit(self) -> None:
        """Build the same persisted toolkit context for realtime use."""
        user_id = "user-1"
        agent_id = "agent-1"
        session_id = "session-1"
        agent_record = AgentRecord(
            id=agent_id,
            user_id=user_id,
            data=AgentData(
                name="Friday",
                system_prompt="Be concise.",
            ),
        )
        session = SessionRecord(
            id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            team_id="team-1",
            origin=ChannelOrigin(
                channel_id="channel-1",
                chat_id="chat-1",
            ),
            config=SessionConfig(workspace_id="workspace-1"),
        )
        storage = SimpleNamespace(
            get_session=AsyncMock(return_value=session),
            get_team=AsyncMock(
                return_value=SimpleNamespace(session_id=session_id),
            ),
        )
        workspace = SimpleNamespace(workdir="/workspace/session-1")
        workspace_manager = SimpleNamespace(
            get_workspace=AsyncMock(return_value=workspace),
        )
        channel_tool = SimpleNamespace(name="send_message")
        channel = SimpleNamespace(
            display_name="ExampleChat",
            list_tools=AsyncMock(return_value=[channel_tool]),
            chat_kind=AsyncMock(return_value=ChatKind.GROUP),
            chat_name=AsyncMock(return_value="Operations"),
        )
        channel_clients = SimpleNamespace(
            get=AsyncMock(return_value=channel),
        )
        access = SimpleNamespace(
            resolve_agent=AsyncMock(return_value=agent_record),
        )
        scheduler = object()
        background_tasks = object()
        message_bus = object()
        extra_factory = AsyncMock()
        templates = {"researcher": object()}
        toolkit = object()
        model = object()
        service = RealtimeService(
            storage=storage,
            workspace_manager=workspace_manager,
            scheduler_manager=scheduler,
            background_task_manager=background_tasks,
            message_bus=message_bus,
            resource_access_service=access,
            extra_agent_tools=extra_factory,
            custom_subagent_templates=templates,
            channel_clients=channel_clients,
        )

        with patch(
            "agentscope.app._service._realtime.get_toolkit",
            new=AsyncMock(return_value=toolkit),
        ) as get_toolkit_mock:
            agent = await service.create_agent(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                model=model,  # type: ignore[arg-type]
            )

        self.assertEqual(
            {
                "name": agent.name,
                "system_prompt": agent.system_prompt,
                "model": agent.model,
                "toolkit": agent.toolkit,
                "state": agent.state,
                "working_directories": {
                    key: value.model_dump()
                    for key, value in (
                        agent.state.permission_context.working_directories
                    ).items()
                },
            },
            {
                "name": "Friday",
                "system_prompt": (
                    "Be concise.\n\n<system-notification>You're within a "
                    "session (id=session-1). This session is bound to a "
                    "chat named \"Operations\" (id 'chat-1') on the "
                    "ExampleChat platform. It is a group chat with "
                    "multiple people. These ExampleChat tools are "
                    "available: send_message. Use this chat's id as their "
                    "target.</system-notification>"
                ),
                "model": model,
                "toolkit": toolkit,
                "state": session.state,
                "working_directories": {
                    "/workspace/session-1": {
                        "path": "/workspace/session-1",
                        "source": "session",
                    },
                },
            },
        )
        access.resolve_agent.assert_awaited_once_with(user_id, agent_id)
        storage.get_session.assert_awaited_once_with(
            user_id,
            agent_id,
            session_id,
        )
        storage.get_team.assert_awaited_once_with(user_id, "team-1")
        workspace_manager.get_workspace.assert_awaited_once_with(
            user_id,
            agent_id,
            session_id,
            "workspace-1",
        )
        channel_clients.get.assert_awaited_once_with("channel-1")
        channel.list_tools.assert_awaited_once_with(workspace)
        channel.chat_kind.assert_awaited_once_with("chat-1")
        channel.chat_name.assert_awaited_once_with("chat-1")
        get_toolkit_mock.assert_awaited_once_with(
            storage=storage,
            workspace=workspace,
            workspace_manager=workspace_manager,
            scheduler_manager=scheduler,
            background_task_manager=background_tasks,
            message_bus=message_bus,
            middlewares=[],
            user_id=user_id,
            agent_record=agent_record,
            session_record=session,
            resource_access_service=access,
            extra_factory=extra_factory,
            sub_agent_templates=templates,
            team_role="leader",
            channel_tools=[channel_tool],
        )


if __name__ == "__main__":
    unittest.main()
