# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Tests for agent error cleanup:

A non-interrupt exception that escapes the reply loop (a failing
``on_acting`` middleware, an offloader error, ...) must close the tool
calls it leaves without a result before propagating, mirroring the
interrupt cleanup — otherwise the assistant message keeps a
``ToolCallBlock`` with no matching ``ToolResultBlock`` and every later
reply in the session sends a request that OpenAI-compatible providers
reject with 400.
"""

import unittest
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import ToolResultEndEvent
from agentscope.middleware import MiddlewareBase
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit

_CLOSING_MESSAGE = (
    "<system-reminder>The tool call failed due to an "
    "internal error.</system-reminder>"
)


class _EchoTool(ToolBase):
    """A healthy tool that echoes its input."""

    name: str = "echo"
    description: str = "Echoes the input."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": [],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="ok",
            message="ok",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=f"echo:{kwargs.get('input', '')}")],
            is_last=True,
        )


class _FailingActingMiddleware(MiddlewareBase):
    """``on_acting`` middleware that fails mid-execution, the way a
    production middleware can (an external service error, a drained
    offloader exception, ...)."""

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Any,
    ) -> AsyncGenerator[Any, None]:
        async for chunk in next_handler(**input_kwargs):
            yield chunk
            raise RuntimeError("middleware infra failure")


class AgentErrorCleanupTest(IsolatedAsyncioTestCase):
    """A non-interrupt exception during acting must not leave unpaired
    tool calls in the context."""

    def _make_agent(
        self,
        tools: list[ToolBase],
        middlewares: list[MiddlewareBase] | None = None,
    ) -> tuple[Agent, MockModel]:
        model = MockModel(model="mock-model", stream=True)
        agent = Agent(
            name="Friday",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(tools=tools),
            # The runtime state injection is covered by
            # agent_injection_test, turn it off to keep the assertions
            # focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
            middlewares=middlewares or [],
        )
        return agent, model

    async def test_error_closes_unfinished_tool_call_and_propagates(
        self,
    ) -> None:
        """The exception reaches the caller, but the tool call it
        abandons is closed with an ERROR result first."""
        tool = _EchoTool()
        agent, model = self._make_agent(
            [tool],
            middlewares=[_FailingActingMiddleware()],
        )
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc-1",
                                name=tool.name,
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = []
        with self.assertRaises(RuntimeError):
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="hi"),
            ):
                events.append(evt)

        end_events = [
            evt
            for evt in events
            if isinstance(evt, ToolResultEndEvent)
            and evt.state == ToolResultState.ERROR
        ]
        self.assertEqual(len(end_events), 1)

        last_msg = agent.state.context[-1]
        calls = [
            block
            for block in last_msg.content
            if isinstance(block, ToolCallBlock)
        ]
        results = [
            block
            for block in last_msg.content
            if isinstance(block, ToolResultBlock)
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(calls[0].id, "tc-1")
        self.assertEqual(results[0].id, "tc-1")
        self.assertEqual(results[0].state, ToolResultState.ERROR)
        self.assertEqual(results[0].output, _CLOSING_MESSAGE)

    async def test_next_reply_request_is_well_formed(self) -> None:
        """After the failure, the formatted request pairs the assistant
        tool call with its tool result message."""
        tool = _EchoTool()
        agent, model = self._make_agent(
            [tool],
            middlewares=[_FailingActingMiddleware()],
        )
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc-1",
                                name=tool.name,
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        with self.assertRaises(RuntimeError):
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="hi"),
            ):
                pass

        formatted = await model.formatter.format(
            agent.state.context + [UserMsg(name="user", content="ok")],
        )
        roles = [item["role"] for item in formatted]
        for index, item in enumerate(formatted):
            if item.get("tool_calls"):
                self.assertEqual(roles[index + 1], "tool")

    async def test_reasoning_failure_adds_no_tool_results(self) -> None:
        """A model failure before any tool call propagates without
        touching the context."""
        tool = _EchoTool()
        agent, model = self._make_agent([tool])
        model.set_responses([RuntimeError("model blew up")])

        n_msgs_before = len(agent.state.context)
        with self.assertRaises(RuntimeError):
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="hi"),
            ):
                pass

        # Only the user message entered the context; no assistant reply
        # and no tool results were synthesized.
        self.assertEqual(len(agent.state.context), n_msgs_before + 1)
        self.assertEqual(agent.state.context[-1].role, "user")
        for msg in agent.state.context:
            for block in msg.content:
                self.assertNotIsInstance(block, ToolResultBlock)


if __name__ == "__main__":
    unittest.main()
