# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`RedisMessageBus` and the domain helpers on the base
:class:`MessageBus` class.

The Redis backend is exercised against ``fakeredis`` so tests cover both
the abstract surface (queue / log / pubsub / lock) and the domain helpers
(``session_run`` / ``session_publish_event`` / ``inbox_*`` / ``wakeup_*``)
that are layered on top.
"""
import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.message_bus import MessageBus, RedisMessageBus


def _make_bus(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> RedisMessageBus:
    """Construct a :class:`RedisMessageBus` that uses *fake_redis*.

    The bus subclass overrides ``__aenter__`` so it talks to fakeredis
    instead of opening a real connection pool.

    Args:
        fake_redis (`fakeredis.aioredis.FakeRedis`):
            A fakeredis client whose pubsub / streams APIs are async.

    Returns:
        `RedisMessageBus`:
            A bus instance ready to be used as an async context manager.
    """

    class _FakeBus(RedisMessageBus):
        """Bus subclass that returns the supplied fakeredis client on
        context entry instead of building a real one."""

        async def __aenter__(self) -> "RedisMessageBus":
            self._client = fake_redis
            return self

        async def aclose(self) -> None:
            # The fakeredis client is owned by the test, not the bus.
            self._client = None

    return _FakeBus()


class TestQueuePrimitive(IsolatedAsyncioTestCase):
    """Mode A — ``queue_push`` + ``queue_drain`` semantics."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

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


class TestLogPrimitive(IsolatedAsyncioTestCase):
    """Mode C — replay log: append / read with cursor / trim."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_read_returns_everything_when_no_cursor(self) -> None:
        """Without a ``since`` cursor, the whole log comes back."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        entries = await self.bus.log_read("k")
        self.assertEqual([p["i"] for _id, p in entries], [1, 2])

    async def test_read_with_cursor_is_exclusive(self) -> None:
        """``since=last_id`` skips that id and returns only newer entries."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        await self.bus.log_append("k", {"i": 3})
        all_entries = await self.bus.log_read("k")
        cursor = all_entries[1][0]  # id of entry 2
        rest = await self.bus.log_read("k", since=cursor)
        self.assertEqual([p["i"] for _id, p in rest], [3])

    async def test_trim_without_before_drops_entire_log(self) -> None:
        """``log_trim(key)`` empties the log; subsequent read is empty."""
        await self.bus.log_append("k", {"i": 1})
        await self.bus.log_append("k", {"i": 2})
        await self.bus.log_trim("k")
        self.assertEqual(await self.bus.log_read("k"), [])


class TestPubSubPrimitive(IsolatedAsyncioTestCase):
    """Mode D — transient broadcast: publish / subscribe."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_subscribe_receives_messages_published_after_ready(
        self,
    ) -> None:
        """Subscribers receive payloads published after the subscription
        is established. The ``on_ready`` hook fires once before any
        payload is yielded."""
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


class TestLockPrimitive(IsolatedAsyncioTestCase):
    """Mode E — distributed mutex semantics."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

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
            # Tiny delay so the holder grabs the lock first.
            await asyncio.sleep(0.005)
            async with self.bus.acquire_lock("k", ttl_secs=10):
                order.append("second-in")

        await asyncio.gather(_holder(), _challenger())
        self.assertEqual(
            order,
            ["first-in", "first-out", "second-in"],
        )


class TestSessionRunAutoTrimsLog(IsolatedAsyncioTestCase):
    """``session_run.__aexit__`` must trim the session's replay log
    *before* releasing the distributed lock, so any subscriber that
    connects between two runs sees a clean slate."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_log_trim_happens_on_session_run_exit(self) -> None:
        """Events published inside a ``session_run`` block are trimmed
        once the block exits — a fresh ``session_read_events`` returns
        an empty list."""
        sid = "s-trim"
        async with self.bus.session_run(sid):
            await self.bus.session_publish_event(sid, {"i": 1})
            await self.bus.session_publish_event(sid, {"i": 2})
            mid = await self.bus.session_read_events(sid)
            self.assertEqual([p["i"] for _id, p in mid], [1, 2])
        self.assertEqual(await self.bus.session_read_events(sid), [])

    async def test_log_trim_runs_even_when_body_raises(self) -> None:
        """If the body raises, the log is still trimmed before the lock
        releases — the next run starts clean."""
        sid = "s-raise"

        class _Boom(RuntimeError):
            """Marker exception raised inside the run body."""

        with self.assertRaises(_Boom):
            async with self.bus.session_run(sid):
                await self.bus.session_publish_event(sid, {"i": 1})
                raise _Boom()

        self.assertEqual(await self.bus.session_read_events(sid), [])


class TestSessionDomainHelpers(IsolatedAsyncioTestCase):
    """``session_publish_event`` + ``session_subscribe_events`` +
    ``session_is_running`` round-trip behaviour."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_publish_event_writes_to_log_and_pubsub(self) -> None:
        """``session_publish_event`` writes one entry to the replay log
        AND fans it out on the live channel; subscribers receive the
        payload with the ``_entry_id`` field stripped."""
        sid = "s-pub"
        ready = asyncio.Event()
        received: list[dict] = []

        async def _consumer() -> None:
            async for payload in self.bus.session_subscribe_events(
                sid,
                on_ready=ready.set,
            ):
                received.append(payload)
                break

        task = asyncio.create_task(_consumer())
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        await self.bus.session_publish_event(sid, {"hello": "world"})
        await asyncio.wait_for(task, timeout=2.0)

        # Replay log captured the entry too.
        log_entries = await self.bus.session_read_events(sid)
        self.assertEqual(len(log_entries), 1)
        self.assertEqual(log_entries[0][1], {"hello": "world"})

        # Live subscriber saw it without the internal _entry_id key.
        self.assertEqual(received, [{"hello": "world"}])

    async def test_session_is_running_reflects_session_run(self) -> None:
        """``session_is_running`` returns True while inside
        ``session_run`` and False after."""
        sid = "s-isrun"
        self.assertFalse(await self.bus.session_is_running(sid))
        async with self.bus.session_run(sid):
            self.assertTrue(await self.bus.session_is_running(sid))
        self.assertFalse(await self.bus.session_is_running(sid))


class TestInboxAndWakeupHelpers(IsolatedAsyncioTestCase):
    """Inbox + wakeup domain helpers used by team / tool-offload /
    scheduler to deliver work to idle sessions."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_inbox_push_drain_round_trip(self) -> None:
        """``inbox_push`` payloads are returned by ``inbox_drain`` in
        push order, exactly once."""
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

    async def test_enqueue_wakeup_signals_and_queues(self) -> None:
        """``enqueue_wakeup`` puts the payload on the durable queue and
        fires the signal channel; a subscriber and a ``dequeue_wakeups``
        call both see it."""
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

        # Signal fired.
        self.assertEqual(len(received), 1)

        # Queue holds the structured entry.
        entries = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0],
            {"user_id": "u", "session_id": "s", "agent_id": "a"},
        )


class TestRegistryPrimitive(IsolatedAsyncioTestCase):
    """Mode F — ``registry_*`` hash-keyed namespace operations."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_set_then_exists_and_getall(self) -> None:
        """``registry_set`` stores a field under a namespace; ``exists``
        is True, ``getall`` returns the full mapping."""
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
        """A second ``registry_set`` for the same field overwrites the
        previous value (``HSET`` semantics)."""
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
        self.assertEqual(
            await self.bus.registry_getall("ns"),
            {"keep": "k"},
        )

    async def test_del_missing_field_is_noop(self) -> None:
        """Deleting a non-existent field does not raise."""
        await self.bus.registry_del("ns", "nope")
        await self.bus.registry_set("ns", "keep", "k")
        await self.bus.registry_del("ns", "still-missing")
        self.assertEqual(
            await self.bus.registry_getall("ns"),
            {"keep": "k"},
        )

    async def test_getall_on_missing_namespace_returns_empty_dict(
        self,
    ) -> None:
        """``registry_getall`` for an unknown namespace returns ``{}``
        rather than ``None``."""
        self.assertEqual(await self.bus.registry_getall("ghost"), {})

    async def test_drop_deletes_entire_namespace(self) -> None:
        """``registry_drop`` removes every field under the namespace."""
        await self.bus.registry_set("ns", "f1", "v1")
        await self.bus.registry_set("ns", "f2", "v2")

        await self.bus.registry_drop("ns")

        self.assertFalse(await self.bus.registry_exists("ns", "f1"))
        self.assertFalse(await self.bus.registry_exists("ns", "f2"))
        self.assertEqual(await self.bus.registry_getall("ns"), {})

    async def test_drop_missing_namespace_is_noop(self) -> None:
        """Dropping a namespace that was never written does not raise."""
        await self.bus.registry_drop("never-existed")

    async def test_set_with_ttl_applies_expire_and_refreshes(self) -> None:
        """``registry_set`` with ``ttl_secs`` sets a TTL on the hash key;
        a subsequent set with a longer ``ttl_secs`` refreshes it."""
        await self.bus.registry_set("ns", "f", "v", ttl_secs=60)
        ttl_first = await self.fr.ttl("ns")
        self.assertGreater(ttl_first, 0)
        self.assertLessEqual(ttl_first, 60)

        # Refresh with a much larger TTL — must overwrite the old one.
        await self.bus.registry_set("ns", "f", "v", ttl_secs=3600)
        ttl_refreshed = await self.fr.ttl("ns")
        self.assertGreater(ttl_refreshed, 60)

    async def test_set_without_ttl_leaves_namespace_persistent(
        self,
    ) -> None:
        """Without ``ttl_secs`` the namespace has no expiry (TTL == -1)."""
        await self.bus.registry_set("ns", "f", "v")
        self.assertEqual(await self.fr.ttl("ns"), -1)


class TestBackgroundTaskRegistryHelpers(IsolatedAsyncioTestCase):
    """Domain helpers built on Mode F: ``bg_task_register / unregister /
    exists / list / purge`` plus ``task_publish_cancel /
    task_subscribe_cancel``."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_register_then_exists_list_unregister(self) -> None:
        """End-to-end registry round-trip for a single session."""
        sid = "s-bg"

        self.assertFalse(await self.bus.bg_task_exists(sid, "t1"))

        await self.bus.bg_task_register(sid, "t1", '{"tool":"a"}')
        await self.bus.bg_task_register(sid, "t2", '{"tool":"b"}')

        self.assertTrue(await self.bus.bg_task_exists(sid, "t1"))
        self.assertTrue(await self.bus.bg_task_exists(sid, "t2"))
        self.assertEqual(
            await self.bus.bg_task_list(sid),
            {"t1": '{"tool":"a"}', "t2": '{"tool":"b"}'},
        )

        await self.bus.bg_task_unregister(sid, "t1")
        self.assertFalse(await self.bus.bg_task_exists(sid, "t1"))
        self.assertTrue(await self.bus.bg_task_exists(sid, "t2"))
        self.assertEqual(
            await self.bus.bg_task_list(sid),
            {"t2": '{"tool":"b"}'},
        )

    async def test_register_isolates_sessions(self) -> None:
        """Tasks registered under one session id are invisible to other
        session ids."""
        await self.bus.bg_task_register("s1", "t", "{}")

        self.assertTrue(await self.bus.bg_task_exists("s1", "t"))
        self.assertFalse(await self.bus.bg_task_exists("s2", "t"))
        self.assertEqual(await self.bus.bg_task_list("s2"), {})

    async def test_register_applies_fallback_ttl(self) -> None:
        """``bg_task_register`` sets the per-session fallback TTL on the
        hash key so abandoned entries can't accumulate forever."""
        sid = "s-ttl"
        await self.bus.bg_task_register(sid, "t", "{}")

        ttl = await self.fr.ttl(self.bus._BG_TASKS_KEY.format(sid=sid))
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, self.bus._BG_TASKS_TTL_SECS)

        # A second register-call refreshes the TTL back near the cap.
        await asyncio.sleep(0)  # let any fakeredis internals settle
        await self.bus.bg_task_register(sid, "t2", "{}")
        ttl_refreshed = await self.fr.ttl(
            self.bus._BG_TASKS_KEY.format(sid=sid),
        )
        self.assertGreater(ttl_refreshed, 0)

    async def test_purge_clears_all_session_entries(self) -> None:
        """``bg_task_purge`` deletes every task entry for a session in
        a single call (used during session deletion)."""
        sid = "s-purge"
        await self.bus.bg_task_register(sid, "t1", "{}")
        await self.bus.bg_task_register(sid, "t2", "{}")
        # Other session must survive.
        await self.bus.bg_task_register("s-keep", "t", "{}")

        await self.bus.bg_task_purge(sid)

        self.assertEqual(await self.bus.bg_task_list(sid), {})
        self.assertFalse(await self.bus.bg_task_exists(sid, "t1"))
        self.assertTrue(await self.bus.bg_task_exists("s-keep", "t"))

    async def test_task_publish_cancel_reaches_subscriber(self) -> None:
        """``task_publish_cancel`` fans out to every active
        ``task_subscribe_cancel`` listener; the yielded value is the
        ``task_id`` from the payload."""
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


class TestBaseClassIsAbstract(IsolatedAsyncioTestCase):
    """``MessageBus`` itself cannot be instantiated."""

    def test_instantiating_base_class_raises(self) -> None:
        """The abstract methods are not implemented on the base — direct
        instantiation must fail."""
        with self.assertRaises(TypeError):
            # pylint: disable=abstract-class-instantiated
            MessageBus()  # type: ignore[abstract]
