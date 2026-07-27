# -*- coding: utf-8 -*-
# pylint: disable=missing-class-docstring,missing-function-docstring
"""Service-layer integration tests for the agent interruption pipeline.

Covers the plumbing that translates an external interrupt signal into a
local ``task.cancel()``:

    message bus publish
        → ``CancelDispatcher`` (background subscriber)
        → ``ChatRunRegistry`` lookup
        → ``task.cancel()``
        → agent exits with ``finished_reason='interrupted'``
        → the next reply on the same session works normally.

FastAPI is intentionally not started — the HTTP layer is a thin wrapper
around :meth:`ChatService.interrupt`. This suite validates the
service-level wiring using :class:`InMemoryMessageBus` so no Redis is
required.
"""
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import Agent
from agentscope.app._manager import (
    CancelDispatcher,
    ChatRunRegistry,
    BackgroundTaskManager,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import ReplyEndEvent
from agentscope.message import (
    TextBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit


class ServiceAgentInterruptTest(IsolatedAsyncioTestCase):
    """Service-layer interrupt plumbing tests."""

    async def test_full_interrupt_flow(self) -> None:
        """Complete flow: user message → agent runs → interrupt published
        → CancelDispatcher cancels task → agent exits with interrupted
        → next message works.
        """
        bus = InMemoryMessageBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        session_id = "full-e2e-session"

        # ---- Build a model that streams slowly ----
        class SlowModel:
            """Streaming model with await points for testing."""

            def __init__(self) -> None:
                self.model = "slow-e2e"
                self.stream = True
                self.max_retries = 0
                self.context_size = 1000

            async def __call__(
                self,
                *_args: Any,
                **_kwargs: Any,
            ) -> Any:
                async def _stream() -> Any:
                    await asyncio.sleep(0.03)
                    yield ChatResponse(
                        content=[TextBlock(text="part1 ")],
                        is_last=False,
                    )
                    await asyncio.sleep(0.03)
                    yield ChatResponse(
                        content=[TextBlock(text="part2")],
                        is_last=False,
                    )
                    await asyncio.sleep(0.03)
                    yield ChatResponse(
                        content=[TextBlock(text="part1 part2 full")],
                        is_last=True,
                    )

                return _stream()

            async def count_tokens(
                self,
                *_args: Any,
                **_kwargs: Any,
            ) -> int:
                return 100

        # ---- Agent ----
        agent = Agent(
            name="FullE2EAgent",
            system_prompt="You are a test agent.",
            model=SlowModel(),
            toolkit=Toolkit(),
        )

        # ---- Start agent in background, register in ChatRunRegistry ----
        finished_reason_1 = None

        async def _chat_run() -> None:
            nonlocal finished_reason_1
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="Hello"),
            ):
                if isinstance(evt, ReplyEndEvent):
                    finished_reason_1 = evt.finished_reason

        registry.spawn(_chat_run(), session_id=session_id)

        # ---- Start CancelDispatcher ----
        async with bus:
            async with CancelDispatcher(
                message_bus=bus,
                registry=registry,
                bg_manager=bg_manager,
            ):
                # Wait for agent to start streaming
                await asyncio.sleep(0.04)

                # Step 1: Publish interrupt (simulating API endpoint)
                await bus.publish(
                    MessageBusKeys.session_interrupt_channel(),
                    {"session_id": session_id},
                )

                # Wait for cancellation to propagate and agent to finish
                await asyncio.sleep(0.3)

                # Step 2: Verify agent was interrupted
                self.assertEqual(
                    finished_reason_1,
                    "interrupted",
                    "Full flow: agent should exit with interrupted",
                )

                # Step 3: Verify chat-run task is done
                task = registry.get(session_id)
                self.assertTrue(
                    task is None or task.done(),
                    "Chat-run task should be cleaned up after interrupt",
                )

        # ---- Step 4: Next conversation round should work ----
        model2 = SlowModel()
        agent2 = Agent(
            name="FullE2EAgent-Round2",
            system_prompt="You are a test agent.",
            model=model2,
            toolkit=Toolkit(),
        )
        # Copy the interrupted context
        agent2.state = agent.state

        finished_reason_2 = None
        async for evt in agent2.reply_stream(
            UserMsg(name="user", content="Continue please"),
        ):
            if isinstance(evt, ReplyEndEvent):
                finished_reason_2 = evt.finished_reason

        self.assertEqual(
            finished_reason_2,
            "completed",
            "Next round after full-flow interruption should complete normally",
        )
