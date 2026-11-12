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
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import BaseModel

from agentscope.app._router._channel import update_channel
from agentscope.app._router._schema import UpdateChannelRequest
from agentscope.app._service import ChannelService
from agentscope.app.channel import (
    ChannelBase,
    ChannelClients,
    ChannelError,
    ChannelEvent,
    ChannelHeartbeat,
    ChannelStatus,
    ChannelTypeRegistry,
)
from agentscope.app.channel._base import LIVENESS_TTL_SECS
from agentscope.app.channel._approval import (
    ChannelApprovalRecord,
    forget_approval,
    new_approval_id,
    purge_session_approvals,
    store_approval,
    load_approval,
    load_reply_requester,
    prune_session_approvals,
    remember_reply_requester,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionSettings,
)
from agentscope.event import (
    EventType,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    UserMsg,
)
from agentscope.state import AgentState


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
        self.state = AgentState(
            reply_id="reply-1",
            context=[
                AssistantMsg(
                    id="reply-1",
                    name="assistant",
                    content=[
                        ToolCallBlock(
                            id="tool-1",
                            name="Bash",
                            input="{}",
                            state="asking",
                        ),
                    ],
                ),
            ],
        )

    async def get_session(self, **_: Any) -> Any:
        """Return the latest persisted session, or a deleted session."""
        return SimpleNamespace(state=self.state) if self.state else None

    async def get_agent(self, **_: Any) -> Any:
        """Return the agent owning the persisted state."""
        return SimpleNamespace(data=SimpleNamespace(name="assistant"))

    async def get_channel(
        self,
        channel_id: str,  # pylint: disable=unused-argument
    ) -> ChannelRecord | None:
        """Return the single record this stub serves."""
        self.calls += 1
        return self.record

    async def upsert_channel(
        self,
        record: ChannelRecord,
        platform_bot_id: str,  # pylint: disable=unused-argument
    ) -> str:
        """Round-trip the record through the storage serialization format."""
        self.record = ChannelRecord.model_validate_json(
            record.model_dump_json(),
        )
        return record.id


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

    # These tests exercise the client's internal delivery and storage paths.
    # pylint: disable=protected-access

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

    async def test_approval_state_is_server_backed_and_keeps_requester(
        self,
    ) -> None:
        """Cards carry only opaque ids, while requester and routing stay
        in the bus and survive a continuation of the same reply."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)

        async def events() -> AsyncIterator[dict]:
            for event in (
                ReplyStartEvent(
                    session_id="s-1",
                    reply_id="reply-1",
                    name="assistant",
                ),
                RequireUserConfirmEvent(
                    reply_id="reply-1",
                    tool_calls=[
                        ToolCallBlock(
                            id="tool-1",
                            name="Bash",
                            input="{}",
                        ),
                    ],
                ),
            ):
                yield event.model_dump(mode="json")

        first = [
            raw
            async for raw in clients._approval_events(  # pylint: disable=W0212
                events(),
                requester_id="requester-1",
                channel_id="chan-1",
                chat_id="chat-1",
                agent_id="agent-x",
                session_id="s-1",
            )
        ]
        approval_id = first[1]["metadata"]["channel_approval_ids"]["tool-1"]
        self.assertRegex(approval_id, r"^s-1:[0-9a-f]{32}$")
        approval = await load_approval(bus, approval_id)
        self.assertIsNotNone(approval)
        self.assertDictEqual(
            approval.model_dump(),
            {
                "channel_id": "chan-1",
                "chat_id": "chat-1",
                "agent_id": "agent-x",
                "session_id": "s-1",
                "reply_id": "reply-1",
                "tool_call_id": "tool-1",
                "requester_id": "requester-1",
            },
        )

        continued = [
            raw
            async for raw in clients._approval_events(  # pylint: disable=W0212
                events(),
                requester_id="",
                channel_id="chan-1",
                chat_id="chat-1",
                agent_id="agent-x",
                session_id="s-1",
            )
        ]
        continued_id = continued[1]["metadata"]["channel_approval_ids"][
            "tool-1"
        ]
        continued_approval = await load_approval(bus, continued_id)
        self.assertIsNotNone(continued_approval)
        self.assertDictEqual(
            continued_approval.model_dump(),
            {
                "channel_id": "chan-1",
                "chat_id": "chat-1",
                "agent_id": "agent-x",
                "session_id": "s-1",
                "reply_id": "reply-1",
                "tool_call_id": "tool-1",
                "requester_id": "requester-1",
            },
        )

    async def test_approval_creation_uses_saved_state_under_session_lock(
        self,
    ) -> None:
        """Create approvals only from saved state under the session lock."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)
        calls = [
            ToolCallBlock(id=call_id, name="Bash", input="{}", state="asking")
            for call_id in ("resolved", "pending")
        ]
        clients._storage.state = AgentState(
            reply_id="reply-1",
            context=[
                AssistantMsg(id="reply-1", name="assistant", content=calls),
            ],
        )

        async def deliver() -> list[dict]:
            async def events() -> AsyncIterator[dict]:
                yield RequireUserConfirmEvent(
                    reply_id="reply-1",
                    tool_calls=calls,
                ).model_dump(mode="json")

            return [
                raw
                async for raw in clients._approval_events(
                    events(),
                    requester_id="requester-1",
                    channel_id="chan-1",
                    chat_id="chat-1",
                    agent_id="agent-x",
                    session_id="s-1",
                )
            ]

        [first] = await deliver()
        ids = first["metadata"]["channel_approval_ids"]
        async with bus.acquire_lock(MessageBusKeys.session_lock("s-1")):
            late = asyncio.create_task(deliver())
            await asyncio.sleep(0)
            self.assertFalse(late.done())
            clients._storage.state = AgentState(
                reply_id="reply-1",
                context=[
                    AssistantMsg(
                        id="reply-1",
                        name="assistant",
                        content=[calls[1]],
                    ),
                ],
            )
            await prune_session_approvals(
                bus,
                "s-1",
                clients._storage.state,
                "assistant",
            )
        [delivered] = await late
        self.assertIsNone(await load_approval(bus, ids["resolved"]))
        self.assertIsNotNone(await load_approval(bus, ids["pending"]))
        self.assertListEqual(
            [tool["id"] for tool in delivered["tool_calls"]],
            ["pending"],
        )

        # A delivery delayed until after deletion cannot recreate records.
        clients._storage.state = None
        self.assertListEqual(await deliver(), [])

    async def test_external_pause_keeps_requester_for_later_approval(
        self,
    ) -> None:
        """Retain the requester while waiting for external execution."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)
        clients._storage.state.context[-1].content[
            0
        ].state = ToolCallState.SUBMITTED

        async def events() -> AsyncIterator[dict]:
            yield {
                "type": EventType.REQUIRE_EXTERNAL_EXECUTION,
                "reply_id": "reply-1",
            }

        async for _ in clients._approval_events(
            events(),
            requester_id="requester-1",
            channel_id="chan-1",
            chat_id="chat-1",
            agent_id="agent-x",
            session_id="s-1",
        ):
            pass
        await prune_session_approvals(
            bus,
            "s-1",
            clients._storage.state,
            "assistant",
        )
        self.assertEqual(
            await load_reply_requester(
                bus,
                session_id="s-1",
                reply_id="reply-1",
            ),
            "requester-1",
        )

    async def test_reply_end_waits_for_persistence_to_remove_requester(
        self,
    ) -> None:
        """Leave completion cleanup to the state persistence path."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)
        await remember_reply_requester(
            bus,
            session_id="s-1",
            reply_id="reply-1",
            requester_id="requester-1",
        )

        async def events() -> AsyncIterator[dict]:
            yield {"type": EventType.REPLY_END, "reply_id": "reply-1"}

        async for _ in clients._approval_events(
            events(),
            requester_id="requester-1",
            channel_id="chan-1",
            chat_id="chat-1",
            agent_id="agent-x",
            session_id="s-1",
        ):
            pass

        self.assertEqual(
            await load_reply_requester(
                bus,
                session_id="s-1",
                reply_id="reply-1",
            ),
            "requester-1",
        )
        await prune_session_approvals(
            bus,
            "s-1",
            AgentState(),
            "assistant",
            completed_reply_ids=["reply-1"],
        )
        self.assertEqual(
            await load_reply_requester(
                bus,
                session_id="s-1",
                reply_id="reply-1",
            ),
            "",
        )

    async def test_web_and_background_inputs_have_no_channel_requester(
        self,
    ) -> None:
        """Web UI and background runs have no platform requester identity."""
        clients = self._clients(InMemoryMessageBus())
        message = UserMsg(name="user", content="hello")
        for run_input in (None, message, [message], []):
            with self.subTest(run_input=run_input):
                self.assertEqual(
                    await clients._requester_for_input("s-1", run_input),
                    "",
                )

    async def test_missing_requester_creates_no_approval_records(self) -> None:
        """Deliver identity-free confirmations without approval records."""
        clients = self._clients(InMemoryMessageBus())
        raw = RequireUserConfirmEvent(
            reply_id="reply-1",
            tool_calls=[ToolCallBlock(id="tool-1", name="Bash", input="{}")],
        ).model_dump(mode="json")

        async def events() -> AsyncIterator[dict]:
            yield raw

        with patch(
            "agentscope.app.channel._clients.store_approval",
            new_callable=AsyncMock,
        ) as store:
            delivered = [
                event
                async for event in clients._approval_events(
                    events(),
                    requester_id="",
                    channel_id="chan-1",
                    chat_id="chat-1",
                    agent_id="agent-x",
                    session_id="s-1",
                )
            ]
        store.assert_not_awaited()
        self.assertListEqual(
            delivered,
            [
                {
                    **raw,
                    "metadata": {
                        **raw["metadata"],
                        "channel_approval_ids": {},
                    },
                },
            ],
        )

    async def test_requester_is_resolved_from_run_input(self) -> None:
        """Fresh messages name the requester; resume events recover it."""
        bus = InMemoryMessageBus()
        clients = self._clients(bus)
        message = UserMsg(
            name="requester-1",
            content=[TextBlock(text="hello")],
        )
        self.assertEqual(
            await clients._requester_for_input(  # pylint: disable=W0212
                "s-1",
                message,
            ),
            "requester-1",
        )
        await remember_reply_requester(
            bus,
            session_id="s-1",
            reply_id="reply-1",
            requester_id="requester-1",
        )
        self.assertDictEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_requesters("s-1"),
            ),
            {"reply-1": "requester-1"},
        )
        self.assertDictEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_approvals("s-1"),
            ),
            {},
        )
        self.assertEqual(
            await clients._requester_for_input(  # pylint: disable=W0212
                "s-1",
                UserInterruptEvent(reply_id="reply-1"),
            ),
            "requester-1",
        )


class ChannelUpdateTest(IsolatedAsyncioTestCase):
    """Channel updates validate before writing or notifying listeners."""

    def setUp(self) -> None:
        """Reuse the fake channel and storage without starting an app."""
        self.record = _record()
        self.record.name = "my bot"
        self.storage = _Storage(self.record)
        bus = InMemoryMessageBus()
        self.registry = ChannelTypeRegistry([_FakeChannel])
        self.service = ChannelService(self.storage, bus, self.registry)
        self.write = self.enterContext(
            patch.object(
                self.storage,
                "upsert_channel",
                wraps=self.storage.upsert_channel,
            ),
        )
        self.publish = self.enterContext(patch.object(bus, "publish"))

    async def test_null_required_fields_leave_storage_unchanged(self) -> None:
        """Reject the whole patch, including otherwise valid changes."""
        before = self.record.model_dump()
        for field in ("routing", "session", "platform_config", "enabled"):
            with self.subTest(field=field):
                with self.assertRaises(ChannelError) as caught:
                    await self.service.update(
                        self.record.id,
                        {field: None, "name": "changed"},
                    )
                self.assertEqual(caught.exception.status_code, 422)
                self.write.assert_not_awaited()
                self.publish.assert_not_awaited()
                stored = await self.storage.get_channel(self.record.id)
                self.assertEqual(stored.model_dump(), before)

    async def test_valid_updates_round_trip(self) -> None:
        """Clearing a name and changing enabled preserve all other fields."""
        patches: list[dict] = [{"name": None}, {"enabled": False}]
        for updates in patches:
            with self.subTest(updates=updates):
                before = self.storage.record.model_dump()
                updated = await self.service.update(self.record.id, updates)
                expected = {
                    **before,
                    **updates,
                    "updated_at": updated.updated_at,
                }
                self.assertEqual(updated.model_dump(), expected)
                stored = await self.storage.get_channel(self.record.id)
                self.assertEqual(stored.model_dump(), expected)
                self.write.assert_awaited_with(updated, "bot-1")
                self.publish.assert_awaited_with(
                    MessageBusKeys.channel_lifecycle(),
                    {"channel_id": self.record.id},
                )

    async def test_router_maps_invalid_update_to_422(self) -> None:
        """The route translates service validation errors to HTTP errors."""
        with self.assertRaises(HTTPException) as caught:
            await update_channel(
                self.record.id,
                UpdateChannelRequest(routing=None),
                storage=self.storage,
                service=self.service,
                registry=self.registry,
                user_id=self.record.user_id,
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.write.assert_not_awaited()
        self.publish.assert_not_awaited()


class ChannelStatusTest(IsolatedAsyncioTestCase):
    """Status is read from the heartbeat, not from local instances."""

    def _service(
        self,
        bus: InMemoryMessageBus,
        enabled: bool = True,
    ) -> ChannelService:
        return ChannelService(
            storage=_Storage(_record(enabled=enabled)),
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

    async def test_an_enabled_channel_with_no_report_is_connecting(
        self,
    ) -> None:
        """A channel just created has not been picked up yet. Calling
        that stopped sends the operator looking for something to start
        that is already starting."""
        bus = InMemoryMessageBus()
        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="connecting"),
        )

    async def test_a_disabled_channel_with_no_report_is_stopped(
        self,
    ) -> None:
        """Nothing is holding it, and nothing is meant to."""
        bus = InMemoryMessageBus()
        self.assertEqual(
            await self._service(bus, enabled=False).get_status("chan-1"),
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

    async def test_only_stale_reports_fall_back_to_the_enabled_flag(
        self,
    ) -> None:
        """Every holder went away; nothing fresh is left to believe, so
        this reads like never having been reported at all."""
        bus = InMemoryMessageBus()
        await self._beat(
            bus,
            "worker-a",
            "connected",
            age_secs=LIVENESS_TTL_SECS + 1,
        )

        self.assertEqual(
            await self._service(bus).get_status("chan-1"),
            ChannelStatus(state="connecting"),
        )
        self.assertEqual(
            await self._service(bus, enabled=False).get_status("chan-1"),
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


class ApprovalTokenStorageTest(IsolatedAsyncioTestCase):
    """Session tokens locate one record without a reverse index."""

    async def test_session_namespace_contains_the_complete_record(
        self,
    ) -> None:
        """Store each complete approval once within its session."""
        bus = InMemoryMessageBus()
        session_id = "session:with/slash.中文"
        token = new_approval_id(session_id)
        record = ChannelApprovalRecord(
            channel_id="channel",
            chat_id="chat",
            agent_id="agent",
            session_id=session_id,
            reply_id="reply",
            tool_call_id="call",
            requester_id="requester",
        )
        await store_approval(bus, token, record)
        self.assertDictEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_approvals(session_id),
            ),
            {token: record.model_dump_json()},
        )
        self.assertEqual(await load_approval(bus, token), record)
        self.assertNotEqual(new_approval_id(session_id), token)
        with self.assertRaises(ValueError):
            await store_approval(
                bus,
                token,
                record.model_copy(update={"session_id": "other"}),
            )
        await forget_approval(bus, token)
        self.assertIsNone(await load_approval(bus, token))

    async def test_malformed_tokens_are_rejected(self) -> None:
        """Reject malformed tokens without reading another namespace."""
        bus = InMemoryMessageBus()
        for token in (
            "old-random-token",
            ":",
            "session:abc",
            "%GG:" + "a" * 32,
        ):
            with self.subTest(token=token):
                self.assertIsNone(await load_approval(bus, token))
                await forget_approval(bus, token)

    async def test_purge_keeps_other_sessions(self) -> None:
        """Purging one session preserves approvals in other sessions."""
        bus = InMemoryMessageBus()
        tokens = {}
        for session_id in ("session-a", "session-b"):
            token = new_approval_id(session_id)
            tokens[session_id] = token
            await store_approval(
                bus,
                token,
                ChannelApprovalRecord(
                    channel_id="channel",
                    chat_id="chat",
                    agent_id="agent",
                    session_id=session_id,
                    reply_id="reply",
                    tool_call_id="call",
                    requester_id="requester",
                ),
            )
        await purge_session_approvals(bus, "session-a")
        self.assertIsNone(await load_approval(bus, tokens["session-a"]))
        self.assertIsNotNone(await load_approval(bus, tokens["session-b"]))
