# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for cancelled Redis Pub/Sub cleanup."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

import anyio

from agentscope.app.message_bus import RedisMessageBus


class RedisSubscribeCleanupTest(IsolatedAsyncioTestCase):
    """Subscription cleanup must return connections despite cancellation."""

    async def asyncSetUp(self) -> None:
        """Prepare a subscription with one available payload."""
        self.pubsub = Mock()
        self.pubsub.subscribe = AsyncMock()
        self.pubsub.unsubscribe = AsyncMock()
        self.pubsub.aclose = AsyncMock()
        self.pubsub.get_message = AsyncMock(
            return_value={"type": "message", "data": '{"ready": true}'},
        )
        self.bus = RedisMessageBus()
        self.bus._client = Mock()
        self.bus._client.pubsub.return_value = self.pubsub
        self.subscription = self.bus.subscribe("channel")
        self.assertEqual(await anext(self.subscription), {"ready": True})

    async def test_unsubscribe_failure_still_closes(self) -> None:
        """An unsubscribe error must not skip connection release."""
        self.pubsub.unsubscribe.side_effect = RuntimeError("unsubscribe")
        with self.assertRaisesRegex(RuntimeError, "unsubscribe"):
            await self.subscription.aclose()
        self.pubsub.aclose.assert_awaited_once()

    async def test_cancellation_during_close_does_not_cancel_release(
        self,
    ) -> None:
        """The close task survives cancellation while waiting for a socket."""
        started = asyncio.Event()
        finish = asyncio.Event()
        released = asyncio.Event()

        async def close() -> None:
            started.set()
            await finish.wait()
            released.set()

        self.pubsub.aclose.side_effect = close
        task = asyncio.create_task(self.subscription.aclose())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        finish.set()
        await asyncio.wait_for(released.wait(), 1)

    async def test_cancelled_anyio_scope_still_releases(self) -> None:
        """Repeated SSE-style cancellation cannot interrupt pool release."""
        released = asyncio.Event()

        async def unsubscribe() -> None:
            await asyncio.sleep(0)

        async def close() -> None:
            await asyncio.sleep(0)
            released.set()

        self.pubsub.unsubscribe.side_effect = unsubscribe
        self.pubsub.aclose.side_effect = close
        with anyio.CancelScope() as scope:
            scope.cancel()
            await self.subscription.aclose()
        await asyncio.wait_for(released.wait(), 1)
        self.pubsub.aclose.assert_awaited_once()
