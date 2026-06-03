# -*- coding: utf-8 -*-
# pylint: disable=abstract-method
"""Unit tests for the generic InboxMiddleware."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app import InboxMiddleware, MessageBus
from agentscope.message import (
    AssistantMsg,
    Msg,
    SystemMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    UserMsg,
)


class _FakeBus(MessageBus):
    """In-memory MessageBus implementing the queue/log/publish triad
    for unit tests."""

    def __init__(self) -> None:
        """Initialise empty per-key queues / logs."""
        self._queues: dict[str, list[tuple[str, dict]]] = {}
        self._next_id = 0

    def _alloc_id(self) -> str:
        """Allocate a monotonically increasing id."""
        self._next_id += 1
        return f"id-{self._next_id}"

    # Mode A — drain queue
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Append a payload to the in-memory queue at ``key``."""
        entry_id = self._alloc_id()
        self._queues.setdefault(key, []).append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Return up to ``max_count`` entries and remove them."""
        entries = self._queues.get(key, [])
        head = entries[:max_count]
        self._queues[key] = entries[max_count:]
        return head

    # Mode C — replay log (not exercised here; abstract method stub)
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        """No-op stub for the abstract method."""
        return ""

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """No-op stub for the abstract method."""
        return []

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        """No-op stub for the abstract method."""

    # Mode D — transient broadcast (not exercised here; abstract stub)
    async def publish(self, key: str, payload: dict) -> None:
        """No-op stub for the abstract method."""

    async def subscribe(
        self,
        key: str,
        *,
        on_ready=None,
    ) -> AsyncGenerator[dict, None]:
        """No-op generator for the abstract method."""
        if on_ready is not None:
            on_ready()
        if False:
            yield {}

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        yield

    async def is_locked(self, key: str) -> bool:
        return False


def make_fake_agent(session_id: str = "session-1") -> SimpleNamespace:
    """Build a minimal fake agent exposing only what the middleware reads."""
    return SimpleNamespace(
        state=SimpleNamespace(session_id=session_id, context=[]),
    )


async def drain(gen: AsyncGenerator) -> list:
    """Drain an async generator into a list."""
    items = []
    async for item in gen:
        items.append(item)
    return items


async def deliver(bus: _FakeBus, session_id: str, msg: Msg) -> None:
    """Helper that mirrors what a producer (e.g. team_say) does."""
    await bus.inbox_push(session_id, msg.model_dump())


class TestInboxMiddleware(IsolatedAsyncioTestCase):
    """Tests for the generic InboxMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = _FakeBus()
        self.middleware = InboxMiddleware(self.bus)
        self.agent = make_fake_agent()
        self.next_called = False

        async def _next(**_: object) -> AsyncGenerator:
            """Track that next_handler was invoked, then yield a sentinel."""
            self.next_called = True
            yield "downstream"

        self._next_handler = _next

    async def test_empty_inbox_falls_through(self) -> None:
        """Empty inbox: next_handler is called and context is unchanged."""
        items = await drain(
            self.middleware.on_reasoning(
                agent=self.agent,
                input_kwargs={"tool_choice": None},
                next_handler=self._next_handler,
            ),
        )
        self.assertTrue(self.next_called)
        self.assertEqual(items, ["downstream"])
        self.assertEqual(self.agent.state.context, [])

    async def test_non_empty_inbox_injects_into_context(self) -> None:
        """Pending messages are appended to context before downstream runs."""
        msg = UserMsg(name="alice", content=[TextBlock(text="hello")])
        await deliver(self.bus, "session-1", msg)

        order_check: list[int] = []

        async def _next(**_: object) -> AsyncGenerator:
            """Capture context length when downstream is reached."""
            order_check.append(len(self.agent.state.context))
            yield "ok"

        await drain(
            self.middleware.on_reasoning(
                agent=self.agent,
                input_kwargs={"tool_choice": None},
                next_handler=_next,
            ),
        )

        self.assertEqual(order_check, [1])
        self.assertEqual(len(self.agent.state.context), 1)
        self.assertEqual(
            self.agent.state.context[0].get_text_content(),
            "hello",
        )

    async def test_multiple_messages_preserve_order(self) -> None:
        """Messages are appended in arrival order."""
        for i in range(3):
            await deliver(
                self.bus,
                "session-1",
                UserMsg(name="x", content=[TextBlock(text=f"m{i}")]),
            )
        await drain(
            self.middleware.on_reasoning(
                agent=self.agent,
                input_kwargs={"tool_choice": None},
                next_handler=self._next_handler,
            ),
        )
        self.assertEqual(
            [m.get_text_content() for m in self.agent.state.context],
            ["m0", "m1", "m2"],
        )

    async def test_assistant_role_is_accepted(self) -> None:
        """AssistantMsg from another agent is allowed."""
        await deliver(
            self.bus,
            "session-1",
            AssistantMsg(name="worker", content=[TextBlock(text="done")]),
        )
        await drain(
            self.middleware.on_reasoning(
                agent=self.agent,
                input_kwargs={"tool_choice": None},
                next_handler=self._next_handler,
            ),
        )
        self.assertEqual(len(self.agent.state.context), 1)
        self.assertEqual(self.agent.state.context[0].role, "assistant")

    async def test_system_role_message_raises(self) -> None:
        """SystemMsg in the inbox is rejected up-front."""
        await deliver(
            self.bus,
            "session-1",
            SystemMsg(name="system", content="instruction"),
        )
        with self.assertRaises(ValueError):
            await drain(
                self.middleware.on_reasoning(
                    agent=self.agent,
                    input_kwargs={"tool_choice": None},
                    next_handler=self._next_handler,
                ),
            )

    async def test_tool_call_block_message_raises(self) -> None:
        """A msg containing tool_call blocks is rejected."""
        await deliver(
            self.bus,
            "session-1",
            AssistantMsg(
                name="worker",
                content=[
                    ToolCallBlock(id="tc-1", name="some_tool", input="{}"),
                ],
            ),
        )
        with self.assertRaises(ValueError):
            await drain(
                self.middleware.on_reasoning(
                    agent=self.agent,
                    input_kwargs={"tool_choice": None},
                    next_handler=self._next_handler,
                ),
            )

    async def test_thinking_block_message_raises(self) -> None:
        """A msg containing thinking blocks is rejected."""
        await deliver(
            self.bus,
            "session-1",
            AssistantMsg(
                name="worker",
                content=[ThinkingBlock(thinking="...")],
            ),
        )
        with self.assertRaises(ValueError):
            await drain(
                self.middleware.on_reasoning(
                    agent=self.agent,
                    input_kwargs={"tool_choice": None},
                    next_handler=self._next_handler,
                ),
            )

    async def test_session_isolation(self) -> None:
        """Messages for another session are not picked up."""
        await deliver(
            self.bus,
            "session-OTHER",
            UserMsg(name="x", content=[TextBlock(text="not for us")]),
        )
        await drain(
            self.middleware.on_reasoning(
                agent=self.agent,
                input_kwargs={"tool_choice": None},
                next_handler=self._next_handler,
            ),
        )
        self.assertEqual(self.agent.state.context, [])
