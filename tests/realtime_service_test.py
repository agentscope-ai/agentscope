# -*- coding: utf-8 -*-
"""Tests for realtime service runtime assembly."""
# pylint: disable=protected-access

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from agentscope.app._service._realtime import RealtimeService
from agentscope.app.channel import ChatKind
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    ChannelOrigin,
    SessionConfig,
    SessionKnowledgeConfig,
    SessionRecord,
)
from agentscope.middleware import RAGMiddleware
from agentscope.message import UserMsg
from agentscope.realtime import InteractionMode, RealtimeModelBase


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
                channel_user_id="staff-1",
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
        model = SimpleNamespace(
            interaction_mode=InteractionMode.TURN_BASED,
            card=SimpleNamespace(delegation_backend="none"),
        )
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

        self.assertDictEqual(
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
        channel.list_tools.assert_awaited_once_with(workspace, None)
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

    async def test_private_chat_tools_receive_channel_user_id(self) -> None:
        """Bind identity-dependent channel tools to the private-chat user."""
        user_id = "user-1"
        agent_id = "agent-1"
        session_id = "session-1"
        agent_record = AgentRecord(
            id=agent_id,
            user_id=user_id,
            data=AgentData(name="Friday", system_prompt="Be concise."),
        )
        session = SessionRecord(
            id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            origin=ChannelOrigin(
                channel_id="channel-1",
                chat_id="chat-1",
                chat_name="Direct Message",
                channel_user_id="staff-1",
            ),
            config=SessionConfig(workspace_id="workspace-1"),
        )
        storage = SimpleNamespace(
            get_session=AsyncMock(return_value=session),
        )
        workspace = SimpleNamespace(workdir="/workspace/session-1")
        workspace_manager = SimpleNamespace(
            get_workspace=AsyncMock(return_value=workspace),
        )
        channel_tool = SimpleNamespace(name="list_wiki_spaces")
        channel = SimpleNamespace(
            display_name="ExampleChat",
            list_tools=AsyncMock(return_value=[channel_tool]),
            chat_kind=AsyncMock(return_value=ChatKind.PRIVATE),
            chat_name=AsyncMock(),
        )
        service = RealtimeService(
            storage=storage,
            workspace_manager=workspace_manager,
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=object(),
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=agent_record),
            ),
            channel_clients=SimpleNamespace(
                get=AsyncMock(return_value=channel),
            ),
        )
        toolkit = object()
        model = cast(
            RealtimeModelBase,
            SimpleNamespace(
                interaction_mode=InteractionMode.TURN_BASED,
                card=SimpleNamespace(delegation_backend="none"),
            ),
        )

        with patch(
            "agentscope.app._service._realtime.get_toolkit",
            new=AsyncMock(return_value=toolkit),
        ) as get_toolkit_mock:
            agent = await service.create_agent(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                model=model,
            )

        self.assertDictEqual(
            {
                "system_prompt": agent.system_prompt,
                "toolkit": agent.toolkit,
                "channel_tools": get_toolkit_mock.await_args.kwargs[
                    "channel_tools"
                ],
                "list_tools_args": channel.list_tools.await_args.args,
            },
            {
                "system_prompt": (
                    "Be concise.\n\n<system-notification>You're within a "
                    "session (id=session-1). This session is bound to a "
                    "chat named \"Direct Message\" (id 'chat-1') on the "
                    "ExampleChat platform. It is a one-to-one private chat. "
                    "These ExampleChat tools are available: "
                    "list_wiki_spaces. Use this chat's id as their target."
                    "</system-notification>"
                ),
                "toolkit": toolkit,
                "channel_tools": [channel_tool],
                "list_tools_args": (workspace, "staff-1"),
            },
        )
        channel.chat_kind.assert_awaited_once_with("chat-1")
        channel.chat_name.assert_not_awaited()

    async def test_gpt_live_reuses_the_session_chat_agent(self) -> None:
        """Client delegation follows the current session chat model."""
        user_id = "user-1"
        agent_id = "agent-1"
        session_id = "session-1"
        agent_record = AgentRecord(
            id=agent_id,
            user_id=user_id,
            data=AgentData(name="Friday", system_prompt="Be concise."),
        )
        chat_config = ChatModelConfig(
            type="openai_chat",
            credential_id="credential-1",
            model="chat-model",
            parameters={},
        )
        session = SessionRecord(
            id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=chat_config,
            ),
        )
        session.state.context = [
            UserMsg(
                id="kept-message",
                name="user",
                content="Persisted chat context.",
            ),
            UserMsg(
                id="live:user:current",
                name="user",
                content="Duplicated Live transcript.",
            ),
        ]
        storage = SimpleNamespace(
            get_session=AsyncMock(return_value=session),
        )
        workspace = SimpleNamespace(workdir="/workspace/session-1")
        middleware = SimpleNamespace(
            is_implemented=lambda name: name == "on_reply",
        )
        middleware_calls: list[tuple] = []

        async def _extra_middlewares(*args: object) -> list[object]:
            middleware_calls.append(args)
            return [middleware]

        service = RealtimeService(
            storage=storage,
            workspace_manager=SimpleNamespace(
                get_workspace=AsyncMock(return_value=workspace),
            ),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=object(),
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=agent_record),
            ),
            extra_agent_middlewares=(
                _extra_middlewares  # type: ignore[arg-type]
            ),
        )
        realtime_model = cast(
            RealtimeModelBase,
            SimpleNamespace(
                interaction_mode=InteractionMode.FULL_DUPLEX,
                card=SimpleNamespace(
                    delegation_backend="session_chat",
                ),
            ),
        )
        toolkit = object()
        backend_model = object()

        with (
            patch(
                "agentscope.app._service._realtime.get_toolkit",
                new=AsyncMock(return_value=toolkit),
            ) as get_toolkit_mock,
            patch(
                "agentscope.app._service._realtime.get_model",
                new=AsyncMock(return_value=backend_model),
            ) as get_model_mock,
        ):
            realtime_agent = await service.create_agent(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                model=realtime_model,
            )

        factory = realtime_agent._delegation_agent_factory
        self.assertIsNotNone(factory)
        assert factory is not None
        delegated_state = realtime_agent._copy_delegation_state()
        delegated_agent = factory(delegated_state)
        self.assertDictEqual(
            {
                "model": delegated_agent.model,
                "toolkit": delegated_agent.toolkit,
                "system_prompt": delegated_agent._system_prompt,
                "state_texts": [
                    message.get_text_content()
                    for message in delegated_agent.state.context
                ],
                "state_isolated": delegated_agent.state is not session.state,
                "message_isolated": (
                    delegated_agent.state.context[0]
                    is not session.state.context[0]
                ),
                "reply_middlewares": delegated_agent._reply_middlewares,
                "middleware_calls": middleware_calls,
            },
            {
                "model": backend_model,
                "toolkit": toolkit,
                "system_prompt": (
                    "Be concise.\n\n<system-notification>You're within a "
                    "session (id=session-1).</system-notification>"
                ),
                "state_texts": [
                    "Persisted chat context.",
                    "Duplicated Live transcript.",
                ],
                "state_isolated": True,
                "message_isolated": True,
                "reply_middlewares": [middleware],
                "middleware_calls": [
                    (user_id, agent_id, session_id, workspace),
                ],
            },
        )
        self.assertListEqual(
            get_toolkit_mock.await_args.kwargs["middlewares"],
            [middleware],
        )
        get_model_mock.assert_awaited_once_with(
            user_id,
            chat_config,
            service._access,
        )

    async def test_gpt_live_builds_knowledge_middleware(self) -> None:
        """Attach configured session knowledge to delegated Chat agents."""
        knowledge = object()
        manager = SimpleNamespace(
            get_knowledge=AsyncMock(return_value=knowledge),
        )
        access = SimpleNamespace(
            resolve_knowledge_base=AsyncMock(
                return_value=SimpleNamespace(user_id="owner-1"),
            ),
        )
        service = RealtimeService(
            storage=object(),  # type: ignore[arg-type]
            workspace_manager=object(),  # type: ignore[arg-type]
            scheduler_manager=object(),  # type: ignore[arg-type]
            background_task_manager=object(),  # type: ignore[arg-type]
            message_bus=object(),  # type: ignore[arg-type]
            resource_access_service=access,  # type: ignore[arg-type]
            knowledge_base_manager=manager,  # type: ignore[arg-type]
        )
        session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id="agent-1",
            config=SessionConfig(
                workspace_id="workspace-1",
                knowledge_config=SessionKnowledgeConfig(
                    knowledge_base_ids=["knowledge-1"],
                    parameters={"top_k": 3},
                ),
            ),
        )

        middlewares = await service._build_delegation_middlewares(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            session=session,
            workspace=object(),
        )

        self.assertEqual(len(middlewares), 1)
        self.assertIsInstance(middlewares[0], RAGMiddleware)
        self.assertListEqual(middlewares[0]._knowledge_bases, [knowledge])
        self.assertEqual(middlewares[0]._parameters.top_k, 3)
        access.resolve_knowledge_base.assert_awaited_once_with(
            "user-1",
            "knowledge-1",
        )
        manager.get_knowledge.assert_awaited_once_with(
            "owner-1",
            "knowledge-1",
        )


if __name__ == "__main__":
    unittest.main()
