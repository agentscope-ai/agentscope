# -*- coding: utf-8 -*-
"""Unit tests for RedisMessageBus using fakeredis."""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app import RedisMessageBus


def make_bus() -> RedisMessageBus:
    """Create a RedisMessageBus instance backed by fakeredis."""
    bus = RedisMessageBus.__new__(RedisMessageBus)
    # pylint: disable=protected-access
    bus._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus._owned_pool = None
    return bus


class TestQueue(IsolatedAsyncioTestCase):
    """Tests for Mode A — drain queue."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = make_bus()
        self.key = "queue:test"

    async def test_push_then_drain_round_trips(self) -> None:
        """A pushed dict comes back from drain unchanged."""
        entry_id = await self.bus.queue_push(self.key, {"text": "hello"})
        self.assertIsInstance(entry_id, str)
        self.assertGreater(len(entry_id), 0)

        results = await self.bus.queue_drain(self.key)
        self.assertEqual(len(results), 1)
        ret_id, payload = results[0]
        self.assertEqual(ret_id, entry_id)
        self.assertEqual(payload, {"text": "hello"})

    async def test_drain_is_destructive(self) -> None:
        """A second drain returns nothing after the first one drained."""
        await self.bus.queue_push(self.key, {"x": 1})
        first = await self.bus.queue_drain(self.key)
        self.assertEqual(len(first), 1)
        second = await self.bus.queue_drain(self.key)
        self.assertEqual(second, [])

    async def test_drain_empty_queue(self) -> None:
        """queue_drain on an absent key returns []."""
        results = await self.bus.queue_drain("queue:absent")
        self.assertEqual(results, [])

    async def test_drain_preserves_arrival_order(self) -> None:
        """Entries are returned in push order."""
        for i in range(5):
            await self.bus.queue_push(self.key, {"i": i})
        results = await self.bus.queue_drain(self.key)
        self.assertEqual([p["i"] for _id, p in results], [0, 1, 2, 3, 4])

    async def test_max_count_caps_batch(self) -> None:
        """max_count limits the batch and leaves the rest."""
        for i in range(5):
            await self.bus.queue_push(self.key, {"i": i})
        first = await self.bus.queue_drain(self.key, max_count=2)
        self.assertEqual([p["i"] for _id, p in first], [0, 1])
        rest = await self.bus.queue_drain(self.key)
        self.assertEqual([p["i"] for _id, p in rest], [2, 3, 4])

    async def test_key_isolation(self) -> None:
        """Queues at different keys do not leak."""
        await self.bus.queue_push("queue:A", {"x": "a"})
        await self.bus.queue_push("queue:B", {"x": "b"})
        a = await self.bus.queue_drain("queue:A")
        b = await self.bus.queue_drain("queue:B")
        self.assertEqual([p for _id, p in a], [{"x": "a"}])
        self.assertEqual([p for _id, p in b], [{"x": "b"}])

    async def test_ttl_sets_expiry(self) -> None:
        """ttl_secs sets a TTL on the key (sliding on each push)."""
        await self.bus.queue_push(self.key, {"x": 1}, ttl_secs=60)
        # pylint: disable=protected-access
        ttl = await self.bus._client.ttl(self.key)
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, 60)

    async def test_no_ttl_by_default(self) -> None:
        """Without ttl_secs the key has no expiry (-1)."""
        await self.bus.queue_push(self.key, {"x": 1})
        # pylint: disable=protected-access
        ttl = await self.bus._client.ttl(self.key)
        self.assertEqual(ttl, -1)


class TestLog(IsolatedAsyncioTestCase):
    """Tests for Mode C — replay log."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = make_bus()
        self.key = "log:test"

    async def test_append_then_read_round_trips(self) -> None:
        """An appended dict comes back from log_read unchanged."""
        entry_id = await self.bus.log_append(self.key, {"a": 1})
        results = await self.bus.log_read(self.key)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], entry_id)
        self.assertEqual(results[0][1], {"a": 1})

    async def test_read_is_non_destructive(self) -> None:
        """Two reads return the same entries — log persists."""
        await self.bus.log_append(self.key, {"a": 1})
        first = await self.bus.log_read(self.key)
        second = await self.bus.log_read(self.key)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)

    async def test_read_since_cursor(self) -> None:
        """log_read(since=id) returns only entries strictly newer."""
        id1 = await self.bus.log_append(self.key, {"i": 1})
        id2 = await self.bus.log_append(self.key, {"i": 2})
        id3 = await self.bus.log_append(self.key, {"i": 3})

        after_first = await self.bus.log_read(self.key, since=id1)
        self.assertEqual([rid for rid, _p in after_first], [id2, id3])

        after_last = await self.bus.log_read(self.key, since=id3)
        self.assertEqual(after_last, [])

    async def test_max_count_caps_batch(self) -> None:
        """max_count limits the batch but does not affect persistence."""
        for i in range(5):
            await self.bus.log_append(self.key, {"i": i})
        first = await self.bus.log_read(self.key, max_count=2)
        self.assertEqual([p["i"] for _id, p in first], [0, 1])
        # Re-read confirms persistence.
        again = await self.bus.log_read(self.key, max_count=2)
        self.assertEqual([p["i"] for _id, p in again], [0, 1])

    async def test_log_trim_with_before_id_drops_older(self) -> None:
        """log_trim(before_id=X) drops entries older than X."""
        ids = [await self.bus.log_append(self.key, {"i": i}) for i in range(5)]
        await self.bus.log_trim(self.key, before_id=ids[2])
        results = await self.bus.log_read(self.key)
        self.assertEqual([p["i"] for _id, p in results], [2, 3, 4])

    async def test_log_trim_without_before_id_deletes_log(self) -> None:
        """log_trim(before_id=None) deletes the entire log."""
        await self.bus.log_append(self.key, {"a": 1})
        await self.bus.log_trim(self.key)
        results = await self.bus.log_read(self.key)
        self.assertEqual(results, [])

    async def test_max_len_caps_log_size(self) -> None:
        """max_len keeps approximately N most recent entries."""
        for i in range(10):
            await self.bus.log_append(self.key, {"i": i}, max_len=3)
        results = await self.bus.log_read(self.key)
        # Approximate cap; we just check we did not keep all 10 entries
        # and we kept the latest one.
        self.assertLessEqual(len(results), 5)
        self.assertEqual(results[-1][1]["i"], 9)

    async def test_ttl_sets_expiry(self) -> None:
        """ttl_secs sets a sliding TTL on the log key."""
        await self.bus.log_append(self.key, {"a": 1}, ttl_secs=60)
        # pylint: disable=protected-access
        ttl = await self.bus._client.ttl(self.key)
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, 60)


class TestBroadcast(IsolatedAsyncioTestCase):
    """Tests for Mode D — transient broadcast."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = make_bus()

    async def test_publish_subscribe_roundtrip(self) -> None:
        """A subscriber sees payloads published after subscribing."""
        received: list[dict] = []
        gen = self.bus.subscribe("ch-1")

        async def consume() -> None:
            async for payload in gen:
                received.append(payload)
                if len(received) >= 2:
                    break

        consumer_task = asyncio.create_task(consume())
        # Yield a few times so the consumer registers the SUBSCRIBE
        # before publishes happen.
        for _ in range(5):
            await asyncio.sleep(0)

        await self.bus.publish("ch-1", {"reason": "first"})
        await self.bus.publish("ch-1", {"reason": "second"})

        await asyncio.wait_for(consumer_task, timeout=2.0)
        await gen.aclose()

        self.assertEqual(received, [{"reason": "first"}, {"reason": "second"}])

    async def test_channel_isolation(self) -> None:
        """A subscriber does not receive payloads from other channels."""
        received: list[dict] = []
        gen = self.bus.subscribe("ch-A")

        async def consume() -> None:
            async for payload in gen:
                received.append(payload)
                if received:
                    break

        consumer_task = asyncio.create_task(consume())
        for _ in range(5):
            await asyncio.sleep(0)

        await self.bus.publish("ch-B", {"x": 1})
        await self.bus.publish("ch-A", {"x": 2})

        await asyncio.wait_for(consumer_task, timeout=2.0)
        await gen.aclose()

        self.assertEqual(received, [{"x": 2}])
