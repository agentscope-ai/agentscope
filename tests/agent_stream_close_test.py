# -*- coding: utf-8 -*-
"""Tests for explicit closure of the public agent event stream."""
import asyncio
from typing import AsyncGenerator, Callable
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    ReplyEndEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
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


class _BlockedTool(ToolBase):
    """Record tool lifecycle without clocks, files, or external services."""

    name = "blocked"
    description = "Wait for release before recording a side effect."
    input_schema = {"type": "object", "properties": {}}
    is_concurrency_safe = True
    is_read_only = False

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.workers: list[asyncio.Task] = []
        self.cancelled = 0
        self.side_effects = 0
        self.interrupt = False

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow offline test execution."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="offline test",
        )

    async def __call__(self) -> AsyncGenerator[ToolChunk, None]:
        worker = asyncio.current_task()
        assert worker is not None
        self.workers.append(worker)
        if len(self.workers) == 2:
            self.started.set()
        try:
            await self.release.wait()
            if self.interrupt:
                raise asyncio.CancelledError()
            self.side_effects += 1
            yield ToolChunk(content=[TextBlock(text="done")], is_last=True)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class _ReplyWrapper(MiddlewareBase):
    """Forward events like a middleware that does not close its handler."""

    def __init__(self) -> None:
        self.closed = False

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        try:
            async for event in next_handler(**input_kwargs):
                yield event
        finally:
            self.closed = True


def _make_agent(tool: _BlockedTool, middlewares: list | None = None) -> Agent:
    """Return an offline agent with two calls followed by a final answer."""
    return Agent(
        name="bot",
        system_prompt="Use the tool.",
        model=MockModel(
            stream=False,
            mock_chat_responses=[
                ChatResponse(
                    content=[
                        ToolCallBlock(id="call-1", name=tool.name, input="{}"),
                        ToolCallBlock(id="call-2", name=tool.name, input="{}"),
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        ),
        toolkit=Toolkit(tools=[tool]),
        injection_config=InjectionConfig(inject_runtime_state=False),
        middlewares=middlewares,
    )


class AgentStreamCloseTest(IsolatedAsyncioTestCase):
    """Closing a stream must join every worker before returning."""

    async def _check_close(self, wrapped: bool) -> None:
        tool = _BlockedTool()
        wrappers = [_ReplyWrapper(), _ReplyWrapper()] if wrapped else []
        agent = _make_agent(tool, wrappers)
        stream = agent.reply_stream(UserMsg("user", "run"))
        try:
            while not isinstance(await anext(stream), ToolResultStartEvent):
                pass
            await asyncio.wait_for(tool.started.wait(), timeout=2)
            await stream.aclose()
            self.assertEqual(tool.cancelled, 2)
            self.assertTrue(all(worker.done() for worker in tool.workers))
            self.assertTrue(all(wrapper.closed for wrapper in wrappers))
            self.assertEqual(tool.side_effects, 0)
            results = [
                result
                for message in agent.state.context
                for result in message.content
                if isinstance(result, ToolResultBlock)
            ]
            self.assertEqual(len(results), 2)
            self.assertTrue(
                all(result.state == "interrupted" for result in results),
            )
            state = agent.state.model_dump()
            tool.release.set()
            await asyncio.gather(*tool.workers, return_exceptions=True)
            self.assertEqual(agent.state.model_dump(), state)
            self.assertEqual(tool.side_effects, 0)
        finally:
            # Also join workers on the unfixed implementation after failure.
            tool.release.set()
            await asyncio.gather(*tool.workers, return_exceptions=True)
            await stream.aclose()

    async def test_close_joins_concurrent_tools(self) -> None:
        """Public stream closure cancels both tools without finalizers."""
        await self._check_close(wrapped=False)

    async def test_close_through_reply_middlewares(self) -> None:
        """Nested reply middleware cannot detach the tool workers."""
        await self._check_close(wrapped=True)

    async def test_complete_consumption_finishes_tools(self) -> None:
        """Fully consuming a stream does not cancel successful tools."""
        tool = _BlockedTool()
        tool.release.set()
        wrapper = _ReplyWrapper()
        agent = _make_agent(tool, [wrapper])
        events = [
            event async for event in agent.reply_stream(UserMsg("user", "run"))
        ]
        self.assertTrue(
            any(isinstance(event, ReplyEndEvent) for event in events),
        )
        self.assertEqual(tool.side_effects, 2)
        self.assertEqual(tool.cancelled, 0)
        self.assertTrue(all(worker.done() for worker in tool.workers))
        self.assertTrue(wrapper.closed)

    async def _check_end_boundary(
        self,
        interrupted: bool,
        event_type: type,
    ) -> None:
        tool = _BlockedTool()
        tool.interrupt = interrupted
        tool.release.set()
        wrapper = _ReplyWrapper()
        agent = _make_agent(tool, [wrapper])
        stream = agent.reply_stream(UserMsg("user", "run"))
        try:
            while not isinstance(await anext(stream), event_type):
                pass
            await stream.aclose()
            self.assertTrue(all(worker.done() for worker in tool.workers))
            self.assertTrue(wrapper.closed)
            self.assertEqual(tool.side_effects, 0 if interrupted else 2)
            results = agent.state.context[-1].get_content_blocks("tool_result")
            self.assertEqual(len(results), 2)
            expected_state = "interrupted" if interrupted else "success"
            self.assertTrue(
                all(result.state == expected_state for result in results),
            )
        finally:
            await stream.aclose()

    async def test_close_at_completed_reply_end(self) -> None:
        """Closing at a normal reply end preserves completed results."""
        await self._check_end_boundary(False, ReplyEndEvent)

    async def test_close_at_interrupted_tool_end(self) -> None:
        """Closing during interruption does not yield from GeneratorExit."""
        await self._check_end_boundary(True, ToolResultEndEvent)

    async def test_close_at_interrupted_reply_end(self) -> None:
        """Closing in the reply finalizer skips the fallback message."""
        await self._check_end_boundary(True, ReplyEndEvent)
