# -*- coding: utf-8 -*-
"""Authorization changes invalidate pending automatic tool approvals."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from contextlib import AsyncExitStack
from itertools import product
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from permission_review_test import _review, _tool
from realtime_agent_test import ScriptedModel
from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.event import RequireUserConfirmEvent, ToolResultEndEvent
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    SystemMsg,
    ToolCallBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import SteeringMiddleware, SteeringQueue
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
)
from agentscope.permission._evidence import ScheduleAuthorization
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


class LiveAuthorizationTest(IsolatedAsyncioTestCase):
    """Check real execution boundaries with deterministic delayed reviewers."""

    async def test_live_changes_stop_chat_and_realtime_tools(self) -> None:
        """Messages, archives, scope, and instructions can revoke approval."""
        for realtime, interactive, change in product(
            (False, True),
            (False, True),
            (
                "message",
                "steering",
                "system_steering",
                "archive",
                "incomplete",
                "scope",
                "instructions",
                "replacement",
                "unchanged",
            ),
        ):
            with self.subTest(
                realtime=realtime,
                interactive=interactive,
                change=change,
            ):
                started, release = asyncio.Event(), asyncio.Event()
                executed = []
                instructions = ["Help with backups"]

                def delete() -> str:
                    executed.append(True)
                    return "deleted"

                async def review(_request: dict) -> object:
                    started.set()
                    await release.wait()
                    return _review()

                tool = FunctionTool(delete, is_read_only=False)
                reviewer = AsyncMock(side_effect=review)
                agent = (RealtimeAgent if realtime else Agent)(
                    "agent",
                    "Help",
                    ScriptedModel([[]]) if realtime else MockModel(),
                    toolkit=Toolkit(tools=[tool]),
                    permission_reviewer=reviewer,
                    state=AgentState(
                        context=[UserMsg("user", "Delete the backup.")],
                        permission_context=PermissionContext(
                            mode=PermissionMode.AUTO,
                            interactive=interactive,
                        ),
                    ),
                )
                results = []
                call = ToolCallBlock(id="call", name=tool.name, input="{}")

                async def execute() -> None:
                    if realtime:
                        msg = AssistantMsg(name="agent", content=[call])
                        agent.state.context.append(msg)
                        agent._reply_id = msg.id
                        await agent._run_tool(msg.id, call)
                    else:
                        async for event in agent._execute_tool_call(call):
                            if isinstance(event, RequireUserConfirmEvent):
                                results.append("asking")
                            elif isinstance(event, ToolResultEndEvent):
                                results.append(event.state)

                ask = AsyncMock(return_value=False)
                async with AsyncExitStack() as stack:
                    if realtime:
                        await stack.enter_async_context(agent)
                        stack.enter_context(
                            patch.object(agent, "_ask_user", ask),
                        )
                    stack.enter_context(
                        patch.object(
                            agent,
                            "_get_permission_instructions",
                            AsyncMock(side_effect=lambda: instructions[0]),
                        ),
                    )
                    task = asyncio.create_task(execute())
                    try:
                        await asyncio.wait_for(started.wait(), 2)
                        stop = UserMsg(
                            "user",
                            "Stop. Do not delete the backup.",
                        )
                        if change == "replacement":
                            agent.state = agent.state.model_copy(deep=True)
                            agent.state.context.append(stop)
                        elif change == "message":
                            agent.state.context.append(stop)
                        elif change in ("steering", "system_steering"):
                            agent.state.context.append(
                                AssistantMsg(
                                    name="agent",
                                    content=[
                                        HintBlock(
                                            hint=stop.get_text_content(),
                                            source="steering",
                                            source_role="system"
                                            if change == "system_steering"
                                            else "user",
                                        ),
                                    ],
                                ),
                            )
                        elif change == "archive":
                            agent.state.retain_authorization([stop])
                        elif change == "incomplete":
                            agent.state.authorization_evidence_complete = False
                        elif change == "scope":
                            agent.state.schedule_authorization = (
                                ScheduleAuthorization(
                                    schedule_id="job",
                                    user_id="user",
                                    source="USER",
                                    description="Inspect only",
                                    authorized_description="Inspect only",
                                )
                            )
                        elif change == "instructions":
                            instructions[
                                0
                            ] = "Deletion requires human confirmation."
                    finally:
                        release.set()
                    await asyncio.wait_for(task, 2)
                self.assertEqual(
                    (executed, reviewer.await_count),
                    ([True] if change == "unchanged" else [], 1),
                )
                if realtime:
                    self.assertEqual(
                        ask.await_count,
                        int(interactive and change != "unchanged"),
                    )
                else:
                    self.assertEqual(
                        results,
                        [
                            ToolResultState.SUCCESS
                            if change == "unchanged"
                            else "asking"
                            if interactive
                            else ToolResultState.DENIED,
                        ],
                    )

    def test_system_steering_survives_compression(self) -> None:
        """Trusted system restrictions remain original authorization."""
        hints = SteeringMiddleware(SteeringQueue())._to_hints(
            "id",
            [SystemMsg("system", "Confirm before publishing.")],
        )
        state = AgentState(context=[AssistantMsg("agent", hints)])
        state.retain_authorization(state.context)
        state.context.clear()
        restored = AgentState.model_validate_json(state.model_dump_json())
        self.assertEqual(
            [(e.source, e.text) for e in restored.authorization_evidence],
            [("system", "Confirm before publishing.")],
        )

    async def test_pending_steering_is_checked_without_consumption(
        self,
    ) -> None:
        """Queued revocations prevent execution without being consumed."""
        for interactive in (False, True):
            queue = SteeringQueue()
            agent = Agent(
                "agent",
                "Help",
                MockModel(),
                middlewares=[SteeringMiddleware(queue)],
            )
            agent.state.permission_context = PermissionContext(
                mode=PermissionMode.AUTO,
                interactive=interactive,
            )
            await queue.start_reply(
                agent.state.session_id,
                agent.state.reply_id,
            )
            receipts = []

            async def review(_request: dict) -> object:
                receipts.append(
                    queue.submit(
                        UserMsg("user", "Stop. Do not delete the backup."),
                        session_id=agent.state.session_id,
                        reply_id=agent.state.reply_id,
                    ),
                )
                return _review()

            agent._engine.reviewer = AsyncMock(side_effect=review)
            tool = _tool()
            decision = await agent._check_permission(
                ToolCallBlock(id="call", name=tool.name, input="{}"),
                tool,
                {},
            )
            self.assertEqual(
                (decision.behavior, decision.review, receipts[0].status),
                (
                    PermissionBehavior.ASK
                    if interactive
                    else PermissionBehavior.DENY,
                    None,
                    "pending",
                ),
            )
            pending = await queue.read(
                agent.state.session_id,
                agent.state.reply_id,
            )
            self.assertEqual([item.id for item in pending], [receipts[0].id])

    async def test_sdk_empty_context_and_post_review_instruction_failure(
        self,
    ) -> None:
        """Empty contexts and failed revalidation invalidate approval."""
        messages = []

        async def revoke(_request: dict) -> object:
            messages.append(UserMsg("user", "Stop"))
            return _review()

        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=revoke,
        )
        decision = await engine.check_permission(
            _tool(),
            {},
            messages=messages,
        )
        self.assertEqual(
            (decision.behavior, decision.review),
            (PermissionBehavior.ASK, None),
        )
        for failure in (ValueError("missing policy"), asyncio.TimeoutError()):
            loader = AsyncMock(side_effect=["Original policy", failure])
            engine.reviewer = AsyncMock(return_value=_review())
            decision = await engine.check_permission(
                _tool(),
                {},
                instructions=loader,
            )
            self.assertEqual(
                (decision.behavior, decision.review),
                (PermissionBehavior.ASK, None),
            )

    async def test_revalidation_obeys_timeout_and_cancellation(self) -> None:
        """Instruction reloads and pending-input reads have bounded waits."""
        for stage, cancelled in product(
            ("instructions", "steering"),
            (False, True),
        ):
            with self.subTest(stage=stage, cancelled=cancelled):
                calls = []

                async def block(*_args: object) -> str:
                    calls.append(True)
                    if stage == "instructions" and len(calls) == 1:
                        return "Policy"
                    if cancelled:
                        raise asyncio.CancelledError()
                    await asyncio.Event().wait()
                    return "unreachable"

                source = SteeringQueue()
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    middlewares=[SteeringMiddleware(source)],
                    permission_reviewer=AsyncMock(return_value=_review()),
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                agent._engine.review_timeout = 0.02
                with patch.object(
                    agent if stage == "instructions" else source,
                    "_get_permission_instructions"
                    if stage == "instructions"
                    else "read",
                    block,
                ):
                    operation = agent._check_permission(
                        ToolCallBlock(id="call", name="Change", input="{}"),
                        _tool(),
                        {},
                    )
                    if cancelled:
                        with self.assertRaises(asyncio.CancelledError):
                            await operation
                    else:
                        decision = await operation
                        self.assertEqual(
                            (decision.behavior, decision.review),
                            (PermissionBehavior.ASK, None),
                        )
                        self.assertIn("TimeoutError", decision.decision_reason)

    async def test_revocation_during_final_tool_check_invalidates_review(
        self,
    ) -> None:
        """The final check also catches changes after the reviewer returns."""
        messages = []
        tool = _tool()
        initial = tool.check_permissions.return_value
        checks = []

        async def check(*_args: object) -> object:
            checks.append(True)
            if len(checks) == 2:
                messages.append(UserMsg("user", "Stop"))
            return initial

        tool.check_permissions = AsyncMock(side_effect=check)
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=AsyncMock(return_value=_review()),
        )
        decision = await engine.check_permission(tool, {}, messages=messages)
        self.assertEqual(
            (decision.behavior, decision.review, len(checks)),
            (PermissionBehavior.ASK, None, 2),
        )
