# -*- coding: utf-8 -*-
# pylint: disable=protected-access,cell-var-from-loop
"""Submission admission and receipt collection preserve cancellation fences."""
import asyncio
import json
from contextlib import ExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import fakeredis.aioredis
from service_message_bus_test import _make_bus
from service_wakeup_dispatcher_test import (
    _FakeBus,
    _FakeChatService,
    _FakeStorage,
)

from agentscope.app._bus_ops import (
    bind_chat_submission,
    ChatQueueFullError,
    ChatSubmissionConflictError,
    cancel_chat_submission,
    create_chat_submission,
    delete_chat_input,
    enqueue_chat_input,
    list_chat_inputs,
)
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._chat_input_store import cleanup_session
from agentscope.app._chat_receipts import (
    ReceiptProbes,
    reap_receipts,
    receipt_count,
    track_receipts,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import UserMsg


class ChatReceiptLifecycleTest(IsolatedAsyncioTestCase):
    """Use a controlled clock on the in-memory and Redis implementations."""

    # pylint: disable=too-many-public-methods

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

    async def test_chat_admission_shares_queue_reads(self) -> None:
        """Both quotas share one read of each indexed queue."""
        async with InMemoryMessageBus() as bus:
            await create_chat_submission(bus, "u", "other", "a")
            with patch.object(
                bus,
                "queue_read",
                wraps=bus.queue_read,
            ) as read:
                await enqueue_chat_input(
                    bus,
                    "u",
                    "new",
                    "a",
                    UserMsg("u", "hello"),
                )
            self.assertEqual(
                [call.args[0] for call in read.await_args_list],
                [
                    MessageBusKeys.chat_inputs("other"),
                    MessageBusKeys.chat_inputs("new"),
                    # Build the post-enqueue queue-changed notification.
                    MessageBusKeys.chat_inputs("new"),
                ],
            )

    async def test_chat_admission_bounds_concurrent_reads(self) -> None:
        """Overlap other FIFO reads, then read the target on its own."""
        async with InMemoryMessageBus() as bus:
            sessions = MessageBusKeys.chat_input_user_sessions("u")
            for i in range(17):
                await bus.registry_set(sessions, str(i), "a")
            await bus.registry_set(sessions, "target", "a")
            read = bus.queue_read
            active = peak = 0
            target_overlap = []

            async def traced_read(
                key: str,
                **kwargs: int,
            ) -> list[tuple[str, dict]]:
                nonlocal active, peak
                if key == MessageBusKeys.chat_inputs("target"):
                    target_overlap.append(active)
                active += 1
                peak = max(peak, active)
                try:
                    # Yield once so every read in this batch can start.
                    await asyncio.sleep(0)
                    return await read(key, **kwargs)
                finally:
                    active -= 1

            with patch.object(bus, "queue_read", traced_read):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "target",
                    "a",
                    UserMsg("u", "hello"),
                )
            self.assertEqual((peak, target_overlap), (8, [0, 0]))

    async def test_quota_read_failure_prevents_enqueue(self) -> None:
        """An unreadable session cannot be treated as unused capacity."""
        async with InMemoryMessageBus() as bus:
            await create_chat_submission(bus, "u", "other", "a")
            with patch.object(
                bus,
                "registry_get",
                side_effect=OSError("quota read failed"),
            ), self.assertRaisesRegex(OSError, "quota read failed"):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "target",
                    "a",
                    UserMsg("u", "hello"),
                )
            self.assertEqual(await list_chat_inputs(bus, "target"), [])

    async def test_last_pending_slot_is_atomic_across_sessions(self) -> None:
        """Only one ordinary send can take the last pending slot."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "seed",
                        "a",
                        UserMsg("u", "first"),
                    )
                    targets = ["one", "two"]
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_INPUT_USER_MAX_LEN",
                        2,
                    ):
                        results = await asyncio.gather(
                            *(
                                enqueue_chat_input(
                                    bus,
                                    "u",
                                    sid,
                                    "a",
                                    UserMsg("u", sid),
                                )
                                for sid in targets
                            ),
                            return_exceptions=True,
                        )
                    self.assertCountEqual(
                        [type(result) for result in results],
                        [dict, ChatQueueFullError],
                    )
                    for sid, result in zip(targets, results):
                        self.assertEqual(
                            await list_chat_inputs(bus, sid),
                            [result] if isinstance(result, dict) else [],
                        )

    async def test_legacy_receipt_stops_at_unfinished_input(self) -> None:
        """Only a fully finished batch starts its retention period."""
        key = MessageBusKeys.chat_submissions("s")
        record = {"state": "claimed", "message_ids": ["m1", "m2", "m3"]}
        async with InMemoryMessageBus() as bus:
            await bus.registry_set(key, "batch", json.dumps(record))
            for states in (
                ["reply"],
                ["finished", "unknown"],
                ["finished"] * 3,
            ):
                with self.subTest(states=states):
                    lookup = AsyncMock(
                        side_effect=[{"state": s} for s in states],
                    )
                    await reap_receipts(
                        bus,
                        "u",
                        "s",
                        "a",
                        lookup,
                        set(),
                        ReceiptProbes(),
                    )
                    self.assertEqual(
                        [call.args for call in lookup.await_args_list],
                        [
                            ("u", "s", "a", mid)
                            for mid in record["message_ids"][: len(states)]
                        ],
                    )
                    expected = dict(record)
                    if all(state == "finished" for state in states):
                        expected["retire_at"] = (
                            self.now
                            + MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS
                        )
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, "batch")),
                        expected,
                    )

    async def test_idle_sessions_add_no_bus_round_trips(self) -> None:
        """Idle receipts add no per-session recovery or admission calls."""
        reads = []
        sweep_reads = [
            call(MessageBusKeys.chat_input_pending_registry()),
            call(MessageBusKeys.chat_input_inflight_registry()),
        ]
        read_methods = (
            "registry_getall",
            "registry_get",
            "queue_read",
            "registry_get_many",
        )
        for size in (0, 20, 100):
            async with InMemoryMessageBus() as bus:
                for i in range(size):
                    sid = str(i)
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        sid,
                        "a",
                        UserMsg("u", "cancel me"),
                    )
                    await delete_chat_input(bus, "u", sid, "a", item["id"])
                with ExitStack() as stack:
                    spies = {
                        name: stack.enter_context(
                            patch.object(bus, name, wraps=getattr(bus, name)),
                        )
                        for name in (
                            *read_methods,
                            "registry_exists",
                            "registry_drop",
                            "registry_set",
                            "registry_set_if",
                            "registry_del",
                            "queue_push",
                            "publish",
                        )
                    }
                    for _ in range(2):
                        queue = self.collector(bus)
                        recover = queue._recover_pending_chat_inputs_locked
                        self.assertEqual(await recover(), 0)
                        self.assertEqual(
                            {
                                k: s.await_args_list
                                for k, s in spies.items()
                                if s.await_count
                            },
                            {"registry_getall": sweep_reads},
                        )
                        for spy in spies.values():
                            spy.reset_mock()
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "new",
                        "a",
                        UserMsg("u", "hello"),
                    )
                    reads.append(
                        [
                            [c.args[0] for c in spies[name].await_args_list]
                            for name in read_methods
                        ],
                    )
        self.assertEqual(reads, [reads[0]] * 3)

    async def test_idle_receipt_quota_survives_reactivation(self) -> None:
        """Count idle and live entries once through retries and collection."""
        for bus in self.backends:
            self.now = 1000
            async with bus:
                queue = self.collector(bus)
                with patch.object(
                    MessageBusKeys,
                    "CHAT_RECEIPT_USER_MAX_LEN",
                    3,
                ):
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "one"),
                    )
                    for _ in range(2):
                        await delete_chat_input(bus, "u", "s", "a", item["id"])
                    second = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "two"),
                    )
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "new",
                        "a",
                        UserMsg("u", "last slot"),
                    )
                    with self.assertRaises(ChatQueueFullError):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            "new",
                            "a",
                            UserMsg("u", "full"),
                        )
                    self.assertEqual(
                        await queue._recover_pending_chat_inputs_locked(),
                        2,
                    )
                    self.now += 10
                    await delete_chat_input(bus, "u", "s", "a", second["id"])
                    with self.assertRaises(ChatQueueFullError):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            "new",
                            "a",
                            UserMsg("u", "full"),
                        )
                    self.now += MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS - 10
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_idle_receipt_counts("u"),
                        ),
                        {"s": "1"},
                    )
                    self.now += 10
                    await queue._recover_pending_chat_inputs_locked()
                    lease = await create_chat_submission(bus, "u", "new", "a")
                    self.assertEqual(
                        lease["expires_at"],
                        self.now + MessageBusKeys.CHAT_SUBMISSION_LEASE_SECS,
                    )

    async def test_receipt_only_sweep_preserves_leases(self) -> None:
        """Clean orphan payloads once and defer the lease until expiry."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    queue = self.collector(bus)
                    await create_chat_submission(bus, "u", "s", "a")
                    payloads = MessageBusKeys.chat_input_payloads("s")
                    await bus.registry_set(payloads, "orphan", "data")
                    with patch.object(
                        bus,
                        "queue_read",
                        wraps=bus.queue_read,
                    ) as read:
                        recover = queue._recover_pending_chat_inputs_locked
                        for _ in range(2):
                            self.assertEqual(await recover(), 0)
                    key = MessageBusKeys.chat_inputs("s")
                    self.assertEqual(
                        read.await_args_list,
                        [call(key, max_count=1), call(key)],
                    )
                    self.assertEqual(await bus.registry_getall(payloads), {})
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_idle_receipt_counts("u"),
                        ),
                        {"s": "2"},
                    )

    async def test_enqueue_between_recovery_and_collection(self) -> None:
        """Cleanup must preserve inputs enqueued after the wakeup scan."""
        async with InMemoryMessageBus() as bus:
            queue = self.collector(bus)
            await enqueue_chat_input(bus, "u", "busy", "a", UserMsg("u", "1"))
            await track_receipts(bus, "u", "empty", "a")
            accepted = []
            publish = bus.publish

            async def enqueue_on_signal(key: str, payload: dict) -> None:
                await publish(key, payload)
                if key == MessageBusKeys.wakeup_signal():
                    with patch.object(bus, "publish", publish):
                        item = await enqueue_chat_input(
                            bus,
                            "u",
                            "empty",
                            "a",
                            UserMsg("u", "new input"),
                        )
                    accepted.append(item)

            with patch.object(bus, "publish", enqueue_on_signal):
                self.assertEqual(
                    await queue._recover_pending_chat_inputs_locked(),
                    1,
                )
            self.assertEqual(len(accepted), 1)
            self.assertEqual(await list_chat_inputs(bus, "empty"), accepted)
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_user_sessions("u"),
                ),
                {"busy": "a", "empty": "a"},
            )

    async def test_cleanup_preserves_indexes_until_last_receipt(self) -> None:
        """Check both receipt registries, including on a legacy bus."""
        legacy = _FakeBus()
        for bus in (*self.backends, legacy):
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await track_receipts(bus, "u", "s", "a")
                    submissions = MessageBusKeys.chat_submissions("s")
                    cancels = MessageBusKeys.chat_input_cancellations("s")
                    pending = MessageBusKeys.chat_input_pending_registry()
                    sessions = MessageBusKeys.chat_input_user_sessions("u")
                    routing = {"user_id": "u", "agent_id": "a"}
                    if bus is not legacy:
                        routing["receipt_check_at"] = 2000
                    counts = MessageBusKeys.chat_idle_receipt_counts("u")
                    for namespace in (submissions, cancels, None):
                        if namespace:
                            await bus.registry_set(
                                namespace,
                                "one",
                                json.dumps({"retire_at": 2000}),
                            )
                        await cleanup_session(bus, "s")
                        self.assertEqual(
                            (
                                await bus.registry_get(pending, "s"),
                                await bus.registry_get(sessions, "s"),
                                await bus.registry_get(counts, "s"),
                            ),
                            (json.dumps(routing), None, "1")
                            if namespace
                            else (None, None, None),
                        )
                        if namespace:
                            await bus.registry_del(namespace, "one")

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
                    wakeups_before_lookup = []
                    timeouts: list[asyncio.Timeout] = []
                    configured_timeout = (
                        MessageBusKeys.CHAT_RECEIPT_COLLECTION_TIMEOUT_SECS
                    )

                    def collection_timeout(delay: float) -> asyncio.Timeout:
                        self.assertEqual(delay, configured_timeout)
                        timeout = asyncio.timeout(None)
                        timeouts.append(timeout)
                        return timeout

                    async def hung(*_args: object) -> dict:
                        wakeups_before_lookup.extend(publish.await_args_list)
                        # Expire after Redis setup, without a tiny clock race.
                        timeouts[-1].reschedule(
                            asyncio.get_running_loop().time(),
                        )
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()
                        return {}

                    with patch.object(
                        queue,
                        "_receipt_input_result",
                        side_effect=hung,
                    ), patch(
                        "agentscope.app._manager._chat_input_queue.asyncio",
                        wraps=asyncio,
                    ) as runtime, patch.object(
                        bus,
                        "publish",
                        wraps=bus.publish,
                    ) as publish:
                        runtime.timeout.side_effect = collection_timeout
                        await asyncio.wait_for(
                            queue._recover_pending_chat_inputs(),
                            10,
                        )
                        publish.assert_awaited_once_with(
                            MessageBusKeys.wakeup_signal(),
                            {},
                        )
                        self.assertEqual(
                            wakeups_before_lookup,
                            publish.await_args_list,
                        )
                    self.assertEqual(len(timeouts), 2)
                    self.assertEqual(sum(t.expired() for t in timeouts), 1)
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

    async def test_slow_terminal_query_releases_receipt_quota(self) -> None:
        """A storage read that takes 250 ms can outlive the sweep lock."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    queue = self.collector(bus)
                    await track_receipts(bus, "u", "s", "a")
                    namespace = MessageBusKeys.chat_submissions("s")
                    record = {"state": "claimed", "message_ids": ["m"]}
                    await bus.registry_set(
                        namespace,
                        "old",
                        json.dumps(record),
                    )
                    finished = asyncio.Event()

                    async def lookup(*_args: str) -> dict:
                        await asyncio.sleep(0.25)
                        finished.set()
                        return {"state": "finished"}

                    with patch.object(
                        queue,
                        "_receipt_input_result",
                        side_effect=lookup,
                    ) as query:
                        await queue._recover_pending_chat_inputs_locked()
                        self.assertFalse(
                            await bus.is_locked(
                                MessageBusKeys.chat_input_mutation_lock("s"),
                            ),
                        )
                        await asyncio.wait_for(finished.wait(), 2)
                        await queue._recover_pending_chat_inputs_locked()
                        self.assertEqual(
                            json.loads(
                                await bus.registry_get(namespace, "old"),
                            ),
                            {**record, "retire_at": 4600},
                        )
                        self.now = 4601
                        await queue._recover_pending_chat_inputs_locked()
                        query.assert_awaited_once_with("u", "s", "a", "m")
                    self.assertEqual(await receipt_count(bus, "s"), 0)
                    with patch.object(
                        MessageBusKeys,
                        "CHAT_RECEIPT_SESSION_MAX_LEN",
                        1,
                    ):
                        await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "text"),
                        )

    async def test_slow_queries_do_not_starve_later_receipts(self) -> None:
        """Cheap expiry and query rotation survive either timeout budget."""
        for bus in self.backends:
            for budget in ("query", "sweep"):
                with self.subTest(backend=type(bus).__name__, budget=budget):
                    self.now = 1000
                    async with bus:
                        queue = self.collector(bus)
                        await create_chat_submission(bus, "u", "s", "a")
                        namespace = MessageBusKeys.chat_submissions("s")
                        await bus.registry_drop(namespace)
                        slow = {
                            "state": "claimed",
                            "queue_item_id": "slow",
                            "message_ids": ["slow"],
                        }
                        fast = {
                            "state": "claimed",
                            "queue_item_id": "fast",
                            "message_ids": ["fast"],
                        }
                        for key, record in (
                            ("a-slow", slow),
                            ("b-fast", fast),
                            (
                                "expired",
                                {"state": "reserved", "expires_at": 0},
                            ),
                        ):
                            await bus.registry_set(
                                namespace,
                                key,
                                json.dumps(record),
                            )
                        cancellations = (
                            MessageBusKeys.chat_input_cancellations(
                                "s",
                            )
                        )
                        await bus.registry_set(
                            cancellations,
                            "expired",
                            json.dumps({"retire_at": 0}),
                        )
                        deadlines = {}
                        queried = []
                        sweep_budget = (
                            MessageBusKeys.CHAT_RECEIPT_COLLECTION_TIMEOUT_SECS
                        )
                        recover = queue._recover_pending_chat_inputs_locked

                        def query_timeout(delay: float) -> asyncio.Timeout:
                            self.assertEqual(
                                delay,
                                MessageBusKeys.CHAT_RECEIPT_QUERY_TIMEOUT_SECS,
                            )
                            deadlines["query"] = asyncio.timeout(None)
                            return deadlines["query"]

                        def sweep_timeout(delay: float) -> asyncio.Timeout:
                            self.assertEqual(delay, sweep_budget)
                            deadlines["sweep"] = asyncio.timeout(None)
                            return deadlines["sweep"]

                        async def lookup(*args: object) -> dict:
                            self.assertIsNone(
                                await bus.registry_get(namespace, "expired"),
                            )
                            self.assertEqual(
                                await bus.registry_getall(cancellations),
                                {},
                            )
                            self.assertEqual(
                                await bus.registry_getall(
                                    MessageBusKeys.chat_idle_receipt_counts(
                                        "u",
                                    ),
                                ),
                                {"s": "2" if self.now == 1000 else "1"},
                            )
                            queried.append(args[-1])
                            if args[-1] == "slow":
                                deadlines[budget].reschedule(
                                    asyncio.get_running_loop().time(),
                                )
                                await asyncio.Event().wait()
                            return {"state": "finished"}

                        with patch.object(
                            queue,
                            "_receipt_input_result",
                            side_effect=lookup,
                        ), patch(
                            "agentscope.app._chat_receipts.asyncio",
                            wraps=asyncio,
                        ) as query_runtime, patch(
                            "agentscope.app._manager."
                            "_chat_input_queue.asyncio",
                            wraps=asyncio,
                        ) as sweep_runtime:
                            query_runtime.timeout.side_effect = query_timeout
                            sweep_runtime.timeout.side_effect = sweep_timeout
                            for _ in range(2):
                                await recover()
                            self.assertEqual(
                                queried,
                                ["slow", "fast"]
                                if budget == "query"
                                else ["slow", "fast", "slow"],
                            )
                            self.assertEqual(
                                {
                                    key: json.loads(value)
                                    for key, value in (
                                        await bus.registry_getall(namespace)
                                    ).items()
                                },
                                {
                                    "a-slow": slow,
                                    "b-fast": {
                                        **fast,
                                        "retire_at": 4600,
                                    },
                                },
                            )
                            self.now = 4601
                            await recover()
                            self.assertEqual(
                                await bus.registry_getall(namespace),
                                {"a-slow": json.dumps(slow)},
                            )
                            self.assertEqual(await receipt_count(bus, "s"), 1)
                            with patch.object(
                                MessageBusKeys,
                                "CHAT_RECEIPT_SESSION_MAX_LEN",
                                3,
                            ):
                                await create_chat_submission(
                                    bus,
                                    "u",
                                    "s",
                                    "a",
                                )
                            self.assertEqual(await receipt_count(bus, "s"), 3)

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
                                    "before_claim": True,
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
                                "before_claim": True,
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
                            None,
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
                                "before_claim": True,
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

                    async def bind_reply(**_: object) -> None:
                        await bind_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            sid,
                            "R",
                        )

                    queue._chat_service.run = AsyncMock(
                        side_effect=bind_reply,
                    )
                    inputs = [UserMsg("u", text) for text in ("a", "b", "c")]
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        inputs,
                        submission_id=sid,
                    )
                    self.now = 10000
                    await queue._recover_pending_chat_inputs_locked()
                    await queue._claim_chat_input("s")
                    # An in-flight claim takes precedence over an idle route.
                    await bus.registry_set(
                        MessageBusKeys.chat_input_pending_registry(),
                        "s",
                        json.dumps(
                            {
                                "user_id": "u",
                                "agent_id": "a",
                                "receipt_check_at": self.now + 3600,
                            },
                        ),
                    )
                    self.assertEqual(
                        await queue._recover_pending_chat_inputs_locked(),
                        1,
                    )
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
                    lookup = queue._chat_service.input_result
                    lookup.reset_mock()
                    await queue._recover_pending_chat_inputs_locked()
                    lookup.assert_awaited_once_with(
                        "u",
                        "s",
                        "a",
                        inputs[0].id,
                        submission_id=sid,
                    )
                    record["retire_at"] = 13600
                    self.assertEqual(
                        json.loads(await bus.registry_get(key, sid)),
                        record,
                    )
                    self.now = 13601
                    await queue._recover_pending_chat_inputs_locked()
                    self.assertEqual(await bus.registry_getall(key), {})

    async def test_deleted_submission_expires_with_queue_receipt(self) -> None:
        """Late recovery cannot give a deleted submission a new lifetime."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                self.now = 1000
                async with bus:
                    submission = await create_chat_submission(
                        bus,
                        "u",
                        "s",
                        "a",
                    )
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "voice"),
                        submission_id=submission["submission_id"],
                    )
                    await delete_chat_input(bus, "u", "s", "a", item["id"])
                    self.now += MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS + 1
                    collector = self.collector(bus)
                    await collector._recover_pending_chat_inputs_locked()
                    self.assertEqual(
                        [
                            await bus.registry_getall(namespace)
                            for namespace in (
                                MessageBusKeys.chat_submissions("s"),
                                MessageBusKeys.chat_input_cancellations("s"),
                            )
                        ],
                        [{}, {}],
                    )

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
