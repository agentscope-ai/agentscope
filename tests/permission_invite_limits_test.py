# -*- coding: utf-8 -*-
"""Invited workers retain hard limits across workspace boundaries."""
# pylint: disable=protected-access,cell-var-from-loop
import json
from itertools import product
from unittest.mock import patch

from realtime_delegation_test import _create_live_agent
from service_team_tools_test import _AgentInviteTestBase
from utils import MockModel

from agentscope.agent import Agent
from agentscope.app._tool import AgentCreate, AgentInvite
from agentscope.event import RequireUserConfirmEvent, ToolResultEndEvent
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
    PermissionRule,
)
from agentscope.permission._evidence import ScheduleAuthorization
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


class InviteHardLimitsTest(_AgentInviteTestBase):
    """Use real invitations, persistence, and worker execution boundaries."""

    async def test_delegation_workers_keep_narrowed_schedule(self) -> None:
        """Both team tools persist the current task description."""
        await self._assert_delegated_workers(clear=False)

    async def test_delegation_workers_drop_revoked_schedule(self) -> None:
        """Both team tools drop a task revoked after delegation started."""
        await self._assert_delegated_workers(clear=True)

    async def _assert_delegated_workers(self, clear: bool) -> None:
        deps = {
            "storage": self.storage,
            "message_bus": self.bus,
            "workspace_manager": self.workspace_manager,
            "user_id": self.user_id,
            "session_id": self.leader_session.id,
            "agent_id": self.leader_agent.id,
        }
        create = AgentCreate(**deps)
        invite = AgentInvite(invitable_pool=[self.monday_agent], **deps)
        live = _create_live_agent(MockModel(), Toolkit(tools=[create, invite]))
        self.addAsyncCleanup(live.close)
        scope = ScheduleAuthorization(
            schedule_id="job",
            user_id=self.user_id,
            source="USER",
            description="Deploy.",
            authorized_description="Deploy.",
        )
        parent = AgentState(schedule_authorization=scope)
        live.state.inherit_member_scope(parent)
        with patch.object(live, "_schedule_client_delegation"):
            live._start_client_delegation("task")
        backend = live._delegations["task"].agent
        assert backend is not None
        parent.schedule_authorization = (
            None
            if clear
            else scope.model_copy(
                update={
                    "description": "Only draft.",
                    "authorized_description": "Only draft.",
                },
            )
        )
        live.state.inherit_member_scope(parent)
        live.state.context.append(UserMsg("user", "Do not publish."))
        for tool, arguments in (
            (create, {"name": "worker", "description": "Draft"}),
            (invite, {"target": f"Monday@{self.monday_agent.id[:8]}"}),
        ):
            call = ToolCallBlock(
                id=tool.name,
                name=tool.name,
                input=json.dumps({**arguments, "prompt": "Review the draft"}),
            )
            results = [
                event.state
                async for event in backend._execute_tool_call(call)
                if isinstance(event, ToolResultEndEvent)
            ]
            self.assertEqual(results, ["success"])
        leader = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, leader.team_id)
        self.assertEqual(len(team.data.members), 2)
        for member in team.data.members:
            worker = await self.storage.get_session(
                self.user_id,
                member.agent_id,
                member.session_id,
            )
            review = worker.state.permission_review_context()
            self.assertEqual(review, backend.state.permission_review_context())
            self.assertEqual(
                (
                    review.get("schedule_authorization"),
                    [entry.text for entry in review["authorization_evidence"]],
                ),
                (parent.schedule_authorization, ["Do not publish."]),
            )

    async def test_invitation_preserves_only_parent_hard_limits(self) -> None:
        """Both invitation paths enforce denials before executing a tool."""
        denied_tool = PermissionRule(
            tool_name="inspect_secret",
            rule_content=None,
            behavior=PermissionBehavior.DENY,
            source="session",
        )
        for index, (read_only, interactive, live) in enumerate(
            product(
                (True, False),
                (True, False),
                (True, False),
            ),
        ):
            with self.subTest(
                read_only=read_only,
                interactive=interactive,
                live=live,
            ):
                parent = AgentState(
                    session_id=self.leader_session.id,
                    permission_context=PermissionContext(
                        mode=PermissionMode.BYPASS,
                        read_only=read_only,
                        interactive=interactive,
                        allow_workspace_edits=True,
                        deny_rules={"inspect_secret": [denied_tool]},
                        working_directories={
                            "parent": AdditionalWorkingDirectory(
                                path="parent",
                                source="session",
                            ),
                        },
                        allow_rules={
                            "write": [
                                PermissionRule(
                                    tool_name="write",
                                    rule_content=None,
                                    behavior=PermissionBehavior.ALLOW,
                                    source="session",
                                ),
                            ],
                        },
                    ),
                )
                stored = parent.model_copy(deep=True)
                if live:
                    stored.permission_context.deny_rules.clear()
                    stored.permission_context.read_only = not read_only
                    stored.permission_context.interactive = not interactive
                await self.storage.upsert_session(
                    self.user_id,
                    self.leader_agent.id,
                    self.leader_session.config,
                    state=stored,
                    session_id=self.leader_session.id,
                )
                invited = self.monday_agent.model_copy(
                    update={"id": f"invited-{index}"},
                    deep=True,
                )
                await self.storage.upsert_agent(self.user_id, invited)
                tool = AgentInvite(
                    storage=self.storage,
                    message_bus=self.bus,
                    workspace_manager=self.workspace_manager,
                    user_id=self.user_id,
                    session_id=self.leader_session.id,
                    agent_id=self.leader_agent.id,
                    invitable_pool=[invited],
                )
                self.assertEqual(
                    (
                        await tool.check_permissions(
                            {},
                            parent.permission_context,
                        )
                    ).behavior,
                    PermissionBehavior.ALLOW,
                )
                result = await tool(
                    target=f"Monday@{invited.id[:8]}",
                    prompt="Write a release file",
                    _agent_state=parent if live else None,
                )
                self.assertEqual(result.state.value, "running")
                sessions = await self.storage.list_sessions(
                    self.user_id,
                    invited.id,
                )
                worker = sessions[0].state
                executed = []

                def write() -> str:
                    executed.append("write")
                    return "written"

                def inspect_secret() -> str:
                    executed.append("inspect_secret")
                    return "restricted fixture"

                def inspect_public() -> str:
                    executed.append("inspect_public")
                    return "public fixture"

                write_tool = FunctionTool(write, is_read_only=False)
                read_tools = [
                    FunctionTool(inspect_secret, is_read_only=True),
                    FunctionTool(inspect_public, is_read_only=True),
                ]
                agent = Agent(
                    "worker",
                    "Help",
                    MockModel(),
                    state=worker,
                    toolkit=Toolkit(tools=[write_tool, *read_tools]),
                )
                events = [
                    event
                    async for event in agent._execute_tool_call(
                        ToolCallBlock(
                            id="write",
                            name=write_tool.name,
                            input="{}",
                        ),
                    )
                ]
                outcomes = [
                    "asking"
                    if isinstance(event, RequireUserConfirmEvent)
                    else event.state
                    for event in events
                    if isinstance(
                        event,
                        (RequireUserConfirmEvent, ToolResultEndEvent),
                    )
                ]
                self.assertEqual(
                    (outcomes, executed),
                    (
                        [
                            "denied"
                            if read_only or not interactive
                            else "asking",
                        ],
                        [],
                    ),
                )
                read_results = []
                for read_tool in read_tools:
                    call = ToolCallBlock(
                        id=read_tool.name,
                        name=read_tool.name,
                        input="{}",
                    )
                    async for event in agent._execute_tool_call(call):
                        if isinstance(event, ToolResultEndEvent):
                            read_results.append(event.state)
                self.assertEqual(
                    (read_results, executed),
                    (["denied", "success"], ["inspect_public"]),
                )
                self.assertEqual(
                    worker.effective_permission_context,
                    PermissionContext(
                        read_only=read_only,
                        interactive=interactive,
                        deny_rules={"inspect_secret": [denied_tool]},
                    ),
                )
