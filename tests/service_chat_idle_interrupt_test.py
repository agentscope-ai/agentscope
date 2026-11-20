# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""Regression tests for interrupting an idle (non-parked) session.

:meth:`ChatService.interrupt` documents that interrupting a session that
is not running and not parked on a HITL call is a silent no-op: the
resume trigger still reaches the service, but the agent returns without
producing anything. The service used to publish a synthetic
``ReplyStartEvent`` for every continuation before asking the agent,
however, so an idle interrupt left clients with a reply that started and
never ended.
"""

import asyncio
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.event import EventType, UserInterruptEvent
from agentscope.state import AgentState


class _Storage:
    """Hold one session and record every write the run makes."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session
        self.messages: list[object] = []
        self.persisted_states: list[AgentState] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return the stored session when the triple matches."""
        if (
            user_id != self.session.user_id
            or agent_id != self.session.agent_id
            or session_id != self.session.id
        ):
            return None
        return self.session.model_copy(deep=True)

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Record the state persisted by the run."""
        del user_id, agent_id, session_id
        self.persisted_states.append(state.model_copy(deep=True))
        self.session.state = state.model_copy(deep=True)

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record a persisted message."""
        del user_id, session_id
        self.messages.append(message)

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> object | None:
        """No previous reply exists for an idle session."""
        del user_id, session_id, message_id
        return None


class _Access:
    """Resolve the one agent owned by the test user."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Return a detached copy of the agent record."""
        assert user_id == self.agent.user_id
        assert agent_id == self.agent.id
        return self.agent.model_copy(deep=True)


class _WorkspaceManager:
    """Return a minimal workspace handle used during assembly."""

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> object:
        """Return a workspace without touching the filesystem."""
        del user_id, agent_id, session_id
        assert workspace_id == "workspace-1"
        from types import SimpleNamespace

        return SimpleNamespace(workdir="/tmp/agentscope-idle-interrupt")


class _IdleAgent:
    """An idle agent: an interrupt produces no events at all."""

    def __init__(self, *, name: str, state: AgentState, **_: object) -> None:
        self.name = name
        self.state = state
        self.received: list[object] = []

    async def reply_stream(
        self,
        inputs: object,
    ) -> AsyncGenerator[object, None]:
        """Mirror ``Agent._reply_impl``: an idle interrupt is a no-op."""
        self.received.append(inputs)
        if False:
            yield object()


async def _get_toolkit(**_: object) -> object:
    """Return an inert toolkit handle."""
    return object()


async def _get_model(*_: object, **__: object) -> object:
    """Return an inert model handle."""
    return object()


class ChatServiceIdleInterruptTest(IsolatedAsyncioTestCase):
    """An interrupt on an idle session must publish no reply events."""

    async def test_idle_interrupt_publishes_no_reply_start(self) -> None:
        """The idle interrupt is a silent no-op end to end."""
        user_id = "user-1"
        agent = AgentRecord(
            id="agent-1",
            user_id=user_id,
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        session = SessionRecord(
            id="session-1",
            user_id=user_id,
            agent_id=agent.id,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="credential-1",
                    model="test-model",
                    parameters={},
                ),
            ),
        )
        storage = _Storage(session)
        bus = InMemoryMessageBus()
        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=_Access(agent),
            custom_agent_cls=_IdleAgent,
        )

        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=_get_model,
            ),
        ):
            await asyncio.wait_for(
                service._run_impl(
                    user_id,
                    session.id,
                    agent.id,
                    UserInterruptEvent(reply_id=""),
                ),
                timeout=5.0,
            )

        entries = await bus.log_read(
            MessageBusKeys.session_events(session.id),
        )
        events = [payload for _, payload in entries]
        starts = [
            payload
            for payload in events
            if payload.get("type") == EventType.REPLY_START
        ]
        self.assertEqual(
            starts,
            [],
            f"an idle interrupt published a reply start: {events}",
        )
