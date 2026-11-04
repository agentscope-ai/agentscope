# -*- coding: utf-8 -*-
"""Observable retry boundaries for the offline tool example."""
import asyncio
import importlib.util
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import ToolResultEndEvent
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolChunk, Toolkit, ToolResponse


class ToolRetryExampleTest(IsolatedAsyncioTestCase):
    """Exercise the example through actual tool dispatch and agent events."""

    def setUp(self) -> None:
        """Load the standalone example without changing the import path."""
        path = (
            Path(__file__).resolve().parents[1] / "examples/tool_retry/main.py"
        )
        self.assertTrue(path.is_file(), "Tool retry example is missing")
        spec = importlib.util.spec_from_file_location("tool_retry_demo", path)
        assert spec is not None and spec.loader is not None
        self.demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.demo)

    async def _dispatch(self, tool: FunctionTool) -> ToolResponse:
        """Collect a real Toolkit response for a no-argument call."""
        results = [
            result
            async for result in Toolkit(tools=[tool]).call_tool(
                ToolCallBlock(id="lookup-1", name=tool.name, input="{}"),
                AgentState(),
            )
        ]
        response = results[-1]
        self.assertIsInstance(response, ToolResponse)
        return response

    def _tool(
        self,
        func: Callable[..., Any],
        read_only: bool = True,
    ) -> FunctionTool:
        """Wrap a local fixture with the example's retry policy."""
        return FunctionTool(
            func=func,
            name="lookup",
            description="Read a local fixture.",
            input_schema={"type": "object", "properties": {}},
            is_read_only=read_only,
            middlewares=[self.demo.ReadOnlyRetryMiddleware()],
            permission=PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Local test fixture.",
            ),
        )

    async def test_demo_scenarios(self) -> None:
        """Baseline fails once; retry recovers or stops at its budget."""
        for scenario, attempts, state, output in [
            ("baseline", 1, ToolResultState.ERROR, "lookup unavailable (1)"),
            ("recovered", 3, ToolResultState.SUCCESS, "status: available"),
            ("exhausted", 3, ToolResultState.ERROR, "lookup unavailable (3)"),
        ]:
            with self.subTest(scenario=scenario):
                count, response = await self.demo.run_scenario(scenario)
                self.assertEqual(count, attempts)
                self.assertEqual(response.state, state)
                self.assertEqual(
                    [block.text for block in response.content],
                    [output],
                )

    async def test_permanent_error_is_not_retried(self) -> None:
        """A different exception cannot consume the transient retry budget."""
        calls = []

        async def lookup() -> str:
            calls.append("call")
            raise ValueError("invalid record")

        response = await self._dispatch(self._tool(lookup))
        self.assertEqual(calls, ["call"])
        self.assertEqual(response.state, ToolResultState.ERROR)
        self.assertEqual(response.content[0].text, "invalid record")

    async def test_non_read_only_tool_is_not_retried(self) -> None:
        """The policy does not retry a tool declared to have side effects."""
        calls = []

        async def lookup() -> str:
            calls.append("call")
            raise self.demo.TransientLookupError("unavailable")

        response = await self._dispatch(self._tool(lookup, read_only=False))
        self.assertEqual(calls, ["call"])
        self.assertEqual(response.state, ToolResultState.ERROR)

    async def test_partial_output_is_not_replayed(self) -> None:
        """A transient failure after a chunk preserves it exactly once."""
        calls = []

        async def lookup() -> AsyncGenerator[ToolChunk, None]:
            calls.append("call")
            yield ToolChunk(content=[TextBlock(text="partial")], is_last=False)
            raise self.demo.TransientLookupError("stream failed")

        response = await self._dispatch(self._tool(lookup))
        self.assertEqual(calls, ["call"])
        self.assertEqual(response.state, ToolResultState.ERROR)
        self.assertEqual(
            [block.text for block in response.content],
            ["partialstream failed"],
        )

    async def test_cancellation_propagates_without_retry(self) -> None:
        """Cancellation reaches the direct caller, rather than restarting."""
        calls = []

        async def lookup() -> str:
            calls.append("call")
            raise asyncio.CancelledError("cancelled")

        stream = await self._tool(lookup)()
        with self.assertRaises(asyncio.CancelledError):
            async for _ in stream:
                pass
        self.assertEqual(calls, ["call"])

    async def test_agent_receives_recovered_and_exhausted_results(
        self,
    ) -> None:
        """Real Agent events and saved results reflect middleware outcomes."""
        for failures, state in [
            (2, ToolResultState.SUCCESS),
            (3, ToolResultState.ERROR),
        ]:
            with self.subTest(failures=failures):
                lookup = self.demo.LocalLookup(failures)
                tool = self.demo.make_tool(lookup, retry=True)
                model = MockModel()
                model.set_responses(
                    [
                        ChatResponse(
                            content=[
                                ToolCallBlock(
                                    id="lookup-1",
                                    name=tool.name,
                                    input="{}",
                                ),
                            ],
                            is_last=True,
                        ),
                        ChatResponse(
                            content=[TextBlock(text="Finished.")],
                            is_last=True,
                        ),
                    ],
                )
                agent = Agent(
                    name="Reader",
                    system_prompt="Use the lookup tool.",
                    model=model,
                    toolkit=Toolkit(tools=[tool]),
                    injection_config=InjectionConfig(
                        inject_runtime_state=False,
                    ),
                )
                events = [
                    event
                    async for event in agent.reply_stream(
                        UserMsg(name="user", content="Read the status."),
                    )
                ]
                ends = [
                    event
                    for event in events
                    if isinstance(event, ToolResultEndEvent)
                ]
                self.assertEqual(
                    [(event.tool_call_id, event.state) for event in ends],
                    [("lookup-1", state)],
                )
                results = [
                    block
                    for msg in agent.state.context
                    for block in msg.get_content_blocks("tool_result")
                ]
                self.assertEqual(
                    [(block.id, block.state) for block in results],
                    [("lookup-1", state)],
                )
                self.assertEqual(lookup.attempts, 3)
