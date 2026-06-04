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


class TestBaseClassIsAbstract(IsolatedAsyncioTestCase):
    """``MessageBus`` itself cannot be instantiated."""

    def test_instantiating_base_class_raises(self) -> None:
        """The abstract methods are not implemented on the base — direct
        instantiation must fail."""
        with self.assertRaises(TypeError):
            # pylint: disable=abstract-class-instantiated
            MessageBus()  # type: ignore[abstract]
