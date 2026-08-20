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

from pydantic import BaseModel
from utils import MockModel

from agentscope.agent import Agent
from agentscope.formatter import OpenAIChatFormatter
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

    async def _assert_chat_model_base_interruption(
        self,
        structured_schema: type[BaseModel] | None = None,
    ) -> None:
        """A cancelled model call must terminate the reply as interrupted."""

        class InterruptibleModel(MockModel):
            """ChatModelBase implementation that blocks on its first call."""

            def __init__(self) -> None:
                super().__init__(stream=False)
                self.call_started = asyncio.Event()
                self.call_count = 0

            async def _call_api(
                self,
                *_args: Any,
                **_kwargs: Any,
            ) -> ChatResponse:
                self.call_count += 1
                if self.call_count > 1:
                    raise AssertionError(
                        "Interrupted reply made another model call",
                    )
                self.call_started.set()
                await asyncio.Event().wait()
                raise AssertionError("Unreachable")

        model = InterruptibleModel()
        agent = Agent(
            name="InterruptibleAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )
        registry = ChatRunRegistry()
        end_events: list[ReplyEndEvent] = []

        async def _chat_run() -> None:
            async for event in agent.reply_stream(
                UserMsg(name="user", content="Hello"),
                structured_schema=structured_schema,
            ):
                if isinstance(event, ReplyEndEvent):
                    end_events.append(event)

        task = registry.spawn(
            _chat_run(),
            session_id="chat-model-base-interruption",
        )
        await asyncio.wait_for(model.call_started.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(end_events), 1)
        self.assertEqual(end_events[0].finished_reason, "interrupted")

    async def test_chat_model_base_interruption(self) -> None:
        """ChatModelBase cancellation is reported as interrupted."""
        await self._assert_chat_model_base_interruption()

    async def test_chat_model_base_interruption_with_structured_schema(
        self,
    ) -> None:
        """Structured output must not retry an interrupted model call."""

        class StructuredOutput(BaseModel):
            """Minimal structured output schema."""

            answer: str

        await self._assert_chat_model_base_interruption(StructuredOutput)

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
                # The agent reads formatter.supported_input_media_types
                # on every incoming message, like a real model exposes.
                self.formatter = OpenAIChatFormatter()

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
