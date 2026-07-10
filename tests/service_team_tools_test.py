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

from utils import AnyString, FakeWorkspaceManager

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._tool import (
    AgentCreate,
    AgentInvite,
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
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState


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
        self.workspace_manager = FakeWorkspaceManager()

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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
                "kind": "wake",
                "input": None,
            },
        )

    async def _spawn_worker_with_template(
        self,
        leader_state: AgentState,
        template: SubAgentTemplate,
        worker_name: str = "worker",
    ) -> PermissionContext:
        """Run ``AgentCreate`` with the given template + leader state
        and return the worker session's :class:`PermissionContext`."""
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates={template.type: template},
        )
        chunk = await tool(
            name=worker_name,
            description="does work",
            prompt="work in the repo",
            subagent_type=template.type,
            _agent_state=leader_state,
        )
        self.assertEqual(chunk.state.value, "running")
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        # The worker is whichever member was added last.
        worker_agent_id = team.data.member_ids[-1]
        worker_sessions = await self.storage.list_sessions(
            self.user_id,
            worker_agent_id,
        )
        return worker_sessions[0].state.permission_context

    def _make_leader_state(self) -> AgentState:
        """Build a leader :class:`AgentState` with one of every kind of
        permission entry, so tests can assert which pieces leak through
        each flag."""
        return AgentState(
            permission_context=PermissionContext(
                mode=PermissionMode.ACCEPT_EDITS,
                working_directories={
                    "/tmp/as-workspace": AdditionalWorkingDirectory(
                        path="/tmp/as-workspace",
                        source="session",
                    ),
                },
                allow_rules={
                    "Bash": [
                        PermissionRule(
                            tool_name="Bash",
                            rule_content="git status",
                            behavior=PermissionBehavior.ALLOW,
                            source="session",
                        ),
                    ],
                },
            ),
        )

    async def test_default_template_follows_leader_completely(self) -> None:
        """The built-in default template inherits the leader's mode,
        working directories, and rules — its own
        :attr:`permission_context` is empty, so the worker effectively
        mirrors the leader."""
        leader_state = self._make_leader_state()
        worker_context = await self._spawn_worker_with_template(
            leader_state,
            DEFAULT_SUB_AGENT_TEMPLATE,
        )
        self.assertEqual(worker_context.mode, PermissionMode.ACCEPT_EDITS)
        self.assertIn("/tmp/as-workspace", worker_context.working_directories)
        self.assertEqual(
            worker_context.allow_rules["Bash"][0].rule_content,
            "git status",
        )

    async def test_override_leader_mode_pins_template_mode(self) -> None:
        """When ``override_leader_mode=True`` the template's mode wins."""
        leader_state = self._make_leader_state()
        explorer = SubAgentTemplate(
            type="explorer",
            description="Read-only worker.",
            system_prompt_template=(
                DEFAULT_SUB_AGENT_TEMPLATE.system_prompt_template
            ),
            permission_context=PermissionContext(
                mode=PermissionMode.EXPLORE,
            ),
            override_leader_mode=True,
        )
        worker_context = await self._spawn_worker_with_template(
            leader_state,
            explorer,
        )
        self.assertEqual(worker_context.mode, PermissionMode.EXPLORE)
        # Rules and dirs still inherited (defaults).
        self.assertIn("/tmp/as-workspace", worker_context.working_directories)
        self.assertIn("Bash", worker_context.allow_rules)

    async def test_extend_flags_off_isolate_template(self) -> None:
        """``extend_*=False`` keeps the leader's rules and dirs out of
        the worker; the template's own entries are the worker's
        complete set."""
        leader_state = self._make_leader_state()
        sandbox = SubAgentTemplate(
            type="sandbox",
            description="Fully isolated worker.",
            system_prompt_template=(
                DEFAULT_SUB_AGENT_TEMPLATE.system_prompt_template
            ),
            permission_context=PermissionContext(
                mode=PermissionMode.BYPASS,
                deny_rules={
                    "Write": [
                        PermissionRule(
                            tool_name="Write",
                            rule_content=None,
                            behavior=PermissionBehavior.DENY,
                            source="template",
                        ),
                    ],
                },
            ),
            override_leader_mode=True,
            extend_leader_permission_rules=False,
            extend_leader_working_directories=False,
        )
        worker_context = await self._spawn_worker_with_template(
            leader_state,
            sandbox,
        )
        self.assertEqual(worker_context.mode, PermissionMode.BYPASS)
        self.assertEqual(worker_context.working_directories, {})
        self.assertNotIn("Bash", worker_context.allow_rules)
        # Template's own deny rule is preserved.
        self.assertEqual(
            worker_context.deny_rules["Write"][0].source,
            "template",
        )

    async def test_extend_rules_keeps_template_rules_first(self) -> None:
        """When merging rules for the same tool, the template's rules
        appear first in the list so the engine evaluates them before
        the leader's."""
        leader_state = AgentState(
            permission_context=PermissionContext(
                mode=PermissionMode.DEFAULT,
                allow_rules={
                    "Bash": [
                        PermissionRule(
                            tool_name="Bash",
                            rule_content="git status",
                            behavior=PermissionBehavior.ALLOW,
                            source="session",
                        ),
                    ],
                },
            ),
        )
        template = SubAgentTemplate(
            type="custom",
            description="Custom worker.",
            system_prompt_template=(
                DEFAULT_SUB_AGENT_TEMPLATE.system_prompt_template
            ),
            permission_context=PermissionContext(
                allow_rules={
                    "Bash": [
                        PermissionRule(
                            tool_name="Bash",
                            rule_content="ls",
                            behavior=PermissionBehavior.ALLOW,
                            source="template",
                        ),
                    ],
                },
            ),
        )
        worker_context = await self._spawn_worker_with_template(
            leader_state,
            template,
        )
        bash_rules = worker_context.allow_rules["Bash"]
        self.assertEqual(
            [r.source for r in bash_rules],
            ["template", "session"],
        )

    async def test_extend_dirs_template_wins_on_collision(self) -> None:
        """When the template and leader both declare the same working
        directory path, the template's entry is kept."""
        leader_state = AgentState(
            permission_context=PermissionContext(
                working_directories={
                    "/tmp/shared": AdditionalWorkingDirectory(
                        path="/tmp/shared",
                        source="session",
                    ),
                },
            ),
        )
        template = SubAgentTemplate(
            type="custom",
            description="Custom worker.",
            system_prompt_template=(
                DEFAULT_SUB_AGENT_TEMPLATE.system_prompt_template
            ),
            permission_context=PermissionContext(
                working_directories={
                    "/tmp/shared": AdditionalWorkingDirectory(
                        path="/tmp/shared",
                        source="template",
                    ),
                },
            ),
        )
        worker_context = await self._spawn_worker_with_template(
            leader_state,
            template,
        )
        self.assertEqual(
            worker_context.working_directories["/tmp/shared"].source,
            "template",
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="team desc")
        # Add 2 workers.
        agent_create = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
                    "kind": "wake",
                    "input": None,
                },
            ],
        )

    async def test_broadcast_delivers_to_all_others(self) -> None:
        """``to=None`` broadcasts to everyone in the team except the
        sender."""
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
                    "kind": "wake",
                    "input": None,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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
            workspace_manager=self.workspace_manager,
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


# ----------------------------------------------------------------------
# AgentInvite tests
# ----------------------------------------------------------------------


class TestAgentDataInvitableValidator(IsolatedAsyncioTestCase):
    """:class:`InviteConfig`'s cross-field validator rejects
    ``invitable=True`` without a non-empty ``invite_description`` at
    model boundary so PATCH / POST return HTTP 422 automatically."""

    async def test_invitable_requires_description(self) -> None:
        """``invitable=True`` with empty description fails validation."""
        from pydantic import ValidationError
        from agentscope.app.storage._model._agent import InviteConfig

        with self.assertRaises(ValidationError):
            AgentData(
                name="x",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                invite_config=InviteConfig(
                    invitable=True,
                    invite_description=None,
                ),
            )
        with self.assertRaises(ValidationError):
            AgentData(
                name="x",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                invite_config=InviteConfig(
                    invitable=True,
                    invite_description="   ",
                ),
            )

    async def test_invitable_off_keeps_description(self) -> None:
        """Draft description is preserved when ``invitable=False``."""
        from agentscope.app.storage._model._agent import InviteConfig

        data = AgentData(
            name="x",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            invite_config=InviteConfig(
                invitable=False,
                invite_description="draft",
            ),
        )
        self.assertEqual(data.invite_config.invite_description, "draft")
        self.assertFalse(data.invite_config.invitable)


class _AgentInviteTestBase(_TeamToolsTestBase):
    """Fixture: leader in a team + one invitable "Monday" agent with
    a primary session so ``AgentInvite`` has something to borrow."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Leader creates a team.
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="team", description="d")

        # A pre-existing invitable user-owned agent.
        from agentscope.app.storage._model._agent import InviteConfig

        self.monday_agent = AgentRecord(
            user_id=self.user_id,
            source="user",
            data=AgentData(
                name="Monday",
                system_prompt="I am Monday.",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                invite_config=InviteConfig(
                    invitable=True,
                    invite_description="Expert on X.",
                ),
            ),
        )
        await self.storage.upsert_agent(self.user_id, self.monday_agent)
        self.monday_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.monday_agent.id,
            config=SessionConfig(workspace_id="ws-monday"),
        )


class TestAgentInviteSuccess(_AgentInviteTestBase):
    """Happy-path: leader borrows Monday; borrowed session is minted
    on top of Monday's existing AgentRecord and joins the team as an
    invited member."""

    async def test_invites_existing_agent(self) -> None:
        """A successful ``AgentInvite`` mints a fresh session on the
        invited agent, tags it invited in the team roster, and delivers
        the initial prompt via inbox + wakeup."""
        pool = [self.monday_agent]
        tool = AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            invitable_pool=pool,
        )
        target = f"Monday@{self.monday_agent.id[:8]}"
        chunk = await tool(target=target, prompt="please look up X")
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

        # No new AgentRecord — the invited agent's id is reused.
        agents = await self.storage.list_agents(self.user_id)
        agent_ids = {a.id for a in agents}
        self.assertIn(self.monday_agent.id, agent_ids)
        # Only leader + Monday exist (no team-spawned worker record).
        self.assertEqual(len(agents), 2)

        # Team now has exactly one invited member pointing at Monday.
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.assertEqual(len(team.data.members), 1)
        member = team.data.members[0]
        self.assertEqual(member.role, "invited")
        self.assertEqual(member.agent_id, self.monday_agent.id)
        self.assertEqual(member.owner_id, self.user_id)

        # The borrowed session is distinct from Monday's primary
        # session but inherits its workspace_id.
        self.assertNotEqual(member.session_id, self.monday_session.id)
        borrowed = await self.storage.get_session(
            self.user_id,
            self.monday_agent.id,
            member.session_id,
        )
        self.assertEqual(borrowed.config.workspace_id, "ws-monday")
        self.assertEqual(borrowed.team_id, team.id)

        # Borrowed session starts with a fresh PermissionContext —
        # no leader / Monday state carried over.
        self.assertEqual(
            borrowed.state.permission_context,
            PermissionContext(),
        )

        # Initial prompt was delivered to the borrowed session only.
        inbox = await self.bus.inbox_drain(member.session_id, max_count=10)
        self.assertEqual(len(inbox), 1)
        self.assertIn("please look up X", inbox[0][1]["hint"])
        # Monday's primary session inbox is untouched.
        primary_inbox = await self.bus.inbox_drain(
            self.monday_session.id,
            max_count=10,
        )
        self.assertEqual(primary_inbox, [])


class TestAgentInviteRejections(_AgentInviteTestBase):
    """``AgentInvite`` rejects the obvious bad inputs / states."""

    _EXPECTED_ERROR_CHUNK = {
        "content": [
            {"type": "text", "text": AnyString(), "id": AnyString()},
        ],
        "state": "error",
        "is_last": True,
        "metadata": {},
        "id": AnyString(),
    }

    def _tool(self, pool: list | None = None) -> AgentInvite:
        """Build an :class:`AgentInvite` bound to the leader session."""
        return AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            invitable_pool=pool or [self.monday_agent],
        )

    async def test_rejects_malformed_target(self) -> None:
        """A ``target`` without an ``@`` separator is rejected."""
        chunk = await self._tool()(target="just-a-name", prompt="hi")
        self.assertDictEqual(chunk.model_dump(), self._EXPECTED_ERROR_CHUNK)

    async def test_rejects_unknown_handle(self) -> None:
        """A syntactically-valid ``target`` whose handle prefix does
        not match any agent in the pool is rejected."""
        chunk = await self._tool()(
            target="Monday@deadbeef",
            prompt="hi",
        )
        self.assertDictEqual(chunk.model_dump(), self._EXPECTED_ERROR_CHUNK)

    async def test_rejects_when_no_longer_invitable(self) -> None:
        """Snapshot said invitable but fresh read shows the toggle off."""
        # Flip the toggle off in storage while pool snapshot still has it.
        stale = self.monday_agent
        stale.data.invite_config.invitable = False
        stale.data.invite_config.invite_description = None
        await self.storage.upsert_agent(self.user_id, stale)

        chunk = await self._tool(pool=[stale])(
            target=f"Monday@{stale.id[:8]}",
            prompt="hi",
        )
        self.assertDictEqual(chunk.model_dump(), self._EXPECTED_ERROR_CHUNK)

    async def test_rejects_when_not_leader(self) -> None:
        """A session that has no team can't invite."""
        loner = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws-lone"),
        )
        tool = AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=loner.id,
            agent_id=self.leader_agent.id,
            invitable_pool=[self.monday_agent],
        )
        chunk = await tool(
            target=f"Monday@{self.monday_agent.id[:8]}",
            prompt="hi",
        )
        self.assertDictEqual(chunk.model_dump(), self._EXPECTED_ERROR_CHUNK)

    async def test_rejects_duplicate_borrow(self) -> None:
        """One team, one borrow per agent."""
        tool = self._tool()
        await tool(
            target=f"Monday@{self.monday_agent.id[:8]}",
            prompt="task 1",
        )
        chunk = await tool(
            target=f"Monday@{self.monday_agent.id[:8]}",
            prompt="task 2",
        )
        self.assertDictEqual(chunk.model_dump(), self._EXPECTED_ERROR_CHUNK)


class TestTeamSayInvitedRouting(_AgentInviteTestBase):
    """``TeamSay`` reaches the borrowed session using the ``@handle``
    display string, and the invited agent can reply back."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Borrow Monday.
        await AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            invitable_pool=[self.monday_agent],
        )(target=f"Monday@{self.monday_agent.id[:8]}", prompt="join")

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        self.borrowed_sid = next(
            m.session_id for m in team.data.members if m.role == "invited"
        )
        # Drain the initial "join" prompt so later inbox checks see
        # only the subsequent TeamSay delivery.
        await self.bus.inbox_drain(self.borrowed_sid, max_count=10)
        await self.bus.dequeue_wakeups(max_count=10)

    async def test_leader_addresses_invited_by_display(self) -> None:
        """Leader's ``TeamSay(to="Monday@<prefix>")`` reaches the
        borrowed session — not Monday's primary session."""
        display = f"Monday@{self.monday_agent.id[:8]}"
        tool = TeamSay(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            role="leader",
        )
        chunk = await tool(content="hey", to=display)
        self.assertEqual(chunk.state.value, "running")

        borrowed_inbox = await self.bus.inbox_drain(
            self.borrowed_sid,
            max_count=10,
        )
        self.assertEqual(len(borrowed_inbox), 1)
        primary_inbox = await self.bus.inbox_drain(
            self.monday_session.id,
            max_count=10,
        )
        self.assertEqual(primary_inbox, [])


class TestTeamDeletePreservesInvited(_AgentInviteTestBase):
    """``TeamDelete`` removes the borrowed session but preserves the
    invited agent's :class:`AgentRecord` and other sessions."""

    async def test_borrowed_session_dies_agent_survives(self) -> None:
        """After ``TeamDelete``, the invited agent's ``AgentRecord``
        and primary session survive; only the team-scoped borrowed
        session is removed."""
        await AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            invitable_pool=[self.monday_agent],
        )(target=f"Monday@{self.monday_agent.id[:8]}", prompt="join")

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        borrowed_sid = next(
            m.session_id for m in team.data.members if m.role == "invited"
        )

        await TeamDelete(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )()

        # Monday's AgentRecord survives.
        monday = await self.storage.get_agent(
            self.user_id,
            self.monday_agent.id,
        )
        self.assertIsNotNone(monday)
        # Monday's primary session survives.
        primary = await self.storage.get_session(
            self.user_id,
            self.monday_agent.id,
            self.monday_session.id,
        )
        self.assertIsNotNone(primary)
        # Borrowed session is gone.
        gone = await self.storage.get_session(
            self.user_id,
            self.monday_agent.id,
            borrowed_sid,
        )
        self.assertIsNone(gone)


class TestDeleteInvitedAgentReverseCascade(_AgentInviteTestBase):
    """Deleting an invited agent while it is borrowed extracts the
    stale entry from the borrowing team's roster."""

    async def test_reverse_cascade(self) -> None:
        """Deleting an invited agent while it is borrowed also strips
        the stale entry from the borrowing team's ``members`` /
        ``member_ids`` — the leader's later ``TeamSay`` will hit a
        clean "no such member" instead of dangling routing."""
        from agentscope.app._service import SessionService

        await AgentInvite(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            invitable_pool=[self.monday_agent],
        )(target=f"Monday@{self.monday_agent.id[:8]}", prompt="join")

        service = SessionService(
            storage=self.storage,
            message_bus=self.bus,
        )
        await service.delete_agent(self.user_id, self.monday_agent.id)

        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        # No stale invited entry remains.
        self.assertEqual(
            [
                m
                for m in team.data.members
                if m.agent_id == self.monday_agent.id
            ],
            [],
        )
        # Legacy member_ids scrubbed too.
        self.assertNotIn(self.monday_agent.id, team.data.member_ids)


class TestEnsureTeamMembersMigration(_TeamToolsTestBase):
    """Legacy ``TeamRecord`` with only ``member_ids`` populated is
    migrated to the new ``members`` shape on first read via
    ``ensure_team_members``."""

    async def test_legacy_member_ids_migrate_to_created_role(self) -> None:
        """A ``TeamRecord`` whose stored shape only carries the legacy
        ``member_ids`` list is migrated in place on first read via
        ``ensure_team_members``: each id becomes a
        ``TeamMember(role="created", ...)`` entry, and the writeback
        means later reads hit the fast path."""
        from agentscope.app.storage._model import TeamData, TeamRecord
        from agentscope.app.storage._utils import _ensure_team_members

        # Fabricate a legacy worker agent + session (no ``members``
        # entry on the team, just ``member_ids``).
        worker = _make_agent_record(
            self.user_id,
            "legacy-worker",
            source="team",
        )
        await self.storage.upsert_agent(self.user_id, worker)
        worker_sess = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=worker.id,
            config=SessionConfig(workspace_id="ws-w"),
        )

        team = TeamRecord(
            user_id=self.user_id,
            session_id=self.leader_session.id,
            data=TeamData(
                name="legacy",
                description="",
                member_ids=[worker.id],
                # members deliberately left empty
            ),
        )
        await self.storage.upsert_team(self.user_id, team)

        members = await _ensure_team_members(
            self.storage,
            self.user_id,
            team,
        )
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].role, "created")
        self.assertEqual(members[0].agent_id, worker.id)
        self.assertEqual(members[0].session_id, worker_sess.id)
        self.assertEqual(members[0].owner_id, self.user_id)

        # And the writeback happened — subsequent reads take the fast
        # path.
        stored = await self.storage.get_team(self.user_id, team.id)
        self.assertEqual(len(stored.data.members), 1)
