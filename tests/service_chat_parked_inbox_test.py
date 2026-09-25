# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""Regression tests for inbox deliveries while a chat run is parked."""

from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._bus_ops import deliver_to_inbox
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
    TeamData,
    TeamRecord,
)
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    ToolCallBlock,
    ToolCallState,
    UserMsg,
)

_USER = "user-1"


class _Storage:
    """Serve a worker session and retain writes made by the chat service."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.messages: list[object] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy of the requested session."""
        del user_id, agent_id
        record = self.sessions.get(session_id)
        return record.model_copy(deep=True) if record else None

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Return a detached copy of the requested agent."""
        del user_id
        record = self.agents.get(agent_id)
        return record.model_copy(deep=True) if record else None

    async def get_team(self, user_id: str, team_id: str) -> TeamRecord | None:
        """Return the worker's team."""
        del user_id
        return self.team if team_id == self.team.id else None

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the state produced by the parked run."""

    async def upsert_message(self, *args: object, **kwargs: object) -> None:
        """Record any synthesized failure reply."""
        del kwargs
        self.messages.append(args[-1])


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-parked-inbox-test")


class _Access:
    """Resolve agents from the test storage."""

    def __init__(self, storage: _Storage) -> None:
        self.storage = storage

    async def resolve_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Return the requested agent."""
        agent = await self.storage.get_agent(user_id, agent_id)
        assert agent is not None
        return agent


def _agent(agent_id: str, name: str, source: str = "user") -> AgentRecord:
    """Build a minimal agent record."""
    return AgentRecord(
        id=agent_id,
        user_id=_USER,
        source=source,
        data=AgentData(
            name=name,
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _session(session_id: str, agent_id: str) -> SessionRecord:
    """Build a team-bound session record."""
    return SessionRecord(
        id=session_id,
        user_id=_USER,
        agent_id=agent_id,
        team_id="team-1",
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


def _agent_cls(calls: list[object]) -> type:
    """Build an Agent stand-in that fails if the parked run is re-entered."""

    class _Agent:
        """Record calls without invoking a model."""

        def __init__(
            self,
            *,
            name: str,
            state: object,
            **_: object,
        ) -> None:
            self.name = name
            self.state = state

        async def reply_stream(
            self,
            inputs: object,
        ) -> AsyncGenerator[object, None]:
            """Raise if the service incorrectly invokes a second turn."""
            calls.append(inputs)
            if len(calls) > 1:
                raise AssertionError("parked agent was re-entered")
            if False:
                yield object()

    return _Agent


class ParkedInboxContinuationTest(IsolatedAsyncioTestCase):
    """A queued inbox payload must not wake a parked agent."""

    async def asyncSetUp(self) -> None:
        """Build a worker session whose context ends in a parked tool call."""
        self.leader_agent = _agent("agent-leader", "leader")
        self.worker_agent = _agent("agent-worker", "worker", source="team")
        self.leader_session = _session("session-leader", self.leader_agent.id)
        self.worker_session = _session("session-worker", self.worker_agent.id)
        self.team = TeamRecord(
            id="team-1",
            user_id=_USER,
            session_id=self.leader_session.id,
            leader_agent_id=self.leader_agent.id,
            data=TeamData(name="team"),
        )
        self.storage = _Storage(
            sessions={
                self.leader_session.id: self.leader_session,
                self.worker_session.id: self.worker_session,
            },
            agents={
                self.leader_agent.id: self.leader_agent,
                self.worker_agent.id: self.worker_agent,
            },
            team=self.team,
        )
        self.bus = InMemoryMessageBus()

    async def _run_parked(self, state: ToolCallState) -> list[object]:
        """Run one parked worker turn after queuing an inbox payload."""
        self.worker_session.state.context.append(
            AssistantMsg(
                name=self.worker_agent.data.name,
                content=[
                    ToolCallBlock(
                        id="tool-call-1",
                        name="permission-gated-tool",
                        input="{}",
                        state=state,
                    ),
                ],
            ),
        )
        await deliver_to_inbox(
            self.bus,
            user_id=_USER,
            session_id=self.worker_session.id,
            agent_id=self.worker_agent.id,
            payload=HintBlock(hint="queued team message").model_dump(
                mode="json",
            ),
        )
        # The delivery happened before the run registered as the consumer.
        await self.bus.queue_drain(MessageBusKeys.wakeup_queue())

        calls: list[object] = []

        async def _get_toolkit(**_: object) -> object:
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            return object()

        service = ChatService(
            storage=self.storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=_Access(self.storage),
            custom_agent_cls=_agent_cls(calls),
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(
                _USER,
                self.worker_session.id,
                self.worker_agent.id,
                UserMsg(name="user", content="start"),
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].role, "user")
        self.assertEqual(len(self.storage.messages), 1)
        self.assertEqual(self.storage.messages[0].role, "user")
        self.assertIsNone(
            await self.bus.registry_get(
                MessageBusKeys.inbox_consumer(self.worker_session.id),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            ),
        )
        inbox = await self.bus.queue_drain(
            MessageBusKeys.inbox(self.worker_session.id),
        )
        self.assertEqual(
            [payload[1]["hint"] for payload in inbox],
            ["queued team message"],
        )
        leader_inbox = await self.bus.queue_drain(
            MessageBusKeys.inbox(self.leader_session.id),
        )
        self.assertEqual(leader_inbox, [])
        return [payload for _entry_id, payload in inbox]

    async def test_asking_agent_keeps_inbox_queued(self) -> None:
        """A confirmation wait must not become an error reply."""
        await self._run_parked(ToolCallState.ASKING)

    async def test_submitted_agent_keeps_inbox_queued(self) -> None:
        """An external execution wait must not become an error reply."""
        await self._run_parked(ToolCallState.SUBMITTED)
