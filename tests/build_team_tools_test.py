# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for build_team_tools_for — the source-aware factory that
decides which team tools an agent gets at assembly time.

The previous design read the session/team state to pick the subset.
The new design picks purely from ``agent.source`` and lets each tool
do runtime validation in ``__call__``. So these tests just verify the
factory's output shape; per-tool runtime checks are tested in
team_service_test / team_e2e_test."""
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._tools import (
    AgentCreate,
    TeamCreate,
    TeamDelete,
    TeamSay,
    build_team_tools_for,
)
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    RedisKeyConfig,
    RedisStorage,
    SessionConfig,
)


def make_storage() -> RedisStorage:
    """RedisStorage backed by fakeredis."""
    s = RedisStorage.__new__(RedisStorage)
    s._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    s.key_ttl = None
    s.key_config = RedisKeyConfig()
    return s


def make_user_agent(user_id: str) -> AgentRecord:
    """A regular source='user' agent."""
    return AgentRecord(
        user_id=user_id,
        data=AgentData(
            name="user-agent",
            system_prompt="hi",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def make_worker_agent(user_id: str, agent_id: str) -> AgentRecord:
    """A source='team' worker agent."""
    return AgentRecord(
        id=agent_id,
        user_id=user_id,
        source="team",
        data=AgentData(
            name=agent_id,
            system_prompt="worker",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def make_session_config() -> SessionConfig:
    """Session config with a chat model."""
    return SessionConfig(
        workspace_id="ws-1",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


class TestBuildTeamToolsFor(IsolatedAsyncioTestCase):
    """The factory now picks tools purely by ``agent.source``."""

    async def asyncSetUp(self) -> None:
        """Set up storage + a sentinel team_service object (the
        factory only forwards it; doesn't call anything on it)."""
        self.user_id = "user-1"
        self.storage = make_storage()
        self.team_service = object()  # type: ignore[assignment]

    async def test_user_agent_gets_full_leader_toolset(self) -> None:
        """source=user → TeamCreate, AgentCreate, TeamSay, TeamDelete."""
        agent = make_user_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, agent)
        session = await self.storage.upsert_session(
            self.user_id,
            agent.id,
            make_session_config(),
        )

        tools = await build_team_tools_for(
            storage=self.storage,
            team_service=self.team_service,  # type: ignore[arg-type]
            user_id=self.user_id,
            agent_record=agent,
            session_record=session,
        )
        self.assertEqual(
            [type(t) for t in tools],
            [TeamCreate, AgentCreate, TeamSay, TeamDelete],
        )

    async def test_user_agent_gets_full_set_even_when_already_in_team(
        self,
    ) -> None:
        """The factory does not consult session.team_id; even an
        already-in-team user agent gets all four leader tools.
        Runtime validation in TeamCreate.__call__ takes care of
        rejecting a redundant team-create call."""
        agent = make_user_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, agent)
        session = await self.storage.upsert_session(
            self.user_id,
            agent.id,
            make_session_config(),
        )
        # Pretend this session already leads a team.
        await self.storage.set_session_team_id(
            self.user_id,
            session.id,
            "some-team-id",
        )
        # The session record we hand to the factory still says no team —
        # that's fine; the factory ignores team_id.
        tools = await build_team_tools_for(
            storage=self.storage,
            team_service=self.team_service,  # type: ignore[arg-type]
            user_id=self.user_id,
            agent_record=agent,
            session_record=session,
        )
        self.assertEqual(
            [type(t) for t in tools],
            [TeamCreate, AgentCreate, TeamSay, TeamDelete],
        )

    async def test_team_agent_gets_only_team_say(self) -> None:
        """source=team → [TeamSay]."""
        agent = make_worker_agent(self.user_id, "worker-a")
        await self.storage.upsert_agent(self.user_id, agent)
        session = await self.storage.upsert_session(
            self.user_id,
            agent.id,
            make_session_config(),
        )

        tools = await build_team_tools_for(
            storage=self.storage,
            team_service=self.team_service,  # type: ignore[arg-type]
            user_id=self.user_id,
            agent_record=agent,
            session_record=session,
        )
        self.assertEqual([type(t) for t in tools], [TeamSay])

    async def test_tool_storage_is_bound(self) -> None:
        """Built tools carry the storage reference for later runtime checks."""
        agent = make_user_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, agent)
        session = await self.storage.upsert_session(
            self.user_id,
            agent.id,
            make_session_config(),
        )

        tools = await build_team_tools_for(
            storage=self.storage,
            team_service=self.team_service,  # type: ignore[arg-type]
            user_id=self.user_id,
            agent_record=agent,
            session_record=session,
        )
        for t in tools:
            self.assertIs(t._storage, self.storage)
            self.assertEqual(t._user_id, self.user_id)
            self.assertEqual(t._session_id, session.id)
            self.assertEqual(t._agent_id, agent.id)
