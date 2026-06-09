# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the four framework-builtin team tools — :class:`TeamCreate`,
:class:`AgentCreate`, :class:`TeamSay`, :class:`TeamDelete`.

Each tool's business logic now lives inline in its ``__call__`` (the
old ``TeamService`` orchestration layer is gone), so unit tests run the
tools directly against a real :class:`RedisStorage` + :class:`RedisMessageBus`
backed by ``fakeredis``. They assert:

- the success path: storage rows updated, inbox + wakeup pushed where
  expected;
- the failure paths: each precondition (not in a team / not the leader /
  recipient missing / etc.) returns an ``ERROR`` ``ToolChunk`` instead
  of raising.
"""
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from utils import AnyString

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._tools import (
    AgentCreate,
    DEFAULT_SUB_AGENT_TEMPLATE,
    TeamCreate,
    TeamDelete,
    TeamSay,
)
from agentscope.app._types import SubAgentTemplate
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    RedisStorage,
    SessionConfig,
)


def _make_storage(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisStorage:
    """Construct a :class:`RedisStorage` that talks to *fr*."""

    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisMessageBus:
    """Construct a :class:`RedisMessageBus` that talks to *fr*."""

    class _B(RedisMessageBus):
        async def __aenter__(  # type: ignore[override]
            self,
        ) -> "RedisMessageBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


def _make_agent_record(
    user_id: str,
    name: str,
    source: str = "user",
) -> AgentRecord:
    """Build a minimal :class:`AgentRecord`."""
    return AgentRecord(
        user_id=user_id,
        source=source,
        data=AgentData(
            name=name,
            system_prompt=f"You are {name}.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


class _TeamToolsTestBase(IsolatedAsyncioTestCase):
    """Shared fixture: a fakeredis-backed storage + bus, a leader
    agent record, and a leader session.

    Sub-classes set up their own teams / workers on top.
    """

    user_id = "u"

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            _make_storage(self.fr),
        )
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

        # Leader agent + its session.
        self.leader_agent = _make_agent_record(self.user_id, "leader")
        await self.storage.upsert_agent(self.user_id, self.leader_agent)
        self.leader_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws"),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()


class TestTeamCreate(_TeamToolsTestBase):
    """``TeamCreate`` creates a TeamRecord and stamps ``team_id`` on
    the calling session."""

    async def test_creates_team_and_stamps_session(self) -> None:
        """A successful ``TeamCreate`` writes a TeamRecord and stamps the
        leader's session with the new ``team_id``."""
        tool = TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool(name="alpha", description="t-desc")

        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
        # A team exists and the leader's session now points at it.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        self.assertIsNotNone(sess.team_id)
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.assertIsNotNone(team)
        self.assertDictEqual(
            {
                "name": team.data.name,
                "session_id": team.session_id,
                "member_ids": team.data.member_ids,
            },
            {
                "name": "alpha",
                "session_id": self.leader_session.id,
                "member_ids": [],
            },
        )

    async def test_rejects_when_session_already_in_team(self) -> None:
        """A session can lead at most one team."""
        tool = TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        await tool(name="first", description="d")
        chunk = await tool(name="second", description="d")
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )


class TestAgentCreate(_TeamToolsTestBase):
    """``AgentCreate`` spawns a worker agent + session, appends it to
    the team, and delivers the initial prompt via inbox + wakeup."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Pre-create a team so the leader has one to add to.
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="team desc")

    async def test_spawns_worker_and_delivers_initial_prompt(
        self,
    ) -> None:
        """A successful ``AgentCreate`` adds the worker agent + session to
        the team and delivers the initial prompt via inbox + wakeup."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool(
            name="worker",
            description="does research",
            prompt="please look up X",
        )
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Team has one member.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.assertEqual(len(team.data.member_ids), 1)
        worker_agent_id = team.data.member_ids[0]

        # Worker agent exists with source=team.
        worker_agent = await self.storage.get_agent(
            self.user_id,
            worker_agent_id,
        )
        self.assertEqual(
            {"source": worker_agent.source, "name": worker_agent.data.name},
            {"source": "team", "name": "worker"},
        )

        # Worker has exactly one session, marked with team_id.
        worker_sessions = await self.storage.list_sessions(
            self.user_id,
            worker_agent_id,
        )
        self.assertEqual(len(worker_sessions), 1)
        self.assertEqual(worker_sessions[0].team_id, sess.team_id)

        # The initial prompt is in the worker's inbox as a HintBlock
        # wrapped in a <team-message> tag.
        inbox = await self.bus.inbox_drain(
            worker_sessions[0].id,
            max_count=10,
        )
        self.assertEqual(len(inbox), 1)
        hint_payload = inbox[0][1]
        self.assertDictEqual(
            hint_payload,
            {
                "type": "hint",
                "id": AnyString(),
                "hint": AnyString(),
                "source": '{"label": "team_message", "sublabel": "leader"}',
            },
        )
        self.assertIn("please look up X", hint_payload["hint"])
        self.assertIn("<team-message", hint_payload["hint"])

        # A wakeup was enqueued for the worker.
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(
            wakeups[0],
            {
                "session_id": worker_sessions[0].id,
                "agent_id": worker_agent_id,
                "user_id": self.user_id,
            },
        )

    async def test_rejects_when_not_in_team(self) -> None:
        """Calling ``AgentCreate`` from a session that hasn't run
        ``TeamCreate`` yet returns an error chunk."""
        # Build a second leader session that is NOT in any team.
        loner_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws2"),
        )
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=loner_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool(
            name="worker",
            description="d",
            prompt="p",
        )
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_rejects_unknown_subagent_type(self) -> None:
        """An unrecognised ``subagent_type`` returns an error chunk."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool(
            name="w",
            description="d",
            prompt="p",
            subagent_type="not-a-type",
        )
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_rejects_duplicate_member_name(self) -> None:
        """Two ``AgentCreate`` calls with the same ``name`` is rejected:
        TeamSay routes by name, so duplicates would be ambiguous."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        first = await tool(
            name="worker",
            description="d",
            prompt="p",
        )
        self.assertEqual(first.state.value, "running")

        second = await tool(
            name="worker",
            description="d",
            prompt="p",
        )
        self.assertEqual(second.state.value, "error")

        # The team still only has one member — the second call did not
        # persist anything.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.assertEqual(len(team.data.member_ids), 1)

    async def test_rejects_member_name_colliding_with_leader(self) -> None:
        """A worker name that matches the leader's name is rejected —
        ``TeamSay(to=<leader name>)`` must remain unambiguous."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool(
            name=self.leader_agent.data.name,  # "leader"
            description="d",
            prompt="p",
        )
        self.assertEqual(chunk.state.value, "error")

        # No worker was added.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.assertEqual(team.data.member_ids, [])


class TestAgentCreateTemplates(_TeamToolsTestBase):
    """Template-aware ``AgentCreate`` behaviour: schema dynamics,
    template routing, and config isolation."""

    _explorer_template = SubAgentTemplate(
        type="explorer",
        description="Read-only exploration agent.",
        system_prompt_template=(
            "You are {member_name}, an explorer in team "
            "'{team_name}' led by {leader_name}.\n\n"
            "Team purpose: {team_description}\n\n"
            "Your role: {member_description}"
        ),
    )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="team desc")

    async def test_schema_omits_subagent_type_when_no_custom_templates(
        self,
    ) -> None:
        """When only the built-in ``"default"`` template exists, the
        ``input_schema`` must NOT contain a ``subagent_type`` field."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        self.assertNotIn("subagent_type", tool.input_schema["properties"])

    async def test_schema_includes_subagent_type_with_custom_templates(
        self,
    ) -> None:
        """When custom templates are registered, ``subagent_type`` appears
        in the schema with the correct enum values."""
        templates = {"explorer": self._explorer_template}
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates=templates,
        )
        self.assertIn("subagent_type", tool.input_schema["properties"])
        enum_values = tool.input_schema["properties"]["subagent_type"]["enum"]
        self.assertIn("default", enum_values)
        self.assertIn("explorer", enum_values)

    async def test_default_template_injected_when_missing(self) -> None:
        """The built-in default template is always available even when
        only custom templates are provided."""
        templates = {"explorer": self._explorer_template}
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates=templates,
        )
        self.assertIn("default", tool._sub_agent_templates)
        self.assertIs(
            tool._sub_agent_templates["default"],
            DEFAULT_SUB_AGENT_TEMPLATE,
        )

    async def test_custom_template_applies_system_prompt(self) -> None:
        """A worker created with a custom template gets the template's
        system prompt (not the default one)."""
        templates = {"explorer": self._explorer_template}
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates=templates,
        )
        chunk = await tool(
            name="scout",
            description="explores code",
            prompt="look around",
            subagent_type="explorer",
        )
        self.assertEqual(chunk.state.value, "running")

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        worker_agent = await self.storage.get_agent(
            self.user_id,
            team.data.member_ids[0],
        )
        self.assertIn("an explorer in team", worker_agent.data.system_prompt)
        self.assertNotIn(
            "You communicate with the team leader",
            worker_agent.data.system_prompt,
        )

    async def test_configs_are_deep_copied(self) -> None:
        """Each spawned worker receives its own copy of the template's
        config objects — mutations must not leak across agents."""
        templates = {"explorer": self._explorer_template}
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates=templates,
        )
        await tool(
            name="w1",
            description="d",
            prompt="p",
            subagent_type="explorer",
        )
        await tool(
            name="w2",
            description="d",
            prompt="p",
            subagent_type="explorer",
        )

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        a1 = await self.storage.get_agent(
            self.user_id,
            team.data.member_ids[0],
        )
        a2 = await self.storage.get_agent(
            self.user_id,
            team.data.member_ids[1],
        )
        self.assertIsNot(
            a1.data.context_config,
            a2.data.context_config,
        )
        self.assertIsNot(
            a1.data.react_config,
            a2.data.react_config,
        )


class TestTeamSay(_TeamToolsTestBase):
    """``TeamSay`` delivers a HintBlock + wakeup to each addressed
    teammate."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="team desc")
        # Add 2 workers.
        agent_create = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        await agent_create(
            name="w1",
            description="d",
            prompt="p1",
        )
        await agent_create(
            name="w2",
            description="d",
            prompt="p2",
        )
        # Drain the pre-existing wakeups so subsequent assertions only
        # see what TeamSay enqueues.
        await self.bus.dequeue_wakeups(max_count=100)
        # Resolve worker IDs.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.worker_ids = team.data.member_ids
        self.worker_sessions = {}
        for aid in self.worker_ids:
            ss = await self.storage.list_sessions(self.user_id, aid)
            self.worker_sessions[aid] = ss[0].id
            # Drain initial-prompt hints too.
            await self.bus.inbox_drain(ss[0].id, max_count=100)

    async def test_targeted_message_delivers_to_one(self) -> None:
        """``to=<member name>`` delivers a HintBlock + wakeup to that worker
        only; other team members see nothing."""
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        target_aid = self.worker_ids[0]
        target_agent = await self.storage.get_agent(
            self.user_id,
            target_aid,
        )
        chunk = await tool(content="hi w1", to=target_agent.data.name)
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Inbox of target has the hint; the other worker's inbox stays
        # empty.
        target_sid = self.worker_sessions[target_aid]
        other_sid = self.worker_sessions[self.worker_ids[1]]
        target_inbox = await self.bus.inbox_drain(target_sid, max_count=10)
        other_inbox = await self.bus.inbox_drain(other_sid, max_count=10)
        self.assertEqual(len(target_inbox), 1)
        self.assertEqual(len(other_inbox), 0)
        self.assertDictEqual(
            target_inbox[0][1],
            {
                "type": "hint",
                "id": AnyString(),
                "hint": AnyString(),
                "source": AnyString(),
            },
        )
        self.assertIn("hi w1", target_inbox[0][1]["hint"])

        # Wakeup for the target only.
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(
            wakeups,
            [
                {
                    "session_id": target_sid,
                    "agent_id": target_aid,
                    "user_id": self.user_id,
                },
            ],
        )

    async def test_broadcast_delivers_to_all_others(self) -> None:
        """``to=None`` broadcasts to everyone in the team except the
        sender."""
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        chunk = await tool(content="all hands", to=None)
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Both workers receive; leader doesn't loopback to itself.
        for aid, sid in self.worker_sessions.items():
            inbox = await self.bus.inbox_drain(sid, max_count=10)
            self.assertEqual(
                len(inbox),
                1,
                f"worker {aid} missed broadcast",
            )
        leader_inbox = await self.bus.inbox_drain(
            self.leader_session.id,
            max_count=10,
        )
        self.assertEqual(leader_inbox, [])

        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(len(wakeups), 2)

    async def test_rejects_when_session_not_in_team(self) -> None:
        """A session without a team can't TeamSay."""
        loner_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws-lone"),
        )
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=loner_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        chunk = await tool(content="hi", to=None)
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_rejects_unknown_recipient(self) -> None:
        """A ``to=`` that doesn't resolve to a team member name returns
        an error chunk."""
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        chunk = await tool(content="hi", to="ghost-name-not-in-team")
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_rejects_self_target(self) -> None:
        """``to=<own name>`` is rejected — talk to yourself in
        reasoning, not via TeamSay."""
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        chunk = await tool(content="hi", to=self.leader_agent.data.name)
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_worker_can_address_leader_by_name(self) -> None:
        """A worker constructed with ``role="worker"`` can address the
        leader using the leader's name — the only identifier a worker
        ever has for the leader (received via the ``from=`` attribute
        of the initial ``<team-message>`` hint)."""
        worker_aid = self.worker_ids[0]
        worker_sid = self.worker_sessions[worker_aid]

        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=worker_sid,
            agent_id=worker_aid,
            role="worker",
        )
        chunk = await tool(
            content="task done",
            to=self.leader_agent.data.name,
        )
        self.assertEqual(chunk.state.value, "running")

        # Leader's inbox received the worker's reply.
        leader_inbox = await self.bus.inbox_drain(
            self.leader_session.id,
            max_count=10,
        )
        self.assertEqual(len(leader_inbox), 1)
        self.assertIn("task done", leader_inbox[0][1]["hint"])

        # Wakeup was enqueued for the leader.
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(
            wakeups,
            [
                {
                    "session_id": self.leader_session.id,
                    "agent_id": self.leader_agent.id,
                    "user_id": self.user_id,
                },
            ],
        )


class TestTeamDelete(_TeamToolsTestBase):
    """``TeamDelete`` dissolves the team and clears the leader's
    ``team_id``."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="d")

    async def test_dissolves_team_from_leader(self) -> None:
        """``TeamDelete`` removes the team and clears the leader's
        ``team_id``."""
        tool = TeamDelete(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool()
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        self.assertIsNone(sess.team_id)

    async def test_rejects_when_not_in_team(self) -> None:
        """Calling ``TeamDelete`` from a session that isn't in any team
        returns an error chunk."""
        loner_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws-lone"),
        )
        tool = TeamDelete(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=loner_session.id,
            agent_id=self.leader_agent.id,
        )
        chunk = await tool()
        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {"type": "text", "text": AnyString(), "id": AnyString()},
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
