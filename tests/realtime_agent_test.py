# -*- coding: utf-8 -*-
"""Tests for RealtimeAgent tool calling and permission checking."""
# pylint: disable=protected-access
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.event import (
    ConfirmResult,
    EventType,
    ToolCallStartEvent,
    ToolCallEndEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
)
from agentscope.realtime import ModelEvents, RealtimeAgent
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.state import AgentState

# ------------------------------------------------------------------ #
# Mock helpers
# ------------------------------------------------------------------ #


class EchoTool(ToolBase):
    """A trivial tool that echoes its input."""

    name: str = "Echo"
    description: str = "Echoes input back"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo"},
        },
        "required": ["text"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="always allow",
            message="ok",
        )

    async def __call__(self, text: str, **kwargs: Any) -> ToolChunk:
        return ToolChunk(content=[TextBlock(text=f"echo: {text}")])


class AskEchoTool(ToolBase):
    """A tool that returns PASSTHROUGH on check_permissions, so DEFAULT mode
    will ASK the user for confirmation."""

    name: str = "AskEcho"
    description: str = "Echoes input, but needs permission"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo"},
        },
        "required": ["text"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            decision_reason="passthrough",
            message="ok",
        )

    async def __call__(self, text: str, **kwargs: Any) -> ToolChunk:
        return ToolChunk(content=[TextBlock(text=f"echo: {text}")])


class FailingTool(ToolBase):
    """A tool that always raises an exception."""

    name: str = "FailTool"
    description: str = "Always fails"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "string"},
        },
        "required": ["x"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="always allow",
            message="ok",
        )

    async def __call__(self, x: str, **kwargs: Any) -> ToolChunk:
        raise RuntimeError("boom")


def _make_realtime_agent(
    toolkit: "Toolkit | None" = None,
    permission_mode: PermissionMode = PermissionMode.BYPASS,
    deny_rules: list[PermissionRule] | None = None,
) -> "RealtimeAgent":
    """Create a RealtimeAgent with a mocked model for unit testing."""

    model = MagicMock()
    model.support_tools = True
    model.send = AsyncMock()
    model.send_raw = AsyncMock()
    model.model_name = "mock-realtime"

    state = AgentState(
        session_id="test-session",
        permission_context=PermissionContext(mode=permission_mode),
    )

    agent = RealtimeAgent(
        name="TestAgent",
        model=model,
        instructions="You are a test agent.",
        toolkit=toolkit,
        state=state,
    )

    if deny_rules:
        for rule in deny_rules:
            agent._engine.add_rule(rule)

    return agent


# ================================================================== #
# 1. RealtimeAgent permission flow tests
# ================================================================== #


class RealtimeAgentPermissionAllowTest(IsolatedAsyncioTestCase):
    """When permission is ALLOW (BYPASS mode), tool should execute directly."""

    async def test_allow_executes_tool(self) -> None:
        """Execute tool with BYPASS mode; expect SUCCESS event."""
        toolkit = Toolkit(tools=[EchoTool()])
        agent = _make_realtime_agent(
            toolkit=toolkit,
            permission_mode=PermissionMode.BYPASS,
        )
        agent.state.reply_id = "reply_1"

        await agent._execute_single_tool_call(
            tool_call_id="call_1",
            tool_name="Echo",
            arguments='{"text": "hello"}',
        )

        events: list = []
        while not agent._internal_event_queue.empty():
            events.append(await agent._internal_event_queue.get())

        types = [e.type for e in events]
        self.assertIn(EventType.TOOL_RESULT_START, types)
        self.assertIn(EventType.TOOL_RESULT_END, types)

        end_evt = next(
            e for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_evt.state, ToolResultState.SUCCESS)

        agent.model.send.assert_called_once()
        sent_block = agent.model.send.call_args[0][0]
        assert isinstance(sent_block, ToolResultBlock)
        self.assertIn("echo: hello", sent_block.output)


class RealtimeAgentPermissionDenyTest(IsolatedAsyncioTestCase):
    """When an explicit DENY rule matches, tool should NOT execute."""

    async def test_deny_emits_error(self) -> None:
        """Explicit DENY rule blocks execution; expect DENIED event."""
        toolkit = Toolkit(tools=[EchoTool()])
        agent = _make_realtime_agent(
            toolkit=toolkit,
            permission_mode=PermissionMode.DEFAULT,
            deny_rules=[
                PermissionRule(
                    tool_name="Echo",
                    rule_content=None,
                    behavior=PermissionBehavior.DENY,
                    source="test",
                ),
            ],
        )
        agent.state.reply_id = "reply_1"

        await agent._execute_single_tool_call(
            tool_call_id="call_1",
            tool_name="Echo",
            arguments='{"text": "hello"}',
        )

        events: list = []
        while not agent._internal_event_queue.empty():
            events.append(await agent._internal_event_queue.get())

        types = [e.type for e in events]
        self.assertIn(EventType.TOOL_RESULT_START, types)
        self.assertIn(EventType.TOOL_RESULT_END, types)

        end_evt = next(
            e for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_evt.state, ToolResultState.DENIED)


class RealtimeAgentPermissionAskConfirmedTest(IsolatedAsyncioTestCase):
    """When permission is ASK and user confirms, tool should execute."""

    async def test_ask_confirmed_executes_tool(self) -> None:
        """ASK mode with user confirmation; expect SUCCESS event."""
        toolkit = Toolkit(tools=[AskEchoTool()])
        agent = _make_realtime_agent(
            toolkit=toolkit,
            permission_mode=PermissionMode.DEFAULT,
        )
        agent.state.reply_id = "reply_1"

        async def _confirm_after_delay() -> None:
            """Simulate user confirming the tool call."""
            await asyncio.sleep(0.05)
            confirm_event = UserConfirmResultEvent(
                reply_id="reply_1",
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=ToolCallBlock(
                            id="call_1",
                            name="AskEcho",
                            input='{"text": "hello"}',
                        ),
                    ),
                ],
            )
            agent.handle_user_confirm(confirm_event)

        asyncio.create_task(_confirm_after_delay())
        await agent._execute_single_tool_call(
            tool_call_id="call_1",
            tool_name="AskEcho",
            arguments='{"text": "hello"}',
        )

        events: list = []
        while not agent._internal_event_queue.empty():
            events.append(await agent._internal_event_queue.get())

        types = [e.type for e in events]
        self.assertIn(EventType.REQUIRE_USER_CONFIRM, types)
        self.assertIn(EventType.TOOL_RESULT_START, types)
        self.assertIn(EventType.TOOL_RESULT_END, types)

        end_evt = next(
            e for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_evt.state, ToolResultState.SUCCESS)


class RealtimeAgentPermissionAskDeniedTest(IsolatedAsyncioTestCase):
    """When permission is ASK and user denies, tool should NOT execute."""

    async def test_ask_denied_emits_denied(self) -> None:
        """ASK mode with user denial; expect DENIED event."""
        toolkit = Toolkit(tools=[AskEchoTool()])
        agent = _make_realtime_agent(
            toolkit=toolkit,
            permission_mode=PermissionMode.DEFAULT,
        )
        agent.state.reply_id = "reply_1"

        async def _deny_after_delay() -> None:
            await asyncio.sleep(0.05)
            confirm_event = UserConfirmResultEvent(
                reply_id="reply_1",
                confirm_results=[
                    ConfirmResult(
                        confirmed=False,
                        tool_call=ToolCallBlock(
                            id="call_1",
                            name="AskEcho",
                            input='{"text": "hello"}',
                        ),
                    ),
                ],
            )
            agent.handle_user_confirm(confirm_event)

        asyncio.create_task(_deny_after_delay())
        await agent._execute_single_tool_call(
            tool_call_id="call_1",
            tool_name="AskEcho",
            arguments='{"text": "hello"}',
        )

        events: list = []
        while not agent._internal_event_queue.empty():
            events.append(await agent._internal_event_queue.get())

        types = [e.type for e in events]
        self.assertIn(EventType.REQUIRE_USER_CONFIRM, types)
        self.assertIn(EventType.TOOL_RESULT_END, types)

        end_evt = next(
            e for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_evt.state, ToolResultState.DENIED)


# ================================================================== #
# 2. Error handling: no duplicate ToolResultStartEvent
# ================================================================== #


class RealtimeAgentToolExecutionFailureTest(IsolatedAsyncioTestCase):
    """When tool execution raises, only ONE ToolResultStartEvent is emitted."""

    async def test_no_duplicate_start_on_exception(self) -> None:
        """Tool exception emits exactly one start and one end event."""
        toolkit = Toolkit(tools=[FailingTool()])
        agent = _make_realtime_agent(
            toolkit=toolkit,
            permission_mode=PermissionMode.BYPASS,
        )
        agent.state.reply_id = "reply_1"

        await agent._execute_single_tool_call(
            tool_call_id="call_1",
            tool_name="FailTool",
            arguments='{"x": "test"}',
        )

        events: list = []
        while not agent._internal_event_queue.empty():
            events.append(await agent._internal_event_queue.get())

        start_count = sum(
            1 for e in events if e.type == EventType.TOOL_RESULT_START
        )
        self.assertEqual(start_count, 1, "Should emit exactly one start event")

        end_count = sum(
            1 for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_count, 1, "Should emit exactly one end event")

        end_evt = next(
            e for e in events if e.type == EventType.TOOL_RESULT_END
        )
        self.assertEqual(end_evt.state, ToolResultState.ERROR)


# ================================================================== #
# 3. handle_user_confirm resolves the correct future
# ================================================================== #


class HandleUserConfirmTest(IsolatedAsyncioTestCase):
    """Test that handle_user_confirm correctly resolves pending futures."""

    async def test_resolves_matching_future(self) -> None:
        """Matching call_id resolves the pending future correctly."""
        agent = _make_realtime_agent()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmResult] = loop.create_future()
        agent._pending_confirmations["call_1"] = future

        confirm_event = UserConfirmResultEvent(
            reply_id="reply_1",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="call_1",
                        name="Echo",
                        input='{"text": "hi"}',
                    ),
                ),
            ],
        )
        agent.handle_user_confirm(confirm_event)

        result = await asyncio.wait_for(future, timeout=1)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.tool_call.name, "Echo")

    async def test_ignores_unmatched_call_id(self) -> None:
        """Non-matching call_id leaves the pending future unresolved."""
        agent = _make_realtime_agent()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmResult] = loop.create_future()
        agent._pending_confirmations["call_1"] = future

        confirm_event = UserConfirmResultEvent(
            reply_id="reply_1",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="call_999",
                        name="Echo",
                        input="{}",
                    ),
                ),
            ],
        )
        agent.handle_user_confirm(confirm_event)

        self.assertFalse(future.done())


# ================================================================== #
# 4. _translate: tool call delta accumulation
# ================================================================== #


class TranslateToolCallDeltaTest(IsolatedAsyncioTestCase):
    """Test that _translate correctly accumulates tool call deltas."""

    async def test_accumulates_fragments(self) -> None:
        """Delta events accumulate; done event emits ToolCallEndEvent."""
        agent = _make_realtime_agent()

        delta1 = ModelEvents.ModelResponseToolCallDeltaEvent(
            response_id="resp_1",
            item_id="item_1",
            tool_call=ToolCallBlock(id="call_1", name="Echo", input='{"te'),
        )
        delta2 = ModelEvents.ModelResponseToolCallDeltaEvent(
            response_id="resp_1",
            item_id="item_1",
            tool_call=ToolCallBlock(
                id="call_1",
                name="Echo",
                input='xt": "hi"}',
            ),
        )
        done = ModelEvents.ModelResponseToolCallDoneEvent(
            response_id="resp_1",
            item_id="item_1",
            tool_call=ToolCallBlock(
                id="call_1",
                name="Echo",
                input='{"text": "hi"}',
            ),
        )

        events1 = agent._translate(delta1)
        events2 = agent._translate(delta2)

        # After two deltas, accumulated arguments should be the concatenation
        self.assertEqual(
            agent._pending_tool_calls["call_1"]["arguments"],
            '{"text": "hi"}',
        )

        events3 = agent._translate(done)

        # Done event overwrites arguments with the authoritative value
        self.assertEqual(
            agent._pending_tool_calls["call_1"]["arguments"],
            '{"text": "hi"}',
        )

        all_events = events1 + events2 + events3
        start_events = [
            e for e in all_events if isinstance(e, ToolCallStartEvent)
        ]
        end_events = [e for e in all_events if isinstance(e, ToolCallEndEvent)]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(len(end_events), 1)
