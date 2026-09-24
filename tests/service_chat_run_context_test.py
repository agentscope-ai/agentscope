# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""ChatService resolves team/channel context once and fans it out."""

from types import SimpleNamespace
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
    SessionConfig,
    SessionRecord,
    TeamData,
    TeamRecord,
)


class _Storage:
    """Serve one worker session + its team/leader, counting team reads."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.get_team_calls = 0

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
        """Return a detached agent record — owner-scoped, like the real
        storage: a cross-owner agent only resolves through an access
        service's grants, never through the owner read."""
        record = self.agents.get(agent_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def get_team(self, user_id: str, team_id: str) -> TeamRecord | None:
        """Count every read — the run must need exactly one."""
        del user_id
        self.get_team_calls += 1
        return self.team if team_id == self.team.id else None

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept synthesized failure messages (none expected)."""


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-run-ctx-test")


class _Access:
    """Resolve agents owner-first, then through a shared-grants map."""

    def __init__(
        self,
        storage: _Storage,
        shared: dict[str, AgentRecord] | None = None,
    ) -> None:
        self._storage = storage
        self._shared = shared or {}

    async def resolve_agent(
        self,
        viewer_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Raise when the agent is not visible to the viewer."""
        record = await self.try_resolve_agent(viewer_id, agent_id)
        if record is None:
            raise LookupError(f"Agent {agent_id!r} not found.")
        return record

    async def try_resolve_agent(
        self,
        viewer_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Owner-scoped read first, then the shared-grants map."""
        record = await self._storage.get_agent(viewer_id, agent_id)
        if record is not None:
            return record
        shared = self._shared.get(agent_id)
        return shared.model_copy(deep=True) if shared is not None else None


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

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_Access(storage),
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

    async def test_worker_run_with_shared_leader(self) -> None:
        """A worker whose leader agent is owned by another user still
        gets the correct leader context — the leader resolves through
        the access service's grants, not the owner-scoped read."""
        user_id = "user-1"
        publisher_id = "publisher"
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
            user_id=publisher_id,
            data=AgentData(
                name="Shared Leader",
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
            leader_agent_id=leader_agent.id,
            data=TeamData(name="team"),
        )
        storage = _Storage(
            sessions={s.id: s for s in (worker_session, leader_session)},
            agents={a.id: a for a in (worker_agent, leader_agent)},
            team=team,
        )
        access = _Access(storage, shared={leader_agent.id: leader_agent})
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

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=access,
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
                "leader_names": ["Shared Leader"],
            },
        )
