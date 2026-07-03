# -*- coding: utf-8 -*-
"""Full end-to-end test for the agent interruption mechanism.

Covers the complete chain: API endpoint publishes signal → CancelDispatcher
cancels the local task → Agent exits gracefully → next round works.
Uses InMemoryMessageBus (no Redis needed).
"""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import Agent
from agentscope.app._manager import (
    CancelDispatcher,
    ChatRunRegistry,
    BackgroundTaskManager,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app._router._session import interrupt_session
from agentscope.app.storage._base import StorageBase
from agentscope.app.storage._model._session import SessionRecord
from agentscope.event import ReplyEndEvent
from agentscope.message import (
    TextBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit


class FakeStorage(StorageBase):
    """Minimal storage for e2e test — only supports get_session."""

    def __init__(self, session: SessionRecord | None = None):
        self._session = session

    async def get_session(self, _user_id, _agent_id, _session_id):
        return self._session

    async def is_locked(self, _key):
        return False


class InterruptFullE2ETest(IsolatedAsyncioTestCase):
    """Full-chain e2e interruption tests."""

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

            def __init__(self):
                self.model = "slow-e2e"
                self.stream = True
                self.max_retries = 0
                self.context_size = 1000

            async def __call__(self, *_args, **_kwargs):
                async def _stream():
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

            async def count_tokens(self, *_args, **_kwargs):
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

        async def _chat_run():
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

    async def test_api_endpoint_publishes_interrupt(self) -> None:
        """The interrupt API endpoint should publish a message on the
        session_interrupt_channel when the session is running."""
        session_id = "api-test-session"

        # Create a fake session record and a fake storage that reports
        # the session as locked (running).
        from agentscope.app.storage._model._session import SessionConfig
        from agentscope.state._state import AgentState

        # Build a minimal session record
        record = SessionRecord(
            id=session_id,
            user_id="test-user",
            agent_id="test-agent",
            config=SessionConfig(workspace_id="ws-1"),
            state=AgentState(),
        )

        # Storage that reports session as locked (running)
        class LockedStorage:
            """Storage always reporting session as locked."""

            async def get_session(self, _user_id, _agent_id, _session_id):
                return record

        # Bus that reports the session as locked
        class LockedBus(InMemoryMessageBus):
            """Bus always reporting session as locked."""

            async def is_locked(self, _key):
                return True

        storage = LockedStorage()
        locked_bus = LockedBus()

        async with locked_bus:
            resp = await interrupt_session(
                session_id=session_id,
                agent_id="test-agent",
                user_id="test-user",
                storage=storage,
                message_bus=locked_bus,
            )

        self.assertEqual(resp.status, "interrupted")

    async def test_interrupt_idle_session_returns_not_running(self) -> None:
        """The endpoint should return 'not_running' when the session
        is not locked (no agent running)."""
        session_id = "idle-session"

        from agentscope.app.storage._model._session import SessionConfig
        from agentscope.state._state import AgentState

        record = SessionRecord(
            id=session_id,
            user_id="test-user",
            agent_id="test-agent",
            config=SessionConfig(workspace_id="ws-1"),
            state=AgentState(),
        )

        class IdleStorage:
            """Storage always reporting session as idle."""

            async def get_session(self, _user_id, _agent_id, _session_id):
                return record

        class IdleBus(InMemoryMessageBus):
            """Bus always reporting session as idle."""

            async def is_locked(self, _key):
                return False

        storage = IdleStorage()
        idle_bus = IdleBus()

        async with idle_bus:
            resp = await interrupt_session(
                session_id=session_id,
                agent_id="test-agent",
                user_id="test-user",
                storage=storage,
                message_bus=idle_bus,
            )

        self.assertEqual(resp.status, "not_running")
