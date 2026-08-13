# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""ChatService resolves team/channel context once and fans it out."""

from types import SimpleNamespace
from collections.abc import Callable
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    ScheduleData,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    TeamData,
    TeamRecord,
)


class _Storage:
    """Serve one worker session + its team/leader, counting team reads."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord | None,
        schedules: dict[str, ScheduleRecord] | None = None,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.schedules = schedules or {}
        self.get_team_calls = 0
        self.get_schedule_calls = 0
        self.after_schedule_read: Callable[[int], None] | None = None

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy; ``agent_id`` may be blank for leaders."""
        del user_id, agent_id
        record = self.sessions.get(session_id)
        return record.model_copy(deep=True) if record else None

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Return a detached agent record."""
        del user_id
        record = self.agents.get(agent_id)
        return record.model_copy(deep=True) if record else None

    async def get_team(self, user_id: str, team_id: str) -> TeamRecord | None:
        """Count every read — the run must need exactly one."""
        del user_id
        self.get_team_calls += 1
        return self.team if self.team and team_id == self.team.id else None

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Return the current schedule and count dynamic revalidation."""
        del user_id
        self.get_schedule_calls += 1
        record = self.schedules.get(schedule_id)
        result = record.model_copy(deep=True) if record else None
        if self.after_schedule_read is not None:
            self.after_schedule_read(self.get_schedule_calls)
        return result

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept synthesized failure messages (none expected)."""


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-run-ctx-test")


class TestRunContextResolution(IsolatedAsyncioTestCase):
    """Team identity is read once and shared by toolkit + middleware."""

    async def test_worker_run_fetches_team_once(self) -> None:
        """One ``get_team`` read serves both consumers of the role."""
        user_id = "user-1"
        worker_agent = AgentRecord(
            id="agent-w",
            user_id=user_id,
            source="team",
            data=AgentData(
                name="worker",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        leader_agent = AgentRecord(
            id="agent-l",
            user_id=user_id,
            data=AgentData(
                name="Leader",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        config = SessionConfig(
            workspace_id="ws-1",
            chat_model_config=ChatModelConfig(
                type="test",
                credential_id="cred-1",
                model="m",
                parameters={},
            ),
        )
        worker_session = SessionRecord(
            id="session-w",
            user_id=user_id,
            agent_id=worker_agent.id,
            team_id="team-1",
            config=config,
        )
        leader_session = SessionRecord(
            id="session-l",
            user_id=user_id,
            agent_id=leader_agent.id,
            team_id="team-1",
            config=config,
        )
        team = TeamRecord(
            id="team-1",
            user_id=user_id,
            session_id=leader_session.id,
            data=TeamData(name="team"),
        )
        storage = _Storage(
            sessions={s.id: s for s in (worker_session, leader_session)},
            agents={a.id: a for a in (worker_agent, leader_agent)},
            team=team,
        )
        equipped: list[list] = []

        class _Agent:
            """Capture the middlewares; reply without doing anything."""

            def __init__(self, *, middlewares: list, **_: object) -> None:
                equipped.append(middlewares)

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Yield nothing."""
                del inputs
                if False:
                    yield object()

        toolkit_kwargs: dict = {}

        async def _get_toolkit(**kwargs: object) -> object:
            toolkit_kwargs.update(kwargs)
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            return object()

        class _Access:
            """Resolve the one worker agent."""

            async def resolve_agent(self, *_: object) -> AgentRecord:
                """Return a detached copy."""
                return worker_agent.model_copy(deep=True)

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_Access(),
            custom_agent_cls=_Agent,
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(
                user_id,
                worker_session.id,
                worker_agent.id,
                None,
            )

        self.assertDictEqual(
            {
                "get_team_calls": storage.get_team_calls,
                "team_role": toolkit_kwargs["team_role"],
                "channel_tools": toolkit_kwargs["channel_tools"],
                "leader_names": [
                    mw._leader_name
                    for mws in equipped
                    for mw in mws
                    if hasattr(mw, "_leader_name")
                ],
            },
            {
                "get_team_calls": 1,
                "team_role": "worker",
                "channel_tools": [],
                "leader_names": ["Leader"],
            },
        )

    async def test_schedule_resolves_current_channel_without_chat_binding(
        self,
    ) -> None:
        """A scheduled run gets its whitelist but no conversation context."""
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
        model_config = ChatModelConfig(
            type="test",
            credential_id="cred-1",
            model="m",
            parameters={},
        )
        session = SessionRecord(
            id="session-1",
            user_id=user_id,
            agent_id=agent.id,
            source=SessionSource.SCHEDULE,
            source_schedule_id="schedule-1",
            config=SessionConfig(
                workspace_id="ws-1",
                chat_model_config=model_config,
            ),
        )
        schedule = ScheduleRecord(
            id="schedule-1",
            user_id=user_id,
            agent_id=agent.id,
            data=ScheduleData(
                name="daily",
                cron_expression="0 9 * * *",
                chat_model_config=model_config,
                channel_id="channel-1",
            ),
        )
        storage = _Storage(
            sessions={session.id: session},
            agents={agent.id: agent},
            team=None,
            schedules={schedule.id: schedule},
        )

        class _Channel:
            """Expose scheduled tools and reject conversation lookups."""

            def __init__(self, channel_id: str) -> None:
                self.channel_id = channel_id

            async def list_scheduled_tools(self, workspace: object) -> list:
                """Return one tool identifying this selected channel."""
                del workspace
                return [
                    SimpleNamespace(name=f"ScheduledSend:{self.channel_id}"),
                ]

            async def chat_kind(self, chat_id: str) -> object:
                """Prove scheduled sessions never request chat context."""
                raise AssertionError(chat_id)

        class _Clients:
            """Record schedule-specific client resolution."""

            def __init__(self) -> None:
                self.requests: list[tuple[str, str]] = []
                self.current = True

            async def get_scheduled(
                self,
                channel_id: str,
                owner_id: str,
            ) -> _Channel:
                """Return a client while recording channel and owner."""
                self.requests.append((channel_id, owner_id))
                return _Channel(channel_id)

            async def is_scheduled_current(
                self,
                channel_id: str,
                owner_id: str,
                channel: _Channel,
            ) -> bool:
                """Return the test-controlled final record check."""
                return (
                    self.current
                    and channel.channel_id == channel_id
                    and owner_id == user_id
                )

        clients = _Clients()
        toolkit_kwargs: dict = {}
        prompts: list[str] = []

        class _Agent:
            """Capture the system prompt and finish without events."""

            def __init__(
                self,
                *,
                system_prompt: str,
                state: object,
                **_: object,
            ) -> None:
                prompts.append(system_prompt)
                self.state = state

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Complete without emitting reply events."""
                del inputs
                if False:
                    yield object()

        async def _get_toolkit(**kwargs: object) -> object:
            toolkit_kwargs.update(kwargs)
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            return object()

        class _Access:
            async def resolve_agent(self, *_: object) -> AgentRecord:
                """Return the schedule's agent."""
                return agent.model_copy(deep=True)

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_Access(),
            custom_agent_cls=_Agent,
            channel_clients=clients,  # type: ignore[arg-type]
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(user_id, session.id, agent.id, None)

        self.assertEqual(storage.get_schedule_calls, 2)
        self.assertEqual(clients.requests, [("channel-1", user_id)])
        self.assertEqual(
            [tool.name for tool in toolkit_kwargs["channel_tools"]],
            ["ScheduledSend:channel-1"],
        )
        self.assertNotIn("bound to a chat", prompts[0])
        self.assertIsNone(session.source_channel_id)
        self.assertIsNone(session.source_chat_id)

        # Stateful schedule sessions must follow later schedule edits.
        schedule.data.channel_id = "channel-2"
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(user_id, session.id, agent.id, None)

        self.assertEqual(storage.get_schedule_calls, 4)
        self.assertEqual(
            clients.requests,
            [("channel-1", user_id), ("channel-2", user_id)],
        )
        self.assertEqual(
            [tool.name for tool in toolkit_kwargs["channel_tools"]],
            ["ScheduledSend:channel-2"],
        )

        # A channel record/version change during assembly removes the tools.
        clients.current = False
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(user_id, session.id, agent.id, None)

        self.assertEqual(storage.get_schedule_calls, 6)
        self.assertEqual(toolkit_kwargs["channel_tools"], [])
        clients.current = True

        # Deleting the schedule removes only its optional channel tools.
        storage.schedules.clear()
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(user_id, session.id, agent.id, None)

        self.assertEqual(storage.get_schedule_calls, 7)
        self.assertEqual(toolkit_kwargs["channel_tools"], [])

        # A concurrent edit between the two reads fails closed for this run.
        schedule.data.channel_id = "channel-3"
        storage.schedules[schedule.id] = schedule

        def _change_after_first_read(read_count: int) -> None:
            if read_count == 8:
                schedule.data.channel_id = "channel-4"

        storage.after_schedule_read = _change_after_first_read
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(user_id, session.id, agent.id, None)

        self.assertEqual(storage.get_schedule_calls, 9)
        self.assertEqual(clients.requests[-1], ("channel-3", user_id))
        self.assertEqual(toolkit_kwargs["channel_tools"], [])
