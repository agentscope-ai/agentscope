# -*- coding: utf-8 -*-
"""Tests for the connection-free half of the channel runtime.

A process that does not hold a channel's long connection must still be
able to use the channel: attach its platform tools to an agent, deliver
a reply, and report its status. These cover the three pieces that make
that work — the client factory, the deliveries it owns, and the status
heartbeat.
"""
import asyncio
import time
from datetime import datetime
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel

from agentscope.app._service import ChannelService
from agentscope.app.channel import (
    ChannelBase,
    ChannelClients,
    ChannelEvent,
    ChannelHeartbeat,
    ChannelStatus,
    ChannelTypeRegistry,
)
from agentscope.app.channel._base import LIVENESS_TTL_SECS
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionSettings,
)


class _FakeChannel(ChannelBase):
    """Records whether anything ever opened its connection."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "bot_id"

    class Credentials(BaseModel):
        """Credentials for the fake platform."""

        bot_id: str

    class Config(BaseModel):
        """Options for the fake platform."""

    def __init__(
        self,
        channel_id: str,
        credentials: "Credentials",
        config: "Config",  # pylint: disable=unused-argument
    ) -> None:
        """Store the identity and start disconnected."""
        self._channel_id = channel_id
        self.bot_id = credentials.bot_id
        self.status = ChannelStatus()
        self.listened = False
        self.closed = False
        self.sent_to = ""
        self.returned = False

    @property
    def channel_id(self) -> str:
        """The unique channel instance identifier."""
        return self._channel_id

    async def start_listening(  # pylint: disable=unused-argument
        self,
        emit: Any,
    ) -> None:
        """Mark that a connection was opened."""
        self.listened = True

    async def aclose(self) -> None:
        """Record that the factory released this instance."""
        self.closed = True

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Record the target, then consume the run's events."""
        self.sent_to = event.chat_id
        async for _ in events:
            pass
        self.returned = True


class _Storage:
    """Storage stub serving one mutable channel record."""

    def __init__(self, record: ChannelRecord | None) -> None:
        self.record = record
        self.calls = 0

    async def get_channel(
        self,
        channel_id: str,  # pylint: disable=unused-argument
    ) -> ChannelRecord | None:
        """Return the single record this stub serves."""
        self.calls += 1
        return self.record


def _record(bot_id: str = "bot-1", enabled: bool = True) -> ChannelRecord:
    """Build a minimal enabled channel record for the fake platform."""
    now = datetime.now().isoformat()
    return ChannelRecord(
        id="chan-1",
        channel_type="fake",
        user_id="owner-1",
        enabled=enabled,
        credentials={"bot_id": bot_id},
        routing=RoutingConfig(
            bindings=[ChannelBinding(match_value="*", agent_id="agent-x")],
        ),
        session=SessionSettings(chat_model_config={"type": "x"}),
        created_at=now,
        updated_at=now,
    )


class ChannelClientsTest(IsolatedAsyncioTestCase):
    """The factory hands out usable channels without connecting."""

    def _clients(self, storage: _Storage) -> ChannelClients:
        return ChannelClients(
            storage=storage,
            message_bus=InMemoryMessageBus(),
            type_registry=ChannelTypeRegistry([_FakeChannel]),
        )

    async def test_builds_without_opening_a_connection(self) -> None:
        """The instance is usable but never listened — that is what lets
        it live in a process that holds no connection."""
        clients = self._clients(_Storage(_record()))

        channel = await clients.get("chan-1")

        self.assertIsInstance(channel, _FakeChannel)
        self.assertFalse(channel.listened)
        self.assertEqual(channel.bot_id, "bot-1")

    async def test_cached_until_the_record_changes(self) -> None:
        """A rotated credential takes effect without a restart."""
        storage = _Storage(_record())
        clients = self._clients(storage)

        first = await clients.get("chan-1")
        self.assertIs(await clients.get("chan-1"), first)

        rotated = _record(bot_id="bot-2")
        rotated.updated_at = "2099-01-01T00:00:00"
        storage.record = rotated

        second = await clients.get("chan-1")
        self.assertIsNot(second, first)
        self.assertEqual(second.bot_id, "bot-2")

    async def test_missing_or_disabled_channel_has_no_client(self) -> None:
        """A disabled channel is dropped from the cache, not served."""
        storage = _Storage(_record())
        clients = self._clients(storage)
        await clients.get("chan-1")

        storage.record = _record(enabled=False)
        self.assertIsNone(await clients.get("chan-1"))

        storage.record = None
        self.assertIsNone(await clients.get("chan-1"))

    async def test_a_replaced_instance_stays_usable_for_borrowers(
        self,
    ) -> None:
        """A run that already took this instance may still be streaming
        a reply through it, so rotation must not close it underneath."""
        storage = _Storage(_record())
        async with self._clients(storage) as clients:
            borrowed = await clients.get("chan-1")

            rotated = _record(bot_id="bot-2")
            rotated.updated_at = "2099-01-01T00:00:00"
            storage.record = rotated
            await clients.get("chan-1")
            self.assertFalse(borrowed.closed)

            storage.record = _record(enabled=False)
            await clients.get("chan-1")
            self.assertFalse(borrowed.closed)

    async def test_shutdown_releases_cached_and_retired_instances(
        self,
    ) -> None:
        """Nothing the factory built outlives it."""
        storage = _Storage(_record())
        async with self._clients(storage) as clients:
            retired = await clients.get("chan-1")

            rotated = _record(bot_id="bot-2")
            rotated.updated_at = "2099-01-01T00:00:00"
            storage.record = rotated
            cached = await clients.get("chan-1")

        self.assertTrue(retired.closed)
        self.assertTrue(cached.closed)

    async def test_unregistered_type_has_no_client(self) -> None:
        """A record whose class this process was not given is skipped."""
        clients = ChannelClients(
            storage=_Storage(_record()),
            message_bus=InMemoryMessageBus(),
            type_registry=ChannelTypeRegistry([]),
        )
        self.assertIsNone(await clients.get("chan-1"))


class ChannelDeliveryTest(IsolatedAsyncioTestCase):
    """Deliveries run in the background but stay owned."""

    def _clients(self, bus: InMemoryMessageBus) -> ChannelClients:
        return ChannelClients(
            storage=_Storage(_record()),
            message_bus=bus,
            type_registry=ChannelTypeRegistry([_FakeChannel]),
        )

    async def _deliver(self, clients: ChannelClients) -> None:
        await clients.deliver(
            session_id="s-1",
            channel_id="chan-1",
            chat_id="chat-1",
            agent_id="agent-x",
        )

    async def test_returns_while_the_reply_is_still_going_out(self) -> None:
        """The caller is mid-run holding the session lock, so it must not
        wait on the platform."""
        bus = InMemoryMessageBus()
        async with self._clients(bus) as clients:
            await self._deliver(clients)
            await asyncio.sleep(0.05)

            channel = await clients.get("chan-1")
            self.assertEqual(channel.sent_to, "chat-1")
            self.assertFalse(channel.returned)

    async def test_shutdown_cancels_a_delivery_in_flight(self) -> None:
        """A delivery outliving the process would be an orphan."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)
        async with clients:
            await self._deliver(clients)
            await asyncio.sleep(0.05)
            channel = await clients.get("chan-1")
            self.assertEqual(channel.sent_to, "chat-1")

        self.assertFalse(channel.returned)
        self.assertEqual(len(clients._deliveries), 0)  # pylint: disable=W0212

    async def test_an_unbuildable_channel_delivers_nothing(self) -> None:
        """A disabled channel must not raise into the run."""
        bus = InMemoryMessageBus()
        storage = _Storage(_record(enabled=False))
        async with ChannelClients(
            storage=storage,
            message_bus=bus,
            type_registry=ChannelTypeRegistry([_FakeChannel]),
        ) as clients:
            await self._deliver(clients)


class ChannelStatusTest(IsolatedAsyncioTestCase):
    """Status is read from the heartbeat, not from local instances."""

    def _service(self, bus: InMemoryMessageBus) -> ChannelService:
        return ChannelService(
            storage=_Storage(_record()),
            message_bus=bus,
            type_registry=ChannelTypeRegistry([_FakeChannel]),
        )

    async def _beat(
        self,
        bus: InMemoryMessageBus,
        node_id: str,
        state: str,
        age_secs: float = 0.0,
    ) -> None:
        """Write one node's report, optionally backdated."""
        await bus.registry_set(
            MessageBusKeys.channel_liveness("chan-1"),
            node_id,
            ChannelHeartbeat(
                status=ChannelStatus(state=state),
                reported_at=time.time() - age_secs,
            ).model_dump_json(),
            ttl_secs=LIVENESS_TTL_SECS,
        )

    async def test_no_heartbeat_reads_as_stopped(self) -> None:
        """Nothing is holding the channel, so nothing reports it."""
        bus = InMemoryMessageBus()
        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="stopped"),
        )

    async def test_reports_the_holder_from_another_node(self) -> None:
        """The reading replica holds no connection of its own."""
        bus = InMemoryMessageBus()
        await self._beat(bus, "worker-a", "connected")

        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="connected"),
        )

    async def test_a_restarted_node_leaves_no_ghost(self) -> None:
        """The namespace TTL expires the hash, not one node's field, so
        a worker that restarted under a fresh id would otherwise report
        ``connected`` forever."""
        bus = InMemoryMessageBus()
        await self._beat(
            bus,
            "worker-a-old",
            "connected",
            age_secs=LIVENESS_TTL_SECS + 1,
        )
        await self._beat(bus, "worker-a-new", "connecting")

        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="connecting"),
        )

    async def test_only_stale_reports_read_as_stopped(self) -> None:
        """Every holder went away; nothing fresh is left to believe."""
        bus = InMemoryMessageBus()
        await self._beat(
            bus,
            "worker-a",
            "connected",
            age_secs=LIVENESS_TTL_SECS + 1,
        )

        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="stopped"),
        )

    async def test_connected_wins_over_a_retrying_node(self) -> None:
        """During a failover one node is still serving; say so."""
        bus = InMemoryMessageBus()
        await self._beat(bus, "worker-a", "retrying")
        await self._beat(bus, "worker-b", "connected")

        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="connected"),
        )
