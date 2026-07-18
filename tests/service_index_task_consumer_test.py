# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`IndexTaskConsumer` — the worker-process side of
the message-bus index dispatch flow.

Verifies the four behaviours callers rely on:

- Lifecycle is purely ACM: ``__aenter__`` starts the loop and performs
  an initial drain; ``__aexit__`` cancels the loop and any in-flight
  ``worker.process`` task cleanly.
- A signal triggers a queue drain; each entry is dispatched as a
  ``worker.process`` call.
- Entries left on the queue from before ``__aenter__`` are picked up
  on the initial drain without waiting for a fresh signal.
- Malformed entries are logged and skipped, not raised; later valid
  entries still dispatch.
- An exception inside ``worker.process`` is logged but does not crash
  the consumer loop — subsequent signals still drain.

Mirrors :mod:`service_wakeup_dispatcher_test` deliberately; the
flows are identical at the primitive level.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service import IndexTaskConsumer
from agentscope.app.message_bus import MessageBus, MessageBusKeys


class _FakeBus(MessageBus):
    """In-memory bus with just enough behaviour for the consumer.

    Implements the primitives the consumer actually uses
    (``queue_push`` / ``queue_drain`` / ``subscribe`` / ``publish``)
    and stubs the others. The consumer reaches for the bus through
    the well-known channel/key constants — no domain methods exist
    on the bus.
    """

    def __init__(self) -> None:
        self.queues: dict[str, list[tuple[str, dict]]] = {}
        self._channels: dict[str, asyncio.Queue] = {}
        self._next = 0
        self._locks: set[str] = set()

    def _channel(self, key: str) -> asyncio.Queue:
        return self._channels.setdefault(key, asyncio.Queue())

    # Mode A — queue
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        self._next += 1
        entry_id = str(self._next)
        self.queues.setdefault(key, []).append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        *,
        max_count: int,
    ) -> list[tuple[str, dict]]:
        entries = self.queues.get(key, [])[:max_count]
        self.queues[key] = self.queues.get(key, [])[max_count:]
        return entries

    async def queue_delete(self, key: str) -> None:
        self.queues.pop(key, None)

    # Mode C — log (unused)
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        max_len: int | None = None,
        ttl_secs: int | None = None,
    ) -> str:
        return "n/a"

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        return None

    # Mode D — pub/sub
    async def publish(self, key: str, payload: dict) -> None:
        await self._channel(key).put(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        while True:
            yield await self._channel(key).get()

    # Mode E — lock
    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        self._locks.add(key)
        try:
            yield
        finally:
            self._locks.discard(key)

    async def is_locked(self, key: str) -> bool:
        return key in self._locks

    # Mode F — registry (unused by IndexTaskConsumer; raise so any
    # accidental dependency surfaces immediately rather than silently
    # passing through a stub).
    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        raise NotImplementedError

    async def registry_del(self, namespace: str, field: str) -> None:
        raise NotImplementedError

    async def registry_exists(self, namespace: str, field: str) -> bool:
        raise NotImplementedError

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        raise NotImplementedError

    async def registry_get(
        self,
        namespace: str,
        field: str,
    ) -> str | None:
        raise NotImplementedError

    async def registry_drop(self, namespace: str) -> None:
        raise NotImplementedError


class _RecordingWorker:
    """Records calls to :meth:`process` so tests can assert dispatch.

    The real ``IndexWorker`` has many more methods, but the consumer
    only ever invokes :meth:`process` — so the test surface stays
    narrow on purpose.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.notify = asyncio.Event()
        self.fail_on_doc: str | None = None
        self.process_delay = 0.0

    async def process(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Record the call and signal the test, optionally failing or
        sleeping first to exercise the consumer's error and timing
        paths.

        Args:
            user_id (`str`):
                The owning user id forwarded by the consumer.
            knowledge_base_id (`str`):
                The knowledge base id forwarded by the consumer.
            document_id (`str`):
                The document id forwarded by the consumer.

        Raises:
            `RuntimeError`:
                When ``fail_on_doc`` matches ``document_id`` — used by
                the consumer-resilience tests.
        """
        if self.process_delay:
            await asyncio.sleep(self.process_delay)
        self.calls.append(
            {
                "user_id": user_id,
                "knowledge_base_id": knowledge_base_id,
                "document_id": document_id,
            },
        )
        self.notify.set()
        if self.fail_on_doc and document_id == self.fail_on_doc:
            raise RuntimeError(f"boom for {document_id}")


async def _yield_a_few_times(ticks: int = 8) -> None:
    """Yield the event loop a few times so spawned tasks make progress."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class TestIndexTaskConsumerDispatch(IsolatedAsyncioTestCase):
    """Verifies the signal-driven dispatch path."""

    async def test_signal_drives_dispatch(self) -> None:
        """A signal causes the queue to be drained and each entry
        dispatched as ``worker.process``."""
        bus = _FakeBus()
        worker = _RecordingWorker()
        async with IndexTaskConsumer(message_bus=bus, worker=worker):
            await bus.queue_push(
                MessageBusKeys.index_tasks_queue(),
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "d1",
                },
            )
            await bus.publish(MessageBusKeys.index_tasks_signal(), {})

            await asyncio.wait_for(worker.notify.wait(), timeout=2.0)

        self.assertEqual(
            worker.calls,
            [
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "d1",
                },
            ],
        )

    async def test_initial_drain_picks_up_pending_entries(self) -> None:
        """Entries on the queue from before ``__aenter__`` are picked
        up without waiting for a fresh signal."""
        bus = _FakeBus()
        worker = _RecordingWorker()
        await bus.queue_push(
            MessageBusKeys.index_tasks_queue(),
            {
                "user_id": "u",
                "knowledge_base_id": "kb",
                "document_id": "pre",
            },
        )

        async with IndexTaskConsumer(message_bus=bus, worker=worker):
            await asyncio.wait_for(worker.notify.wait(), timeout=2.0)

        self.assertEqual(
            worker.calls,
            [
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "pre",
                },
            ],
        )

    async def test_malformed_entry_skipped(self) -> None:
        """A queue entry missing required fields is logged and
        skipped, not raised; later valid entries still dispatch."""
        bus = _FakeBus()
        worker = _RecordingWorker()

        async with IndexTaskConsumer(message_bus=bus, worker=worker):
            await bus.queue_push(
                MessageBusKeys.index_tasks_queue(),
                {"oops": True},
            )
            await bus.queue_push(
                MessageBusKeys.index_tasks_queue(),
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "d2",
                },
            )
            await bus.publish(MessageBusKeys.index_tasks_signal(), {})
            await asyncio.wait_for(worker.notify.wait(), timeout=2.0)

        self.assertEqual(
            worker.calls,
            [
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "d2",
                },
            ],
        )

    async def test_worker_exception_is_isolated(self) -> None:
        """An exception inside ``worker.process`` for one entry does
        not stop subsequent entries from dispatching."""
        bus = _FakeBus()
        worker = _RecordingWorker()
        worker.fail_on_doc = "boom"

        async with IndexTaskConsumer(message_bus=bus, worker=worker):
            await bus.queue_push(
                MessageBusKeys.index_tasks_queue(),
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "boom",
                },
            )
            await bus.publish(MessageBusKeys.index_tasks_signal(), {})
            # Reset notify so we can wait for the *second* dispatch.
            await _yield_a_few_times()
            worker.notify.clear()

            await bus.queue_push(
                MessageBusKeys.index_tasks_queue(),
                {
                    "user_id": "u",
                    "knowledge_base_id": "kb",
                    "document_id": "ok",
                },
            )
            await bus.publish(MessageBusKeys.index_tasks_signal(), {})
            await asyncio.wait_for(worker.notify.wait(), timeout=2.0)

        doc_ids = [c["document_id"] for c in worker.calls]
        self.assertEqual(doc_ids, ["boom", "ok"])


class TestIndexTaskConsumerLifecycle(IsolatedAsyncioTestCase):
    """Tests covering the ``__aenter__`` / ``__aexit__`` ACM behaviour."""

    async def test_exit_cancels_loop_cleanly(self) -> None:
        """``__aexit__`` cancels the consumer's loop task and returns
        without re-raising the cancellation."""
        bus = _FakeBus()
        worker = _RecordingWorker()
        consumer = IndexTaskConsumer(message_bus=bus, worker=worker)

        # pylint: disable=unnecessary-dunder-call
        await consumer.__aenter__()
        loop_task = consumer._task
        self.assertIsNotNone(loop_task)

        await consumer.__aexit__(None, None, None)

        self.assertIsNone(consumer._task)
        self.assertTrue(loop_task.cancelled() or loop_task.done())

    async def test_exit_drains_inflight_tasks(self) -> None:
        """``__aexit__`` cancels in-flight ``worker.process`` tasks
        and waits for them to settle so exceptions surface in logs
        and the event loop does not close on top of running
        coroutines."""
        bus = _FakeBus()
        worker = _RecordingWorker()
        # Make worker.process slow so the consumer exits while one
        # process call is still mid-flight.
        worker.process_delay = 0.2

        consumer = IndexTaskConsumer(message_bus=bus, worker=worker)
        # pylint: disable=unnecessary-dunder-call
        await consumer.__aenter__()

        await bus.queue_push(
            MessageBusKeys.index_tasks_queue(),
            {
                "user_id": "u",
                "knowledge_base_id": "kb",
                "document_id": "slow",
            },
        )
        await bus.publish(MessageBusKeys.index_tasks_signal(), {})
        # Give the loop a tick to start the worker task.
        await asyncio.sleep(0)
        self.assertGreaterEqual(len(consumer._inflight), 1)

        await consumer.__aexit__(None, None, None)

        # The in-flight set is cleared and the consumer task is gone.
        self.assertEqual(consumer._inflight, set())
        self.assertIsNone(consumer._task)
