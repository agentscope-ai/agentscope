# -*- coding: utf-8 -*-
"""Regression tests for fatal tool-error cleanup."""
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.exception import DeveloperOrientedException
from agentscope.event import (
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import (
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
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class _AllowedTool(ToolBase):
    """A tool whose execution is interrupted by the middleware below."""

    name: str = "allowed_tool"
    description: str = "A test tool."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the test call without a confirmation round."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Raise the fatal tool error under test."""
        raise DeveloperOrientedException("boom: fatal tool failure")


class _FatalActingMiddleware(MiddlewareBase):
    """Pass the call through to exercise the real toolkit path."""

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Delegate to the next handler without changing its events."""
        del agent
        async for event in next_handler(**input_kwargs):
            yield event


class FatalErrorCleanupTest(IsolatedAsyncioTestCase):
    """Fatal acting errors close pending tool calls before propagating."""

    async def test_fatal_error_closes_unfinished_tool_call(self) -> None:
        """The reply emits an interrupted result and does not leave ASKING."""
        model = MockModel()
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="fatal-call",
                            name="allowed_tool",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        agent = Agent(
            name="test-agent",
            system_prompt="test",
            model=model,
            toolkit=Toolkit(tools=[_AllowedTool()]),
            middlewares=[_FatalActingMiddleware()],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

        events = []
        with self.assertRaises(ExceptionGroup):
            async for event in agent.reply_stream(
                UserMsg(name="user", content="run the tool"),
            ):
                events.append(event)

        self.assertTrue(
            any(isinstance(event, ToolResultStartEvent) for event in events),
        )
        self.assertTrue(
            any(
                isinstance(event, ToolResultTextDeltaEvent)
                and "internal error" in event.delta
                for event in events
            ),
        )
        end_events = [
            event for event in events if isinstance(event, ToolResultEndEvent)
        ]
        self.assertEqual(len(end_events), 1)
        self.assertEqual(end_events[0].state, ToolResultState.INTERRUPTED)

        assistant = agent.state.context[-1]
        call = assistant.get_content_blocks("tool_call")[0]
        result = assistant.get_content_blocks("tool_result")[0]
        self.assertEqual(call.state, ToolCallState.FINISHED)
        self.assertEqual(result.id, "fatal-call")
        self.assertEqual(result.state, ToolResultState.INTERRUPTED)
