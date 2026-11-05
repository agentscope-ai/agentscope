# -*- coding: utf-8 -*-
"""Failed steering items must not run as ordinary queued turns."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._bus_ops import enqueue_chat_input
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import Msg, TextBlock
from agentscope.state import AgentState


class _Storage:
    """Minimal storage surface used by the queue pump."""

    async def get_session(self, *args: object) -> object:
        """Return an idle session for every lookup."""
        del args
        return SimpleNamespace(state=AgentState())

    async def upsert_message(self, *args: object, **kwargs: object) -> None:
        """Discard persisted messages; this test only checks dispatch."""
        del args, kwargs


class _ChatService:
    """Record the turns the queue pump tries to start."""

    def __init__(self) -> None:
        """Create an empty call log."""
        self.calls: list[dict] = []

    async def run(self, **kwargs: object) -> None:
        """Record one dispatched run."""
        self.calls.append(kwargs)


class FailedQueueItemTest(IsolatedAsyncioTestCase):
    """A failed item remains editable until the user retries it."""

    async def test_failed_head_is_not_dispatched(self) -> None:
        """The pump leaves a failed head in the FIFO and runs nothing."""
        async with InMemoryMessageBus() as bus:
            service = _ChatService()
            queue = ChatInputQueue(bus, _Storage(), service)
            item = await enqueue_chat_input(
                bus,
                "alice",
                "session-1",
                "agent-1",
                Msg(
                    name="alice",
                    role="user",
                    content=[TextBlock(text="failed steering input")],
                ),
            )

            entries = await bus.queue_read(
                MessageBusKeys.chat_inputs("session-1"),
                max_count=1,
            )
            entry = entries[0][1]
            self.assertEqual(entry["id"], item["id"])
            entry["state"] = "failed"
            await bus.queue_replace(
                MessageBusKeys.chat_inputs("session-1"),
                [entry],
            )

            dispatched = await queue.drain_chat_inputs(
                "alice",
                "session-1",
                "agent-1",
            )

            self.assertFalse(dispatched)
            self.assertEqual(service.calls, [])
            entries = await bus.queue_read(
                MessageBusKeys.chat_inputs("session-1"),
                max_count=1,
            )
            self.assertEqual(entries[0][1]["state"], "failed")

    async def test_edited_failed_item_is_dispatched(self) -> None:
        """Changing the item back to queued lets the pump run it."""
        async with InMemoryMessageBus() as bus:
            service = _ChatService()
            queue = ChatInputQueue(bus, _Storage(), service)
            await enqueue_chat_input(
                bus,
                "alice",
                "session-1",
                "agent-1",
                Msg(
                    name="alice",
                    role="user",
                    content=[TextBlock(text="retry me")],
                ),
            )

            entries = await bus.queue_read(
                MessageBusKeys.chat_inputs("session-1"),
                max_count=1,
            )
            entry = entries[0][1]
            entry["state"] = "failed"
            await bus.queue_replace(
                MessageBusKeys.chat_inputs("session-1"),
                [entry],
            )
            self.assertFalse(
                await queue.drain_chat_inputs(
                    "alice",
                    "session-1",
                    "agent-1",
                ),
            )

            entry["state"] = "queued"
            await bus.queue_replace(
                MessageBusKeys.chat_inputs("session-1"),
                [entry],
            )
            self.assertTrue(
                await queue.drain_chat_inputs(
                    "alice",
                    "session-1",
                    "agent-1",
                ),
            )
            self.assertEqual(len(service.calls), 1)
