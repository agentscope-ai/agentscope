# -*- coding: utf-8 -*-
"""Tests for the shared WhatsApp webhook ingress path."""
import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
FastAPI = fastapi.FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from agentscope.app._router._whatsapp_webhook import (
    create_whatsapp_webhook_router,
)
from agentscope.app.channel import (
    ChannelEvent,
    ChannelLifecycleDispatcher,
    ChannelTypeRegistry,
    WhatsAppChannel,
)
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage import (
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


def _signed_body(payload: dict) -> tuple[bytes, str]:
    """Serialize a payload exactly as the request body and sign it."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={digest}"


def test_webhook_enqueues_without_a_local_channel() -> None:
    """Ingress resolves shared storage and never needs a dispatcher."""
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
    app.state.message_bus.queue_push.assert_awaited_once()
    queued = app.state.message_bus.queue_push.await_args.args
    assert queued[0] == MessageBusKeys.channel_webhook_queue(record.id)
    assert queued[1]["channel_id"] == record.id
    assert queued[1]["message_id"] == "wamid-1"
    app.state.message_bus.publish.assert_awaited_once_with(
        MessageBusKeys.channel_webhook_signal(),
        {"accepted": 1, "channel_ids": [record.id]},
    )


def test_create_app_registers_webhook_only_for_whatsapp() -> None:
    """The built-in route follows the configured channel registry."""
    from agentscope.app import create_app
    from agentscope.app.message_bus import InMemoryMessageBus
    from agentscope.app.workspace_manager import WorkspaceManagerBase

    storage = AsyncMock()
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=MagicMock(spec=WorkspaceManagerBase),
        channels=[WhatsAppChannel],
        enable_index_worker=False,
    )
    paths = set(app.openapi()["paths"])
    assert "/webhooks/whatsapp" in paths

    app_without_whatsapp = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=MagicMock(spec=WorkspaceManagerBase),
        enable_index_worker=False,
    )
    assert "/webhooks/whatsapp" not in app_without_whatsapp.openapi()[
        "paths"
    ]


def test_inbound_consumer_uses_shared_record_without_local_instance() -> None:
    """A worker can normalize a queued message without hosting the channel."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.try_lock.return_value = True
    bus.registry_exists.return_value = False
    gateway = AsyncMock()
    gateway.process_with_result.return_value = True
    event = ChannelEvent(
        channel_id=record.id,
        channel_user_id="8613800138000",
        chat_id="8613800138000",
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
        asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-1",
                    "payload": {},
                },
            ),
        )

    gateway.process_with_result.assert_awaited_once_with(event)
    bus.registry_set.assert_awaited_once_with(
        MessageBusKeys.channel_webhook_dedupe(record.id),
        "wamid-1",
        "1",
        ttl_secs=7 * 24 * 60 * 60,
    )
    bus.unlock.assert_awaited_once()


def test_failed_gateway_processing_is_not_marked_as_deduplicated() -> None:
    """A failed gateway delivery remains eligible for a later retry."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.try_lock.return_value = True
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
        chat_id="8613800138000",
        content=[],
    )
    with patch.object(
        WhatsAppChannel,
        "normalize_webhook",
        new=AsyncMock(return_value=[event]),
    ):
        asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-failed",
                    "payload": {},
                },
            ),
        )

    bus.registry_set.assert_not_awaited()
    bus.unlock.assert_awaited_once()


def test_failed_webhook_normalization_is_not_marked_as_deduplicated() -> None:
    """A media normalization failure remains eligible for retry."""
    record = _record()
    storage = AsyncMock()
    storage.get_channel.return_value = record
    bus = AsyncMock()
    bus.try_lock.return_value = True
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
        asyncio.run(
            dispatcher._process_webhook_job(
                {
                    "channel_id": record.id,
                    "message_id": "wamid-media-failed",
                    "payload": {},
                },
            ),
        )

    bus.registry_set.assert_not_awaited()
    bus.unlock.assert_awaited_once()


def test_inbound_drain_processes_jobs_in_queue_order() -> None:
    """Only one worker advances the shared inbound queue at a time."""
    bus = AsyncMock()
    bus.try_lock.return_value = True
    bus.queue_drain.side_effect = [
        [("1-0", {"message_id": "first"})],
        [("2-0", {"message_id": "second"})],
        [],
    ]
    dispatcher = ChannelLifecycleDispatcher(
        storage=AsyncMock(),
        message_bus=bus,
        type_registry=ChannelTypeRegistry([WhatsAppChannel]),
        gateway=AsyncMock(),
    )

    @asynccontextmanager
    async def acquire_lock(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[None]:
        yield

    bus.acquire_lock = acquire_lock

    with patch.object(
        dispatcher,
        "_process_webhook_job",
        new=AsyncMock(),
    ) as process:
        asyncio.run(dispatcher._drain_webhook_queue("whatsapp-1"))

    processed_ids = [
        call.args[0]["message_id"] for call in process.await_args_list
    ]
    assert processed_ids == [
        "first",
        "second",
    ]
    assert bus.queue_drain.await_count == 3
    bus.queue_drain.assert_awaited_with(
        MessageBusKeys.channel_webhook_queue("whatsapp-1"),
    )


def test_inbound_subscribes_before_startup_drain() -> None:
    """Persisted queue draining starts only after the signal is subscribed."""
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
