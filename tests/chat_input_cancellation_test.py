# -*- coding: utf-8 -*-
# pylint: disable=protected-access,cell-var-from-loop
"""Queue cancellation receipts survive ambiguous writes and lost replies."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from service_message_bus_test import _make_bus
from service_wakeup_dispatcher_test import _FakeChatService, _FakeStorage

from agentscope.app._bus_ops import (
    delete_chat_input,
    enqueue_chat_input,
    list_chat_inputs,
    register_active_chat_reply,
    steer_chat_input,
    update_chat_input,
)
from agentscope.app._chat_input_store import cleanup_session
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._router._chat import chat_router
from agentscope.app._service import SessionService
from agentscope.app.deps import (
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import UserMsg


class ChatInputCancellationTest(IsolatedAsyncioTestCase):
    """Exercise both backends; callbacks finish before each loop advances."""

    async def asyncSetUp(self) -> None:
        """Use independent in-memory and Redis-compatible bus instances."""
        self.enterContext(
            patch("agentscope.app._chat_receipts.time", return_value=1000),
        )
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        self.backends = [InMemoryMessageBus(), _make_bus(redis)]

    async def test_lost_response_retry_and_session_cleanup(self) -> None:
        """Repeat DELETE using its receipt, without persisting chat text."""
        for backend in self.backends:
            with self.subTest(backend=type(backend).__name__):
                async with backend as bus:
                    storage = _FakeStorage()
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "delete me"),
                    )
                    app = FastAPI()
                    app.include_router(chat_router)
                    app.dependency_overrides[get_storage] = lambda: storage
                    app.dependency_overrides[get_message_bus] = lambda: bus
                    app.dependency_overrides[get_current_user_id] = lambda: "u"
                    async with AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://test",
                    ) as client:
                        path = f"/chat/queue/{item['id']}"
                        params = {"agent_id": "a", "session_id": "s"}
                        first = await client.delete(path, params=params)
                        # The first response is lost; queue cleanup/replay
                        # trimming must not erase the cancellation proof.
                        await cleanup_session(bus, "s")
                        await bus.log_trim(MessageBusKeys.session_events("s"))
                        second = await client.delete(path, params=params)
                    self.assertEqual(
                        [(r.status_code, r.json()) for r in (first, second)],
                        [(200, {"items": []}), (200, {"items": []})],
                    )
                    self.assertEqual(storage.persisted_messages, [])
                    self.assertEqual(
                        json.loads(
                            await bus.registry_get(
                                MessageBusKeys.chat_input_cancellations("s"),
                                item["id"],
                            ),
                        ),
                        {"user_id": "u", "agent_id": "a", "retire_at": 4600},
                    )
                    for user, sid, agent in (
                        ("other", "s", "a"),
                        ("u", "other", "a"),
                        ("u", "s", "other"),
                    ):
                        with self.assertRaises(LookupError):
                            await delete_chat_input(
                                bus,
                                user,
                                sid,
                                agent,
                                item["id"],
                            )
                    await SessionService(storage, bus)._purge_session_bus("s")
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_cancellations("s"),
                        ),
                        {},
                    )
                    with self.assertRaises(LookupError):
                        await delete_chat_input(bus, "u", "s", "a", item["id"])

    async def test_partial_cancel_never_executes_and_reused_msg_id_is_safe(
        self,
    ) -> None:
        """Receipt-first writes survive failure before or after dequeue."""
        for backend in self.backends:
            async with backend as bus:
                for boundary in ("receipt", "before_dequeue", "after_dequeue"):
                    with self.subTest(
                        backend=type(backend).__name__,
                        boundary=boundary,
                    ):
                        message = UserMsg("u", "same message id", id="shared")
                        cancelled = await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            message,
                        )
                        later = await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            message,
                        )
                        write = bus.registry_set
                        replace = bus.queue_replace

                        async def fail_receipt(*args: object) -> None:
                            await write(*args)
                            if args[
                                0
                            ] == MessageBusKeys.chat_input_cancellations("s"):
                                raise ConnectionError("receipt response lost")

                        async def fail_dequeue(*args: object) -> None:
                            if boundary == "after_dequeue":
                                await replace(*args)
                            raise ConnectionError("dequeue response lost")

                        target = (
                            "registry_set"
                            if boundary == "receipt"
                            else "queue_replace"
                        )
                        operation = (
                            fail_receipt
                            if boundary == "receipt"
                            else fail_dequeue
                        )
                        with patch.object(bus, target, side_effect=operation):
                            with self.assertRaises(ConnectionError):
                                await delete_chat_input(
                                    bus,
                                    "u",
                                    "s",
                                    "a",
                                    cancelled["id"],
                                )
                        service = _FakeChatService()
                        service.run = AsyncMock(wraps=service.run)
                        queue = ChatInputQueue(bus, _FakeStorage(), service)
                        self.addAsyncCleanup(queue.close)
                        self.assertTrue(
                            await queue.drain_chat_inputs("u", "s", "a"),
                        )
                        self.assertEqual(
                            [
                                (
                                    call.kwargs["queue_item_id"],
                                    call.kwargs["input_msg"],
                                )
                                for call in service.run.await_args_list
                            ],
                            [(later["id"], message)],
                        )
                        self.assertEqual(
                            await delete_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                cancelled["id"],
                            ),
                            [],
                        )
                        with self.assertRaises(LookupError):
                            await delete_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                later["id"],
                            )

    async def test_claim_wins_without_false_cancellation_receipt(self) -> None:
        """A durable claim wins even if its queue removal did not finish."""
        for backend in self.backends:
            with self.subTest(backend=type(backend).__name__):
                async with backend as bus:
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "claimed"),
                    )
                    service = _FakeChatService()
                    service.run = AsyncMock(wraps=service.run)
                    queue = ChatInputQueue(bus, _FakeStorage(), service)
                    self.addAsyncCleanup(queue.close)
                    with patch.object(
                        bus,
                        "queue_drain",
                        side_effect=ConnectionError,
                    ):
                        with self.assertRaises(ConnectionError):
                            await queue._claim_chat_input("s")
                    with self.assertRaises(LookupError):
                        await delete_chat_input(bus, "u", "s", "a", item["id"])
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_cancellations("s"),
                        ),
                        {},
                    )
                    self.assertTrue(
                        await queue.drain_chat_inputs("u", "s", "a"),
                    )
                    self.assertEqual(
                        [
                            call.kwargs["queue_item_id"]
                            for call in service.run.await_args_list
                        ],
                        [item["id"]],
                    )

    async def test_cancelled_input_cannot_be_edited_or_steered(self) -> None:
        """A leftover FIFO entry cannot undo the committed cancellation."""
        async with self.backends[0] as bus:
            msg = UserMsg("u", "pending")
            item = await enqueue_chat_input(bus, "u", "s", "a", msg)
            with patch.object(
                bus,
                "queue_replace",
                side_effect=ConnectionError,
            ):
                with self.assertRaises(ConnectionError):
                    await delete_chat_input(bus, "u", "s", "a", item["id"])
            with self.assertRaises(LookupError):
                await update_chat_input(bus, "u", "s", "a", item["id"], msg)
            async with bus.acquire_lock(MessageBusKeys.session_lock("s")):
                await register_active_chat_reply(bus, "s", "reply")
                with self.assertRaises(LookupError):
                    await steer_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        item["id"],
                        "reply",
                    )
            self.assertEqual(
                await delete_chat_input(bus, "u", "s", "a", item["id"]),
                [],
            )

    async def test_failed_receipt_write_does_not_claim_cancellation(
        self,
    ) -> None:
        """Without a durable receipt the original item stays executable."""
        async with self.backends[0] as bus:
            msg = UserMsg("u", "pending")
            item = await enqueue_chat_input(bus, "u", "s", "a", msg)
            with patch.object(
                bus,
                "registry_set",
                new=AsyncMock(side_effect=ConnectionError),
            ):
                with self.assertRaises(ConnectionError):
                    await delete_chat_input(bus, "u", "s", "a", item["id"])
            self.assertEqual(await list_chat_inputs(bus, "s"), [item])
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_cancellations("s"),
                ),
                {},
            )
