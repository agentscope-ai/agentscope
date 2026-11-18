# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Subscription ownership does not depend on platform iterator consumption."""
import asyncio
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import channel_clients_test as fixture
from agentscope.app.channel import ChannelClients, ChannelTypeRegistry
from agentscope.app.channel._stream import open_reply_stream
from agentscope.app.message_bus import InMemoryMessageBus


class DeliveryCleanupTest(IsolatedAsyncioTestCase):
    """Cancel streams and queued sends without leaving live subscriptions."""

    async def asyncSetUp(self) -> None:
        self.bus = InMemoryMessageBus()
        self.addAsyncCleanup(self.bus.aclose)
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        owner = self

        class Channel(fixture._FakeChannel):
            """Block before consuming the stream, like a platform request."""

            async def send_response(self, event: Any, events: Any) -> None:
                owner.entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    owner.closed.set()

        self.clients = ChannelClients(
            fixture._Storage(fixture._record()),
            self.bus,
            ChannelTypeRegistry([Channel]),
        )
        self.addAsyncCleanup(self.clients.__aexit__, None, None, None)

    async def deliver(self) -> None:
        """Start a delivery with the default persisted-checkpoint behavior."""
        await self.clients.deliver(
            session_id="s",
            channel_id="chan-1",
            chat_id="chat",
            agent_id="agent",
        )

    async def test_close_unstarted_reader(self) -> None:
        """An explicit close releases a feeder before the first anext."""
        stream = await open_reply_stream(self.bus, "s")
        self.assertTrue(any(self.bus._subscribers.values()))
        await stream.aclose()
        self.assertFalse(any(self.bus._subscribers.values()))

    async def test_cancel_before_send_task_first_execution(self) -> None:
        """Cancel before the send task gets its first execution."""
        await self.deliver()
        task = next(iter(self.clients._deliveries))
        stream = self.clients._streams[task]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(stream.subscription, return_exceptions=True)
        self.assertFalse(self.entered.is_set())
        self.assertFalse(any(self.bus._subscribers.values()))
        self.assertFalse(self.clients._deliveries)

    async def test_shutdown_cancels_predecessor_and_waiting_successor(
        self,
    ) -> None:
        """Queued sends own subscriptions before platform work starts."""
        await self.deliver()
        await asyncio.wait_for(self.entered.wait(), 5)
        await self.deliver()
        self.assertEqual(len(self.clients._deliveries), 2)
        await asyncio.wait_for(self.clients.__aexit__(None, None, None), 5)
        self.assertTrue(self.closed.is_set())
        self.assertFalse(self.clients._deliveries)
        self.assertFalse(self.clients._streams)
        self.assertFalse(self.clients._send_tails)
        self.assertFalse(any(self.bus._subscribers.values()))

    async def test_cancel_waiting_send_does_not_cancel_predecessor(
        self,
    ) -> None:
        """A queued task cancellation stays local to that task."""
        await self.deliver()
        await asyncio.wait_for(self.entered.wait(), 5)
        predecessor = self.clients._send_tails["s"]
        await self.deliver()
        successor = self.clients._send_tails["s"]
        # A barrier callback ensures the successor has entered shield().
        ready = asyncio.Event()
        asyncio.get_running_loop().call_soon(ready.set)
        await ready.wait()
        successor.cancel()
        await asyncio.gather(successor, return_exceptions=True)
        self.assertFalse(predecessor.done())
        self.assertIs(self.clients._send_tails["s"], predecessor)

    async def test_shutdown_joins_cleanup_of_an_unstarted_cancelled_send(
        self,
    ) -> None:
        """Retain ownership until a cancelled subscription finishes closing."""
        closing = asyncio.Event()
        release = asyncio.Event()
        original = self.bus.subscribe

        async def subscription(*args: Any, **kwargs: Any) -> Any:
            try:
                async for event in original(*args, **kwargs):
                    yield event
            finally:
                closing.set()
                await release.wait()

        with patch.object(self.bus, "subscribe", new=subscription):
            await self.deliver()
            task = next(iter(self.clients._deliveries))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(closing.wait(), 5)
            shutdown = asyncio.create_task(
                self.clients.__aexit__(None, None, None),
            )
            ready = asyncio.Event()
            asyncio.get_running_loop().call_soon(ready.set)
            await ready.wait()
            self.assertFalse(shutdown.done())
            release.set()
            await asyncio.wait_for(shutdown, 5)
        self.assertFalse(self.clients._streams)
        self.assertFalse(any(self.bus._subscribers.values()))
