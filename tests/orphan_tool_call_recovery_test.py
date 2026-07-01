# -*- coding: utf-8 -*-
"""Regression tests for orphan tool call recovery (issue #1888).

A mid-stream failure can append an assistant tool call to the agent context
before its matching tool result is recorded and persisted. On the next run,
sending that unpaired tool call back to the model provider is rejected
("an assistant message with tool_calls must be followed by tool messages"),
which bricks the session until the stored context is repaired by hand.

These tests verify the agent recovers by closing the orphan tool call with an
``interrupted`` tool result before the context ever reaches the model.
"""
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import Toolkit, ToolBase, ToolChunk


class _MockOkTool(ToolBase):
    """A simple tool that always succeeds. Used so the agent can emit a real
    tool call; its execution is short-circuited by the raising middleware."""

    name: str = "mock_ok_tool"
    description: str = "A mock tool that returns a result."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string."},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow the tool call."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows.",
            message="Mock tool always allows.",
        )

    # pylint: disable=redefined-builtin
    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Return a plain result."""
        return ToolChunk(content=[TextBlock(text=f"Result: {input}")])


class _RaisingActingMiddleware(MiddlewareBase):
    """An ``on_acting`` middleware that raises to simulate a mid-stream
    failure during tool execution.

    The hook wraps only ``toolkit.call_tool`` and runs outside its error
    handling, so raising here leaves the already-appended tool call behind as
    an orphan without a result, exactly like a run interrupted mid-stream.
    """

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        raise RuntimeError("Simulated mid-stream failure during acting.")
        yield  # pylint: disable=unreachable


def _collect_blocks(context: list, block_type: str) -> list:
    """Flatten content blocks of the given type across the whole context."""
    return [
        block
        for msg in context
        for block in msg.get_content_blocks(block_type)
    ]


class OrphanToolCallRecoveryTest(IsolatedAsyncioTestCase):
    """The agent must not brick when a prior run left an orphan tool call."""

    async def asyncSetUp(self) -> None:
        """Shared setup."""
        self.model = MockModel()
        self.tool_call_id = "orphan_tool_call"

    async def test_orphan_recovered_after_in_run_acting_failure(self) -> None:
        """A mid-stream failure that strikes after a tool call is appended but
        before its result is persisted must not brick the session: the next run
        recovers by closing the orphan tool call with an interrupted result.

        The failure is simulated with an ``on_acting`` middleware that raises,
        which (unlike an exception inside a tool) is not converted into a tool
        result and therefore leaves a real orphan in the context.

        Regression test for
        https://github.com/agentscope-ai/agentscope/issues/1888
        """
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[_MockOkTool()]),
            middlewares=[_RaisingActingMiddleware()],
        )

        # Turn 1: the model emits a tool call (appended to the context), then
        # the acting middleware raises before any result is written.
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=self.tool_call_id,
                            name="mock_ok_tool",
                            input='{"input": "test"}',
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
                # Turn 2: a plain text reply, consumed after recovery.
                ChatResponse(
                    content=[TextBlock(text="Recovered")],
                    is_last=True,
                    usage=None,
                ),
            ],
        )

        with self.assertRaises(RuntimeError):
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="Run the tool"),
            ):
                pass

        # The context now holds the orphan tool call with no result, which is
        # exactly the state that bricks the session on the next run.
        call_ids = [
            b.id for b in _collect_blocks(agent.state.context, "tool_call")
        ]
        result_ids = [
            b.id for b in _collect_blocks(agent.state.context, "tool_result")
        ]
        self.assertIn(self.tool_call_id, call_ids)
        self.assertNotIn(self.tool_call_id, result_ids)

        # Turn 2: a new user message. Without recovery the orphan tool call
        # would be sent back to the model provider and brick the session.
        await agent.reply(UserMsg(name="user", content="Continue"))

        # The orphan tool call must now be paired with an interrupted result.
        recovered = [
            b
            for b in _collect_blocks(agent.state.context, "tool_result")
            if b.id == self.tool_call_id
        ]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, ToolResultState.INTERRUPTED)

        # No tool call anywhere in the context may lack a matching result.
        all_call_ids = {
            b.id for b in _collect_blocks(agent.state.context, "tool_call")
        }
        all_result_ids = {
            b.id for b in _collect_blocks(agent.state.context, "tool_result")
        }
        self.assertTrue(all_call_ids.issubset(all_result_ids))

    async def test_recovery_synthesizes_result_in_original_message(
        self,
    ) -> None:
        """The interrupted result is attached to the message that holds the
        orphan tool call, mirroring the structure produced when a tool runs
        successfully, and the orphan tool call is marked finished.
        """
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[_MockOkTool()]),
        )

        # Inject the post-failure state directly: an assistant message with a
        # tool call but no result, followed by a fresh user message.
        orphan_call = ToolCallBlock(
            id=self.tool_call_id,
            name="mock_ok_tool",
            input='{"input": "test"}',
        )
        agent.state.context = [
            AssistantMsg(name="Friday", content=[orphan_call]),
            UserMsg(name="user", content="Continue"),
        ]

        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Recovered")],
                    is_last=True,
                    usage=None,
                ),
            ],
        )

        await agent.reply(UserMsg(name="user", content="Continue"))

        # The original assistant message now carries the interrupted result
        # right after the orphan tool call.
        assistant_msg = agent.state.context[0]
        self.assertEqual(assistant_msg.role, "assistant")
        types_in_order = [b.type for b in assistant_msg.get_content_blocks()]
        self.assertEqual(types_in_order, ["tool_call", "tool_result"])

        call_block = assistant_msg.get_content_blocks("tool_call")[0]
        self.assertEqual(call_block.state, ToolCallState.FINISHED)

        result_block = assistant_msg.get_content_blocks("tool_result")[0]
        self.assertEqual(result_block.id, self.tool_call_id)
        self.assertEqual(result_block.name, "mock_ok_tool")
        self.assertEqual(result_block.state, ToolResultState.INTERRUPTED)
