# -*- coding: utf-8 -*-
"""
# mypy: disable-error-code="no-untyped-def"End-to-end tests for the agent interruption mechanism.

Covers the full path: task.cancel() → CancelledError/GeneratorExit
at the Model/Tool layer → is_interrupted → Agent graceful exit.
"""
import asyncio
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import ReplyEndEvent
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.tool import (
    Toolkit,
    ToolBase,
    ToolChunk,
)
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)


class SlowStreamingTool(ToolBase):
    """A tool that streams chunks slowly, allowing interruption
    mid-execution."""

    name: str = "slow_tool"
    description: str = "A slow streaming tool"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string"}},
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
            message="Allowed",
        )

    async def __call__(self, **kwargs: Any) -> AsyncGenerator[ToolChunk, None]:
        """Simulate slow streaming with five chunks."""
        for _ in range(5):
            await asyncio.sleep(0.05)
            yield ToolChunk(
                content=[TextBlock(text="processing...")],
                is_last=False,
            )
        yield ToolChunk(
            content=[TextBlock(text="done")],
            is_last=True,
        )


class InterruptE2ETest(IsolatedAsyncioTestCase):
    """End-to-end interruption tests."""

    async def test_model_interruption_e2e(self) -> None:
        """Cancel the agent task during a slow model streaming call.
        The Model layer should catch the interruption signal and return
        a ChatResponse with is_interrupted=True, which the Agent should
        handle gracefully.
        """

        class SlowCancelModel(MockModel):
            """Model whose streaming generator has real await points."""

            async def _call_api(self, *args, **kwargs) -> None:
                mock_responses = self.mock_chat_responses[self.cnt]
                self.cnt += 1
                if isinstance(mock_responses, list):

                    async def _stream():
                        for response in mock_responses:
                            await asyncio.sleep(0.05)
                            yield response

                    return _stream()
                return mock_responses

        model = SlowCancelModel(model="mock-e2e", stream=True)
        # Chunks simulate a slow LLM generation
        chunks = [
            ChatResponse(
                content=[TextBlock(text="Hello ")],
                is_last=False,
            ),
            ChatResponse(
                content=[TextBlock(text="world")],
                is_last=False,
            ),
            ChatResponse(
                content=[TextBlock(text="!")],
                is_last=False,
            ),
            # Final chunk would normally arrive last but we cancel before it
            ChatResponse(
                content=[TextBlock(text="Hello world! The complete answer.")],
                is_last=True,
            ),
        ]
        model.set_responses([chunks])

        agent = Agent(
            name="E2EAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )

        # Run reply_stream in a background task and cancel it during streaming
        events: list = []
        collected_finished = None

        async def _collect_events() -> None:
            nonlocal collected_finished
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="Hi"),
            ):
                events.append(evt)
                if isinstance(evt, ReplyEndEvent):
                    collected_finished = evt.finished_reason

        task = asyncio.create_task(_collect_events())

        # Let the task start but cancel before the final chunk arrives
        await asyncio.sleep(0.01)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # The agent should have exited gracefully with an interrupted event
        self.assertIsNotNone(collected_finished)
        self.assertEqual(
            collected_finished,
            "interrupted",
            "Interrupted model should produce finished_reason='interrupted'",
        )

        # Agent state should be consistent after interruption
        self.assertIsNotNone(agent.state)

    async def test_tool_interruption_e2e(self) -> None:
        """Cancel the agent task during slow tool execution.
        The Tool layer should catch the interruption and return a
        ToolResponse with is_interrupted=True.
        """

        class SlowCancelModel(MockModel):
            """Model whose streaming generator has real await points."""

            async def _call_api(self, *args, **kwargs) -> None:
                mock_responses = self.mock_chat_responses[self.cnt]
                self.cnt += 1
                if isinstance(mock_responses, list):

                    async def _stream():
                        for response in mock_responses:
                            await asyncio.sleep(0.05)
                            yield response

                    return _stream()
                return mock_responses

        model = SlowCancelModel(model="mock-e2e", stream=True)
        # Model returns a tool call, then the tool runs slowly
        # Agent will execute the tool after receiving this response
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Let me use a tool."),
                            ToolCallBlock(
                                id="tc-slow",
                                name="slow_tool",
                                input='{"input": "test"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        agent = Agent(
            name="E2EAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(tools=[SlowStreamingTool()]),
        )

        events: list = []
        collected_finished = None

        async def _collect_events() -> None:
            nonlocal collected_finished
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="Hi"),
            ):
                events.append(evt)
                if isinstance(evt, ReplyEndEvent):
                    collected_finished = evt.finished_reason

        task = asyncio.create_task(_collect_events())

        # Let the agent start executing tools, then cancel
        await asyncio.sleep(0.15)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Tool interruption should result in interrupted finished_reason
        self.assertIsNotNone(collected_finished)
        self.assertEqual(
            collected_finished,
            "interrupted",
            "interrupted tool should produce finished_reason='interrupted'",
        )

    async def test_middleware_interruption_e2e(self) -> None:
        """Cancel the agent task while middleware is processing.
        The reply_stream catch should handle CancelledError even when
        it hits middleware hooks, not Model/Tool directly.

        Uses a model with an async streaming generator (containing
        ``await`` points) so the cancel reliably lands mid-generation.
        """

        # A model whose streaming generator has real await points so
        # task.cancel() can inject CancelledError.
        class SlowCancelModel(MockModel):
            """Model whose streaming generator has real await points."""

            async def _call_api(self, *args, **kwargs) -> None:
                mock_responses = self.mock_chat_responses[self.cnt]
                self.cnt += 1
                if isinstance(mock_responses, list):

                    async def _stream():
                        for response in mock_responses:
                            await asyncio.sleep(0.05)
                            yield response

                    return _stream()
                return mock_responses

        model = SlowCancelModel(model="mock-slow", stream=True)
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="Thinking...")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="Final answer.")],
                        is_last=True,
                    ),
                ],
            ],
        )

        agent = Agent(
            name="E2EAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )

        collected_finished = None

        async def _collect_events() -> None:
            nonlocal collected_finished
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="Hi"),
            ):
                if isinstance(evt, ReplyEndEvent):
                    collected_finished = evt.finished_reason

        task = asyncio.create_task(_collect_events())

        # Let the stream start, then cancel — lands mid-generation
        await asyncio.sleep(0.01)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have been interrupted mid-stream
        self.assertIsNotNone(collected_finished)
        self.assertEqual(
            collected_finished,
            "interrupted",
            "Cancellation during model streaming should produce interrupted",
        )

        # Verify context is consistent — no crash means success
        self.assertIsNotNone(agent.state)
