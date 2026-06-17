# -*- coding: utf-8 -*-
# pylint: disable=protected-access,redefined-builtin,unused-argument
# pylint: disable=unreachable
"""Test the general-purpose, context-var based status event mechanism."""
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.model import StructuredResponse, ChatResponse
from agentscope.agent import Agent, ContextConfig
from agentscope.agent._agent import _STATUS_EVENT_QUEUE
from agentscope.state import AgentState
from agentscope.message import UserMsg, AssistantMsg, TextBlock, ToolCallBlock
from agentscope.tool import Toolkit, ToolBase, ToolChunk
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.event import StatusEvent


def _final_text_response(text: str = "done") -> ChatResponse:
    """A non-streaming response with only final text (no tool call)."""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


class StatusEmittingTool(ToolBase):
    """A tool that emits status events while it runs."""

    name: str = "status_tool"
    description: str = "A tool that emits status events"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    def __init__(self) -> None:
        """Initialize, the owning agent is injected after construction."""
        self.agent: Agent | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="allow",
            message="allow",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Emit start/end status events and return a result."""
        await self.agent.emit_status(name="tool_busy", status="start")
        await self.agent.emit_status(name="tool_busy", status="end")
        return ToolChunk(content=[TextBlock(text="tool done")])


class StatusEventTest(IsolatedAsyncioTestCase):
    """Test the general-purpose status event mechanism."""

    async def test_emit_status_is_noop_outside_reply(self) -> None:
        """``emit_status`` is a no-op when no reply stream is active."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )
        # Should not raise even though nothing is consuming the events.
        await agent.emit_status(name="custom_op", status="start")
        self.assertIsNone(_STATUS_EVENT_QUEUE.get())

    async def test_compression_emits_status_events_in_reply(self) -> None:
        """Context compaction inside the reply loop interleaves start/end
        status events into the reply stream."""
        model = MockModel(context_size=100)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )
        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        )
        # The reasoning step after compaction returns a final text.
        model.set_responses([_final_text_response("done")])

        status_events = []
        async for event in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            if isinstance(event, StatusEvent):
                status_events.append(event)

        names = [(e.name, e.status) for e in status_events]
        self.assertIn(("compressing_context", "start"), names)
        self.assertIn(("compressing_context", "end"), names)
        # The context var must be cleared after the stream completes.
        self.assertIsNone(_STATUS_EVENT_QUEUE.get())

    async def test_tool_can_emit_status_events_in_reply(self) -> None:
        """A tool emitting status events during the reply surfaces them in
        the stream — proving the mechanism is not tied to compaction."""
        model = MockModel()
        tool = StatusEmittingTool()
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
        )
        tool.agent = agent

        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            type="tool_call",
                            id="c0",
                            name="status_tool",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                ),
                _final_text_response("done"),
            ],
        )

        status_events = []
        async for event in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            if isinstance(event, StatusEvent):
                status_events.append(event)

        names = [(e.name, e.status) for e in status_events]
        self.assertIn(("tool_busy", "start"), names)
        self.assertIn(("tool_busy", "end"), names)

    async def test_early_close_cancels_pipeline_and_resets(self) -> None:
        """Closing the stream early stops the background pipeline and resets
        the status context var (no hang / leak)."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )

        async def _fake_reply(inputs: Any = None) -> Any:
            yield StatusEvent(reply_id="r", name="op", status="start")
            await asyncio.sleep(10)  # Simulate a long-running pipeline.
            yield StatusEvent(reply_id="r", name="op", status="end")

        agent._reply = _fake_reply  # type: ignore[assignment]

        gen = agent.reply_stream()
        first = await gen.__anext__()
        self.assertIsInstance(first, StatusEvent)

        await gen.aclose()
        self.assertIsNone(_STATUS_EVENT_QUEUE.get())

    async def test_pipeline_propagates_exception(self) -> None:
        """Exceptions raised inside the reply pipeline propagate to the
        consumer and still reset the status context var."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )

        async def _boom_reply(inputs: Any = None) -> Any:
            raise ValueError("boom")
            yield  # pragma: no cover - makes this an async generator

        agent._reply = _boom_reply  # type: ignore[assignment]

        with self.assertRaises(ValueError):
            async for _ in agent.reply_stream():
                pass

        self.assertIsNone(_STATUS_EVENT_QUEUE.get())
