# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""A team member's failed turn is reported to its leader.

A member reports through ``TeamSay``; a turn that errored, was
interrupted, or never assembled never got there, so the chat service
delivers the news into the leader's inbox instead of leaving it
waiting.
"""
import json
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from utils import AnyString

from agentscope.agent import ContextConfig, ReActConfig
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
from agentscope.event import ReplyEndEvent, ReplyStartEvent
from agentscope.types import ErrorInfo, ErrorType, ReplyFinishedReason

_USER = "user-1"


class _Storage:
    """Serve the team's records and swallow every write."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.fail_writes = False

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
        """Return the one team."""
        del user_id
        return self.team if team_id == self.team.id else None

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence, or fail it on demand."""
        if self.fail_writes:
            raise RuntimeError("storage is down")

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept persisted replies, or fail them on demand."""
        if self.fail_writes:
            raise RuntimeError("storage is down")


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-team-failure-test")


class _FailingWorkspaceManager:
    """A backend that is down — assembly dies before the agent exists."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Fail the way an unreachable sandbox does."""
        raise RuntimeError("workspace backend unreachable")


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
            workspace_id="ws-1",
            chat_model_config=ChatModelConfig(
                type="test",
                credential_id="cred-1",
                model="m",
                parameters={},
            ),
        ),
    )


def _agent_cls(events: list) -> type:
    """An Agent stand-in replaying a fixed event sequence."""

    class _Agent:
        """Replay ``events``; the state is only read back for persistence."""

        def __init__(self, *, state: object = None, **_: object) -> None:
            self.state = state or SimpleNamespace()

        async def reply_stream(
            self,
            inputs: object,
        ) -> AsyncGenerator[object, None]:
            """Yield the configured events."""
            del inputs
            for event in events:
                yield event

    return _Agent


class TeamFailureReportTest(IsolatedAsyncioTestCase):
    """Which endings reach the leader's inbox, and which do not."""

    async def asyncSetUp(self) -> None:
        """Wire a two-member team: one leader, one worker."""
        self.leader_agent = _agent("agent-l", "Leader")
        self.worker_agent = _agent("agent-w", "worker", source="team")
        self.leader_session = _session("session-l", self.leader_agent.id)
        self.worker_session = _session("session-w", self.worker_agent.id)
        self.team = TeamRecord(
            id="team-1",
            user_id=_USER,
            session_id=self.leader_session.id,
            leader_agent_id=self.leader_agent.id,
            data=TeamData(name="team"),
        )
        self.storage = _Storage(
            sessions={
                s.id: s for s in (self.leader_session, self.worker_session)
            },
            agents={a.id: a for a in (self.leader_agent, self.worker_agent)},
            team=self.team,
        )
        self.bus = InMemoryMessageBus()
        self.workspace_manager: object = _WorkspaceManager()

    async def _run(
        self,
        session: SessionRecord,
        agent_id: str,
        events: list,
        *,
        model_fails: bool = False,
        workspace_fails: bool = False,
        persist_fails: bool = False,
    ) -> list[dict]:
        """Run one turn and return whatever landed in the leader's inbox."""

        async def _get_toolkit(**_: object) -> object:
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            if model_fails:
                raise RuntimeError("no credential for the worker")
            return object()

        if workspace_fails:
            self.workspace_manager = _FailingWorkspaceManager()
        if persist_fails:
            self.storage.fail_writes = True

        class _Access:
            """Resolve any agent of this user."""

            def __init__(self, storage: _Storage) -> None:
                self._storage = storage

            async def resolve_agent(
                self,
                user_id: str,
                agent_id: str,
            ) -> AgentRecord:
                """Return the requested agent record."""
                return await self._storage.get_agent(user_id, agent_id)

        service = ChatService(
            storage=self.storage,
            workspace_manager=self.workspace_manager,
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=_Access(self.storage),
            custom_agent_cls=_agent_cls(events),
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(_USER, session.id, agent_id, None)

        entries = await self.bus.queue_drain(
            MessageBusKeys.inbox(self.leader_session.id),
        )
        return [payload for _entry_id, payload in entries]

    def _events(
        self,
        reason: ReplyFinishedReason,
        error: ErrorInfo | None = None,
    ) -> list:
        """A start/end pair ending with ``reason``."""
        return [
            ReplyStartEvent(
                session_id=self.worker_session.id,
                reply_id="reply-1",
                name="worker",
            ),
            ReplyEndEvent(
                session_id=self.worker_session.id,
                reply_id="reply-1",
                finished_reason=reason,
                error=error,
            ),
        ]

    async def test_worker_error_reaches_the_leader(self) -> None:
        """An errored worker turn lands as a hint in the leader's inbox."""
        delivered = await self._run(
            self.worker_session,
            self.worker_agent.id,
            self._events(
                ReplyFinishedReason.ERROR,
                ErrorInfo(type=ErrorType.INTERNAL, message="model exploded"),
            ),
        )
        self.assertEqual(len(delivered), 1)
        self.assertDictEqual(
            delivered[0],
            {
                "type": "hint",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "hint": (
                    "<system-reminder>Team member 'worker' hit an error "
                    "while running, so it never called TeamSay to "
                    "report. Error: model exploded. Judge from the error "
                    "type whether to retry — with this member or a fresh "
                    "one — or to raise it with the user: invalid "
                    "credentials or an exhausted quota will fail the "
                    "same way again. Assume no usable output unless the "
                    "member reported partial results earlier."
                    "</system-reminder>"
                ),
                "source": json.dumps(
                    {"label": "System", "sublabel": "Reminder"},
                    ensure_ascii=False,
                ),
            },
        )

    async def test_worker_interruption_reaches_the_leader(self) -> None:
        """An interrupted worker turn is reported too."""
        delivered = await self._run(
            self.worker_session,
            self.worker_agent.id,
            self._events(ReplyFinishedReason.INTERRUPTED),
        )
        self.assertEqual(len(delivered), 1)
        self.assertDictEqual(
            delivered[0],
            {
                "type": "hint",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "hint": (
                    "<system-reminder>Team member 'worker' was "
                    "interrupted mid-task and has stopped. Someone "
                    "cancelled that run deliberately — possibly the "
                    "user, who may be working with this member directly. "
                    "Do not silently re-dispatch it: ask the user what "
                    "should happen to the task, or ask the member how "
                    "far it got. Left unresolved, the member is stranded "
                    "with work nobody is tracking.</system-reminder>"
                ),
                "source": json.dumps(
                    {"label": "System", "sublabel": "Reminder"},
                    ensure_ascii=False,
                ),
            },
        )

    async def test_completed_worker_turn_is_silent(self) -> None:
        """A COMPLETED ending already implies a successful TeamSay."""
        delivered = await self._run(
            self.worker_session,
            self.worker_agent.id,
            self._events(ReplyFinishedReason.COMPLETED),
        )
        self.assertListEqual(delivered, [])

    async def test_leader_failure_notifies_nobody(self) -> None:
        """The leader's own failed turn has no one to report to."""
        delivered = await self._run(
            self.leader_session,
            self.leader_agent.id,
            self._events(
                ReplyFinishedReason.ERROR,
                ErrorInfo(type=ErrorType.INTERNAL, message="boom"),
            ),
        )
        self.assertListEqual(delivered, [])

    async def test_assembly_failure_reaches_the_leader(self) -> None:
        """A worker that never assembled is reported as well."""
        delivered = await self._run(
            self.worker_session,
            self.worker_agent.id,
            [],
            model_fails=True,
        )
        self.assertEqual(len(delivered), 1)
        self.assertDictEqual(
            delivered[0],
            {
                "type": "hint",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "hint": (
                    "<system-reminder>Team member 'worker' hit an error "
                    "while running, so it never called TeamSay to "
                    "report. Error: The session could not be prepared — "
                    "check the agent's model, tools and knowledge bases. "
                    "Judge from the error type whether to retry — with "
                    "this member or a fresh one — or to raise it with "
                    "the user: invalid credentials or an exhausted quota "
                    "will fail the same way again. Assume no usable "
                    "output unless the member reported partial results "
                    "earlier.</system-reminder>"
                ),
                "source": json.dumps(
                    {"label": "System", "sublabel": "Reminder"},
                    ensure_ascii=False,
                ),
            },
        )

    async def test_workspace_failure_still_reaches_the_leader(self) -> None:
        """Assembly dying before the workspace resolves still reports.

        The team identity has to be resolved ahead of every fallible
        assembly step, or a worker whose sandbox is down goes silent.
        """
        delivered = await self._run(
            self.worker_session,
            self.worker_agent.id,
            [],
            workspace_fails=True,
        )
        self.assertEqual(len(delivered), 1)
        self.assertIn(
            "Team member 'worker' hit an error while running",
            delivered[0]["hint"],
        )

    async def test_persistence_failure_still_reaches_the_leader(self) -> None:
        """A storage failure must not swallow the notification."""
        with self.assertRaises(RuntimeError):
            await self._run(
                self.worker_session,
                self.worker_agent.id,
                self._events(
                    ReplyFinishedReason.ERROR,
                    ErrorInfo(type=ErrorType.INTERNAL, message="boom"),
                ),
                persist_fails=True,
            )
        entries = await self.bus.queue_drain(
            MessageBusKeys.inbox(self.leader_session.id),
        )
        self.assertEqual(len(entries), 1)
        self.assertIn(
            "Error: boom.",
            entries[0][1]["hint"],
        )
