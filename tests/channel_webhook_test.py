# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the shared WhatsApp webhook ingress path."""
import asyncio
import hashlib
import hmac
import json
from typing import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

fastapi = pytest.importorskip("fastapi")
FastAPI = fastapi.FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

# pylint: disable=wrong-import-position
from agentscope.app._router._whatsapp_webhook import (  # noqa: E402
    create_whatsapp_webhook_router,
)
from agentscope.app.channel import (  # noqa: E402
    ChannelEvent,
    ChannelLifecycleDispatcher,
    ChannelTypeRegistry,
    WhatsAppChannel,
)
from agentscope.app.message_bus import (  # noqa: E402
    InMemoryMessageBus,
    MessageBusKeys,
)
from agentscope.app.storage import (  # noqa: E402
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionSettings,
)


def _record() -> ChannelRecord:
    """Build the smallest valid persisted WhatsApp channel record."""
    return ChannelRecord(
        id="whatsapp-1",
        channel_type="whatsapp",
        user_id="alice",
        credentials={
            "phone_number_id": "phone-id",
            "access_token": "access-token",
            "meta_app_secret": "app-secret",
            "verify_token": "verify-token",
        },
        routing=RoutingConfig(
            bindings=[ChannelBinding(agent_id="agent-1")],
        ),
        session=SessionSettings(chat_model_config={}),
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


def _dispatcher(
    bus: object,
    gateway: object | None = None,
) -> ChannelLifecycleDispatcher:
    """Build a dispatcher around a test bus."""
    return ChannelLifecycleDispatcher(
        storage=AsyncMock(),
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=gateway or AsyncMock(),
    )


def _signed_body(payload: dict) -> tuple[bytes, str]:
    """Serialize a payload exactly as the request body and sign it."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={digest}"


def test_webhook_persists_without_a_local_channel() -> None:
    """Ingress persists work before returning and never needs a dispatcher."""
    app = FastAPI()
    record = _record()
    storage = AsyncMock()
    storage.get_channel_id_by_platform_bot_id.return_value = record.id
    storage.get_channel.return_value = record
    app.state.storage = storage
    app.state.message_bus = AsyncMock()
    app.state.channel_type_registry = ChannelTypeRegistry([WhatsAppChannel])
    app.include_router(create_whatsapp_webhook_router())

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "account-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-id"},
                            "messages": [
                                {
                                    "id": "wamid-1",
                                    "from": "8613800138000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                },
                            ],
                        },
                    },
                ],
            },
        ],
    }
    raw, signature = _signed_body(payload)

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/whatsapp",
            content=raw,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": signature,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "accepted": 1}
    app.state.message_bus.log_append.assert_awaited_once()
    persisted = app.state.message_bus.log_append.await_args.args
    assert persisted[0] == MessageBusKeys.channel_webhook_queue(record.id)
    assert persisted[1]["channel_id"] == record.id
    assert persisted[1]["message_id"] == "wamid-1"
    app.state.message_bus.publish.assert_awaited_once_with(
        MessageBusKeys.channel_webhook_signal(),
        {"accepted": 1, "channel_ids": [record.id]},
    )


def test_create_app_registers_webhook_only_for_whatsapp() -> None:
    """The built-in route follows the configured channel registry."""
    from agentscope.app import create_app
    from agentscope.app.workspace_manager import WorkspaceManagerBase

    storage = AsyncMock()
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=MagicMock(spec=WorkspaceManagerBase),
        channels=[WhatsAppChannel],
        enable_index_worker=False,
    )
    assert "/webhooks/whatsapp" in set(app.openapi()["paths"])

    app_without_whatsapp = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=MagicMock(spec=WorkspaceManagerBase),
        enable_index_worker=False,
    )
    assert "/webhooks/whatsapp" not in app_without_whatsapp.openapi()["paths"]


def test_inbound_consumer_uses_independently_expiring_dedupe_key() -> None:
    """A processed wamid gets its own TTL namespace."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.registry_exists.return_value = False
    gateway = AsyncMock()
    gateway.process_with_result.return_value = True
    event = ChannelEvent(
        channel_id=record.id,
        channel_user_id="8613800138000",
        chat_id="",
        content=[],
    )
    dispatcher = ChannelLifecycleDispatcher(
        storage=storage,
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=gateway,
    )

    with patch.object(
        WhatsAppChannel,
        "normalize_webhook",
        new=AsyncMock(return_value=[event]),
    ):
        handled = asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-1",
                    "payload": {},
                },
            ),
        )

    assert handled
    gateway.process_with_result.assert_awaited_once_with(event)
    bus.registry_set.assert_awaited_once_with(
        f"{MessageBusKeys.channel_webhook_dedupe(record.id)}:wamid-1",
        "processed",
        "1",
        ttl_secs=7 * 24 * 60 * 60,
    )


def test_failed_gateway_processing_is_not_acknowledged() -> None:
    """A failed Gateway delivery leaves the durable job retryable."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.registry_exists.return_value = False
    gateway = AsyncMock()
    gateway.process_with_result.return_value = False
    dispatcher = ChannelLifecycleDispatcher(
        storage=storage,
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=gateway,
    )
    event = ChannelEvent(
        channel_id=record.id,
        channel_user_id="8613800138000",
        chat_id="",
        content=[],
    )

    with patch.object(
        WhatsAppChannel,
        "normalize_webhook",
        new=AsyncMock(return_value=[event]),
    ):
        handled = asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-failed",
                    "payload": {},
                },
            ),
        )

    assert not handled
    bus.registry_set.assert_not_awaited()


def test_failed_webhook_normalization_is_not_acknowledged() -> None:
    """A media normalization failure remains eligible for retry."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.registry_exists.return_value = False
    dispatcher = ChannelLifecycleDispatcher(
        storage=storage,
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=AsyncMock(),
    )

    with patch.object(
        WhatsAppChannel,
        "normalize_webhook",
        new=AsyncMock(side_effect=RuntimeError("media download failed")),
    ):
        handled = asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-media-failed",
                    "payload": {},
                },
            ),
        )

    assert not handled
    bus.registry_set.assert_not_awaited()


def test_failed_durable_job_is_consumed_again() -> None:
    """A failed job stays behind the shared cursor for the next drain."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        dispatcher = _dispatcher(bus)
        key = MessageBusKeys.channel_webhook_queue("whatsapp-1")
        entry_id = await bus.log_append(key, {"message_id": "retry-me"})
        process = AsyncMock(side_effect=[False, True])

        with patch.object(dispatcher, "_process_webhook_job", new=process):
            await dispatcher._drain_webhook_queue("whatsapp-1")
            assert process.await_count == 1
            assert await bus.log_read(key) == [
                (entry_id, {"message_id": "retry-me"}),
            ]
            await dispatcher._drain_webhook_queue("whatsapp-1")

        assert process.await_count == 2
        cursor = await bus.registry_get(
            dispatcher._webhook_cursor_namespace("whatsapp-1"),
            "entry_id",
        )
        assert cursor == entry_id
        assert await bus.log_read(key, since=entry_id) == []

    asyncio.run(run())


def test_inbound_drain_preserves_log_order() -> None:
    """One channel advances its shared log cursor in arrival order."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        dispatcher = _dispatcher(bus)
        key = MessageBusKeys.channel_webhook_queue("whatsapp-1")
        await bus.log_append(key, {"message_id": "first"})
        second_id = await bus.log_append(key, {"message_id": "second"})

        with patch.object(
            dispatcher,
            "_process_webhook_job",
            new=AsyncMock(return_value=True),
        ) as process:
            await dispatcher._drain_webhook_queue("whatsapp-1")

        assert [
            item.args[0]["message_id"] for item in process.await_args_list
        ] == ["first", "second"]
        assert (
            await bus.registry_get(
                dispatcher._webhook_cursor_namespace("whatsapp-1"),
                "entry_id",
            )
            == second_id
        )

    asyncio.run(run())


def test_webhook_drain_tasks_are_coalesced_per_channel() -> None:
    """Repeated wakeups reuse one active task instead of piling waiters."""

    async def run() -> None:
        dispatcher = _dispatcher(AsyncMock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def drain(_channel_id: str) -> None:
            started.set()
            await release.wait()

        with patch.object(
            dispatcher,
            "_drain_webhook_queue",
            new=AsyncMock(side_effect=drain),
        ) as mocked:
            dispatcher._spawn_webhook_drain("whatsapp-1")
            await started.wait()
            first = dispatcher._webhook_drains["whatsapp-1"]
            dispatcher._spawn_webhook_drain("whatsapp-1")
            assert dispatcher._webhook_drains["whatsapp-1"] is first
            release.set()
            await first

        assert mocked.await_count == 2

    asyncio.run(run())


def test_shared_consumer_persists_chat_metadata() -> None:
    """Observed chats are stored outside the short-lived normalizer."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.registry_exists.return_value = False
    gateway = AsyncMock()
    gateway.process_with_result.return_value = True
    dispatcher = ChannelLifecycleDispatcher(
        storage=storage,
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=gateway,
    )
    event = ChannelEvent(
        channel_id=record.id,
        channel_user_id="8613800138000",
        chat_id="group-1",
        chat_name="Project",
        content=[],
        metadata={"chat_type": "group"},
    )

    with patch.object(
        WhatsAppChannel,
        "normalize_webhook",
        new=AsyncMock(return_value=[event]),
    ):
        assert asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-group",
                    "payload": {},
                },
            ),
        )

    expected_chat = json.dumps(
        {"chat_type": "group", "chat_name": "Project"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert (
        call(
            MessageBusKeys.channel_seen_chats(record.id),
            "group-1",
            expected_chat,
        )
        in bus.registry_set.await_args_list
    )


def test_inmemory_registry_ttl_expires_namespace() -> None:
    """In-memory registry TTL follows the Redis namespace-TTL contract."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        with patch(
            "agentscope.app.message_bus._in_memory_message_bus.time.monotonic",
            side_effect=[100.0, 102.0],
        ):
            await bus.registry_set("dedupe:1", "processed", "1", ttl_secs=1)
            assert not await bus.registry_exists("dedupe:1", "processed")

    asyncio.run(run())


def test_whatsapp_group_name_populates_channel_event() -> None:
    """The resolved group name reaches the first-class event field."""

    async def run() -> None:
        channel = WhatsAppChannel(
            "whatsapp-1",
            WhatsAppChannel.Credentials(
                phone_number_id="phone-id",
                access_token="access-token",
                verify_token="verify-token",
                meta_app_secret="app-secret",
            ),
            WhatsAppChannel.Config(),
        )
        event = await channel._normalize_message(
            {
                "id": "wamid-group",
                "from": "8613800138000",
                "group_id": "group-1",
                "group_name": "Project",
                "type": "text",
                "text": {"body": "hello"},
            },
            {},
        )
        assert isinstance(event, ChannelEvent)
        assert event.chat_name == "Project"

    asyncio.run(run())


def test_inbound_subscribes_before_startup_drain() -> None:
    """Persisted draining starts only after the signal subscription exists."""
    record = _record()
    storage = AsyncMock()
    storage.list_all_channels.return_value = [record]
    bus = MagicMock()
    subscribed = False

    async def subscribe(
        _key: str,
        *,
        on_ready: Callable[[], None],
    ) -> AsyncIterator[dict]:
        nonlocal subscribed
        subscribed = True
        on_ready()
        await asyncio.Event().wait()
        yield {}

    bus.subscribe = subscribe
    dispatcher = ChannelLifecycleDispatcher(
        storage=storage,
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=AsyncMock(),
    )

    async def run() -> None:
        ready = asyncio.Event()
        with patch.object(dispatcher, "_spawn_webhook_drain") as spawn:
            task = asyncio.create_task(
                dispatcher._consume_webhook_signals(ready),
            )
            await asyncio.wait_for(ready.wait(), timeout=1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert subscribed
            spawn.assert_called_once_with(record.id)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_webhook_queue_and_lock_are_scoped_per_channel() -> None:
    """Slow work for one channel must not serialize another channel."""
    assert MessageBusKeys.channel_webhook_queue("channel-a") != (
        MessageBusKeys.channel_webhook_queue("channel-b")
    )
    assert MessageBusKeys.channel_webhook_drain_lock("channel-a") != (
        MessageBusKeys.channel_webhook_drain_lock("channel-b")
    )
