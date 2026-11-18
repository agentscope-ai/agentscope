# -*- coding: utf-8 -*-
# pylint: disable=protected-access,cell-var-from-loop
"""Submission admission and receipt collection preserve cancellation fences."""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import fakeredis.aioredis
from service_message_bus_test import _make_bus
from service_wakeup_dispatcher_test import _FakeChatService, _FakeStorage

from agentscope.app._bus_ops import (
    ChatQueueFullError,
    ChatSubmissionConflictError,
    cancel_chat_submission,
    create_chat_submission,
    delete_chat_input,
    enqueue_chat_input,
)
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._chat_receipts import receipt_count
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import UserMsg


class ChatReceiptLifecycleTest(IsolatedAsyncioTestCase):
    """Use a controlled clock on the in-memory and Redis implementations."""

    async def asyncSetUp(self) -> None:
        self.now = 1000
        self.enterContext(
            patch(
                "agentscope.app._bus_ops.time",
                side_effect=lambda: self.now,
            ),
        )
        self.enterContext(
            patch(
                "agentscope.app._chat_receipts.time",
                side_effect=lambda: self.now,
            ),
        )
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        self.backends = [InMemoryMessageBus(), _make_bus(redis)]

    def collector(self, bus: object) -> ChatInputQueue:
        """Build the real periodic collector with explicit result evidence."""
        service = _FakeChatService()
        service.input_result = AsyncMock(
            return_value={
                "state": "reply",
                "reply_id": "R",
            },
        )
        queue = ChatInputQueue(bus, _FakeStorage(), service)
        self.addAsyncCleanup(queue.close)
        return queue

    async def test_legacy_full_session_is_adopted_for_collection(self) -> None:
        """Old unindexed receipts cannot permanently consume the quota."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    key = MessageBusKeys.chat_submissions("s")
                    await bus.registry_set(
                        key,
                        "old",
                        json.dumps(
                            {
                                "user_id": "u",
                                "agent_id": "a",
                                "state": "claimed",
                                "queue_item_id": "old-q",
                            },
                        ),
                    )
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_SESSION_MAX_LEN",
                        1,
                    ):
                        with self.assertRaises(ChatQueueFullError):
                            await create_chat_submission(bus, "u", "s", "a")
                    queue = self.collector(bus)
                    queue._storage.parked_session_ids.add("s")
                    await queue._recover_pending_chat_inputs_locked()
                    record = json.loads(await bus.registry_get(key, "old"))
                    self.assertNotIn("retire_at", record)
                    queue._storage.parked_session_ids.clear()
                    await queue._recover_pending_chat_inputs_locked()
                    record["retire_at"] = 4600
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, "old")),
                        record,
                    )
                    self.now = 4601
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(await bus.registry_getall(key), {})
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_user_sessions("u"),
                        ),
                        {},
                    )

    async def test_collection_failure_still_wakes_pending_input(self) -> None:
        """Busy locks and failed result reads must not suppress wakeups."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    queue = self.collector(bus)
                    await create_chat_submission(bus, "u", "s", "a")
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "ordinary"),
                    )
                    self.now = 2000
                    async with bus.acquire_lock(
                        MessageBusKeys.chat_input_mutation_lock("s"),
                    ):
                        self.assertEqual(
                            await queue._recover_pending_chat_inputs_locked(),
                            1,
                        )
                    self.assertEqual(
                        await queue._recover_pending_chat_inputs_locked(),
                        1,
                    )
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_submissions("s"),
                        ),
                        {},
                    )
                    await bus.registry_set(
                        MessageBusKeys.chat_submissions("s"),
                        "old",
                        json.dumps(
                            {
                                "state": "claimed",
                                "queue_item_id": "old",
                                "message_ids": ["missing"],
                            },
                        ),
                    )
                    with patch.object(
                        queue,
                        "_receipt_input_result",
                        side_effect=ConnectionError,
                    ):
                        self.assertEqual(
                            await queue._recover_pending_chat_inputs_locked(),
                            1,
                        )
                    self.assertEqual(
                        [
                            p["id"]
                            for _, p in await bus.queue_read(
                                MessageBusKeys.chat_inputs("s"),
                            )
                        ],
                        [item["id"]],
                    )

    async def test_hung_collection_releases_lock_and_continues(self) -> None:
        """A hung result query must not block either session's wakeup."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    queue = self.collector(bus)
                    for sid in ("slow", "fast"):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            sid,
                            "a",
                            UserMsg("u", "pending"),
                        )
                    await bus.queue_drain(MessageBusKeys.wakeup_queue())
                    key = MessageBusKeys.chat_submissions("slow")
                    record = {
                        "state": "claimed",
                        "queue_item_id": "old",
                        "message_ids": ["old-message"],
                    }
                    await bus.registry_set(key, "old", json.dumps(record))
                    cancelled = asyncio.Event()

                    async def hung(*_args: object) -> dict:
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()
                        return {}

                    with patch.object(
                        queue,
                        "_receipt_input_result",
                        side_effect=hung,
                    ), patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_COLLECTION_TIMEOUT_SECS",
                        0.02,
                    ):
                        self.assertEqual(
                            await asyncio.wait_for(
                                queue._recover_pending_chat_inputs_locked(),
                                1,
                            ),
                            2,
                        )
                    self.assertTrue(cancelled.is_set())
                    self.assertFalse(
                        await bus.is_locked(
                            MessageBusKeys.chat_input_mutation_lock("slow"),
                        ),
                    )
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, "old")),
                        record,
                    )
                    self.assertEqual(
                        sorted(
                            p["session_id"]
                            for _, p in await bus.queue_drain(
                                MessageBusKeys.wakeup_queue(),
                            )
                        ),
                        ["fast", "slow"],
                    )
                    with patch.object(
                        queue,
                        "_receipt_input_result",
                        return_value={"state": "finished"},
                    ):
                        await queue._recover_pending_chat_inputs_locked()
                    record["retire_at"] = 4600
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, "old")),
                        record,
                    )

    async def test_lease_reserves_complete_turn_capacity(self) -> None:
        """Issuance needs two slots; existing leases can submit at capacity."""
        for bus in self.backends:
            async with bus:
                for quota in (
                    "CHAT_RECEIPT_SESSION_MAX_LEN",
                    "CHAT_RECEIPT_USER_MAX_LEN",
                ):
                    with self.subTest(backend=type(bus).__name__, quota=quota):
                        with patch.object(MessageBusKeys, quota, 1):
                            with self.assertRaises(ChatQueueFullError):
                                await create_chat_submission(
                                    bus,
                                    quota,
                                    quota,
                                    "a",
                                )
                        self.assertEqual(
                            await bus.registry_getall(
                                MessageBusKeys.chat_submissions(quota),
                            ),
                            {},
                        )
                        with patch.object(MessageBusKeys, quota, 2):
                            lease = await create_chat_submission(
                                bus,
                                quota,
                                quota,
                                "a",
                            )
                            self.assertEqual(
                                await receipt_count(bus, quota),
                                2,
                            )
                            with self.assertRaises(ChatQueueFullError):
                                await enqueue_chat_input(
                                    bus,
                                    quota,
                                    quota,
                                    "a",
                                    UserMsg("u", "ordinary"),
                                )
                            item = await enqueue_chat_input(
                                bus,
                                quota,
                                quota,
                                "a",
                                UserMsg("u", "voice"),
                                submission_id=lease["submission_id"],
                            )
                            self.assertEqual(
                                await receipt_count(bus, quota),
                                2,
                            )
                            self.assertEqual(
                                await cancel_chat_submission(
                                    bus,
                                    quota,
                                    quota,
                                    "a",
                                    lease["submission_id"],
                                ),
                                {
                                    "state": "cancelled",
                                    "queue_item_id": item["id"],
                                },
                            )
                            self.assertEqual(
                                await receipt_count(bus, quota),
                                2,
                            )

    async def test_partial_enqueue_keeps_cancellation_capacity(self) -> None:
        """A reserved queue slot survives failure before the FIFO write."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_SESSION_MAX_LEN",
                        2,
                    ):
                        lease = await create_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                        )
                        with patch.object(
                            bus,
                            "queue_push",
                            side_effect=ConnectionError,
                        ):
                            with self.assertRaises(ConnectionError):
                                await enqueue_chat_input(
                                    bus,
                                    "u",
                                    "s",
                                    "a",
                                    UserMsg("u", "voice"),
                                    submission_id=lease["submission_id"],
                                )
                        self.assertEqual(await receipt_count(bus, "s"), 2)
                        with self.assertRaises(ChatQueueFullError):
                            await create_chat_submission(bus, "u", "s", "a")
                        result = await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            lease["submission_id"],
                        )
                        record = json.loads(
                            await bus.registry_get(
                                MessageBusKeys.chat_submissions("s"),
                                lease["submission_id"],
                            ),
                        )
                        self.assertEqual(
                            result,
                            {
                                "state": "cancelled",
                                "queue_item_id": record["queue_item_id"],
                            },
                        )
                        self.assertEqual(await receipt_count(bus, "s"), 2)

    async def test_unknown_ids_never_allocate_or_submit(self) -> None:
        """Arbitrary cancellations leave no records and cannot enable POST."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    for _ in range(1000):
                        sid = str(uuid4())
                        self.assertEqual(
                            await cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                sid,
                            ),
                            {"state": "cancelled", "queue_item_id": None},
                        )
                    with self.assertRaises(ChatSubmissionConflictError):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "late"),
                            submission_id=sid,
                        )
                    for namespace in (
                        MessageBusKeys.chat_submissions("s"),
                        MessageBusKeys.chat_input_cancellations("s"),
                        MessageBusKeys.chat_input_pending_registry(),
                        MessageBusKeys.chat_input_user_sessions("u"),
                    ):
                        self.assertEqual(
                            await bus.registry_getall(namespace),
                            {},
                        )

    async def test_full_quota_allows_cancel_and_expired_ids_stay_rejected(
        self,
    ) -> None:
        """A queued input reserves cancellation space before quota fills."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    queue = self.collector(bus)
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_SESSION_MAX_LEN",
                        2,
                    ):
                        lease = await create_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                        )
                        sid = lease["submission_id"]
                        item = await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "voice"),
                            submission_id=sid,
                        )
                        with self.assertRaises(ChatQueueFullError):
                            await create_chat_submission(bus, "u", "s", "a")
                        self.assertEqual(
                            await cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                sid,
                            ),
                            {
                                "state": "cancelled",
                                "queue_item_id": item["id"],
                            },
                        )
                        self.now = 4599
                        await cancel_chat_submission(bus, "u", "s", "a", sid)
                        await queue._recover_pending_chat_inputs_locked()
                        self.assertEqual(
                            len(
                                await bus.registry_getall(
                                    MessageBusKeys.chat_submissions("s"),
                                ),
                            ),
                            1,
                        )
                        self.now = 4601
                        await queue._recover_pending_chat_inputs_locked()
                        for namespace in (
                            MessageBusKeys.chat_submissions("s"),
                            MessageBusKeys.chat_input_cancellations("s"),
                            MessageBusKeys.chat_input_pending_registry(),
                            MessageBusKeys.chat_input_user_sessions("u"),
                        ):
                            self.assertEqual(
                                await bus.registry_getall(namespace),
                                {},
                            )
                        with self.assertRaises(ChatSubmissionConflictError):
                            await enqueue_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                UserMsg("u", "late"),
                                submission_id=sid,
                            )
                        fresh = await create_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                        )
                        self.assertEqual(fresh["expires_at"], 5201)
                        self.assertNotEqual(fresh["submission_id"], sid)

    async def test_user_quota_is_atomic_across_sessions(self) -> None:
        """Concurrent issuance cannot overrun the user's aggregate bound."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_USER_MAX_LEN",
                        4,
                    ):
                        results = await asyncio.gather(
                            *(
                                create_chat_submission(bus, "u", str(i), "a")
                                for i in range(5)
                            ),
                            return_exceptions=True,
                        )
                    self.assertEqual(
                        sorted(type(r).__name__ for r in results),
                        ["ChatQueueFullError"] * 3 + ["dict"] * 2,
                    )
                    self.assertEqual(
                        len(
                            await bus.registry_getall(
                                MessageBusKeys.chat_input_pending_registry(),
                            ),
                        ),
                        2,
                    )

    async def test_active_and_parked_inputs_outlive_leases(self) -> None:
        """Only durable completion begins the claimed receipt's retention."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    queue = self.collector(bus)
                    lease = await create_chat_submission(bus, "u", "s", "a")
                    sid = lease["submission_id"]
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "voice"),
                        submission_id=sid,
                    )
                    self.now = 10000
                    await queue._recover_pending_chat_inputs_locked()
                    await queue._claim_chat_input("s")
                    await queue._recover_pending_chat_inputs_locked()
                    queue._chat_service.input_result.assert_not_awaited()
                    await queue.drain_chat_inputs("u", "s", "a")
                    await queue._recover_pending_chat_inputs_locked()
                    key = MessageBusKeys.chat_submissions("s")
                    record = json.loads(await bus.registry_get(key, sid))
                    self.assertNotIn("retire_at", record)
                    queue._chat_service.input_result.return_value = {
                        "state": "finished",
                        "reply_id": "R",
                    }
                    await queue._recover_pending_chat_inputs_locked()
                    record["retire_at"] = 13600
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, sid)),
                        record,
                    )
                    self.now = 13601
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(await bus.registry_getall(key), {})

    async def test_unused_lease_and_partial_deletion_collection(self) -> None:
        """Expiry cannot erase a fence protecting a failed queue removal."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    queue = self.collector(bus)
                    write = bus.registry_set

                    async def lose_lease_response(*args: str) -> None:
                        await write(*args)
                        if args[0] == MessageBusKeys.chat_submissions("s"):
                            raise ConnectionError("lease response lost")

                    with patch.object(
                        bus,
                        "registry_set",
                        side_effect=lose_lease_response,
                    ):
                        with self.assertRaises(ConnectionError):
                            await create_chat_submission(bus, "u", "s", "a")
                    issued = await bus.registry_getall(
                        MessageBusKeys.chat_submissions("s"),
                    )
                    self.assertEqual(len(issued), 1)
                    lease = {"submission_id": next(iter(issued))}
                    self.now = 1601
                    with self.assertRaises(ChatSubmissionConflictError):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "expired"),
                            submission_id=lease["submission_id"],
                        )
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_submissions("s"),
                        ),
                        {},
                    )
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "ordinary"),
                    )
                    with patch.object(
                        bus,
                        "queue_replace",
                        side_effect=ConnectionError,
                    ):
                        with self.assertRaises(ConnectionError):
                            await delete_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                item["id"],
                            )
                    self.now = 10000
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_cancellations("s"),
                        ),
                        {
                            item["id"]: json.dumps(
                                {
                                    "user_id": "u",
                                    "agent_id": "a",
                                    "retire_at": 5201,
                                },
                            ),
                        },
                    )
                    await queue.drain_chat_inputs("u", "s", "a")
                    self.assertEqual(queue._chat_service.calls, [])
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_cancellations("s"),
                        ),
                        {},
                    )
