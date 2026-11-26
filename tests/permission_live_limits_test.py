# -*- coding: utf-8 -*-
"""Live hard restrictions survive approval waits and scheduled execution."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
from itertools import product
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from permission_review_test import _review, _tool
from realtime_agent_test import ScriptedModel
from service_scheduler_test import _SchedulerFireTestBase, _make_record
from schedule_router_test import _Scheduler
from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.app._manager._scheduler._tools._schedule_create import (
    ScheduleCreate,
)
from agentscope.app.storage import ScheduleRecord
from agentscope.app._router._schedule import update_schedule
from agentscope.app._router._schema._schedule import UpdateScheduleRequest
from agentscope.event import RequireUserConfirmEvent, ToolResultEndEvent
from agentscope.message import AssistantMsg, ToolCallBlock, ToolResultState
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import Bash, FunctionTool, Toolkit


def _deny(name: str) -> PermissionRule:
    """Build an explicit whole-tool prohibition from the parent session."""
    return PermissionRule(
        tool_name=name,
        rule_content=None,
        behavior=PermissionBehavior.DENY,
        source="session",
    )


class LiveApprovalLimitsTest(IsolatedAsyncioTestCase):
    """A review result never overrides restrictions added while waiting."""

    async def test_directory_removal_invalidates_review(self) -> None:
        """Directory changes invalidate a pending review."""
        for remove, interactive in product((False, True), repeat=2):
            policy = PermissionContext(
                mode=PermissionMode.AUTO,
                interactive=interactive,
            )
            dirs = policy.working_directories
            for path in ("first", "second"):
                dirs[path] = AdditionalWorkingDirectory(
                    path=path,
                    source="session",
                )

            async def review(request: dict) -> object:
                self.assertEqual(
                    request["working_directories"],
                    ["first", "second"],
                )
                first = dirs.pop("first")
                if not remove:
                    dirs["first"] = first
                return _review()

            engine = PermissionEngine(policy, reviewer=review)
            result = await engine.check_permission(_tool(), {})
            expected = PermissionBehavior.ALLOW
            if remove:
                expected = (
                    PermissionBehavior.ASK
                    if interactive
                    else PermissionBehavior.DENY
                )
            self.assertEqual(result.behavior, expected)

    async def test_mode_changes_discard_stale_auto_results(self) -> None:
        """Current modes handle approval, denial, and failed old reviews."""
        for mode, interactive, replaced, outcome, policy in product(
            (PermissionMode.ASK, PermissionMode.BYPASS),
            (True, False),
            (True, False),
            ("allow", "deny", "error"),
            ("pending", "allow", "deny"),
        ):
            with self.subTest(
                mode=mode,
                interactive=interactive,
                replaced=replaced,
                outcome=outcome,
                policy=policy,
            ):
                started = asyncio.Event()
                release = asyncio.Event()

                async def review(_request: dict) -> object:
                    started.set()
                    await release.wait()
                    if outcome == "error":
                        raise ValueError("Review failed")
                    return _review(policy_violation=outcome == "deny")

                reviewer = AsyncMock(side_effect=review)
                engine = PermissionEngine(
                    PermissionContext(mode=PermissionMode.AUTO),
                    reviewer=reviewer,
                )
                tool = _tool()
                task = asyncio.create_task(engine.check_permission(tool, {}))
                try:
                    await asyncio.wait_for(started.wait(), timeout=2)
                    if replaced:
                        engine.context = engine.context.model_copy(deep=True)
                    engine.context.mode = mode
                    engine.context.interactive = interactive
                    engine.context.read_only = policy == "deny"
                    if policy == "allow":
                        tool.check_permissions.return_value = (
                            PermissionDecision(
                                behavior=PermissionBehavior.ALLOW,
                                message="Granted by current policy",
                            )
                        )
                finally:
                    release.set()
                decision = await asyncio.wait_for(task, timeout=2)
                expected = await PermissionEngine(
                    engine.context,
                ).check_permission(tool, {})
                self.assertEqual(
                    (decision, reviewer.await_count, decision.review),
                    (expected, 1, None),
                )

    async def test_changed_approval_basis_invalidates_old_allow(self) -> None:
        """Mutable safety checks and rules cannot reuse an earlier review."""
        for interactive, masked, change in product(
            (True, False),
            (True, False),
            (
                "safety_ask",
                "message",
                "reason",
                "safety_flag",
                "ask_rule",
                "mutated_rule",
                "unchanged",
                "suggestions",
                "other_tool_rule",
            ),
        ):
            with self.subTest(
                interactive=interactive,
                masked=masked,
                change=change,
            ):
                started = asyncio.Event()
                release = asyncio.Event()

                async def review(_request: dict) -> object:
                    started.set()
                    await release.wait()
                    return _review()

                reviewer = AsyncMock(side_effect=review)
                engine = PermissionEngine(
                    PermissionContext(
                        mode=PermissionMode.AUTO,
                        interactive=interactive,
                    ),
                    reviewer=reviewer,
                )
                tool = _tool()
                original = tool.check_permissions.return_value
                if change in ("message", "reason", "safety_flag"):
                    original.behavior = PermissionBehavior.ASK
                rule = PermissionRule(
                    tool_name=tool.name,
                    rule_content=None,
                    behavior=PermissionBehavior.ASK,
                    source="session",
                )
                if masked or change == "mutated_rule":
                    engine.add_rule(rule)
                task = asyncio.create_task(engine.check_permission(tool, {}))
                try:
                    await asyncio.wait_for(started.wait(), timeout=2)
                    if change == "safety_ask":
                        original.behavior = PermissionBehavior.ASK
                        original.bypass_immune = True
                        original.message = "Destination now requires approval"
                    elif change == "message":
                        original.message = "New sensitive destination"
                    elif change == "reason":
                        original.decision_reason = "New disclosure risk"
                    elif change == "safety_flag":
                        original.bypass_immune = True
                    elif change == "ask_rule":
                        engine.add_rule(
                            rule.model_copy(
                                update={"rule_content": "new destination"},
                            ),
                        )
                    elif change == "mutated_rule":
                        rule.source = "updated policy"
                    elif change == "suggestions":
                        original.suggested_rules = [rule]
                        tool.generate_suggestions.return_value = [rule]
                    elif change == "other_tool_rule":
                        engine.add_rule(
                            rule.model_copy(update={"tool_name": "Other"}),
                        )
                finally:
                    release.set()
                decision = await asyncio.wait_for(task, timeout=2)
                changed = change not in (
                    "unchanged",
                    "suggestions",
                    "other_tool_rule",
                )
                self.assertEqual(
                    (decision.behavior, decision.review, reviewer.await_count),
                    (
                        PermissionBehavior.ASK
                        if interactive and changed
                        else PermissionBehavior.DENY
                        if changed
                        else PermissionBehavior.ALLOW,
                        None if changed else _review(),
                        1,
                    ),
                )
                if changed:
                    current, _ = await engine._evaluate(tool, {})
                    expected = replace(
                        current,
                        suggested_rules=tool.generate_suggestions.return_value,
                        decision_reason=(
                            "Approval requirements changed during automatic "
                            "review; user approval is required. "
                            f"{current.decision_reason or ''}"
                        ).strip(),
                        review=None,
                    )
                    if not interactive:
                        expected = replace(
                            expected,
                            behavior=PermissionBehavior.DENY,
                            message=(
                                f"Permission denied for {tool.name}: "
                                "no user is available to approve "
                                "this operation"
                            ),
                            decision_reason=(
                                "Unattended approval denied. "
                                f"{expected.decision_reason}"
                            ),
                        )
                    self.assertEqual(decision, expected)

    async def test_engine_rechecks_after_review(self) -> None:
        """Check mutated and replaced contexts, including tool hard denials."""
        for replace_context, restriction in product(
            (False, True),
            ("read_only", "deny_rule", "tool_deny", "unchanged"),
        ):
            with self.subTest(
                replacement=replace_context,
                restriction=restriction,
            ):
                started = asyncio.Event()
                release = asyncio.Event()

                async def review(_request: dict) -> object:
                    started.set()
                    await release.wait()
                    return _review()

                reviewer = AsyncMock(side_effect=review)
                engine = PermissionEngine(
                    PermissionContext(mode=PermissionMode.AUTO),
                    reviewer=reviewer,
                )
                tool = _tool()
                task = asyncio.create_task(engine.check_permission(tool, {}))
                try:
                    await asyncio.wait_for(started.wait(), timeout=2)
                    if replace_context:
                        engine.context = engine.context.model_copy(deep=True)
                    if restriction == "read_only":
                        engine.context.read_only = True
                    elif restriction == "deny_rule":
                        engine.context.deny_rules = {
                            tool.name: [_deny(tool.name)],
                        }
                    elif restriction == "tool_deny":
                        tool.check_permissions.return_value = (
                            PermissionDecision(
                                behavior=PermissionBehavior.DENY,
                                message="Tool became unavailable",
                            )
                        )
                finally:
                    release.set()
                decision = await asyncio.wait_for(task, timeout=2)
                self.assertEqual(
                    (decision.behavior, reviewer.await_count),
                    (
                        PermissionBehavior.ALLOW
                        if restriction == "unchanged"
                        else PermissionBehavior.DENY,
                        1,
                    ),
                )
                if restriction != "unchanged":
                    expected, _ = await engine._evaluate(tool, {})
                    self.assertEqual(decision, expected)
                else:
                    self.assertEqual(decision.review, _review())

    async def test_agents_rebind_before_execution(self) -> None:
        """Replacing live state during review blocks actual tool execution."""
        for realtime, restriction in product(
            (False, True),
            (
                "read_only",
                "deny_rule",
                "ask",
                "ask_unattended",
                "bypass",
                "safety_ask",
                "safety_ask_unattended",
                "new_ask_rule",
                "new_ask_rule_unattended",
            ),
        ):
            with self.subTest(realtime=realtime, restriction=restriction):
                executed = []
                started = asyncio.Event()
                release = asyncio.Event()

                def write() -> str:
                    executed.append(True)
                    return "written"

                async def review(_request: dict) -> object:
                    started.set()
                    await release.wait()
                    return _review()

                reviewer = AsyncMock(side_effect=review)
                tool = FunctionTool(write, is_read_only=False)
                cached = PermissionDecision(
                    behavior=PermissionBehavior.PASSTHROUGH,
                    message="Initial tool check",
                )
                if restriction.startswith("safety_ask"):
                    tool.check_permissions = AsyncMock(return_value=cached)
                agent_class = RealtimeAgent if realtime else Agent
                agent = agent_class(
                    "agent",
                    "Help",
                    ScriptedModel([[]]) if realtime else MockModel(),
                    toolkit=Toolkit(tools=[tool]),
                    state=AgentState(
                        permission_context=PermissionContext(
                            mode=PermissionMode.AUTO,
                        ),
                    ),
                    permission_reviewer=reviewer,
                )
                call = ToolCallBlock(id="call", name=tool.name, input="{}")
                results = []

                async def execute() -> None:
                    if realtime:
                        message = AssistantMsg(name=agent.name, content=[call])
                        agent.state.context.append(message)
                        agent._reply_id = message.id
                        await agent._run_tool(message.id, call)
                    else:
                        async for event in agent._execute_tool_call(call):
                            if isinstance(event, ToolResultEndEvent):
                                results.append(event.state)
                            elif isinstance(event, RequireUserConfirmEvent):
                                results.append("asking")

                ask = AsyncMock(return_value=False)
                async with AsyncExitStack() as stack:
                    if realtime:
                        await stack.enter_async_context(agent)
                        stack.enter_context(
                            patch.object(agent, "_ask_user", ask),
                        )
                    task = asyncio.create_task(execute())
                    try:
                        await asyncio.wait_for(started.wait(), timeout=2)
                        if restriction.startswith("safety_ask"):
                            cached.behavior = PermissionBehavior.ASK
                            cached.bypass_immune = True
                            cached.message = (
                                "Confirm the sensitive destination"
                            )
                        agent.state.permission_context = PermissionContext(
                            mode=PermissionMode.ASK
                            if restriction.startswith("ask")
                            else PermissionMode.BYPASS
                            if restriction == "bypass"
                            else PermissionMode.AUTO,
                            interactive=not restriction.endswith("unattended"),
                            read_only=restriction == "read_only",
                            deny_rules={tool.name: [_deny(tool.name)]}
                            if restriction == "deny_rule"
                            else {},
                            ask_rules={
                                tool.name: [
                                    PermissionRule(
                                        tool_name=tool.name,
                                        rule_content=None,
                                        behavior=PermissionBehavior.ASK,
                                        source="session",
                                    ),
                                ],
                            }
                            if restriction.startswith("new_ask_rule")
                            else {},
                        )
                    finally:
                        release.set()
                    await asyncio.wait_for(task, timeout=2)
                self.assertEqual(
                    (executed, reviewer.await_count),
                    ([True] if restriction == "bypass" else [], 1),
                )
                if realtime:
                    self.assertEqual(
                        ask.await_count,
                        int(
                            restriction
                            in ("ask", "safety_ask", "new_ask_rule"),
                        ),
                    )
                else:
                    self.assertEqual(
                        results,
                        ["asking"]
                        if restriction in ("ask", "safety_ask", "new_ask_rule")
                        else [ToolResultState.SUCCESS]
                        if restriction == "bypass"
                        else [ToolResultState.DENIED],
                    )


class ScheduledDenyInheritanceTest(_SchedulerFireTestBase):
    """Inherited rules survive storage and fresh or reused schedule runs."""

    async def test_schedule_permission_increase_requires_review(self) -> None:
        """An ordinary schedule grant cannot silently authorize escalation."""
        tool = ScheduleCreate(
            "u",
            "a",
            _make_record().data.chat_model_config,
            self.storage,
            _Scheduler(),
        )
        context = PermissionContext(mode=PermissionMode.AUTO)
        reviewer = AsyncMock(return_value=_review(policy_violation=True))
        engine = PermissionEngine(context, reviewer=reviewer)
        for arguments in (
            {"permission_mode": "bypass"},
            {"allow_workspace_edits": True},
        ):
            decision = await engine.check_permission(tool, arguments)
            self.assertEqual(decision.behavior, PermissionBehavior.DENY)
        ordinary = await engine.check_permission(tool, {})
        context.mode = PermissionMode.ASK
        upgrade = await engine.check_permission(tool, {})
        self.assertEqual(
            (ordinary.behavior, upgrade.behavior, reviewer.await_count),
            (PermissionBehavior.ALLOW, PermissionBehavior.ASK, 2),
        )

    async def test_create_persist_and_fire_preserves_parent_denials(
        self,
    ) -> None:
        """All modes retain hard denials without inheriting parent grants."""
        for mode, stateful in product(PermissionMode, (False, True)):
            with self.subTest(mode=mode, stateful=stateful):
                expected = {
                    "Change": [_deny("Change")],
                    "Bash": [
                        PermissionRule(
                            tool_name="Bash",
                            rule_content="git push:*",
                            behavior=PermissionBehavior.DENY,
                            source="projectSettings",
                        ),
                    ],
                }
                parent = AgentState(
                    permission_context=PermissionContext(
                        deny_rules=expected,
                    ),
                )
                create = ScheduleCreate(
                    user_id="u",
                    agent_id="a",
                    chat_model_config=_make_record().data.chat_model_config,
                    storage=self.storage,
                    scheduler_manager=_Scheduler(),
                )
                self.assertNotIn(
                    "deny_rules",
                    create.input_schema["properties"],
                )
                result = await create(
                    name="bounded job",
                    cron_expression="0 0 * * *",
                    permission_mode=mode.value,
                    stateful=stateful,
                    _agent_state=parent,
                )
                self.assertEqual(result.state, ToolResultState.SUCCESS)
                records = await self.storage.list_schedules("u")
                # Find this iteration by the unique source session identifier.
                record = next(
                    r
                    for r in records
                    if r.data.source_session_id == parent.session_id
                )
                record = ScheduleRecord.model_validate_json(
                    record.model_dump_json(),
                )
                self.assertEqual(record.data.deny_rules, expected)
                parent.permission_context.deny_rules.clear()
                state = AgentState()
                record.apply_to_session(state, new_session=True)
                rule = state.permission_context.deny_rules["Change"][0]
                rule.source = "modified runtime"
                self.assertEqual(record.data.deny_rules, expected)
                next_state = AgentState()
                record.apply_to_session(next_state, new_session=True)
                self.assertEqual(
                    next_state.permission_context.deny_rules,
                    expected,
                )
                record = await update_schedule(
                    record.id,
                    UpdateScheduleRequest(description="Updated bounded job"),
                    user_id="u",
                    storage=self.storage,
                    scheduler=_Scheduler(),
                    message_bus=self.bus,
                )
                self.assertEqual(record.data.deny_rules, expected)

                trigger = self.manager._build_trigger(record)
                for _ in range(2):
                    await trigger()
                    sessions = await self.storage.list_sessions("u", "a")
                    sessions = [
                        session
                        for session in sessions
                        if getattr(session.origin, "schedule_id", None)
                        == record.id
                    ]
                    for session in sessions:
                        policy = session.state.permission_context
                        self.assertEqual(policy.deny_rules, expected)
                        reviewer = AsyncMock(return_value=_review())
                        decision = await PermissionEngine(
                            policy,
                            reviewer=reviewer,
                        ).check_permission(_tool(), {})
                        self.assertEqual(
                            (decision.behavior, reviewer.await_count),
                            (PermissionBehavior.DENY, 0),
                        )
                        decision = await PermissionEngine(
                            policy,
                            reviewer=reviewer,
                        ).check_permission(
                            Bash(),
                            {"command": "echo ready && git push origin main"},
                        )
                        self.assertEqual(
                            (decision.behavior, reviewer.await_count),
                            (PermissionBehavior.DENY, 0),
                        )
                self.assertEqual(len(sessions), 1 if stateful else 2)

    async def test_legacy_schedule_has_no_invented_denials(self) -> None:
        """Old records load without claiming to recover lost restrictions."""
        data = _make_record().model_dump(mode="json")
        data["data"].pop("deny_rules", None)
        record = ScheduleRecord.model_validate(data)
        state = AgentState()
        record.apply_to_session(state, new_session=True)
        self.assertEqual(state.permission_context.deny_rules, {})
