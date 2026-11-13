# -*- coding: utf-8 -*-
"""Failed steering items must not run as ordinary queued turns."""

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app import _bus_ops
from agentscope.app._bus_ops import (
    delete_chat_input,
    enqueue_chat_input,
    list_chat_inputs,
    update_chat_input,
)
from agentscope.app._manager import ChatRunRegistry, WakeupDispatcher
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import Msg, TextBlock, UserMsg
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

    async def test_mutations_wake_dispatcher_without_recovery(self) -> None:
        """Editing or deleting wakes the FIFO without bypassing failures."""
        original = ChatInputQueue.drain_chat_inputs
        for operation, next_failed in (
            ("edit", False),
            ("delete", False),
            ("delete", True),
        ):
            with self.subTest(operation=operation, next_failed=next_failed):
                async with (
                    InMemoryMessageBus() as bus,
                    ChatRunRegistry() as registry,
                ):
                    messages = [
                        UserMsg(name="alice", content=text)
                        for text in ("first", "second", "third")
                    ]
                    items = [
                        await enqueue_chat_input(
                            bus,
                            "alice",
                            "session-1",
                            "agent-1",
                            message,
                        )
                        for message in messages
                    ]
                    entries = await bus.queue_read(
                        MessageBusKeys.chat_inputs("session-1"),
                    )
                    entries[0][1]["state"] = "failed"
                    if next_failed:
                        entries[1][1]["state"] = "failed"
                    await bus.queue_replace(
                        MessageBusKeys.chat_inputs("session-1"),
                        [entry for _, entry in entries],
                    )
                    service = _ChatService()
                    dispatcher = WakeupDispatcher(
                        bus,
                        _Storage(),
                        service,
                        registry,
                    )
                    outcomes: asyncio.Queue[bool] = asyncio.Queue()

                    async def record_drain(
                        queue: ChatInputQueue,
                        user_id: str,
                        session_id: str,
                        agent_id: str,
                        *,
                        results: asyncio.Queue[bool] = outcomes,
                    ) -> bool:
                        result = await original(
                            queue,
                            user_id,
                            session_id,
                            agent_id,
                        )
                        results.put_nowait(result)
                        return result

                    with (
                        patch.object(
                            ChatInputQueue,
                            "schedule_recovery",
                        ),
                        patch.object(
                            WakeupDispatcher,
                            "_fallback_loop",
                        ),
                        patch.object(
                            ChatInputQueue,
                            "drain_chat_inputs",
                            record_drain,
                        ),
                    ):
                        async with dispatcher:
                            self.assertFalse(
                                await asyncio.wait_for(outcomes.get(), 1),
                            )
                            self.assertEqual(service.calls, [])
                            task = registry.get("session-1")
                            if task is not None:
                                await task
                            if operation == "edit":
                                messages[0] = UserMsg(
                                    name="alice",
                                    content="fixed",
                                )
                                snapshot = await update_chat_input(
                                    bus,
                                    "alice",
                                    "session-1",
                                    "agent-1",
                                    items[0]["id"],
                                    messages[0],
                                )
                                selected = list(zip(items, messages))
                            else:
                                snapshot = await delete_chat_input(
                                    bus,
                                    "alice",
                                    "session-1",
                                    "agent-1",
                                    items[0]["id"],
                                )
                                selected = list(zip(items[1:], messages[1:]))
                            if next_failed:
                                selected = []
                            expected = [
                                {
                                    "user_id": "alice",
                                    "session_id": "session-1",
                                    "agent_id": "agent-1",
                                    "input_msg": message,
                                    "queue_item_id": item["id"],
                                    "input_persisted": True,
                                }
                                for item, message in selected
                            ]
                            for _ in range(max(1, len(expected))):
                                self.assertEqual(
                                    await asyncio.wait_for(outcomes.get(), 1),
                                    bool(expected),
                                )
                            self.assertEqual(service.calls, expected)
                            self.assertEqual(
                                await list_chat_inputs(bus, "session-1"),
                                snapshot if next_failed else [],
                            )

    async def test_mutation_survives_wakeup_failure(self) -> None:
        """A failed wakeup never rolls back a committed queue mutation."""
        for operation in ("edit", "delete"):
            with self.subTest(operation=operation):
                async with InMemoryMessageBus() as bus:
                    items = [
                        await enqueue_chat_input(
                            bus,
                            "alice",
                            "session-1",
                            "agent-1",
                            UserMsg(name="alice", content=text),
                        )
                        for text in ("first", "second")
                    ]
                    replacement = UserMsg(
                        name="alice",
                        content="fixed",
                    )
                    with patch.object(
                        _bus_ops,
                        "enqueue_run_trigger",
                        side_effect=RuntimeError("bus unavailable"),
                    ) as wakeup:
                        if operation == "edit":
                            snapshot = await update_chat_input(
                                bus,
                                "alice",
                                "session-1",
                                "agent-1",
                                items[0]["id"],
                                replacement,
                            )
                            expected = [
                                {
                                    **items[0],
                                    "input": replacement.model_dump(
                                        mode="json",
                                    ),
                                },
                                items[1],
                            ]
                        else:
                            snapshot = await delete_chat_input(
                                bus,
                                "alice",
                                "session-1",
                                "agent-1",
                                items[0]["id"],
                            )
                            expected = items[1:]
                    wakeup.assert_awaited_once()
                    self.assertEqual(snapshot, expected)
                    self.assertEqual(
                        await list_chat_inputs(bus, "session-1"),
                        expected,
                    )

    async def test_deleting_last_item_does_not_wake_dispatcher(self) -> None:
        """An empty queue needs no execution trigger."""
        async with InMemoryMessageBus() as bus:
            item = await enqueue_chat_input(
                bus,
                "alice",
                "session-1",
                "agent-1",
                UserMsg(name="alice", content="last"),
            )
            with patch.object(_bus_ops, "enqueue_run_trigger") as wakeup:
                self.assertEqual(
                    await delete_chat_input(
                        bus,
                        "alice",
                        "session-1",
                        "agent-1",
                        item["id"],
                    ),
                    [],
                )
            wakeup.assert_not_awaited()
