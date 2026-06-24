# -*- coding: utf-8 -*-
"""Tests for :class:`InMemoryMessageBus`.

The same abstract surface exercised in ``service_message_bus_test.py``
(queue / log / pubsub / lock / registry) is tested here against the
pure-Python in-memory backend, plus the domain helpers inherited from
the base :class:`MessageBus` class.

No external dependencies (no Redis, no fakeredis) — just asyncio.
"""
import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

from agentscope.app.message_bus import InMemoryMessageBus


class TestQueuePrimitive(IsolatedAsyncioTestCase):
    """Mode A — ``queue_push`` + ``queue_drain`` semantics."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_push_drain_returns_payloads_in_order(self) -> None:
        """Entries pushed in order come back out in order, once each."""
        await self.bus.queue_push("k", {"i": 1})
        await self.bus.queue_push("k", {"i": 2})
        entries = await self.bus.queue_drain("k", max_count=10)
        self.assertEqual([p for _id, p in entries], [{"i": 1}, {"i": 2}])

    async def test_drain_is_destructive(self) -> None:
        """A drained entry is gone; a second drain yields nothing."""
        await self.bus.queue_push("k", {"x": 1})
        await self.bus.queue_drain("k", max_count=10)
        self.assertEqual(await self.bus.queue_drain("k", max_count=10), [])

    async def test_drain_respects_max_count(self) -> None:
        """``max_count`` caps the batch size; remaining entries persist."""
        for i in range(5):
            await self.bus.queue_push("k", {"i": i})
        first = await self.bus.queue_drain("k", max_count=3)
        rest = await self.bus.queue_drain("k", max_count=10)
        self.assertEqual([p["i"] for _id, p in first], [0, 1, 2])
        self.assertEqual([p["i"] for _id, p in rest], [3, 4])

    async def test_drain_empty_queue_returns_empty(self) -> None:
        """Draining a key that was never pushed returns an empty list."""
        self.assertEqual(await self.bus.queue_drain("nope"), [])

    async def test_push_returns_unique_ids(self) -> None:
        """Each ``queue_push`` returns a distinct entry id."""
        id1 = await self.bus.queue_push("k", {"a": 1})
        id2 = await self.bus.queue_push("k", {"a": 2})
        self.assertNotEqual(id1, id2)

    async def test_queue_delete_removes_all(self) -> None:
        """``queue_delete`` drops the entire queue."""
        await self.bus.queue_push("k", {"i": 1})
        await self.bus.queue_push("k", {"i": 2})
        await self.bus.queue_delete("k")
        self.assertEqual(await self.bus.queue_drain("k", max_count=10), [])

    async def test_queue_delete_missing_is_noop(self) -> None:
        """Deleting a non-existent queue does not raise."""
        await self.bus.queue_delete("never-existed")

    async def test_queue_isolation_between_keys(self) -> None:
        """Pushes to different keys are independent."""
        await self.bus.queue_push("a", {"x": 1})
        await self.bus.queue_push("b", {"x": 2})
        a = await self.bus.queue_drain("a", max_count=10)
        b = await self.bus.queue_drain("b", max_count=10)
        self.assertEqual([p for _id, p in a], [{"x": 1}])
        self.assertEqual([p for _id, p in b], [{"x": 2}])


class TestLogPrimitive(IsolatedAsyncioTestCase):
    """Mode C — replay log: append / read with cursor / trim."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_read_returns_everything_when_no_cursor(self) -> None:
        """Without a ``since`` cursor, the whole log comes back."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        entries = await self.bus.log_read("k")
        self.assertEqual([p["i"] for _id, p in entries], [1, 2])

    async def test_read_with_cursor_is_exclusive(self) -> None:
        """``since=last_id`` skips that id and returns only newer."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        await self.bus.log_append("k", {"i": 3})
        all_entries = await self.bus.log_read("k")
        cursor = all_entries[1][0]  # id of entry 2
        rest = await self.bus.log_read("k", since=cursor)
        self.assertEqual([p["i"] for _id, p in rest], [3])

    async def test_read_respects_max_count(self) -> None:
        """``max_count`` caps the batch; remaining entries persist."""
        for i in range(5):
            await self.bus.log_append("k", {"i": i})
        first = await self.bus.log_read("k", max_count=3)
        self.assertEqual([p["i"] for _id, p in first], [0, 1, 2])

    async def test_read_is_non_destructive(self) -> None:
        """Multiple reads on the same log return the same data."""
        await self.bus.log_append("k", {"i": 1})
        r1 = await self.bus.log_read("k")
        r2 = await self.bus.log_read("k")
        self.assertEqual(
            [p["i"] for _id, p in r1],
            [p["i"] for _id, p in r2],
        )

    async def test_read_empty_log(self) -> None:
        """Reading a log that never had entries returns ``[]``."""
        self.assertEqual(await self.bus.log_read("nope"), [])

    async def test_read_all_before_cursor(self) -> None:
        """When all entries are at or before the cursor, result is ``[]``."""
        id2 = await self.bus.log_append("k", {"i": 2})
        self.assertEqual(await self.bus.log_read("k", since=id2), [])

    async def test_trim_without_before_drops_entire_log(self) -> None:
        """``log_trim(key)`` empties the log."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        await self.bus.log_trim("k")
        self.assertEqual(await self.bus.log_read("k"), [])

    async def test_trim_with_before_id_keeps_newer(self) -> None:
        """``log_trim(key, before_id)`` drops older entries only."""
        await self.bus.log_append("k", {"i": 1})
        id2 = await self.bus.log_append("k", {"i": 2})
        await self.bus.log_append("k", {"i": 3})
        await self.bus.log_trim("k", before_id=id2)
        entries = await self.bus.log_read("k")
        self.assertEqual([p["i"] for _id, p in entries], [2, 3])

    async def test_trim_missing_key_is_noop(self) -> None:
        """Trimming a non-existent log does not raise."""
        await self.bus.log_trim("nope")

    async def test_max_len_caps_log_size(self) -> None:
        """``max_len`` on ``log_append`` trims older entries when the
        log exceeds the cap."""
        for i in range(10):
            await self.bus.log_append("k", {"i": i}, max_len=5)
        entries = await self.bus.log_read("k", max_count=100)
        self.assertLessEqual(len(entries), 5)
        # The newest entries must survive.
        self.assertEqual(entries[-1][1]["i"], 9)


class TestPubSubPrimitive(IsolatedAsyncioTestCase):
    """Mode D — transient broadcast: publish / subscribe."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_subscribe_receives_messages_published_after_ready(
        self,
    ) -> None:
        """Subscribers receive payloads published after the subscription
        is established."""
        ready = asyncio.Event()
        received: list[dict] = []

        async def _consumer() -> None:
            async for payload in self.bus.subscribe(
                "ch",
                on_ready=ready.set,
            ):
                received.append(payload)
                if len(received) == 2:
                    break

        task = asyncio.create_task(_consumer())
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        await self.bus.publish("ch", {"i": 1})
        await self.bus.publish("ch", {"i": 2})
        await asyncio.wait_for(task, timeout=2.0)
        self.assertEqual([p["i"] for p in received], [1, 2])

    async def test_publish_without_subscribers_is_noop(self) -> None:
        """Publishing when no one is listening does not raise."""
        await self.bus.publish("ch", {"i": 1})

    async def test_multiple_subscribers_each_receive(self) -> None:
        """All active subscribers on a channel receive the payload."""
        ready1 = asyncio.Event()
        ready2 = asyncio.Event()
        r1: list[dict] = []
        r2: list[dict] = []

        async def _c1() -> None:
            async for payload in self.bus.subscribe(
                "ch",
                on_ready=ready1.set,
            ):
                r1.append(payload)
                break

        async def _c2() -> None:
            async for payload in self.bus.subscribe(
                "ch",
                on_ready=ready2.set,
            ):
                r2.append(payload)
                break

        t1 = asyncio.create_task(_c1())
        t2 = asyncio.create_task(_c2())
        await asyncio.wait_for(ready1.wait(), timeout=2.0)
        await asyncio.wait_for(ready2.wait(), timeout=2.0)

        await self.bus.publish("ch", {"x": 42})

        await asyncio.wait_for(t1, timeout=2.0)
        await asyncio.wait_for(t2, timeout=2.0)
        self.assertEqual(r1, [{"x": 42}])
        self.assertEqual(r2, [{"x": 42}])


class TestLockPrimitive(IsolatedAsyncioTestCase):
    """Mode E — distributed mutex (process-local asyncio.Lock)."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_is_locked_reflects_acquire_release(self) -> None:
        """``is_locked`` flips to True while the body runs and back to
        False once the context exits."""
        self.assertFalse(await self.bus.is_locked("k"))
        async with self.bus.acquire_lock("k", ttl_secs=10):
            self.assertTrue(await self.bus.is_locked("k"))
        self.assertFalse(await self.bus.is_locked("k"))

    async def test_second_acquirer_waits_until_release(self) -> None:
        """A second ``acquire_lock`` on the same key blocks until the
        first releases."""
        order: list[str] = []

        async def _holder() -> None:
            async with self.bus.acquire_lock("k", ttl_secs=10):
                order.append("first-in")
                await asyncio.sleep(0.05)
                order.append("first-out")

        async def _challenger() -> None:
            await asyncio.sleep(0.005)
            async with self.bus.acquire_lock("k", ttl_secs=10):
                order.append("second-in")

        await asyncio.gather(_holder(), _challenger())
        self.assertEqual(
            order,
            ["first-in", "first-out", "second-in"],
        )


class TestRegistryPrimitive(IsolatedAsyncioTestCase):
    """Mode F — ``registry_*`` hash-keyed namespace operations."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_set_then_exists_and_getall(self) -> None:
        """``registry_set`` stores a field; ``exists`` and ``getall``
        round-trip correctly."""
        await self.bus.registry_set("ns", "f1", "v1")
        await self.bus.registry_set("ns", "f2", "v2")

        self.assertTrue(await self.bus.registry_exists("ns", "f1"))
        self.assertTrue(await self.bus.registry_exists("ns", "f2"))
        self.assertFalse(await self.bus.registry_exists("ns", "missing"))
        self.assertFalse(await self.bus.registry_exists("other-ns", "f1"))

        self.assertEqual(
            await self.bus.registry_getall("ns"),
            {"f1": "v1", "f2": "v2"},
        )

    async def test_set_overwrites_existing_field(self) -> None:
        """A second ``registry_set`` for the same field overwrites."""
        await self.bus.registry_set("ns", "f", "v1")
        await self.bus.registry_set("ns", "f", "v2")
        self.assertEqual(
            await self.bus.registry_getall("ns"),
            {"f": "v2"},
        )

    async def test_del_removes_only_the_named_field(self) -> None:
        """``registry_del`` removes a single field; siblings survive."""
        await self.bus.registry_set("ns", "keep", "k")
        await self.bus.registry_set("ns", "drop", "d")
        await self.bus.registry_del("ns", "drop")
        self.assertFalse(await self.bus.registry_exists("ns", "drop"))
        self.assertTrue(await self.bus.registry_exists("ns", "keep"))

    async def test_del_missing_field_is_noop(self) -> None:
        """Deleting a non-existent field does not raise."""
        await self.bus.registry_del("ns", "nope")

    async def test_getall_on_missing_namespace_returns_empty(self) -> None:
        """``registry_getall`` for an unknown namespace returns ``{}``."""
        self.assertEqual(await self.bus.registry_getall("ghost"), {})

    async def test_drop_deletes_entire_namespace(self) -> None:
        """``registry_drop`` removes every field under the namespace."""
        await self.bus.registry_set("ns", "f1", "v1")
        await self.bus.registry_set("ns", "f2", "v2")
        await self.bus.registry_drop("ns")
        self.assertEqual(await self.bus.registry_getall("ns"), {})

    async def test_drop_missing_namespace_is_noop(self) -> None:
        """Dropping a namespace that was never written does not raise."""
        await self.bus.registry_drop("never-existed")

    async def test_getall_returns_copy(self) -> None:
        """Mutating the returned dict does not affect bus state."""
        await self.bus.registry_set("ns", "f", "v")
        out = await self.bus.registry_getall("ns")
        out["injected"] = "evil"
        self.assertEqual(
            await self.bus.registry_getall("ns"),
            {"f": "v"},
        )


class TestDomainHelpers(IsolatedAsyncioTestCase):
    """Domain helpers inherited from ``MessageBus`` work end-to-end
    on the in-memory backend."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_session_run_trims_log_on_exit(self) -> None:
        """``session_run`` + ``session_publish_event`` + auto trim."""
        sid = "s-trim"
        async with self.bus.session_run(sid):
            await self.bus.session_publish_event(sid, {"i": 1})
            await self.bus.session_publish_event(sid, {"i": 2})
            mid = await self.bus.session_read_events(sid)
            self.assertEqual([p["i"] for _id, p in mid], [1, 2])
        self.assertEqual(await self.bus.session_read_events(sid), [])

    async def test_session_is_running_reflects_lock(self) -> None:
        """``session_is_running`` is True while inside ``session_run``."""
        sid = "s-isrun"
        self.assertFalse(await self.bus.session_is_running(sid))
        async with self.bus.session_run(sid):
            self.assertTrue(await self.bus.session_is_running(sid))
        self.assertFalse(await self.bus.session_is_running(sid))

    async def test_inbox_round_trip(self) -> None:
        """``inbox_push`` + ``inbox_drain`` FIFO semantics."""
        sid = "s-inbox"
        await self.bus.inbox_push(sid, {"hint": "a"})
        await self.bus.inbox_push(sid, {"hint": "b"})
        entries = await self.bus.inbox_drain(sid, max_count=10)
        self.assertEqual(
            [p["hint"] for _id, p in entries],
            ["a", "b"],
        )
        self.assertEqual(
            await self.bus.inbox_drain(sid, max_count=10),
            [],
        )

    async def test_enqueue_wakeup_round_trip(self) -> None:
        """``enqueue_wakeup`` → ``dequeue_wakeups`` round-trip."""
        ready = asyncio.Event()
        received: list[dict] = []

        async def _signal_consumer() -> None:
            async for payload in self.bus.subscribe_wakeup_signal(
                on_ready=ready.set,
            ):
                received.append(payload)
                break

        task = asyncio.create_task(_signal_consumer())
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        await self.bus.enqueue_wakeup(
            user_id="u",
            session_id="s",
            agent_id="a",
        )
        await asyncio.wait_for(task, timeout=2.0)
        self.assertEqual(len(received), 1)

        entries = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0],
            {
                "user_id": "u",
                "session_id": "s",
                "agent_id": "a",
                "kind": "wake",
                "input": None,
            },
        )

    async def test_bg_task_round_trip(self) -> None:
        """``bg_task_register / exists / list / unregister / purge``
        work on the in-memory backend."""
        sid = "s-bg"
        self.assertFalse(await self.bus.bg_task_exists(sid, "t1"))

        await self.bus.bg_task_register(sid, "t1", '{"tool":"a"}')
        await self.bus.bg_task_register(sid, "t2", '{"tool":"b"}')

        self.assertTrue(await self.bus.bg_task_exists(sid, "t1"))
        self.assertEqual(
            await self.bus.bg_task_list(sid),
            {"t1": '{"tool":"a"}', "t2": '{"tool":"b"}'},
        )

        await self.bus.bg_task_unregister(sid, "t1")
        self.assertFalse(await self.bus.bg_task_exists(sid, "t1"))

        await self.bus.bg_task_purge(sid)
        self.assertEqual(await self.bus.bg_task_list(sid), {})

    async def test_session_purge_clears_all_bus_state(self) -> None:
        """``session_purge`` deletes the session's events + inbox +
        bg_tasks in one call."""
        sid = "s-purge"
        await self.bus.session_publish_event(sid, {"e": 1})
        await self.bus.inbox_push(sid, {"m": 1})
        await self.bus.bg_task_register(sid, "t1", "{}")

        await self.bus.session_purge(sid)

        self.assertEqual(await self.bus.session_read_events(sid), [])
        self.assertEqual(
            await self.bus.inbox_drain(sid, max_count=10),
            [],
        )
        self.assertEqual(await self.bus.bg_task_list(sid), {})

    async def test_task_cancel_pub_sub(self) -> None:
        """``task_publish_cancel`` → ``task_subscribe_cancel``
        round-trip."""
        ready = asyncio.Event()
        received: list[str] = []

        async def _consumer() -> None:
            async for tid in self.bus.task_subscribe_cancel(
                on_ready=ready.set,
            ):
                received.append(tid)
                break

        task = asyncio.create_task(_consumer())
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        await self.bus.task_publish_cancel("task-X")
        await asyncio.wait_for(task, timeout=2.0)

        self.assertEqual(received, ["task-X"])
