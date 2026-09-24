# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :func:`agentscope.app._bus_ops.enqueue_index_task`.

The helper is a three-line composition over two bus primitives —
``queue_push`` and ``publish`` — so the tests verify only that:

- the queued payload carries the three fields the
  :class:`~agentscope.app._service.IndexTaskConsumer` reads;
- a signal is published exactly once per call;
- both primitives reach the production
  :class:`~agentscope.app.message_bus.RedisMessageBus` backend
  (proxied by ``fakeredis``), not just an in-memory mock — keeping
  the test honest against the wire-level contract the worker side
  relies on.

If a future change adds metadata to the payload, this test is the
contract gate: callers are expected to keep ``user_id`` /
``knowledge_base_id`` / ``document_id`` as the primary keys.
"""
import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app._bus_ops import enqueue_index_task
from agentscope.app.message_bus import MessageBusKeys, RedisMessageBus


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    """Construct a :class:`RedisMessageBus` bound to *fr*.

    Args:
        fr (`fakeredis.aioredis.FakeRedis`):
            The fake Redis client to inject into the bus.

    Returns:
        `RedisMessageBus`:
            A bus subclass whose ``__aenter__`` skips the real
            connection setup and binds *fr* as the client.
    """

    class _B(RedisMessageBus):
        """Test-only bus that reuses the supplied fakeredis client."""

        async def __aenter__(
            self,
        ) -> "RedisMessageBus":  # type: ignore[override]
            """Bind the pre-supplied fakeredis client and return self.

            Returns:
                `RedisMessageBus`:
                    This bus, ready for the with-block body.
            """
            self._client = fr
            return self

        async def aclose(self) -> None:
            """Drop the client reference without touching the network."""
            self._client = None

    return _B()


class TestEnqueueIndexTask(IsolatedAsyncioTestCase):
    """Verifies the queue_push + publish composition."""

    async def asyncSetUp(self) -> None:
        """Wire a fakeredis-backed bus into an async exit stack."""
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))

    async def asyncTearDown(self) -> None:
        """Tear the exit stack and the fakeredis client down."""
        await self._stack.aclose()
        await self.fr.aclose()

    async def test_enqueue_pushes_payload_and_publishes_signal(
        self,
    ) -> None:
        """One enqueue puts one structured entry on the durable queue
        and one opaque payload on the signal channel.

        Order matters: the queue push must precede the publish so a
        worker woken by the signal is guaranteed to find the entry
        when it drains. We do not assert order directly (would
        require instrumenting the bus), but we assert both primitives
        landed.
        """
        ready = asyncio.Event()
        received: list[dict] = []

        async def _signal_consumer() -> None:
            """Consume one signal payload and exit."""
            async for payload in self.bus.subscribe(
                MessageBusKeys.index_tasks_signal(),
                on_ready=ready.set,
            ):
                received.append(payload)
                break

        task = asyncio.create_task(_signal_consumer())
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        await enqueue_index_task(
            self.bus,
            user_id="u",
            knowledge_base_id="kb",
            document_id="doc",
        )
        await asyncio.wait_for(task, timeout=2.0)

        # Signal was published.
        self.assertEqual(len(received), 1)

        # Queue holds the structured entry under the well-known key.
        entries = await self.bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
            max_count=10,
        )
        self.assertEqual(len(entries), 1)
        _entry_id, payload = entries[0]
        self.assertEqual(
            payload,
            {
                "user_id": "u",
                "knowledge_base_id": "kb",
                "document_id": "doc",
            },
        )

    async def test_double_enqueue_queues_twice(self) -> None:
        """Two enqueues for the same document leave two entries on
        the queue — the helper is intentionally not deduplicating,
        the worker's lease CAS is the dedup contract."""
        await enqueue_index_task(
            self.bus,
            user_id="u",
            knowledge_base_id="kb",
            document_id="doc",
        )
        await enqueue_index_task(
            self.bus,
            user_id="u",
            knowledge_base_id="kb",
            document_id="doc",
        )
        entries = await self.bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
            max_count=10,
        )
        self.assertEqual(len(entries), 2)
