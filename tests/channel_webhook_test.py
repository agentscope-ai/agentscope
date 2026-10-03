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
from utils import AnyString

fastapi = pytest.importorskip("fastapi")
FastAPI = fastapi.FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

# pylint: disable=wrong-import-position
from agentscope.app._manager import (  # noqa: E402
    ChatRunRegistry,
    WakeupDispatcher,
)
from agentscope.app._router._whatsapp_webhook import (  # noqa: E402
    create_whatsapp_webhook_router,
)
from agentscope.app.channel import (  # noqa: E402
    ChannelClients,
    ChannelEvent,
    ChannelLifecycleDispatcher,
    ChannelTypeRegistry,
    WhatsAppChannel,
)
from agentscope.app.channel._gateway import ChannelGateway  # noqa: E402
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
from agentscope.message import Msg, TextBlock, UserMsg  # noqa: E402


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


def _text_block(text: str) -> dict:
    """Expected serialized TextBlock with nondeterministic fields."""
    return {
        "id": AnyString(),
        "text": text,
        "type": "text",
        "created_at": AnyString(),
        "finished_at": None,
    }


def _user_input(message_id: str, name: str, text: str) -> dict:
    """Expected serialized channel UserMsg."""
    return {
        "id": message_id,
        "name": name,
        "content": [_text_block(text)],
        "role": "user",
        "metadata": {},
        "created_at": AnyString(),
        "finished_at": AnyString(),
        "finished_reason": None,
        "structured_output": None,
        "error": None,
        "usage": None,
    }


def _channel_event(
    *,
    message_id: str,
    text: str,
    chat_id: str,
    chat_name: str = "",
    chat_type: str = "private",
) -> dict:
    """Expected serialized WhatsApp ChannelEvent."""
    return {
        "channel_id": "whatsapp-1",
        "channel_user_id": "8613800138000",
        "channel_user_name": "",
        "chat_id": chat_id,
        "chat_name": chat_name,
        "channel_message_id": message_id,
        "content": [_text_block(text)],
        "metadata": {
            "chat_type": chat_type,
            "chat_name": chat_name,
            "display_phone_number": "",
        },
        "received_at": AnyString(),
    }


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

    message = {
        "id": "wamid-1",
        "from": "8613800138000",
        "type": "text",
        "text": {"body": "hello"},
    }
    change = {
        "field": "messages",
        "value": {
            "metadata": {"phone_number_id": "phone-id"},
            "messages": [message],
        },
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "account-1", "changes": [change]}],
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
    app.state.message_bus.log_append.assert_awaited_once_with(
        MessageBusKeys.channel_webhook_queue(record.id),
        {
            "channel_id": record.id,
            "message_id": "wamid-1",
            "payload": payload,
        },
    )
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


def test_failed_durable_job_retries_without_another_signal() -> None:
    """A retained failure retries autonomously without later traffic."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        dispatcher = _dispatcher(bus)
        key = MessageBusKeys.channel_webhook_queue("whatsapp-1")
        entry_id = await bus.log_append(key, {"message_id": "retry-me"})
        process = AsyncMock(side_effect=[False, True])

        with (
            patch.object(dispatcher, "_process_webhook_job", new=process),
            patch(
                "agentscope.app.channel._dispatcher.WEBHOOK_RETRY_BASE_SECS",
                0.0,
            ),
        ):
            await dispatcher._run_webhook_drain("whatsapp-1")

        assert process.await_args_list == [
            call({"message_id": "retry-me"}),
            call({"message_id": "retry-me"}),
        ]
        assert (
            await bus.registry_get(
                dispatcher._webhook_cursor_namespace("whatsapp-1"),
                "entry_id",
            )
            == entry_id
        )
        assert await bus.log_read(key, since=entry_id) == []

    asyncio.run(run())


def test_poison_webhook_is_dead_lettered_and_does_not_block_later_work() -> (
    None
):
    """A bounded poison retry eventually advances to the next log entry."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        dispatcher = _dispatcher(bus)
        key = MessageBusKeys.channel_webhook_queue("whatsapp-1")
        poison = {"message_id": "poison"}
        good = {"message_id": "good"}
        poison_id = await bus.log_append(key, poison)
        good_id = await bus.log_append(key, good)

        async def process(job: dict) -> bool:
            return job["message_id"] == "good"

        with (
            patch.object(
                dispatcher,
                "_process_webhook_job",
                new=AsyncMock(side_effect=process),
            ) as mocked,
            patch(
                "agentscope.app.channel._dispatcher.WEBHOOK_MAX_ATTEMPTS",
                2,
            ),
            patch(
                "agentscope.app.channel._dispatcher.WEBHOOK_RETRY_BASE_SECS",
                0.0,
            ),
        ):
            await dispatcher._run_webhook_drain("whatsapp-1")

        assert mocked.await_args_list == [
            call(poison),
            call(poison),
            call(good),
        ]
        assert await bus.log_read(
            dispatcher._webhook_dead_letter_log("whatsapp-1"),
        ) == [
            (
                AnyString(),
                {
                    "source_entry_id": poison_id,
                    "attempts": 2,
                    "job": poison,
                },
            ),
        ]
        assert (
            await bus.registry_get(
                dispatcher._webhook_cursor_namespace("whatsapp-1"),
                "entry_id",
            )
            == good_id
        )

    asyncio.run(run())


def test_inbound_drain_preserves_log_order() -> None:
    """One channel advances its shared log cursor in arrival order."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        dispatcher = _dispatcher(bus)
        key = MessageBusKeys.channel_webhook_queue("whatsapp-1")
        first = {"message_id": "first"}
        second = {"message_id": "second"}
        await bus.log_append(key, first)
        second_id = await bus.log_append(key, second)

        with patch.object(
            dispatcher,
            "_process_webhook_job",
            new=AsyncMock(return_value=True),
        ) as process:
            await dispatcher._drain_webhook_queue("whatsapp-1")

        assert process.await_args_list == [call(first), call(second)]
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

        assert mocked.await_args_list == [
            call("whatsapp-1"),
            call("whatsapp-1"),
        ]

    asyncio.run(run())


def test_gateway_persists_chat_metadata_before_stable_run_trigger() -> None:
    """Shared chat metadata lands before a stable channel user turn."""

    async def run() -> None:
        record = _record()
        storage = AsyncMock()
        storage.get_channel.return_value = record
        storage.get_session.return_value = object()
        bus = AsyncMock()
        bus.is_locked.return_value = False
        bus.queue_drain.return_value = []
        order: list[str] = []

        async def registry_set(*_args: object, **_kwargs: object) -> None:
            order.append("metadata")

        async def queue_push(*_args: object, **_kwargs: object) -> str:
            order.append("trigger")
            return "1-0"

        bus.registry_set.side_effect = registry_set
        bus.queue_push.side_effect = queue_push
        gateway = ChannelGateway(storage, bus, MagicMock())
        event = ChannelEvent(
            channel_id=record.id,
            channel_user_id="8613800138000",
            chat_id="group-1",
            chat_name="Project",
            channel_message_id="wamid-1",
            content=[TextBlock(text="hello")],
            metadata={"chat_type": "group"},
        )

        await gateway._handle_message(event)

        assert order == ["metadata", "trigger"]
        assert bus.queue_push.await_args.args == (
            MessageBusKeys.wakeup_queue(),
            {
                "user_id": "alice",
                "session_id": AnyString(),
                "agent_id": "agent-1",
                "kind": MessageBusKeys.WAKEUP_KIND_MESSAGE,
                "input": _user_input(
                    "channel:whatsapp-1:wamid-1",
                    "8613800138000",
                    "hello",
                ),
            },
        )
        assert bus.registry_set.await_args.args == (
            MessageBusKeys.channel_seen_chats(record.id),
            "group-1",
            json.dumps(
                {"chat_type": "group", "chat_name": "Project"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    asyncio.run(run())


def test_replayed_run_trigger_skips_an_already_persisted_user_message() -> (
    None
):
    """A replayed channel trigger does not create a second Agent turn."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        storage = AsyncMock()
        storage.get_session.return_value = object()
        chat_service = AsyncMock()
        registry = ChatRunRegistry()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat_service,
            chat_run_registry=registry,
        )
        msg = UserMsg(
            id="channel:whatsapp-1:wamid-1",
            name="8613800138000",
            content=[TextBlock(text="hello")],
        )
        storage.get_message.return_value = msg

        await dispatcher._dispatch_one(
            user_id="alice",
            session_id="session-1",
            agent_id="agent-1",
            kind=MessageBusKeys.WAKEUP_KIND_MESSAGE,
            raw_input=msg.model_dump(mode="json"),
        )
        task = registry.get("session-1")
        assert task is not None
        await task

        assert storage.get_message.await_args_list == [
            call(
                "alice",
                "session-1",
                msg.id,
            ),
        ]
        chat_service.run.assert_not_awaited()

    asyncio.run(run())


def test_replayed_gateway_trigger_runs_one_user_turn_after_persistence() -> (
    None
):
    """A replayed trigger is idempotent after the first run persists."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        record = _record()
        storage = AsyncMock()
        storage.get_channel.return_value = record
        storage.get_session.return_value = object()
        gateway = ChannelGateway(storage, bus, MagicMock())
        event = ChannelEvent(
            channel_id=record.id,
            channel_user_id="8613800138000",
            chat_id="direct-1",
            channel_message_id="wamid-replayed",
            content=[TextBlock(text="hello")],
        )

        assert await gateway.process_with_result(event)
        assert await gateway.process_with_result(event)
        entries = await bus.queue_drain(
            MessageBusKeys.wakeup_queue(),
            max_count=10,
        )
        expected_payload = {
            "user_id": "alice",
            "session_id": AnyString(),
            "agent_id": "agent-1",
            "kind": MessageBusKeys.WAKEUP_KIND_MESSAGE,
            "input": _user_input(
                "channel:whatsapp-1:wamid-replayed",
                "8613800138000",
                "hello",
            ),
        }
        assert entries == [
            (AnyString(), expected_payload),
            (AnyString(), expected_payload),
        ]

        persisted_ids: set[str] = set()
        chat_service = AsyncMock()

        async def run_chat(**kwargs: object) -> None:
            input_msg = kwargs["input_msg"]
            assert isinstance(input_msg, Msg)
            assert input_msg.id not in persisted_ids
            persisted_ids.add(input_msg.id)

        chat_service.run.side_effect = run_chat

        async def get_message(
            _user_id: str,
            _session_id: str,
            message_id: str,
        ) -> Msg | None:
            if message_id in persisted_ids:
                return UserMsg(
                    id=message_id,
                    name=event.channel_user_id,
                    content=event.content,
                )
            return None

        storage.get_message.side_effect = get_message
        registry = ChatRunRegistry()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat_service,
            chat_run_registry=registry,
        )

        for _entry_id, payload in entries:
            await dispatcher._dispatch_one(
                user_id=payload["user_id"],
                session_id=payload["session_id"],
                agent_id=payload["agent_id"],
                kind=payload["kind"],
                raw_input=payload["input"],
            )
            task = registry.get(payload["session_id"])
            if task is not None:
                await task

        assert persisted_ids == {"channel:whatsapp-1:wamid-replayed"}
        assert storage.get_message.await_args_list == [
            call(
                "alice",
                entries[0][1]["session_id"],
                "channel:whatsapp-1:wamid-replayed",
            ),
            call(
                "alice",
                entries[1][1]["session_id"],
                "channel:whatsapp-1:wamid-replayed",
            ),
        ]
        run_kwargs = chat_service.run.await_args.kwargs.copy()
        run_kwargs["input_msg"] = run_kwargs["input_msg"].model_dump(
            mode="json",
        )
        assert run_kwargs == {
            "user_id": "alice",
            "session_id": entries[0][1]["session_id"],
            "agent_id": "agent-1",
            "input_msg": _user_input(
                "channel:whatsapp-1:wamid-replayed",
                "8613800138000",
                "hello",
            ),
        }

    asyncio.run(run())


def test_shared_chat_metadata_hydrates_another_client_instance() -> None:
    """An API process can hydrate a client from worker-observed metadata."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        record = _record()
        storage = AsyncMock()
        storage.get_channel.return_value = record
        clients = ChannelClients(
            storage=storage,
            type_registry=ChannelTypeRegistry([WhatsAppChannel]),
            message_bus=bus,
        )
        await bus.registry_set(
            MessageBusKeys.channel_seen_chats(record.id),
            "group-1",
            json.dumps(
                {"chat_type": "group", "chat_name": "Project"},
                separators=(",", ":"),
            ),
        )

        channel = await clients.get(record.id)

        assert isinstance(channel, WhatsAppChannel)
        assert await channel.chat_name("group-1") == "Project"
        assert await channel.list_bot_chats() == [
            {
                "chat_id": "group-1",
                "name": "Project",
                "chat_type": "group",
            },
        ]

    asyncio.run(run())


def test_inmemory_registry_ttl_expires_namespace() -> None:
    """In-memory registry TTL follows the Redis namespace-TTL contract."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        clock = MagicMock(return_value=100.0)
        with patch(
            "agentscope.app.message_bus._in_memory_message_bus.time.monotonic",
            new=clock,
        ):
            await bus.registry_set("dedupe:1", "processed", "1", ttl_secs=1)
            clock.return_value = 102.0
            assert not await bus.registry_exists("dedupe:1", "processed")

    asyncio.run(run())


def test_inmemory_registry_reclaims_unvisited_expired_namespaces() -> None:
    """Touching a new namespace sweeps old independently-expiring keys."""

    async def run() -> None:
        bus = InMemoryMessageBus()
        clock = MagicMock(return_value=100.0)
        with patch(
            "agentscope.app.message_bus._in_memory_message_bus.time.monotonic",
            new=clock,
        ):
            for index in range(5):
                await bus.registry_set(
                    f"dedupe:{index}",
                    "processed",
                    "1",
                    ttl_secs=1,
                )
            clock.return_value = 102.0
            await bus.registry_set("fresh", "value", "1")

        assert bus._registries == {"fresh": {"value": "1"}}
        assert not bus._registry_expiries

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
        assert event.model_dump() == _channel_event(
            message_id="wamid-group",
            text="hello",
            chat_id="group-1",
            chat_name="Project",
            chat_type="group",
        )

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
