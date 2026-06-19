# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test the iteration extension events in the agent class."""
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
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.agent import ReActConfig
from agentscope.event import (
    EventType,
    IterationExtensionResultEvent,
    RequireIterationExtensionEvent,
)


class MockAllowTool(ToolBase):
    """A mock tool that is always allowed (sequential execution)."""

    name: str = "mock_allow_tool"
    description: str = "A mock tool that always allows execution"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
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
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows",
            message="Mock tool always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Tool result: {input}")],
        )


def _tool_call_response(idx: int) -> ChatResponse:
    """Build a non-streaming response that emits a single tool call."""
    return ChatResponse(
        content=[
            ToolCallBlock(
                type="tool_call",
                id=f"call_{idx}",
                name="mock_allow_tool",
                input=f'{{"input": "step_{idx}"}}',
            ),
        ],
        is_last=True,
    )


def _final_text_response(text: str = "done") -> ChatResponse:
    """Build a non-streaming response with only final text (no tool call)."""
    return ChatResponse(
        content=[TextBlock(text=text)],
        is_last=True,
    )


class AgentIterationExtensionTest(IsolatedAsyncioTestCase):
    """Test the iteration extension events in the agent class."""

    def _build_agent(
        self,
        allow_extension: bool,
        max_iters: int,
    ) -> tuple[Agent, MockModel]:
        """Build an agent with the always-allow tool registered."""
        model = MockModel()
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[MockAllowTool()]),
            react_config=ReActConfig(
                max_iters=max_iters,
                allow_iteration_extension=allow_extension,
            ),
        )
        return agent, model

    async def test_disabled_falls_back_to_exceed_max_iters(self) -> None:
        """When extension is disabled, reaching the limit emits
        ``ExceedMaxItersEvent`` and terminates (backward-compatible)."""
        agent, model = self._build_agent(allow_extension=False, max_iters=2)
        # Two reasoning steps, both emit a tool call (never finishes).
        model.set_responses(
            [_tool_call_response(0), _tool_call_response(1)],
        )

        events = []
        async for event in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            events.append(event)

        types = [e.type for e in events]
        self.assertIn(EventType.EXCEED_MAX_ITERS, types)
        self.assertNotIn(EventType.REQUIRE_ITERATION_EXTENSION, types)
        self.assertFalse(agent.state.awaiting_iteration_extension)

    async def test_enabled_requests_extension_then_approve(self) -> None:
        """When enabled, reaching the limit pauses with a
        ``RequireIterationExtensionEvent``; approving it resumes the loop."""
        agent, model = self._build_agent(allow_extension=True, max_iters=2)
        # 2 tool-call steps to hit the limit, then (after extension) 1 more
        # tool-call step and a final text step.
        model.set_responses(
            [
                _tool_call_response(0),
                _tool_call_response(1),
                _tool_call_response(2),
                _final_text_response("all done"),
            ],
        )

        # Phase 1: run until the loop limit is reached.
        events = []
        async for event in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            events.append(event)

        require_events = [
            e for e in events if isinstance(e, RequireIterationExtensionEvent)
        ]
        self.assertEqual(len(require_events), 1)
        self.assertEqual(require_events[0].current_max_iters, 2)
        self.assertEqual(require_events[0].name, "Friday")
        self.assertTrue(agent.state.awaiting_iteration_extension)
        reply_id = require_events[0].reply_id

        # Phase 2: approve the extension and resume.
        resume_events = []
        async for event in agent.reply_stream(
            inputs=IterationExtensionResultEvent(
                reply_id=reply_id,
                approved=True,
                extra_iterations=2,
            ),
        ):
            resume_events.append(event)

        # The loop should have continued and finished normally.
        self.assertFalse(agent.state.awaiting_iteration_extension)
        self.assertEqual(agent.state.iteration_extension, 2)
        resume_types = [e.type for e in resume_events]
        self.assertIn(EventType.REPLY_END, resume_types)
        self.assertNotIn(EventType.EXCEED_MAX_ITERS, resume_types)
        self.assertNotIn(
            EventType.REQUIRE_ITERATION_EXTENSION,
            resume_types,
        )

    async def test_approve_with_zero_iterations_grants_full_budget(
        self,
    ) -> None:
        """Approving with ``extra_iterations <= 0`` grants another full
        ``max_iters`` budget instead of looping forever."""
        agent, model = self._build_agent(allow_extension=True, max_iters=2)
        model.set_responses(
            [
                _tool_call_response(0),
                _tool_call_response(1),
                _tool_call_response(2),
                _final_text_response("all done"),
            ],
        )

        async for _ in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            pass
        self.assertTrue(agent.state.awaiting_iteration_extension)
        reply_id = agent.state.reply_id

        resume_events = []
        async for event in agent.reply_stream(
            inputs=IterationExtensionResultEvent(
                reply_id=reply_id,
                approved=True,
                extra_iterations=0,
            ),
        ):
            resume_events.append(event)

        # A full ``max_iters`` budget (2) should have been granted.
        self.assertEqual(agent.state.iteration_extension, 2)
        resume_types = [e.type for e in resume_events]
        self.assertIn(EventType.REPLY_END, resume_types)
        self.assertNotIn(
            EventType.REQUIRE_ITERATION_EXTENSION,
            resume_types,
        )

    async def test_enabled_request_extension_then_deny(self) -> None:
        """Denying the extension terminates with ``ExceedMaxItersEvent``."""
        agent, model = self._build_agent(allow_extension=True, max_iters=1)
        model.set_responses([_tool_call_response(0)])

        events = []
        async for event in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            events.append(event)

        require_events = [
            e for e in events if isinstance(e, RequireIterationExtensionEvent)
        ]
        self.assertEqual(len(require_events), 1)
        reply_id = require_events[0].reply_id

        # Deny the extension.
        resume_events = []
        async for event in agent.reply_stream(
            inputs=IterationExtensionResultEvent(
                reply_id=reply_id,
                approved=False,
            ),
        ):
            resume_events.append(event)

        self.assertFalse(agent.state.awaiting_iteration_extension)
        resume_types = [e.type for e in resume_events]
        self.assertIn(EventType.EXCEED_MAX_ITERS, resume_types)
        self.assertNotIn(
            EventType.REQUIRE_ITERATION_EXTENSION,
            resume_types,
        )

    async def test_extension_event_when_not_awaiting_raises(self) -> None:
        """Feeding an extension result when not awaiting raises an error."""
        agent, _ = self._build_agent(allow_extension=True, max_iters=5)

        with self.assertRaises(ValueError):
            async for _ in agent.reply_stream(
                inputs=IterationExtensionResultEvent(
                    reply_id=agent.state.reply_id,
                    approved=True,
                    extra_iterations=1,
                ),
            ):
                pass

    async def test_extension_event_reply_id_mismatch_raises(self) -> None:
        """A mismatched reply_id on the extension result raises an error."""
        agent, model = self._build_agent(allow_extension=True, max_iters=1)
        model.set_responses([_tool_call_response(0)])

        async for _ in agent.reply_stream(
            inputs=UserMsg(name="user", content="hi"),
        ):
            pass
        self.assertTrue(agent.state.awaiting_iteration_extension)

        with self.assertRaises(ValueError):
            async for _ in agent.reply_stream(
                inputs=IterationExtensionResultEvent(
                    reply_id="not-the-right-id",
                    approved=True,
                    extra_iterations=1,
                ),
            ):
                pass
