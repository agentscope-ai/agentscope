# -*- coding: utf-8 -*-
"""Test that mid-stream tool execution failures leave recoverable context.

Regression test for https://github.com/modelscope/agentscope/issues/1888
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.model import ChatResponse
from agentscope.tool import (
    ToolBase,
    Toolkit,
    ToolChunk,
)
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolCallState,
    ToolResultState,
    UserMsg,
)


class FailingTool(ToolBase):
    """A mock tool that raises an exception during execution."""

    name: str = "failing_tool"
    description: str = "A tool that always raises an exception"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
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
            decision_reason="Always allows",
            message="Always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        raise RuntimeError("Simulated mid-stream tool failure")


class WorkingTool(ToolBase):
    """A mock tool that works normally."""

    name: str = "working_tool"
    description: str = "A tool that works"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
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
            decision_reason="Always allows",
            message="Always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=f"Result: {input}")],
        )


class OrphanToolCallRecoveryTest(IsolatedAsyncioTestCase):
    """Test that mid-stream failures produce recoverable context."""

    async def asyncSetUp(self) -> None:
        self.model = MockModel()

    async def test_orphan_tool_calls_recovered_on_failure(self) -> None:
        """When a tool execution fails mid-stream, the agent's context
        should contain error tool results for all orphan tool calls.

        This prevents the next turn from sending invalid history (assistant
        with tool_calls but no matching tool results) to the model provider.
        """
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=self.model,
            toolkit=Toolkit(tools=[FailingTool()]),
        )

        tool_call_id = "tc_1"
        tool_call = ToolCallBlock(
            id=tool_call_id,
            name="failing_tool",
            input='{"input": "test"}',
        )

        # Model returns a response with a tool call, then should fail
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="Calling tool..."), tool_call],
                        is_last=True,
                    ),
                ],
            ],
        )

        # The reply_stream should raise the RuntimeError from FailingTool
        with self.assertRaises(RuntimeError) as ctx:
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="Test"),
            ):
                pass

        self.assertIn("Simulated mid-stream tool failure", str(ctx.exception))

        # Verify: context should have the assistant message with the tool
        # call AND a matching error tool result (not orphaned).
        context = agent.state.context
        self.assertGreaterEqual(len(context), 1)

        # The last message should be the assistant message with tool_calls
        assistant_msg = context[-1]
        self.assertEqual(assistant_msg.role, "assistant")

        tool_calls_in_ctx = assistant_msg.get_content_blocks("tool_call")
        tool_results_in_ctx = assistant_msg.get_content_blocks(
            "tool_result",
        )

        # There should be at least one tool call
        self.assertGreaterEqual(len(tool_calls_in_ctx), 1)

        # Every tool call must have a matching tool result
        result_ids = {r.id for r in tool_results_in_ctx}
        for tc in tool_calls_in_ctx:
            self.assertIn(
                tc.id,
                result_ids,
                f"Tool call {tc.id} ({tc.name}) has no matching tool "
                f"result — orphan tool call would brick the next turn.",
            )

        # The orphan tool result should be INTERRUPTED state
        for tr in tool_results_in_ctx:
            if tr.id == tool_call_id:
                self.assertEqual(tr.state, ToolResultState.INTERRUPTED)

    async def test_no_orphan_on_normal_execution(self) -> None:
        """Normal tool execution should not trigger recovery."""
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=self.model,
            toolkit=Toolkit(tools=[WorkingTool()]),
        )

        tool_call = ToolCallBlock(
            id="tc_ok",
            name="working_tool",
            input='{"input": "hello"}',
        )

        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="Calling tool..."), tool_call],
                        is_last=True,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="Done!")],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = []
        async for event in agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event)

        # Context should have the assistant message with tool call
        # and the tool result — no orphans.
        context = agent.state.context
        assistant_msg = context[-1]
        tool_calls = assistant_msg.get_content_blocks("tool_call")
        tool_results = assistant_msg.get_content_blocks("tool_result")

        result_ids = {r.id for r in tool_results}
        for tc in tool_calls:
            self.assertIn(tc.id, result_ids)

        # Tool result should be SUCCESS, not INTERRUPTED
        for tr in tool_results:
            self.assertEqual(tr.state, ToolResultState.SUCCESS)

    async def test_recover_orphan_with_multiple_tool_calls(self) -> None:
        """When multiple tool calls are issued and one fails, the others
        that already completed should keep their results, and only the
        orphan should get an INTERRUPTED result.
        """
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=self.model,
            toolkit=Toolkit(tools=[WorkingTool(), FailingTool()]),
        )

        tool_call_ok = ToolCallBlock(
            id="tc_ok",
            name="working_tool",
            input='{"input": "hello"}',
        )
        tool_call_fail = ToolCallBlock(
            id="tc_fail",
            name="failing_tool",
            input='{"input": "boom"}',
        )

        # Two tool calls: first succeeds, second fails
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Calling tools..."),
                            tool_call_ok,
                            tool_call_fail,
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        with self.assertRaises(RuntimeError):
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="Test"),
            ):
                pass

        context = agent.state.context
        assistant_msg = context[-1]
        tool_calls = assistant_msg.get_content_blocks("tool_call")
        tool_results = assistant_msg.get_content_blocks("tool_result")

        # Both tool calls must have results
        result_ids = {r.id for r in tool_results}
        for tc in tool_calls:
            self.assertIn(tc.id, result_ids)

        # The working tool's result may be SUCCESS (it ran before the
        # failure), or INTERRUPTED if the failure happened before it
        # could complete.  Either way, no orphan.
        self.assertEqual(len(tool_results), len(tool_calls))
