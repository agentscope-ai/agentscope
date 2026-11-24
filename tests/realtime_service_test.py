# -*- coding: utf-8 -*-
"""Tests for realtime service runtime assembly."""
# pylint: disable=protected-access,cell-var-from-loop

import unittest
import asyncio
from itertools import product
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from utils import MockModel
from realtime_agent_test import ScriptedModel
from permission_review_test import _review

from agentscope.agent import Agent
from agentscope.app._service._realtime import RealtimeService
from agentscope.app.channel import ChatKind
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.middleware import StateChangeMiddleware
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
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
)
from agentscope.realtime import InteractionMode, RealtimeModelBase
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, TaskCreate, Toolkit


def _workspace(workdir: str) -> SimpleNamespace:
    """Provide the workspace operations used by realtime assembly."""
    return SimpleNamespace(
        workdir=workdir,
        get_instructions=AsyncMock(return_value=""),
    )


def _review_service(
    session: SessionRecord,
    workspace: SimpleNamespace | None = None,
) -> RealtimeService:
    """Assemble optional reviewer tests without external services."""
    return RealtimeService(
        storage=SimpleNamespace(get_session=AsyncMock(return_value=session)),
        workspace_manager=SimpleNamespace(
            get_workspace=AsyncMock(
                return_value=workspace or _workspace("/workspace"),
            ),
        ),
        scheduler_manager=object(),
        background_task_manager=object(),
        message_bus=object(),
        resource_access_service=SimpleNamespace(
            resolve_agent=AsyncMock(
                return_value=AgentRecord(
                    user_id="user",
                    data=AgentData(name="Voice"),
                ),
            ),
        ),
    )


class RealtimeServiceTest(unittest.IsolatedAsyncioTestCase):
    """Verify complete toolkit and session-context assembly."""

    async def test_direct_tool_review_uses_live_workspace_policy(self) -> None:
        """Exercise workspace policy through the assembled realtime agent."""
        restriction = "Ask the user before changing a draft."
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
        )
        workspace = _workspace("/workspace")
        service = _review_service(session, workspace)
        instructions = workspace.get_instructions
        executed = []

        def edit() -> str:
            executed.append(True)
            return "done"

        tool = FunctionTool(edit, is_read_only=False)
        with patch(
            "agentscope.app._service._realtime.get_toolkit",
            AsyncMock(return_value=Toolkit(tools=[tool])),
        ):
            agent = await service.create_agent(
                user_id="user",
                agent_id="agent",
                session_id="session",
                model=ScriptedModel([[]]),
            )

        async def stall() -> str:
            await asyncio.Event().wait()
            return ""

        async with agent:
            instructions.assert_not_awaited()
            for case, interactive in product(
                ("restricted", "changed", "error", "timeout"),
                (True, False),
            ):
                with self.subTest(case=case, interactive=interactive):
                    executed.clear()
                    initial = restriction if case == "restricted" else ""
                    instructions.reset_mock()
                    instructions.return_value = initial
                    instructions.side_effect = {
                        "error": OSError("Workspace unavailable"),
                        "timeout": stall,
                    }.get(case)

                    async def review(request: dict) -> object:
                        if case == "changed":
                            instructions.return_value = restriction
                        return _review(
                            policy_violation=restriction
                            in request["application_instructions"],
                        )

                    reviewer = AsyncMock(side_effect=review)
                    agent._engine.reviewer = reviewer
                    agent._engine.review_timeout = 0.05
                    agent.state.permission_context = PermissionContext(
                        mode=PermissionMode.AUTO,
                        interactive=interactive,
                    )
                    call = ToolCallBlock(id="call", name=tool.name, input="{}")
                    agent._reply_id = "reply"
                    agent.state.context = [
                        AssistantMsg(
                            id="reply",
                            name=agent.name,
                            content=[call],
                        ),
                    ]
                    with patch.object(
                        agent,
                        "_ask_user",
                        AsyncMock(return_value=False),
                    ) as ask:
                        await asyncio.wait_for(
                            agent._run_tool("reply", call),
                            timeout=2,
                        )
                    results = agent.state.context[0].get_content_blocks(
                        "tool_result",
                    )
                    self.assertEqual([r.state for r in results], ["denied"])
                    self.assertEqual(
                        (
                            executed,
                            instructions.await_count,
                            reviewer.await_count,
                            ask.await_count,
                        ),
                        (
                            [],
                            2 if case == "changed" else 1,
                            int(case in ("restricted", "changed")),
                            int(interactive and case != "restricted"),
                        ),
                    )
                    if case == "restricted":
                        self.assertEqual(
                            reviewer.call_args.args[0][
                                "application_instructions"
                            ],
                            f"{agent.system_prompt}\n\n{restriction}",
                        )

    async def test_optional_reviewer_cannot_prevent_voice_start(self) -> None:
        """Pending AUTO loads Chat config; failed loads remain retryable."""
        for mode, interactive, outcome in product(
            PermissionMode,
            (False, True),
            ("valid", "invalid", "missing"),
        ):
            with self.subTest(mode=mode, interactive=interactive, out=outcome):
                session = SessionRecord(
                    id="session",
                    user_id="user",
                    agent_id="agent",
                    config=SessionConfig(workspace_id="workspace"),
                )
                session.state.permission_context.mode = mode
                session.state.permission_context.interactive = interactive
                if outcome != "missing":
                    session.config.chat_model_config = ChatModelConfig(
                        type="test",
                        credential_id="credential",
                        model="chat",
                        parameters={},
                    )
                service = _review_service(session)
                review_model = MockModel(
                    context_size=32768,
                    mock_structured_response=StructuredResponse(
                        content={
                            "risk_level": "low",
                            "user_authorization": "high",
                            "policy_violation": False,
                            "rationale": "Authorized",
                        },
                    ),
                )
                loader = AsyncMock(
                    return_value=review_model,
                    side_effect=ValueError("Deleted credential")
                    if outcome == "invalid"
                    else None,
                )
                with (
                    patch(
                        "agentscope.app._service._realtime.get_toolkit",
                        new=AsyncMock(return_value=Toolkit()),
                    ),
                    patch(
                        "agentscope.app._service._realtime.get_model",
                        new=loader,
                    ),
                ):
                    agent = await service.create_agent(
                        user_id="user",
                        agent_id="agent",
                        session_id="session",
                        model=SimpleNamespace(
                            interaction_mode=InteractionMode.TURN_BASED,
                            card=SimpleNamespace(delegation_backend="none"),
                        ),
                    )
                    loader.assert_not_awaited()
                    decision = await agent._engine.check_permission(
                        FunctionTool(lambda: "done", is_read_only=False),
                        {},
                    )
                expected = (
                    PermissionBehavior.ALLOW
                    if mode == PermissionMode.BYPASS
                    or (mode == PermissionMode.AUTO and outcome == "valid")
                    else PermissionBehavior.ASK
                    if interactive
                    else PermissionBehavior.DENY
                )
                self.assertEqual(
                    {
                        "loads": loader.await_count,
                        "reviewer": agent._engine.reviewer is not None,
                        "decision": decision.behavior,
                    },
                    {
                        "loads": int(
                            mode == PermissionMode.AUTO
                            and outcome != "missing",
                        ),
                        "reviewer": True,
                        "decision": expected,
                    },
                )
                if mode == PermissionMode.AUTO and outcome == "invalid":
                    loader.side_effect = asyncio.CancelledError
                    with (
                        patch(
                            "agentscope.app._service._realtime.get_model",
                            new=loader,
                        ),
                        self.assertRaises(asyncio.CancelledError),
                    ):
                        await agent._engine.check_permission(
                            FunctionTool(lambda: "done", is_read_only=False),
                            {},
                        )

                    async def stalled(*_args: object) -> MockModel:
                        await asyncio.Event().wait()
                        return review_model

                    loader.side_effect = stalled
                    agent._engine.review_timeout = 0.01
                    with patch(
                        "agentscope.app._service._realtime.get_model",
                        new=loader,
                    ):
                        decision = await asyncio.wait_for(
                            agent._engine.check_permission(
                                FunctionTool(
                                    lambda: "done",
                                    is_read_only=False,
                                ),
                                {},
                            ),
                            timeout=1,
                        )
                    self.assertEqual(
                        decision.behavior,
                        PermissionBehavior.ASK
                        if interactive
                        else PermissionBehavior.DENY,
                    )
                    self.assertIn("TimeoutError", decision.decision_reason)
                    agent._engine.review_timeout = 300.0

                current_session = session.model_copy(deep=True)
                current_session.config.chat_model_config = ChatModelConfig(
                    type="test",
                    credential_id="restored",
                    model="chat",
                    parameters={},
                )
                service._storage.get_session.return_value = current_session
                loader.side_effect = None
                loads_before = loader.await_count
                agent.state.permission_context.mode = PermissionMode.AUTO
                with patch(
                    "agentscope.app._service._realtime.get_model",
                    new=loader,
                ):
                    for _ in range(2):
                        decision = await agent._engine.check_permission(
                            FunctionTool(lambda: "done", is_read_only=False),
                            {},
                        )
                        self.assertEqual(
                            decision.behavior,
                            PermissionBehavior.ALLOW,
                        )
                cached = mode == PermissionMode.AUTO and outcome == "valid"
                self.assertEqual(
                    loader.await_count - loads_before,
                    int(not cached),
                )
                if not cached:
                    loader.assert_awaited_with(
                        "user",
                        current_session.config.chat_model_config,
                        service._access,
                    )

    async def test_live_auto_switch_loads_one_reviewer_for_parallel_calls(
        self,
    ) -> None:
        """ASK/BYPASS can switch to AUTO without rebuilding the session."""
        for initial_mode in (PermissionMode.ASK, PermissionMode.BYPASS):
            with self.subTest(initial_mode=initial_mode):
                session = SessionRecord(
                    id="session",
                    user_id="user",
                    agent_id="agent",
                    config=SessionConfig(
                        workspace_id="workspace",
                        chat_model_config=ChatModelConfig(
                            type="test",
                            credential_id="credential",
                            model="chat",
                            parameters={},
                        ),
                    ),
                )
                session.state.permission_context = PermissionContext(
                    mode=initial_mode,
                    interactive=False,
                )
                workspace = _workspace("/workspace")
                service = _review_service(session, workspace)
                executed = []

                def write() -> str:
                    executed.append(True)
                    return "written"

                tool = FunctionTool(write, is_read_only=False)
                review_model = MockModel(context_size=32768)
                generate = AsyncMock(
                    return_value=StructuredResponse(
                        content=_review().model_dump(),
                    ),
                )
                review_model.generate_structured_output = generate

                async def load(*_args: object) -> MockModel:
                    await asyncio.sleep(0)
                    return review_model

                with (
                    patch(
                        "agentscope.app._service._realtime.get_toolkit",
                        AsyncMock(return_value=Toolkit(tools=[tool])),
                    ),
                    patch(
                        "agentscope.app._service._realtime.get_model",
                        AsyncMock(side_effect=load),
                    ) as loader,
                ):
                    agent = await service.create_agent(
                        user_id="user",
                        agent_id="agent",
                        session_id="session",
                        model=ScriptedModel([[]]),
                    )
                    async with agent:
                        calls = [
                            ToolCallBlock(
                                id=f"call-{index}",
                                name=tool.name,
                                input="{}",
                            )
                            for index in range(3)
                        ]
                        agent._reply_id = "reply"
                        agent.state.context = [
                            AssistantMsg(
                                id="reply",
                                name=agent.name,
                                content=calls,
                            ),
                        ]
                        await agent._run_tool("reply", calls[0])
                        loader.assert_not_awaited()
                        workspace.get_instructions.assert_not_awaited()
                        agent.state.permission_context = PermissionContext(
                            mode=PermissionMode.AUTO,
                            interactive=False,
                        )
                        await asyncio.gather(
                            *(
                                agent._run_tool("reply", call)
                                for call in calls[1:]
                            ),
                        )
                        self.assertEqual(
                            (
                                executed,
                                loader.await_count,
                                generate.await_count,
                                agent.model.sessions,
                            ),
                            (
                                [True]
                                * (
                                    3
                                    if initial_mode == PermissionMode.BYPASS
                                    else 2
                                ),
                                1,
                                2,
                                1,
                            ),
                        )

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
        workspace = _workspace("/workspace/session-1")
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
        workspace = _workspace("/workspace/session-1")
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

    async def test_gpt_live_rejects_pending_chat_interactions(self) -> None:
        """Check freshly loaded state before allocating runtime resources."""
        record = AgentRecord(
            id="agent",
            user_id="user",
            data=AgentData(name="Friday"),
        )
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
        )
        workspace_manager = SimpleNamespace(get_workspace=AsyncMock())
        service = RealtimeService(
            storage=SimpleNamespace(
                get_session=AsyncMock(return_value=session),
            ),
            workspace_manager=workspace_manager,
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=object(),
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=record),
            ),
        )
        model = cast(
            RealtimeModelBase,
            SimpleNamespace(
                card=SimpleNamespace(delegation_backend="session_chat"),
            ),
        )
        for state in ("asking", "submitted"):
            with self.subTest(state=state):
                session.state.context = [
                    AssistantMsg(
                        name="Friday",
                        id=session.state.reply_id,
                        content=[
                            ToolCallBlock(
                                id="old_tool",
                                name="Write",
                                input="{}",
                                state=state,
                            ),
                        ],
                    ),
                    UserMsg("user", "Later speech"),
                    AssistantMsg("Friday", "Voice response"),
                ]
                original = session.state.model_dump()
                with self.assertRaises(ValueError) as raised:
                    await service.create_agent(
                        user_id="user",
                        agent_id="agent",
                        session_id="session",
                        model=model,
                    )
                self.assertEqual(
                    {
                        "error": str(raised.exception),
                        "state": session.state.model_dump(),
                        "workspace_calls": (
                            workspace_manager.get_workspace.await_count
                        ),
                    },
                    {
                        "error": (
                            "Complete the pending Chat confirmation or "
                            "external execution before starting GPT Live."
                        ),
                        "state": original,
                        "workspace_calls": 0,
                    },
                )

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
        workspace = _workspace("/workspace/session-1")
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
        backend_model = MockModel(
            context_size=32768,
            mock_structured_response=StructuredResponse(
                content=_review().model_dump(),
            ),
        )

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
        notifier = delegated_agent._reply_middlewares[0]
        self.assertIsInstance(notifier, StateChangeMiddleware)
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
                "reply_middlewares": [notifier, middleware],
                "middleware_calls": [
                    (user_id, agent_id, session_id, workspace),
                ],
            },
        )
        self.assertListEqual(
            get_toolkit_mock.await_args.kwargs["middlewares"],
            [notifier, middleware],
        )
        realtime_agent.state.permission_context.mode = PermissionMode.AUTO
        decision = await realtime_agent._engine.check_permission(
            FunctionTool(lambda: "done", is_read_only=False),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)
        get_model_mock.assert_awaited_once_with(
            user_id,
            chat_config,
            service._access,
        )

    async def test_delegation_publishes_state_and_team_changes(self) -> None:
        """Assembled middleware publishes tool changes to the session bus."""
        bus = SimpleNamespace(
            log_append=AsyncMock(return_value="1-0"),
            publish=AsyncMock(),
        )
        service = RealtimeService(
            storage=object(),
            workspace_manager=object(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=object(),
        )
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
        )
        middlewares = await service._build_delegation_middlewares(
            user_id="user",
            agent_id="agent",
            session_id=session.id,
            session=session,
            workspace=object(),
        )
        state = AgentState()
        initial_permission = state.permission_context.model_dump(mode="json")

        def update_permission() -> str:
            """Simulate a tool changing session permission state."""
            state.permission_context.mode = PermissionMode.AUTO
            return "Updated"

        def create_team() -> str:
            """Simulate a successful team tool without external storage."""
            return "Created"

        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="task",
                            name="TaskCreate",
                            input=(
                                '{"subject":"Test",'
                                '"description":"Notify UI"}'
                            ),
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="permission",
                            name="update_permission",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="team",
                            name="TeamCreate",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ],
        )
        agent = Agent(
            "Friday",
            "Be brief.",
            model,
            state=state,
            middlewares=middlewares,
            toolkit=Toolkit(
                tools=[
                    TaskCreate(),
                    FunctionTool(update_permission, is_read_only=True),
                    FunctionTool(
                        create_team,
                        name="TeamCreate",
                        is_read_only=True,
                    ),
                ],
            ),
        )
        async for _ in agent.reply_stream(
            UserMsg(name="user", content="Create a task and team."),
        ):
            pass

        final_state = {
            "tasks_context": state.tasks_context.model_dump(mode="json"),
            "permission_context": state.permission_context.model_dump(
                mode="json",
            ),
        }
        key = MessageBusKeys.session_events(session.id)
        self.assertEqual(
            {
                "task_count": len(state.tasks_context.tasks),
                "notifications": [
                    (call.args[0], call.args[1]["name"], call.args[1]["value"])
                    for call in bus.publish.call_args_list
                ],
            },
            {
                "task_count": 1,
                "notifications": [
                    (
                        key,
                        "state_updated",
                        {
                            **final_state,
                            "permission_context": initial_permission,
                        },
                    ),
                    (key, "state_updated", final_state),
                    (key, "team_updated", {}),
                    (key, "state_updated", final_state),
                ],
            },
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

        self.assertEqual(len(middlewares), 2)
        self.assertIsInstance(middlewares[0], StateChangeMiddleware)
        self.assertIsInstance(middlewares[1], RAGMiddleware)
        self.assertListEqual(middlewares[1]._knowledge_bases, [knowledge])
        self.assertEqual(middlewares[1]._parameters.top_k, 3)
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
