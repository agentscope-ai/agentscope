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
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import fakeredis.aioredis
from utils import AnyString

from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    RedisMessageBus,
)


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

        def __init__(self) -> None:
            """Initialize the bus and declare the test-owned client."""
            super().__init__()
            self._client: fakeredis.aioredis.FakeRedis | None = None

        async def __aenter__(self) -> "RedisMessageBus":
            self._client = fake_redis
            return self

        async def aclose(self) -> None:
            """Detach the client without closing the test-owned connection."""
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

    async def test_queue_read_is_non_destructive(self) -> None:
        """Queue management can inspect entries without consuming them."""
        await self.bus.queue_push("k", {"i": 1})
        await self.bus.queue_push("k", {"i": 2})
        first = await self.bus.queue_read("k", max_count=10)
        second = await self.bus.queue_read("k", max_count=10)
        self.assertEqual(
            [payload for _entry_id, payload in first],
            [{"i": 1}, {"i": 2}],
        )
        self.assertEqual(first, second)

    async def test_queue_replace_preserves_new_order(self) -> None:
        """An editable Redis queue is transactionally replaced."""
        await self.bus.queue_push("k", {"i": 1})
        await self.bus.queue_replace("k", [{"i": 3}, {"i": 2}])
        entries = await self.bus.queue_drain("k", max_count=10)
        self.assertEqual(
            [payload for _entry_id, payload in entries],
            [{"i": 3}, {"i": 2}],
        )

    async def test_drain_reads_and_deletes_in_one_command(self) -> None:
        """Reading and deleting as two commands lets a competing
        consumer land in between, read the same entry and dispatch it a
        second time (#1868)."""
        await self.bus.queue_push("k", {"x": 1})

        execute_command = self.fr.execute_command
        issued: list[str] = []

        async def recording_execute_command(*args: Any, **kwargs: Any) -> Any:
            issued.append(args[0])
            return await execute_command(*args, **kwargs)

        self.fr.execute_command = recording_execute_command

        self.assertListEqual(
            await self.bus.queue_drain("k"),
            [(AnyString(), {"x": 1})],
        )
        # One command, whatever it is — the contract is that the read
        # and the delete cannot be observed apart.
        self.assertEqual(len(issued), 1)


class TestRegistryConditionalPrimitives(IsolatedAsyncioTestCase):
    """Mode F — the compare-and-set and take-once registry ops."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_set_if_writes_only_on_the_expected_value(self) -> None:
        """A caller whose read is stale loses, and changes nothing."""
        await self.bus.registry_set("ns", "f", "v1")

        self.assertFalse(
            await self.bus.registry_set_if(
                "ns",
                "f",
                "v2",
                expected="stale",
            ),
        )
        self.assertEqual(await self.bus.registry_get("ns", "f"), "v1")

        self.assertTrue(
            await self.bus.registry_set_if("ns", "f", "v2", expected="v1"),
        )
        self.assertEqual(await self.bus.registry_get("ns", "f"), "v2")

    async def test_set_if_refreshes_the_expiry_only_when_it_writes(
        self,
    ) -> None:
        """A losing caller must not extend someone else's lifetime."""
        await self.bus.registry_set("ns", "f", "v1", ttl_secs=1000)

        await self.bus.registry_set_if(
            "ns",
            "f",
            "v2",
            expected="stale",
            ttl_secs=5000,
        )
        self.assertLessEqual(await self.fr.ttl("ns"), 1000)

        await self.bus.registry_set_if(
            "ns",
            "f",
            "v2",
            expected="v1",
            ttl_secs=5000,
        )
        self.assertGreater(await self.fr.ttl("ns"), 1000)

    async def test_pop_hands_the_value_to_exactly_one_caller(self) -> None:
        """Whatever races for it, only the first take succeeds."""
        await self.bus.registry_set("ns", "f", "v1")

        self.assertEqual(await self.bus.registry_pop("ns", "f"), "v1")
        self.assertIsNone(await self.bus.registry_pop("ns", "f"))
        self.assertIsNone(await self.bus.registry_get("ns", "f"))

    async def test_pop_of_an_absent_field_is_not_an_error(self) -> None:
        """An expired session reads as gone, not as a failure."""
        self.assertIsNone(await self.bus.registry_pop("missing", "f"))


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

    async def test_subscribe_cleanup_survives_cancellation(self) -> None:
        """A consumer cancelled again while closing the pubsub must not
        abort the close, otherwise the connection leaks from the pool."""
        ready, closing = asyncio.Event(), asyncio.Event()
        finish, released = asyncio.Event(), asyncio.Event()
        pubsub = self.fr.pubsub()
        original_aclose = pubsub.aclose

        async def _slow_aclose() -> None:
            closing.set()
            await finish.wait()
            await original_aclose()
            released.set()

        async def _consumer() -> None:
            async for _ in self.bus.subscribe("ch", on_ready=ready.set):
                pass

        pubsub.aclose = _slow_aclose
        with patch.object(self.fr, "pubsub", return_value=pubsub):
            task = asyncio.create_task(_consumer())
            await asyncio.wait_for(ready.wait(), timeout=2.0)
            task.cancel()
            await asyncio.wait_for(closing.wait(), timeout=2.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        finish.set()
        await asyncio.wait_for(released.wait(), timeout=2.0)


class _FlakyEvalClient:
    """Redis client proxy whose ``eval`` fails a configurable number
    of initial calls with a transient error before delegating.

    Args:
        inner (`fakeredis.aioredis.FakeRedis`):
            The real client every other attribute and call delegates to.
        fail_times (int):
            How many initial ``eval`` calls raise :class:`TimeoutError`
            before the delegation starts.
    """

    def __init__(self, inner: Any, fail_times: int) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self.eval_calls = 0

    def __getattr__(self, name: str) -> Any:
        """Delegate every non-overridden attribute to the inner client."""
        return getattr(self._inner, name)

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        """Count the call, then fail or delegate to the inner client."""
        self.eval_calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise TimeoutError("transient redis outage")
        return await self._inner.eval(*args, **kwargs)


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

    async def test_timeout_resolves_committed_set_before_returning(
        self,
    ) -> None:
        """An acquisition timeout cannot leave an unrenewed committed lock."""
        committed = asyncio.Event()
        release_response = asyncio.Event()
        original = self.fr.set

        async def delayed_set(*args: Any, **kwargs: Any) -> object:
            result = await original(*args, **kwargs)
            committed.set()
            await release_response.wait()
            return result

        async def acquire() -> None:
            async with self.bus.acquire_lock_bounded(
                "delayed",
                ttl_secs=10,
                timeout_secs=0.01,
            ):
                self.fail("Timed-out acquisition entered the body.")

        with patch.object(self.fr, "set", delayed_set):
            task = asyncio.create_task(acquire())
            await asyncio.wait_for(committed.wait(), 1)
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            release_response.set()
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(await self.fr.get("delayed"), None)

    async def test_repeated_cancel_does_not_release_new_owner(self) -> None:
        """Late SET cleanup is shielded and checks its ownership token."""
        committed = asyncio.Event()
        release_response = asyncio.Event()
        original = self.fr.set

        async def delayed_set(*args: Any, **kwargs: Any) -> object:
            result = await original(*args, **kwargs)
            committed.set()
            await release_response.wait()
            return result

        async def acquire() -> None:
            async with self.bus.acquire_lock("cancelled", ttl_secs=10):
                self.fail("Cancelled acquisition entered the body.")

        with patch.object(self.fr, "set", delayed_set):
            task = asyncio.create_task(acquire())
            await asyncio.wait_for(committed.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            await original("cancelled", "new-owner", ex=10)
            release_response.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(await self.fr.get("cancelled"), "new-owner")

    async def test_stalled_set_cleanup_has_absolute_deadline(self) -> None:
        """Repeated cancellation cannot extend cleanup of a stuck SET."""
        committed, release = asyncio.Event(), asyncio.Event()
        original = self.fr.set

        async def blocked_set(*args: Any, **kwargs: Any) -> object:
            result = await original(*args, **kwargs)
            committed.set()
            await release.wait()
            return result

        async def acquire() -> None:
            async with self.bus.acquire_lock("stalled", ttl_secs=10):
                self.fail("Cancelled acquisition entered its body.")

        self.bus._lock_cleanup_timeout_secs = 0.03
        with patch.object(self.fr, "set", blocked_set):
            task = asyncio.create_task(acquire())
            try:
                await asyncio.wait_for(committed.wait(), 1)
                task.cancel()
                for _ in range(5):
                    await asyncio.sleep(0.01)
                    task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.3)
                self.assertEqual(done, {task})
                self.assertTrue(task.cancelled())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual(self.bus._lock_cleanup_tasks, set())
                self.assertGreater(await self.fr.ttl("stalled"), 0)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stalled_release_does_not_block_acquisition_cancellation(
        self,
    ) -> None:
        """The deadline also covers release Lua, not only the SET response."""
        committed, response, releasing, release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        original_set = self.fr.set

        async def delayed_set(*args: Any, **kwargs: Any) -> object:
            result = await original_set(*args, **kwargs)
            committed.set()
            await response.wait()
            return result

        async def blocked_eval(*_args: Any, **_kwargs: Any) -> None:
            releasing.set()
            await release.wait()

        async def acquire() -> None:
            async with self.bus.acquire_lock("stalled-release", ttl_secs=10):
                self.fail("Cancelled acquisition entered its body.")

        self.bus._lock_cleanup_timeout_secs = 0.03
        with patch.object(self.fr, "set", delayed_set), patch.object(
            self.fr,
            "eval",
            blocked_eval,
        ):
            task = asyncio.create_task(acquire())
            try:
                await asyncio.wait_for(committed.wait(), 1)
                task.cancel()
                response.set()
                await asyncio.wait_for(releasing.wait(), 1)
                done, _ = await asyncio.wait({task}, timeout=0.3)
                self.assertEqual(done, {task})
                self.assertTrue(task.cancelled())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual(self.bus._lock_cleanup_tasks, set())
            finally:
                response.set()
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_heartbeat_does_not_renew_another_owner(self) -> None:
        """A stale holder cannot extend or delete a replacement lease."""
        async with self.bus.acquire_lock("ownership", ttl_secs=2):
            await self.fr.set("ownership", "replacement", ex=20)
            await asyncio.sleep(1.05)
            self.assertGreater(await self.fr.ttl("ownership"), 10)
        self.assertEqual(await self.fr.get("ownership"), "replacement")

    async def test_heartbeat_survives_transient_renewal_error(self) -> None:
        """One failed renewal must not kill the heartbeat: the short
        backoff retry re-arms the TTL and the lease outlives its
        original expiry."""
        flaky = _FlakyEvalClient(self.fr, fail_times=1)
        self.bus._client = flaky

        async with self.bus.acquire_lock("k", ttl_secs=2):
            # The heartbeat ticks at 1s; its first renewal fails and
            # the backoff retry ~0.1s later re-arms the 2s TTL. At
            # 2.2s the original TTL would already have lapsed the
            # lease had the heartbeat died on the failed tick.
            await asyncio.sleep(2.2)
            self.assertTrue(await self.bus.is_locked("k"))
            self.assertGreaterEqual(flaky.eval_calls, 3)

        self.assertFalse(await self.bus.is_locked("k"))

    async def test_heartbeat_stops_after_lease_is_lost(self) -> None:
        """Once a renewal finds the key re-acquired by another holder
        the heartbeat stops instead of retrying forever, and the
        successor's lease is neither extended nor deleted."""
        flaky = _FlakyEvalClient(self.fr, fail_times=0)
        self.bus._client = flaky

        async with self.bus.acquire_lock("k", ttl_secs=2):
            await self.fr.set("k", "successor", ex=20)
            await asyncio.sleep(1.05)
            calls_after_loss = flaky.eval_calls
            self.assertGreaterEqual(calls_after_loss, 1)
            await asyncio.sleep(1.0)
            self.assertEqual(flaky.eval_calls, calls_after_loss)
            self.assertEqual(await self.fr.get("k"), "successor")
            self.assertGreater(await self.fr.ttl("k"), 15)


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

        # Queue holds the structured entry. ``enqueue_wakeup`` is the
        # idle-wake shortcut, so the entry carries ``kind="wake"`` and a
        # null input alongside the routing fields.
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


class TestRegistryPrimitive(IsolatedAsyncioTestCase):
    """Mode F — ``registry_*`` hash-keyed namespace operations."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_batch_read_preserves_order_missing_and_custom_bus_fallback(
        self,
    ) -> None:
        """The optional primitive has compatible semantics on every backend."""
        memory_bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )
        for bus in (memory_bus, self.bus):
            await bus.registry_set("test-batch", "one", "1")
            await bus.registry_set("test-batch", "two", "2")
            fields = ["two", "absent", "one", "two"]
            self.assertEqual(
                await bus.registry_get_many("test-batch", fields),
                ["2", None, "1", "2"],
            )
            self.assertEqual(
                await MessageBus.registry_get_many(
                    bus,
                    "test-batch",
                    fields,
                ),
                ["2", None, "1", "2"],
            )
            self.assertEqual(
                await bus.registry_get_many("test-batch", []),
                [],
            )
        self.assertNotIn("registry_get_many", MessageBus.__abstractmethods__)

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

    async def test_historical_bus_remains_instantiable(self) -> None:
        """Optional editable-queue methods do not add abstract requirements."""
        self.assertFalse(
            {"queue_read", "queue_replace", "acquire_lock_bounded"}
            & MessageBus.__abstractmethods__,
        )
        bus = InMemoryMessageBus()
        with self.assertRaises(NotImplementedError):
            await MessageBus.queue_read(bus, "unsupported")
        with self.assertRaises(NotImplementedError):
            await MessageBus.queue_replace(bus, "unsupported", [])
