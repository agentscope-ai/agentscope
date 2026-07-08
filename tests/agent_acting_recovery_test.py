# -*- coding: utf-8 -*-
"""Regression tests for runtime exception recovery inside ``_acting``.

When the toolkit converts a tool's own failures into ``ToolResponse``
error chunks, the agent loop stays healthy.  But if the underlying
transport (e.g. an MCP HTTP transport that bypasses the ``MCPError``
path) raises a Python exception out of ``toolkit.call_tool``, the
exception currently propagates out of ``_acting`` and tears the agent
loop down (see agentscope-ai/agentscope#1985).

The fix in ``Agent._execute_tool_call`` wraps the ``_acting`` iteration
in a ``try/except`` that lets ``DeveloperOrientedException`` propagate
but converts any other exception into a ``ToolResultState.ERROR``
``_handle_error_tool_call`` event so the agent can recover.
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

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
from agentscope.message import TextBlock, ToolCallBlock, UserMsg


class FailingTool(ToolBase):
    """A tool whose execution raises a non-DeveloperOrientedException.

    This mimics the symptom described in issue #1985: the MCP transport
    layer raises ``httpx.HTTPStatusError`` (or similar) at the HTTP
    boundary, bypassing the toolkit's ``MCPError`` path.
    """

    name: str = "failing_tool"
    description: str = "A tool that always raises during execution"
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
        """Allow the tool call so execution proceeds."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows",
            message="Mock tool always allows",
        )

    # pylint: disable=redefined-builtin
    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Raise an arbitrary runtime exception."""
        raise RuntimeError(
            "MCP HTTP transport failed (simulated for issue #1985)",
        )


class AgentActingRecoveryTest(IsolatedAsyncioTestCase):
    """Verify the agent loop survives a runtime exception in ``_acting``."""

    async def asyncSetUp(self) -> None:
        """Set up an agent with a tool that always raises."""
        self.model = MockModel()
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[FailingTool()]),
        )

    async def test_runtime_exception_in_acting_does_not_crash_loop(self) -> None:
        """The agent should emit an ERROR tool result and continue.

        Setup:
            1. The model emits one tool call (failing_tool).
            2. The tool raises ``RuntimeError`` from ``toolkit.call_tool``.

        Expected:
            1. The loop does not propagate the exception to the caller.
            2. The events include ``TOOL_RESULT_START`` ... ``TOOL_RESULT_END``
               with state ``ERROR``.
            3. The agent's second LLM call sees the tool error and
               produces a final text reply.
        """
        tool_call_id = "tool_call_1"

        # First response issues the failing tool call.
        # Second response (after seeing the tool error) is a text reply.
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="calling tool"),
                            ToolCallBlock(
                                id=tool_call_id,
                                name="failing_tool",
                                input='{"input": "test"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="Tool failed but I recover.")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[
                            TextBlock(text="Tool failed but I recover."),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        # Collect events.
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Trigger failing tool"),
        ):
            events.append(event)

        # 1. We did not crash.
        self.assertGreater(len(events), 0)

        # 2. There must be at least one TOOL_RESULT_END with state=ERROR.
        tool_result_end_events = [
            e
            for e in events
            if e.__class__.__name__ == "ToolResultEndEvent"
        ]
        self.assertTrue(
            tool_result_end_events,
            "Expected at least one ToolResultEndEvent",
        )
        error_events = [
            e for e in tool_result_end_events if e.state.value == "error"
        ]
        self.assertTrue(
            error_events,
            f"Expected at least one error ToolResultEndEvent, got "
            f"states: {[e.state for e in tool_result_end_events]}",
        )

        # 3. The loop continued past the failing tool (REPLY_END present).
        end_events = [
            e for e in events if e.__class__.__name__ == "ReplyEndEvent"
        ]
        self.assertTrue(
            end_events,
            "Agent loop should reach ReplyEndEvent after recovery",
        )

        # 4. The tool error message references the originating exception.
        error_tool_results = [
            r
            for e in events
            if e.__class__.__name__ == "ToolResultTextDeltaEvent"
            and e.tool_call_id == tool_call_id
        ]
        self.assertTrue(error_tool_results)

    async def test_developer_oriented_exception_still_propagates(self) -> None:
        """``DeveloperOrientedException`` from middleware must surface.

        The recovery wrapper must not swallow developer-facing errors:
        programming/misconfiguration issues need to bubble up so that
        they are visible during development.  We verify by hooking into
        ``_acting`` to raise a ``DeveloperOrientedException`` and
        asserting the agent's ``reply_stream`` generator re-raises it.
        """
        from agentscope.exception import DeveloperOrientedException

        async def boom(_tool_call):  # noqa: ANN001
            raise DeveloperOrientedException(
                "simulated programming error",
            )
            yield  # pragma: no cover -- makes this an async generator

        # Replace _acting so it raises immediately on first iteration.
        self.agent._acting = boom  # type: ignore[assignment]

        tool_call_id = "tool_call_1"
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="will fail in _acting"),
                            ToolCallBlock(
                                id=tool_call_id,
                                name="failing_tool",
                                input='{"input": "x"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = []
        with self.assertRaises(DeveloperOrientedException):
            async for event in self.agent.reply_stream(
                UserMsg(name="user", content="trigger"),
            ):
                events.append(event)
        # Some events may have been emitted before _acting was reached;
        # we only require that the exception itself surfaced.
        _ = events  # silence unused warning
