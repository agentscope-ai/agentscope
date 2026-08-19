# -*- coding: utf-8 -*-
"""Unit tests for the WhatsApp channel's transport-independent behavior."""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agentscope.app.channel._base import ChannelConfirmationResultEvent
from agentscope.app.channel import WhatsAppChannel
from agentscope.app.channel._whatsapp._tools import (
    ListChatMembers,
    ListChats,
    SendMessage,
)
from agentscope.message import Base64Source, DataBlock


def _channel() -> WhatsAppChannel:
    return WhatsAppChannel(
        "whatsapp-1",
        WhatsAppChannel.Credentials(
            phone_number_id="phone-id",
            access_token="access-token",
            verify_token="verify-token",
            meta_app_secret="app-secret",
        ),
        WhatsAppChannel.Config(),
    )


def test_webhook_verification() -> None:
    """Accept valid Meta verification challenges only."""
    channel = _channel()
    assert (
        channel.verify_webhook("subscribe", "verify-token", "challenge")
        == "challenge"
    )
    assert channel.verify_webhook("subscribe", "wrong", "challenge") is None
    assert channel.verify_webhook("other", "verify-token", "challenge") is None


def test_webhook_normalizes_text_and_deduplicates() -> None:
    """Normalize a text webhook and ignore a repeated message id."""

    async def run() -> None:
        channel = _channel()
        received = []

        async def emit(event: object) -> None:
            received.append(event)

        listener = asyncio.create_task(channel.start_listening(emit))
        await asyncio.sleep(0)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"profile": {"name": "Alice"}}],
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
        assert await channel.handle_webhook(payload) == 1
        assert await channel.handle_webhook(payload) == 0
        await asyncio.sleep(0)
        assert len(received) == 1
        assert received[0].message == "hello"
        assert received[0].channel_user_name == "Alice"
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_credentials_require_meta_app_secret() -> None:
    """Never allow webhook verification to be configured fail-open."""
    with pytest.raises(ValidationError):
        WhatsAppChannel.Credentials(
            phone_number_id="phone-id",
            access_token="access-token",
            verify_token="verify-token",
        )


def test_cloud_api_lists_groups_and_members() -> None:
    """Expose Groups API groups and their participants."""

    async def run() -> None:
        channel = _channel()
        channel._http = AsyncMock()  # pylint: disable=protected-access
        responses = [
            {
                "data": {
                    "groups": [
                        {"id": "group-1", "subject": "Project"},
                    ],
                },
                "paging": {},
            },
            {
                "participants": [
                    {"wa_id": "8613800138000", "username": "Alice"},
                ],
            },
        ]
        getter = AsyncMock(side_effect=responses)
        with patch.object(
            channel,
            "_get_json",
            new=getter,
        ):
            assert await channel.list_bot_chats() == [
                {
                    "chat_id": "group-1",
                    "name": "Project",
                    "chat_type": "group",
                },
            ]
            assert await channel.list_chat_members("group-1") == [
                {"id": "8613800138000", "name": "Alice"},
            ]
        assert getter.await_args_list[1].kwargs == {
            "params": {"fields": "participants"},
        }
        assert await channel.chat_kind("group-1") == "group"
        assert await channel.chat_kind("8613800138000") == "private"

    asyncio.run(run())


def test_webhook_supports_group_chat_context() -> None:
    """Use a webhook group id as the shared chat/session identity."""

    async def run() -> None:
        channel = _channel()
        received = []

        async def emit(event: object) -> None:
            received.append(event)

        listener = asyncio.create_task(channel.start_listening(emit))
        await asyncio.sleep(0)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid-group-1",
                                        "from": "8613800138000",
                                        "type": "text",
                                        "text": {"body": "hello group"},
                                        "group_id": "group-1",
                                    },
                                ],
                            },
                        },
                    ],
                },
            ],
        }
        assert await channel.handle_webhook(payload) == 1
        await asyncio.sleep(0)
        assert received[0].chat_id == "group-1"
        assert received[0].metadata["chat_type"] == "group"
        assert await channel.chat_kind("group-1") == "group"
        assert await channel.list_bot_chats() == [
            {"chat_id": "group-1", "name": "group-1", "chat_type": "group"},
        ]
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_webhook_uses_business_scoped_user_id_fallback() -> None:
    """Use from_user_id when username-based webhooks omit from."""

    async def run() -> None:
        channel = _channel()
        # pylint: disable=protected-access
        event = await channel._normalize_message(
            {
                "id": "wamid-bsuid-1",
                "from_user_id": "bsuid-1",
                "type": "text",
                "text": {"body": "hello"},
            },
            {},
        )
        assert event is not None
        assert event.channel_user_id == "bsuid-1"
        assert event.chat_id == "bsuid-1"

    asyncio.run(run())


def test_webhook_signature() -> None:
    """Validate Meta's HMAC signature when an app secret is configured."""
    import hashlib
    import hmac

    channel = _channel()
    body = b'{"hello":"world"}'
    digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert channel.verify_webhook_signature(body, f"sha256={digest}")
    assert not channel.verify_webhook_signature(body, "sha256=wrong")


def test_whatsapp_schema_uses_platform_specific_app_secret_name() -> None:
    """Do not reuse Feishu's translated app_secret form field."""
    properties = WhatsAppChannel.Credentials.model_json_schema()["properties"]
    assert "meta_app_secret" in properties
    assert "app_secret" not in properties


def test_group_send_uses_group_recipient_type() -> None:
    """Include the Groups API recipient discriminator on outbound sends."""

    async def run() -> None:
        channel = _channel()
        sender = AsyncMock(return_value={"messages": [{"id": "out-1"}]})
        with patch.object(channel, "_send_json", new=sender):
            await channel.send_message_to("group-1", "group", "hello")
        sender.assert_awaited_once_with(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "group",
                "to": "group-1",
                "type": "text",
                "text": {"body": "hello"},
            },
        )

    asyncio.run(run())


def test_tools_use_discovery_address_pairs() -> None:
    """Keep WhatsApp discovery and send schemas aligned like Feishu's."""

    async def run() -> None:
        channel = _channel()
        backend = AsyncMock()
        with patch.object(
            channel,
            "list_bot_chats",
            new=AsyncMock(
                return_value=[
                    {
                        "chat_id": "group-1",
                        "chat_type": "group",
                        "name": "Project",
                    },
                ],
            ),
        ):
            chunk = await ListChats(channel, backend)(query="proj")
        assert json.loads(chunk.content[0].text) == [
            {
                "to": "group-1",
                "recipient_type": "group",
                "name": "Project",
            },
        ]

        with patch.object(
            channel,
            "list_chat_members",
            new=AsyncMock(
                return_value=[{"id": "8613800138000", "name": "Alice"}],
            ),
        ):
            chunk = await ListChatMembers(channel, backend)("group-1")
        assert json.loads(chunk.content[0].text) == [
            {
                "to": "8613800138000",
                "recipient_type": "individual",
                "name": "Alice",
            },
        ]

        recipient_schema = SendMessage.input_schema["properties"][
            "recipient_type"
        ]
        assert recipient_schema["enum"] == ["individual", "group"]

    asyncio.run(run())


def test_duplicate_media_is_filtered_before_download() -> None:
    """Do not download the same webhook media more than once."""

    async def run() -> None:
        channel = _channel()
        media = DataBlock(
            source=Base64Source(
                data=base64.b64encode(b"image").decode(),
                media_type="image/png",
            ),
            name="image.png",
        )
        download = AsyncMock(return_value=media)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid-media-1",
                                        "from": "8613800138000",
                                        "type": "image",
                                        "image": {
                                            "id": "media-1",
                                            "mime_type": "image/png",
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                },
            ],
        }
        with patch.object(channel, "_download_media", new=download):
            assert await channel.handle_webhook(payload) == 1
            assert await channel.handle_webhook(payload) == 0
        download.assert_awaited_once()

    asyncio.run(run())


def test_failed_media_download_can_be_retried() -> None:
    """Do not commit the dedupe id until media normalization succeeds."""

    async def run() -> None:
        channel = _channel()
        media = DataBlock(
            source=Base64Source(
                data=base64.b64encode(b"image").decode(),
                media_type="image/png",
            ),
            name="image.png",
        )
        download = AsyncMock(side_effect=[None, media])
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid-media-retry",
                                        "from": "8613800138000",
                                        "type": "image",
                                        "image": {
                                            "id": "media-retry",
                                            "mime_type": "image/png",
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                },
            ],
        }
        with patch.object(channel, "_download_media", new=download):
            assert await channel.handle_webhook(payload) == 0
            assert await channel.handle_webhook(payload) == 1
            assert await channel.handle_webhook(payload) == 0
        assert download.await_count == 2

    asyncio.run(run())


def test_confirmation_reply_is_normalized() -> None:
    """Turn WhatsApp approval replies into confirmation result events."""

    async def run() -> None:
        channel = _channel()
        token = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "tool_call_id": "call-1",
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                    },
                    separators=(",", ":"),
                ).encode(),
            )
            .decode()
            .rstrip("=")
        )
        # pylint: disable=protected-access
        event = await channel._normalize_message(
            {
                "id": "wamid-confirm-1",
                "from": "8613800138000",
                "type": "interactive",
                "interactive": {
                    "button_reply": {
                        "id": f"agentscope:approve {token}",
                    },
                },
            },
            {},
        )
        assert isinstance(event, ChannelConfirmationResultEvent)
        assert event.tool_call_id == "call-1"
        assert event.agent_id == "agent-1"
        assert event.session_id == "session-1"
        assert event.approved

    asyncio.run(run())
