# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Link tests for sub-agent tool configuration: :class:`SubAgentTemplate`
→ :class:`AgentCreate` → :func:`get_toolkit` → :class:`PermissionEngine`
enforcement.

:class:`service_team_tools_test` covers the storage side effects of
``AgentCreate`` and the template permission-merge policy in isolation;
:class:`service_toolkit_test` covers ``get_toolkit`` assembly rules with
hand-built records.  This file stitches the two together into one
end-to-end scenario — a leader creates a team and spawns a
**read-only researcher** worker from a template, then we verify:

- the worker's system prompt is rendered from the template;
- the worker's toolkit is role-gated (``TeamSay`` yes, leader-side team
  tools no) even though it is assembled by the same ``get_toolkit``
  entry point the leader uses;
- the template's tool-level ``deny`` rule survives into the worker's
  runtime :class:`PermissionEngine` and actually blocks tool execution,
  while the leader can still run the same tool;
- ``extra_factory`` can differentiate leader vs. worker tool injection.

The template uses :attr:`PermissionMode.BYPASS` + a ``Write`` deny rule
deliberately: in ``EXPLORE`` mode ``Write`` is rejected by the mode
itself (write is never read-only), which would mask the contribution of
the template's deny rule.  Under ``BYPASS`` the deny rule is the *only*
guardrail, so a ``DENY`` verdict is attributable to the template rule
alone.
"""
from contextlib import AsyncExitStack
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from utils import FakeWorkspaceManager

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import BackgroundTaskManager, SchedulerManager
from agentscope.app._service import ResourceAccessService, get_toolkit
from agentscope.app._tool import AgentCreate, TeamCreate
from agentscope.app._types import SubAgentTemplate
from agentscope.app.access import DenyAllResourceAccessPolicy
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    RedisStorage,
    SessionConfig,
)
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import Read, ToolBase, Write


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


class _FakeWorkspace:
    """Stand-in for a resolved workspace — only the three discovery
    methods :func:`get_toolkit` calls are implemented."""

    def __init__(self, tools: list[ToolBase] | None = None) -> None:
        self._tools = tools or []

    async def list_tools(self) -> list[ToolBase]:
        """Return the configured workspace tools."""
        return list(self._tools)

    async def list_skills(self) -> list:
        """Return the configured workspace skills."""
        return []

    async def list_mcps(self) -> list:
        """Return the configured workspace MCP descriptors."""
        return []


class _StubTool(ToolBase):
    """Minimal :class:`ToolBase` subclass that satisfies the abstract
    methods so :func:`get_toolkit` can register the instance.  Nothing
    in these tests actually calls the tool."""

    name: str = "stub"
    description: str = "stub tool"
    input_schema: dict = {}
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(self, *args: Any, **kwargs: Any) -> None:
        """No-op permission check."""

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        """No-op invocation."""


def _tool_names(toolkit: Any) -> list[str]:
    """Extract every registered tool name from a :class:`Toolkit`,
    walking its tool groups."""
    return [t.name for group in toolkit.tool_groups for t in group.tools]


class TestSubAgentToolConfiguration(IsolatedAsyncioTestCase):
    """The full chain: a restricted worker template ends up restricting
    the tools the worker can actually execute.

    Fixture: a leader owns a session, creates team ``codex-team``, and
    spawns a ``researcher`` worker from a BYPASS + deny-Write template.
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
        # Leader state: BYPASS so a plain Write call is ALLOWED — the
        # only thing that can reject it is an explicit deny rule.
        self.leader_state = AgentState(
            permission_context=PermissionContext(
                mode=PermissionMode.BYPASS,
            ),
        )

        # The "read-only researcher" template.
        self.researcher_template = SubAgentTemplate(
            type="researcher",
            description="Read-only code researcher.",
            system_prompt_template=(
                "You are {member_name}, a member of team {team_name} "
                "led by {leader_name}."
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
        )

        # Leader creates a team, then spawns the worker from the
        # template.
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="codex-team", description="analyze the codebase")
        create_chunk = await AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates={
                self.researcher_template.type: self.researcher_template,
            },
        )(
            name="researcher",
            description="reads and reports on the codebase",
            prompt="find how authentication is handled",
            subagent_type="researcher",
            _agent_state=self.leader_state,
        )
        self.assertEqual(create_chunk.state.value, "running")

        # Resolve the worker record + session from storage.  Re-read
        # the leader session: ``TeamCreate`` stamps ``team_id`` on the
        # stored record, not on the object we hold.
        leader_sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        self.assertIsNotNone(leader_sess.team_id)
        team = await self.storage.get_team(
            self.user_id,
            leader_sess.team_id,
        )
        self.assertIsNotNone(team)
        self.worker_agent_id = team.data.member_ids[-1]
        self.worker_agent = await self.storage.get_agent(
            self.user_id,
            self.worker_agent_id,
        )
        worker_sessions = await self.storage.list_sessions(
            self.user_id,
            self.worker_agent_id,
        )
        self.assertEqual(len(worker_sessions), 1)
        self.worker_session = worker_sessions[0]

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def _get_toolkit_for(
        self,
        agent: AgentRecord,
        session: Any,
        extra_factory: Any = None,
    ) -> Any:
        """Assemble the toolkit for *session* exactly as
        :class:`ChatService` would, with an optional extra factory."""
        return await get_toolkit(
            storage=self.storage,
            workspace=_FakeWorkspace(
                tools=[type("_WsStub", (_StubTool,), {"name": "ws-stub"})()],
            ),
            workspace_manager=self.workspace_manager,
            scheduler_manager=SchedulerManager(
                storage=self.storage,
                message_bus=self.bus,
            ),
            background_task_manager=BackgroundTaskManager(
                message_bus=self.bus,
            ),
            message_bus=self.bus,
            user_id=self.user_id,
            agent_record=agent,
            session_record=session,
            extra_factory=extra_factory,
            middlewares=[],
            resource_access_service=ResourceAccessService(
                storage=self.storage,
                policy=DenyAllResourceAccessPolicy(),
            ),
        )

    async def test_template_prompt_rendered_in_worker_record(self) -> None:
        """The template's ``system_prompt_template`` is rendered with
        the team/member/leader names into the worker's record."""
        prompt = self.worker_agent.data.system_prompt
        self.assertIn("researcher", prompt)
        self.assertIn("codex-team", prompt)
        self.assertIn("leader", prompt)
        # No unreplaced placeholders.
        self.assertNotIn("{", prompt)
        self.assertNotIn("}", prompt)

    async def test_worker_toolkit_role_gated_after_create(self) -> None:
        """The worker toolkit is assembled by the same ``get_toolkit``
        as the leader, but the team-tool variant is worker-scoped:
        ``TeamSay`` yes, leader-side team tools no.  Shared sources
        (workspace builtins, planning tools) remain available."""
        toolkit = await self._get_toolkit_for(
            self.worker_agent,
            self.worker_session,
        )
        names = set(_tool_names(toolkit))

        # Workspace tool present.
        self.assertIn("ws-stub", names)
        # Planning tools present.
        self.assertTrue(
            {"TaskCreate", "TaskList", "TaskGet", "TaskUpdate"} <= names,
        )
        # Worker-scoped team tool: only TeamSay.
        self.assertIn("TeamSay", names)
        for absent in (
            "TeamCreate",
            "AgentCreate",
            "TeamDelete",
            "AgentInvite",
        ):
            self.assertNotIn(absent, names)

    async def test_template_deny_rule_blocks_worker_write(self) -> None:
        """The template's ``Write`` deny rule survives into the worker's
        runtime permission context: the worker's engine returns DENY for
        a plain file write, the leader's engine (same BYPASS mode, no
        deny rule) returns ALLOW, and read-only tools stay usable for
        the worker."""
        worker_engine = PermissionEngine(
            self.worker_session.state.permission_context,
        )
        leader_engine = PermissionEngine(
            self.leader_state.permission_context,
        )
        write_input = {
            "file_path": "/tmp/as-workspace/out.txt",
            "content": "hello",
        }

        # The worker is blocked by the template's deny rule.
        worker_write = await worker_engine.check_permission(
            Write(),
            write_input,
        )
        self.assertEqual(worker_write.behavior, PermissionBehavior.DENY)

        # The leader (same BYPASS mode, no deny rule) is allowed.
        leader_write = await leader_engine.check_permission(
            Write(),
            write_input,
        )
        self.assertEqual(leader_write.behavior, PermissionBehavior.ALLOW)

        # Read-only tools remain available to the worker — the
        # restriction is tool-specific, not a blanket ban.
        worker_read = await worker_engine.check_permission(
            Read(),
            {"file_path": "/tmp/as-workspace/out.txt"},
        )
        self.assertEqual(worker_read.behavior, PermissionBehavior.ALLOW)

    async def test_extra_factory_differentiates_leader_and_worker(
        self,
    ) -> None:
        """``extra_factory`` receives the session id and can resolve the
        team role: the leader-only tool is injected into the leader's
        toolkit and withheld from the worker's."""

        class _CodeReviewTool(_StubTool):
            """Leader-only extra tool."""

            name: str = "CodeReviewTool"
            description: str = "stub leader-only tool"

        code_review_tool = _CodeReviewTool()

        async def factory(
            user_id: str,
            agent_id: str,
            session_id: str,
        ) -> list[ToolBase]:
            """Leader-only injection: resolve the team role from
            storage and only hand the tool to the leader session."""
            session = await self.storage.get_session(
                user_id,
                agent_id,
                session_id,
            )
            if session is None or session.team_id is None:
                return []
            team = await self.storage.get_team(user_id, session.team_id)
            if team is None or team.session_id != session_id:
                return []  # worker
            return [code_review_tool]  # leader

        leader_toolkit = await self._get_toolkit_for(
            self.leader_agent,
            self.leader_session,
            extra_factory=factory,
        )
        worker_toolkit = await self._get_toolkit_for(
            self.worker_agent,
            self.worker_session,
            extra_factory=factory,
        )

        self.assertIn("CodeReviewTool", _tool_names(leader_toolkit))
        self.assertNotIn("CodeReviewTool", _tool_names(worker_toolkit))
