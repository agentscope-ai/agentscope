# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The synthesized failure reply carries the agent's display name.

A turn that fails before it produces a reply gets a start/end pair
published and persisted, so the client shows "reply failed" rather than
a stream that simply stops. That reply shares the transcript with real
ones, which are named with the agent's display name; naming it with the
agent id makes it read as a second, unknown participant.
"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.message import AssistantMsg

_USER = "user-1"


class _Storage:
    """Serve one agent + session and record what gets persisted."""

    def __init__(self, session: SessionRecord, agent: AgentRecord) -> None:
        self.session = session
        self.agent = agent
        self.messages: list[AssistantMsg] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy of the requested session."""
        del user_id, agent_id
        if session_id != self.session.id:
            return None
        return self.session.model_copy(deep=True)

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: AssistantMsg,
    ) -> None:
        """Record the persisted reply."""
        del user_id, session_id
        self.messages.append(message)


class _Access:
    """Resolve the one agent this test owns."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(self, user_id: str, agent_id: str) -> AgentRecord:
        """Return the requested agent record."""
        del user_id
        assert agent_id == self.agent.id
        return self.agent.model_copy(deep=True)


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-failure-name-test")


class FailureReplyNameTest(IsolatedAsyncioTestCase):
    """A failed run's reply is named like every other reply."""

    async def test_failure_reply_uses_display_name(self) -> None:
        """The agent id never reaches the transcript as a speaker name."""
        agent = AgentRecord(
            id="agent-0123456789abcdef",
            user_id=_USER,
            source="user",
            data=AgentData(
                name="Researcher",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        session = SessionRecord(
            id="session-1",
            user_id=_USER,
            agent_id=agent.id,
            config=SessionConfig(
                workspace_id="ws-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="cred-1",
                    model="m",
                    parameters={},
                ),
            ),
        )
        storage = _Storage(session, agent)

        async def _get_toolkit(**_: object) -> object:
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            raise RuntimeError("no credential for the agent")

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_Access(agent),
        )
        with patch(
            "agentscope.app._service._chat.get_toolkit",
            new=_get_toolkit,
        ), patch("agentscope.app._service._chat.get_model", new=_get_model):
            await service._run_impl(_USER, session.id, agent.id, None)

        self.assertEqual(len(storage.messages), 1)
        reply = storage.messages[0]
        self.assertEqual(reply.name, "Researcher")
        self.assertTrue(reply.id)
