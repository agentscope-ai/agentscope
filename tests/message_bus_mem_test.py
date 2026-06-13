# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MemMessageBus."""

import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app.message_bus._mem_message_bus import MemMessageBus


class TestLifecycle(IsolatedAsyncioTestCase):
    """Tests for bus lifecycle: create, context manager, close."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_context_manager(self) -> None:
        """Bus can be used as an async context manager."""
        async with MemMessageBus() as bus:
            self.assertIsInstance(bus, MemMessageBus)
            # push something to verify it's alive
            await bus.queue_push("k", {"v": 1})

    async def test_aclose_clears_all_state(self) -> None:
        """aclose clears all internal data structures."""
        await self.bus.queue_push("q", {"x": 1})
        await self.bus.log_append("l", {"y": 2})
        await self.bus.registry_set("ns", "f", "val")
        await self.bus.aclose()
        self.assertEqual(self.bus._queues, {})
        self.assertEqual(self.bus._logs, {})
        self.assertEqual(self.bus._log_counters, {})
        self.assertEqual(self.bus._channels, {})
        self.assertEqual(self.bus._locks, {})
        self.assertEqual(self.bus._registries, {})


class TestDrainQueue(IsolatedAsyncioTestCase):
    """Tests for Mode A — drain queue operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_push_returns_entry_id(self) -> None:
        """queue_push returns a string entry id."""
        eid = await self.bus.queue_push("key-a", {"msg": "hello"})
        self.assertIsInstance(eid, str)
        self.assertTrue(len(eid) > 0)

    async def test_drain_returns_pushed_entry(self) -> None:
        """queue_drain returns the entry previously pushed."""
        await self.bus.queue_push("key-b", {"msg": "hello"})
        entries = await self.bus.queue_drain("key-b")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], {"msg": "hello"})

    async def test_drain_removes_entries(self) -> None:
        """After drain, the same entry cannot be read again."""
        await self.bus.queue_push("key-c", {"msg": "once"})
        _ = await self.bus.queue_drain("key-c")
        entries = await self.bus.queue_drain("key-c")
        self.assertEqual(entries, [])

    async def test_drain_empty_queue_returns_empty(self) -> None:
        """queue_drain on a non-existent key returns an empty list."""
        entries = await self.bus.queue_drain("no-such-key")
        self.assertEqual(entries, [])

    async def test_drain_respects_max_count(self) -> None:
        """queue_drain returns at most max_count entries."""
        for i in range(5):
            await self.bus.queue_push("key-d", {"i": i})
        entries = await self.bus.queue_drain("key-d", max_count=3)
        self.assertEqual(len(entries), 3)

    async def test_drain_fifo_order(self) -> None:
        """queue_drain returns entries in FIFO order."""
        await self.bus.queue_push("key-e", {"seq": 1})
        await self.bus.queue_push("key-e", {"seq": 2})
        await self.bus.queue_push("key-e", {"seq": 3})
        entries = await self.bus.queue_drain("key-e")
        self.assertEqual(
            [e[1]["seq"] for e in entries],
            [1, 2, 3],
        )

    async def test_queue_delete_removes_key(self) -> None:
        """queue_delete removes the queue entirely."""
        await self.bus.queue_push("key-f", {"x": 1})
        await self.bus.queue_delete("key-f")
        entries = await self.bus.queue_drain("key-f")
        self.assertEqual(entries, [])

    async def test_ttl_is_ignored(self) -> None:
        """ttl_secs is accepted but has no effect on in-memory queues."""
        eid = await self.bus.queue_push("key-g", {"x": 1}, ttl_secs=60)
        self.assertIsInstance(eid, str)
        # No expiry in memory — entry still present after "time passes"
        await asyncio.sleep(0.01)
        entries = await self.bus.queue_drain("key-g")
        self.assertEqual(len(entries), 1)


class TestReplayLog(IsolatedAsyncioTestCase):
    """Tests for Mode C — replay log operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_log_append_returns_entry_id(self) -> None:
        """log_append returns a monotonic integer as string."""
        eid = await self.bus.log_append("log-a", {"msg": "hello"})
        self.assertEqual(eid, "0")

    async def test_log_append_ids_are_monotonic(self) -> None:
        """Each log_append increments the entry id."""
        eid1 = await self.bus.log_append("log-b", {"n": 1})
        eid2 = await self.bus.log_append("log-b", {"n": 2})
        eid3 = await self.bus.log_append("log-b", {"n": 3})
        self.assertEqual(eid1, "0")
        self.assertEqual(eid2, "1")
        self.assertEqual(eid3, "2")

    async def test_log_read_returns_all_entries(self) -> None:
        """log_read without since returns all entries."""
        await self.bus.log_append("log-c", {"n": 1})
        await self.bus.log_append("log-c", {"n": 2})
        entries = await self.bus.log_read("log-c")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][1], {"n": 1})
        self.assertEqual(entries[1][1], {"n": 2})

    async def test_log_read_empty_log_returns_empty(self) -> None:
        """log_read on a non-existent key returns an empty list."""
        entries = await self.bus.log_read("no-such-log")
        self.assertEqual(entries, [])

    async def test_log_read_since_filters_older(self) -> None:
        """log_read with since returns only newer entries."""
        await self.bus.log_append("log-d", {"n": 1})
        await self.bus.log_append("log-d", {"n": 2})
        await self.bus.log_append("log-d", {"n": 3})
        entries = await self.bus.log_read("log-d", since="0")
        self.assertEqual(len(entries), 2)
        self.assertEqual([e[1]["n"] for e in entries], [2, 3])

    async def test_log_read_respects_max_count(self) -> None:
        """log_read returns at most max_count entries."""
        for i in range(5):
            await self.bus.log_append("log-e", {"i": i})
        entries = await self.bus.log_read("log-e", max_count=3)
        self.assertEqual(len(entries), 3)

    async def test_log_trim_before_id_removes_older(self) -> None:
        """log_trim with before_id prunes older entries."""
        await self.bus.log_append("log-f", {"n": 1})
        await self.bus.log_append("log-f", {"n": 2})
        await self.bus.log_append("log-f", {"n": 3})
        await self.bus.log_trim("log-f", before_id="1")
        entries = await self.bus.log_read("log-f")
        self.assertEqual(len(entries), 2)
        self.assertEqual([e[1]["n"] for e in entries], [2, 3])

    async def test_log_trim_none_drops_entire_log(self) -> None:
        """log_trim with before_id=None drops the entire log."""
        await self.bus.log_append("log-g", {"n": 1})
        await self.bus.log_trim("log-g", before_id=None)
        entries = await self.bus.log_read("log-g")
        self.assertEqual(entries, [])

    async def test_log_trim_nonexistent_key_noop(self) -> None:
        """log_trim on non-existent key does not raise."""
        await self.bus.log_trim("no-such-log", before_id="0")

    async def test_max_len_caps_log_size(self) -> None:
        """max_len removes oldest entries when exceeded."""
        for i in range(5):
            await self.bus.log_append("log-h", {"i": i}, max_len=3)
        entries = await self.bus.log_read("log-h")
        self.assertEqual(len(entries), 3)
        # Only the 3 most recent entries survive
        self.assertEqual([e[1]["i"] for e in entries], [2, 3, 4])

    async def test_log_read_is_non_destructive(self) -> None:
        """Reading the replay log does not remove entries."""
        await self.bus.log_append("log-i", {"n": 1})
        _ = await self.bus.log_read("log-i")
        entries = await self.bus.log_read("log-i")
        self.assertEqual(len(entries), 1)

    async def test_ttl_is_ignored(self) -> None:
        """ttl_secs is accepted but has no effect on in-memory logs."""
        eid = await self.bus.log_append("log-j", {"x": 1}, ttl_secs=60)
        self.assertIsInstance(eid, str)
        await asyncio.sleep(0.01)
        entries = await self.bus.log_read("log-j")
        self.assertEqual(len(entries), 1)


class TestBroadcast(IsolatedAsyncioTestCase):
    """Tests for Mode D — transient broadcast."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_subscribe_receives_published(self) -> None:
        """A subscriber receives published payloads."""
        received: list[dict] = []
        ready = asyncio.Event()

        async def _collect() -> None:
            async for payload in self.bus.subscribe("chan-a", on_ready=ready.set):
                received.append(payload)
                if len(received) >= 2:
                    break

        task = asyncio.create_task(_collect())
        await ready.wait()
        await self.bus.publish("chan-a", {"msg": "hello"})
        await self.bus.publish("chan-a", {"msg": "world"})
        await task
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0], {"msg": "hello"})
        self.assertEqual(received[1], {"msg": "world"})

    async def test_publish_no_subscribers_noop(self) -> None:
        """publish to a channel with no subscribers does not raise."""
        await self.bus.publish("empty-chan", {"msg": "nobody"})
        # Should not raise or block

    async def test_unsubscribe_on_generator_close(self) -> None:
        """When a subscriber generator closes, previously published
        messages are received."""
        received: list[dict] = []
        ready = asyncio.Event()

        async def _consume() -> None:
            async for payload in self.bus.subscribe("chan-b", on_ready=ready.set):
                received.append(payload)
                break

        task = asyncio.create_task(_consume())
        await ready.wait()
        await self.bus.publish("chan-b", {"msg": "release"})
        await task
        self.assertEqual(received, [{"msg": "release"}])

    async def test_on_ready_callback(self) -> None:
        """on_ready is called after subscription is established and
        messages are received."""
        ready_called = False
        received: list[dict] = []

        def _on_ready() -> None:
            nonlocal ready_called
            ready_called = True

        async def _drain() -> None:
            async for payload in self.bus.subscribe("chan-c", on_ready=_on_ready):
                received.append(payload)
                break

        task = asyncio.create_task(_drain())
        await asyncio.sleep(0.01)
        self.assertTrue(ready_called)
        await self.bus.publish("chan-c", {"msg": "done"})
        await task
        self.assertEqual(received, [{"msg": "done"}])

    async def test_multiple_subscribers_same_channel(self) -> None:
        """Multiple subscribers on the same channel all receive payloads."""
        received_a: list[dict] = []
        received_b: list[dict] = []
        ready_a = asyncio.Event()
        ready_b = asyncio.Event()

        async def _collect_a() -> None:
            async for payload in self.bus.subscribe("chan-d", on_ready=ready_a.set):
                received_a.append(payload)
                if len(received_a) >= 1:
                    break

        async def _collect_b() -> None:
            async for payload in self.bus.subscribe("chan-d", on_ready=ready_b.set):
                received_b.append(payload)
                if len(received_b) >= 1:
                    break

        ta = asyncio.create_task(_collect_a())
        tb = asyncio.create_task(_collect_b())
        await ready_a.wait()
        await ready_b.wait()
        await self.bus.publish("chan-d", {"msg": "fanout"})
        await asyncio.gather(ta, tb)
        self.assertEqual(received_a, [{"msg": "fanout"}])
        self.assertEqual(received_b, [{"msg": "fanout"}])

    async def test_late_subscriber_misses_earlier(self) -> None:
        """Only connected subscribers receive; late-joiners miss earlier
        publishes (transient broadcast)."""
        received: list[dict] = []
        ready = asyncio.Event()

        await self.bus.publish("chan-e", {"msg": "before"})

        async def _collect() -> None:
            async for payload in self.bus.subscribe("chan-e", on_ready=ready.set):
                received.append(payload)
                break

        task = asyncio.create_task(_collect())
        await ready.wait()
        await self.bus.publish("chan-e", {"msg": "after"})
        await task
        self.assertEqual(received, [{"msg": "after"}])


class TestLock(IsolatedAsyncioTestCase):
    """Tests for Mode E — distributed lock (in-process mutex)."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_acquire_lock_yields(self) -> None:
        """acquire_lock context manager yields control."""
        async with self.bus.acquire_lock("lk-a") as _:
            self.assertIsNone(_)

    async def test_is_locked_returns_true_while_held(self) -> None:
        """is_locked returns True while a lock is held."""
        check_result = False

        async def _holder() -> None:
            nonlocal check_result
            async with self.bus.acquire_lock("lk-b"):
                check_result = await self.bus.is_locked("lk-b")

        await _holder()
        self.assertTrue(check_result)

    async def test_is_locked_returns_false_when_not_held(self) -> None:
        """is_locked returns False when no one holds the lock."""
        result = await self.bus.is_locked("lk-c")
        self.assertFalse(result)
        async with self.bus.acquire_lock("lk-c"):
            pass
        result_after = await self.bus.is_locked("lk-c")
        self.assertFalse(result_after)

    async def test_lock_is_mutually_exclusive(self) -> None:
        """Only one coroutine can hold the lock at a time."""
        order: list[str] = []

        async def _hold(name: str) -> None:
            async with self.bus.acquire_lock("lk-d"):
                order.append(f"{name}-enter")
                await asyncio.sleep(0.05)
                order.append(f"{name}-exit")

        t1 = asyncio.create_task(_hold("a"))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(_hold("b"))
        await asyncio.gather(t1, t2)
        # a enters, a exits, b enters, b exits (sequential)
        self.assertEqual(
            order,
            ["a-enter", "a-exit", "b-enter", "b-exit"],
        )

    async def test_lock_cleans_up_after_release(self) -> None:
        """After release with no waiters, the lock key is removed from _locks."""
        async with self.bus.acquire_lock("lk-e"):
            self.assertIn("lk-e", self.bus._locks)
        self.assertNotIn("lk-e", self.bus._locks)
        self.assertFalse(await self.bus.is_locked("lk-e"))

    async def test_ttl_parameter_accepted(self) -> None:
        """ttl_secs parameter is accepted (interface parity)."""
        async with self.bus.acquire_lock("lk-f", ttl_secs=30):
            self.assertTrue(await self.bus.is_locked("lk-f"))


class TestRegistry(IsolatedAsyncioTestCase):
    """Tests for Mode F — registry map operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bus = MemMessageBus()

    async def test_set_and_exists(self) -> None:
        """registry_set writes a field; registry_exists confirms it."""
        await self.bus.registry_set("ns-a", "key1", "val1")
        self.assertTrue(await self.bus.registry_exists("ns-a", "key1"))
        self.assertFalse(await self.bus.registry_exists("ns-a", "no-such"))

    async def test_exists_nonexistent_namespace(self) -> None:
        """registry_exists on unknown namespace returns False."""
        result = await self.bus.registry_exists("no-such-ns", "f")
        self.assertFalse(result)

    async def test_getall_returns_all_fields(self) -> None:
        """registry_getall returns all field-value pairs."""
        await self.bus.registry_set("ns-b", "a", "1")
        await self.bus.registry_set("ns-b", "b", "2")
        all_fields = await self.bus.registry_getall("ns-b")
        self.assertEqual(all_fields, {"a": "1", "b": "2"})

    async def test_getall_empty_namespace_returns_empty(self) -> None:
        """registry_getall on non-existent namespace returns {}."""
        result = await self.bus.registry_getall("no-such-ns")
        self.assertEqual(result, {})

    async def test_del_removes_field(self) -> None:
        """registry_del removes a field."""
        await self.bus.registry_set("ns-c", "key1", "val1")
        await self.bus.registry_del("ns-c", "key1")
        self.assertFalse(await self.bus.registry_exists("ns-c", "key1"))

    async def test_del_nonexistent_noop(self) -> None:
        """registry_del on non-existent field/namespace does not raise."""
        await self.bus.registry_del("no-such-ns", "f")

    async def test_drop_deletes_entire_namespace(self) -> None:
        """registry_drop removes all fields in a namespace."""
        await self.bus.registry_set("ns-d", "a", "1")
        await self.bus.registry_set("ns-d", "b", "2")
        await self.bus.registry_drop("ns-d")
        self.assertFalse(await self.bus.registry_exists("ns-d", "a"))
        self.assertFalse(await self.bus.registry_exists("ns-d", "b"))
        self.assertEqual(await self.bus.registry_getall("ns-d"), {})

    async def test_drop_nonexistent_noop(self) -> None:
        """registry_drop on non-existent namespace does not raise."""
        await self.bus.registry_drop("no-such-ns")

    async def test_overwrite_value(self) -> None:
        """Setting the same field again overwrites the value."""
        await self.bus.registry_set("ns-e", "key1", "first")
        await self.bus.registry_set("ns-e", "key1", "second")
        all_fields = await self.bus.registry_getall("ns-e")
        self.assertEqual(all_fields, {"key1": "second"})

    async def test_namespace_isolation(self) -> None:
        """Different namespaces do not interfere."""
        await self.bus.registry_set("ns-x", "f", "v1")
        await self.bus.registry_set("ns-y", "f", "v2")
        self.assertEqual(
            await self.bus.registry_getall("ns-x"),
            {"f": "v1"},
        )
        self.assertEqual(
            await self.bus.registry_getall("ns-y"),
            {"f": "v2"},
        )

    async def test_ttl_is_ignored(self) -> None:
        """ttl_secs parameter is accepted but has no effect."""
        await self.bus.registry_set("ns-f", "k", "v", ttl_secs=60)
        self.assertTrue(await self.bus.registry_exists("ns-f", "k"))
