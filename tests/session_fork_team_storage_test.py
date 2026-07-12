# -*- coding: utf-8 -*-
"""Storage Fork tests for Teams with created members."""
# pylint: disable=protected-access

from unittest import IsolatedAsyncioTestCase
from itertools import cycle
from typing import Any

from _storage_test_helpers import make_config, make_storage
from redis.exceptions import ResponseError, WatchError

from agentscope._utils import _common
from agentscope.agent import ContextConfig, ReActConfig
import agentscope.app.storage._utils as storage_utils
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionForkConflictError,
    SessionForkCorruptedGraphError,
    SessionSource,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.message import TextBlock, UserMsg
from agentscope.state import AgentState


class TestTeamCreatedMemberFork(IsolatedAsyncioTestCase):
    """Verify complete Team Graph cloning for created members."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage(key_ttl=60)
        self.user_id = "user-1"
        self.leader_agent_id = "leader-agent"
        self.leader_session_id = "leader-session"
        await self.storage.upsert_agent(
            self.user_id,
            self._agent(self.leader_agent_id, "leader-data", "leader"),
        )
        await self.storage.upsert_session(
            self.user_id,
            self.leader_agent_id,
            make_config("Leader"),
            state=AgentState(),
            session_id=self.leader_session_id,
        )
        self.team = TeamRecord(
            user_id=self.user_id,
            session_id=self.leader_session_id,
            data=TeamData(name="Team", description="Description"),
        )
        await self.storage.upsert_team(self.user_id, self.team)
        await self.storage.set_session_team_id(
            self.user_id,
            self.leader_session_id,
            self.team.id,
        )

    @staticmethod
    def _agent(agent_id: str, data_id: str, name: str) -> AgentRecord:
        """Build a complete AgentRecord for a graph fixture."""
        return AgentRecord(
            id=agent_id,
            user_id="user-1",
            source="team" if name != "leader" else "user",
            data=AgentData(
                id=data_id,
                name=name,
                system_prompt=f"Prompt for {name}",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )

    async def _add_member(
        self,
        agent_id: str,
        data_id: str,
        session_id: str,
        name: str,
    ) -> None:
        """Add one created member and append its Team roster entry."""
        await self.storage.upsert_agent(
            self.user_id,
            self._agent(agent_id, data_id, name),
        )
        await self.storage.upsert_session(
            self.user_id,
            agent_id,
            make_config(name),
            state=AgentState(),
            session_id=session_id,
        )
        await self.storage.set_session_team_id(
            self.user_id,
            session_id,
            self.team.id,
        )
        self.team.data.members.append(
            TeamMember(
                owner_id=self.user_id,
                agent_id=agent_id,
                session_id=session_id,
                role="created",
            ),
        )
        self.team.data.member_ids.append(agent_id)
        await self.storage.upsert_team(self.user_id, self.team)

    async def test_fork_copies_leader_and_created_graph(self) -> None:
        """Fork all Team resources and rewrite every graph identity."""
        await self._add_member(
            "member-agent",
            "member-data",
            "member-session",
            "Member",
        )
        await self.storage.upsert_message(
            self.user_id,
            self.leader_session_id,
            UserMsg(name="user", content=[TextBlock(text="leader")]),
        )
        await self.storage.upsert_message(
            self.user_id,
            "member-session",
            UserMsg(name="user", content=[TextBlock(text="member")]),
        )

        fork = await self.storage.fork_session(
            self.user_id,
            self.leader_agent_id,
            self.leader_session_id,
        )
        self.assertNotEqual(fork.id, self.leader_session_id)
        self.assertEqual(fork.agent_id, self.leader_agent_id)
        self.assertEqual(fork.source, SessionSource.USER)
        self.assertIsNone(fork.source_schedule_id)
        self.assertEqual(fork.config.name, "Leader (Fork)")

        fork_team = await self.storage.get_team(self.user_id, fork.team_id)
        assert fork_team is not None
        self.assertEqual(fork_team.session_id, fork.id)
        self.assertEqual(
            fork_team.data.member_ids,
            [
                fork_team.data.members[0].agent_id,
            ],
        )
        self.assertNotEqual(fork_team.id, self.team.id)

        member = fork_team.data.members[0]
        self.assertNotEqual(member.agent_id, "member-agent")
        self.assertNotEqual(member.session_id, "member-session")
        agent = await self.storage.get_agent(self.user_id, member.agent_id)
        member_session = await self.storage.get_session(
            self.user_id,
            member.agent_id,
            member.session_id,
        )
        assert agent is not None
        assert member_session is not None
        self.assertEqual(agent.id, member.agent_id)
        self.assertNotEqual(agent.data.id, "member-data")
        self.assertEqual(member_session.team_id, fork_team.id)
        self.assertEqual(member_session.agent_id, agent.id)
        self.assertEqual(member_session.config.name, "Member")
        owner_agent_index = await self.storage._client.smembers(
            self.storage._key(
                self.storage.key_config.agent_index,
                user_id=self.user_id,
            ),
        )
        self.assertIn(member.agent_id, owner_agent_index)
        self.assertIn(self.leader_agent_id, owner_agent_index)
        self.assertIn(
            fork_team.id,
            await self.storage._client.smembers(
                self.storage._key(
                    self.storage.key_config.team_index,
                    user_id=self.user_id,
                ),
            ),
        )
        self.assertIn(
            fork.id,
            await self.storage._client.smembers(
                self.storage._key(
                    self.storage.key_config.session_index,
                    user_id=self.user_id,
                    agent_id=self.leader_agent_id,
                ),
            ),
        )
        self.assertIn(
            member.session_id,
            await self.storage._client.smembers(
                self.storage._key(
                    self.storage.key_config.session_index,
                    user_id=self.user_id,
                    agent_id=member.agent_id,
                ),
            ),
        )
        self.assertEqual(
            await self.storage.list_messages(
                self.user_id,
                fork.id,
                limit=10,
            ),
            await self.storage.list_messages(
                self.user_id,
                self.leader_session_id,
                limit=10,
            ),
        )
        self.assertEqual(
            await self.storage.list_messages(
                self.user_id,
                member.session_id,
                limit=10,
            ),
            await self.storage.list_messages(
                self.user_id,
                "member-session",
                limit=10,
            ),
        )
        self.assertGreater(
            await self.storage._client.ttl(
                self.storage._key(
                    self.storage.key_config.team,
                    user_id=self.user_id,
                    team_id=fork_team.id,
                ),
            ),
            0,
        )

        fork.config.chat_model_config.parameters["temperature"] = 0.9
        fork.state.cur_iter = 3
        source = await self.storage.get_session(
            self.user_id,
            self.leader_agent_id,
            self.leader_session_id,
        )
        assert source is not None
        self.assertEqual(
            source.config.chat_model_config.parameters["temperature"],
            0.2,
        )
        self.assertEqual(source.state.cur_iter, 0)

    async def test_empty_team_has_no_member_index_or_empty_sadd(self) -> None:
        """An empty Team creates no member Agent or Session indexes."""
        fork = await self.storage.fork_session(
            self.user_id,
            self.leader_agent_id,
            self.leader_session_id,
        )
        fork_team = await self.storage.get_team(self.user_id, fork.team_id)
        assert fork_team is not None
        self.assertEqual(fork_team.data.members, [])
        self.assertEqual(fork_team.data.member_ids, [])
        self.assertEqual(
            await self.storage._client.smembers(
                self.storage._key(
                    self.storage.key_config.agent_index,
                    user_id=self.user_id,
                ),
            ),
            {self.leader_agent_id},
        )

    async def test_invited_member_is_conflict(self) -> None:
        """The created-only phase rejects invited members."""
        self.team.data.members = [
            TeamMember(
                owner_id=self.user_id,
                agent_id="invited-agent",
                session_id="invited-session",
                role="invited",
            ),
        ]
        await self.storage.upsert_team(self.user_id, self.team)
        with self.assertRaises(SessionForkConflictError):
            await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )

    async def test_worker_is_conflict(self) -> None:
        """A created worker Session cannot be forked as a Team root."""
        await self._add_member(
            "member-agent",
            "member-data",
            "member-session",
            "Member",
        )
        with self.assertRaises(SessionForkConflictError):
            await self.storage.fork_session(
                self.user_id,
                "member-agent",
                "member-session",
            )

    async def test_missing_team_is_corrupted_graph(self) -> None:
        """A Team reference without a TeamRecord is corrupted."""
        await self.storage.set_session_team_id(
            self.user_id,
            self.leader_session_id,
            "missing-team",
        )
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )

    async def test_missing_member_agent_is_corrupted_graph(self) -> None:
        """A Team member without its Agent record is corrupted."""
        await self._add_member(
            "member-agent",
            "member-data",
            "member-session",
            "Member",
        )
        await self.storage._client.delete(
            self.storage._key(
                self.storage.key_config.agent,
                user_id=self.user_id,
                agent_id="member-agent",
            ),
        )
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )

    async def test_target_collision_rebuilds_the_complete_plan(self) -> None:
        """A target collision retries with a fresh Team and Session ID."""
        collision_team_key = self.storage._key(
            self.storage.key_config.team,
            user_id=self.user_id,
            team_id="collision-team",
        )
        await self.storage._client.set(collision_team_key, "occupied")
        old_factory = _common._id_factory
        ids = iter(
            (
                "collision-team",
                "collision-session",
                "fresh-team",
                "fresh-session",
            ),
        )
        _common.set_id_factory(lambda: next(ids))
        try:
            fork = await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )
        finally:
            _common.set_id_factory(old_factory)
        self.assertEqual(fork.id, "fresh-session")
        self.assertEqual(fork.team_id, "fresh-team")
        self.assertEqual(
            await self.storage._client.get(collision_team_key),
            "occupied",
        )

    async def test_legacy_members_are_migrated_before_formal_read(
        self,
    ) -> None:
        """The formal graph read sees the migrated member roster."""
        await self.storage.upsert_agent(
            self.user_id,
            self._agent("legacy-agent", "legacy-data", "Legacy"),
        )
        await self.storage.upsert_session(
            self.user_id,
            "legacy-agent",
            make_config("Legacy"),
            session_id="legacy-session",
        )
        await self.storage.set_session_team_id(
            self.user_id,
            "legacy-session",
            self.team.id,
        )
        self.team.data.member_ids = ["legacy-agent"]
        self.team.data.members = []
        await self.storage.upsert_team(self.user_id, self.team)

        fork = await self.storage.fork_session(
            self.user_id,
            self.leader_agent_id,
            self.leader_session_id,
        )
        fork_team = await self.storage.get_team(self.user_id, fork.team_id)
        assert fork_team is not None
        self.assertEqual(len(fork_team.data.members), 1)
        self.assertEqual(
            fork_team.data.member_ids,
            [fork_team.data.members[0].agent_id],
        )

    async def test_two_created_members_are_fully_forked(self) -> None:
        """Every created member in order receives a new Graph identity."""
        await self._add_member(
            "member-a",
            "data-a",
            "session-a",
            "A",
        )
        await self._add_member(
            "member-b",
            "data-b",
            "session-b",
            "B",
        )
        fork = await self.storage.fork_session(
            self.user_id,
            self.leader_agent_id,
            self.leader_session_id,
        )
        fork_team = await self.storage.get_team(self.user_id, fork.team_id)
        assert fork_team is not None
        self.assertEqual(len(fork_team.data.members), 2)
        self.assertEqual(
            fork_team.data.member_ids,
            [member.agent_id for member in fork_team.data.members],
        )
        self.assertNotEqual(
            [member.agent_id for member in fork_team.data.members],
            ["member-a", "member-b"],
        )

    async def test_source_team_change_restarts_migration(self) -> None:
        """A changed Team ID causes migration and reading to restart."""
        team_b = TeamRecord(
            user_id=self.user_id,
            session_id=self.leader_session_id,
            data=TeamData(name="Team B", member_ids=["member-b"]),
        )
        await self.storage.upsert_team(self.user_id, team_b)
        await self.storage.upsert_agent(
            self.user_id,
            self._agent("member-b", "data-b", "B"),
        )
        await self.storage.upsert_session(
            self.user_id,
            "member-b",
            make_config("B"),
            session_id="session-b",
        )
        await self.storage.set_session_team_id(
            self.user_id,
            "session-b",
            team_b.id,
        )
        original_ensure = storage_utils._ensure_team_members
        changed = False

        async def ensure_and_change(
            storage: Any,
            user_id: str,
            team: TeamRecord,
        ) -> list[TeamMember]:
            nonlocal changed
            result = await original_ensure(storage, user_id, team)
            if not changed:
                changed = True
                source = await storage.get_session(
                    self.user_id,
                    self.leader_agent_id,
                    self.leader_session_id,
                )
                assert source is not None
                source.team_id = team_b.id
                await storage._client.set(
                    storage._key(
                        storage.key_config.session,
                        user_id=self.user_id,
                        session_id=self.leader_session_id,
                    ),
                    source.model_dump_json(),
                )
            return result

        storage_utils._ensure_team_members = ensure_and_change
        try:
            fork = await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )
        finally:
            storage_utils._ensure_team_members = original_ensure
        fork_team = await self.storage.get_team(self.user_id, fork.team_id)
        assert fork_team is not None
        self.assertEqual(len(fork_team.data.members), 1)

    async def test_exclusive_member_index_key_overlap_is_rejected(
        self,
    ) -> None:
        """A new member Session Index cannot overlap another target Key."""
        await self._add_member(
            "member-agent",
            "member-data",
            "member-session",
            "Member",
        )
        self.storage.key_config.session_index = "agentscope:collision"
        old_factory = _common._id_factory
        ids = cycle(("same", "leader-new", "same", "new-data", "new-session"))
        _common.set_id_factory(lambda: next(ids))
        try:
            with self.assertRaises(SessionForkConflictError):
                await self.storage.fork_session(
                    self.user_id,
                    self.leader_agent_id,
                    self.leader_session_id,
                )
        finally:
            _common.set_id_factory(old_factory)

    async def test_shared_index_template_overlap_is_corrupted(self) -> None:
        """Overlapping shared Index templates are rejected as corruption."""
        self.storage.key_config.team_index = "agentscope:overlap"
        self.storage.key_config.session_index = "agentscope:overlap"
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )

    async def test_watch_error_rebuilds_team_pipeline(self) -> None:
        """A transaction WatchError rebuilds the whole Team plan."""
        original_pipeline = self.storage._client.pipeline
        pipeline_count = 0
        raised = False

        class WatchOnce:
            """Wrap one pipeline and fail its first execution."""

            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __aenter__(self) -> "WatchOnce":
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *args: Any) -> Any:
                """Close the wrapped pipeline."""
                return await self.inner.__aexit__(*args)

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                """Raise once, then execute normally."""
                nonlocal raised
                if not raised:
                    raised = True
                    raise WatchError()
                return await self.inner.execute(*args, **kwargs)

            def __getattr__(self, name: str) -> Any:
                return getattr(self.inner, name)

        def pipeline(*args: Any, **kwargs: Any) -> WatchOnce:
            nonlocal pipeline_count
            pipeline_count += 1
            return WatchOnce(original_pipeline(*args, **kwargs))

        self.storage._client.pipeline = pipeline
        try:
            await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )
        finally:
            self.storage._client.pipeline = original_pipeline
        self.assertEqual(pipeline_count, 2)

    async def test_wrongtype_during_preread_retries(self) -> None:
        """A pre-read WRONGTYPE retries before member migration."""
        original_get = self.storage._client.get
        raised = False

        async def get_once(key: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal raised
            if not raised:
                raised = True
                raise ResponseError("WRONGTYPE concurrent change")
            return await original_get(key, *args, **kwargs)

        self.storage._client.get = get_once
        try:
            result = await self.storage.fork_session(
                self.user_id,
                self.leader_agent_id,
                self.leader_session_id,
            )
        finally:
            self.storage._client.get = original_get
        self.assertIsNotNone(result.team_id)
