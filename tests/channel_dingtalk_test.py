# -*- coding: utf-8 -*-
"""Unit tests for the DingTalk Channel text and media paths."""

# pylint: disable=protected-access,missing-function-docstring
import asyncio
import base64
import json
from typing import Any, AsyncIterator, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID

from agentscope.app.channel import DingTalkChannel
from agentscope.app.channel._base import (
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChatKind,
)
from agentscope.app.channel._dingtalk._openapi import _DingTalkOpenAPI
from agentscope.app.channel._registry import ChannelTypeRegistry
from agentscope.event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
)
from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.workspace import WorkspaceBase

_REPLY_ID = "reply-1"


class _FakeResponse:
    """Minimal successful HTTP response."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self._result = result if result is not None else {"errcode": 0}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._result


class _FakeHTTP:
    """Record outbound HTTP calls."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append((url, kwargs))
        return _FakeResponse()

    async def aclose(self) -> None:
        self.closed = True


class _FakeStreamClient:
    """A cancellable stand-in for the official Stream client."""

    def __init__(self) -> None:
        self.websocket: object | None = object()
        self.started = asyncio.Event()
        self._stopped = asyncio.Event()
        self.stop_calls = 0

    async def start(self) -> None:
        self.started.set()
        await self._stopped.wait()

    async def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()


class _FakeMediaOpenAPI:
    """Return fixed downloads and record channel media sends."""

    def __init__(
        self,
        download: tuple[bytes, str] | None = (b"media", "image/png"),
    ) -> None:
        self.download = download
        self.download_calls: list[tuple[str, int]] = []
        self.send_calls: list[tuple[str, bytes, str, str]] = []
        self.text_calls: list[tuple[str, str]] = []
        self.search_result: list[dict[str, Any]] = []
        self.approval_calls: list[tuple[str, str, str, dict[str, str]]] = []
        self.card_updates: list[tuple[str, dict[str, str]]] = []
        self.streaming_card_id: str | None = "stream-track-1"
        self.streaming_card_calls: list[tuple[str, str, str]] = []
        self.streaming_updates: list[tuple[str, str, str, bool, bool]] = []
        self.streaming_update_success = True

    async def download_media(
        self,
        download_code: str,
        max_bytes: int,
    ) -> tuple[bytes, str] | None:
        self.download_calls.append((download_code, max_bytes))
        return self.download

    async def send_media(
        self,
        chat_id: str,
        data: bytes,
        file_name: str,
        media_type: str,
    ) -> bool:
        self.send_calls.append((chat_id, data, file_name, media_type))
        return True

    async def send_text(self, chat_id: str, text: str) -> bool:
        self.text_calls.append((chat_id, text))
        return True

    async def search_users(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        del query
        return self.search_result[:limit]

    async def create_approval_card(
        self,
        chat_id: str,
        approver_id: str,
        template_id: str,
        card_data: dict[str, str],
    ) -> str:
        self.approval_calls.append(
            (chat_id, approver_id, template_id, card_data),
        )
        return "track-1"

    async def update_approval_card(
        self,
        out_track_id: str,
        card_data: dict[str, str],
    ) -> bool:
        self.card_updates.append((out_track_id, card_data))
        return True

    async def create_streaming_card(
        self,
        chat_id: str,
        template_id: str,
        content_key: str,
    ) -> str | None:
        self.streaming_card_calls.append(
            (chat_id, template_id, content_key),
        )
        return self.streaming_card_id

    async def stream_card(
        self,
        out_track_id: str,
        content_key: str,
        content: str,
        *,
        finalize: bool = False,
        is_error: bool = False,
    ) -> bool:
        self.streaming_updates.append(
            (out_track_id, content_key, content, finalize, is_error),
        )
        return self.streaming_update_success


class _FakeBackend:
    """Workspace backend that records reads and returns fixed files."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.reads: list[str] = []

    async def read_file(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _FakeWorkspace:
    """Workspace wrapper exposing a fake backend."""

    def __init__(self, backend: _FakeBackend) -> None:
        self.backend = backend

    def get_backend(self) -> _FakeBackend:
        return self.backend


class _StreamResponse(_FakeResponse):
    """Async streaming download response."""

    def __init__(
        self,
        chunks: list[bytes],
        media_type: str,
        content_length: int | None = None,
    ) -> None:
        super().__init__()
        self._chunks = chunks
        self.headers = {"content-type": media_type}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)

    async def __aenter__(self) -> "_StreamResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _OpenAPIHTTP(_FakeHTTP):
    """Queue JSON responses and one streaming media response."""

    def __init__(
        self,
        responses: list[_FakeResponse],
        stream_response: _StreamResponse | None = None,
    ) -> None:
        super().__init__()
        self._responses = responses
        self._stream_response = stream_response
        self.streams: list[tuple[str, str, dict[str, Any]]] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append((url, kwargs))
        return self._responses.pop(0)

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> _FakeResponse:
        self.requests.append((method, url, kwargs))
        return self._responses.pop(0)

    def stream(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> _StreamResponse:
        self.streams.append((method, url, kwargs))
        if self._stream_response is None:
            raise RuntimeError("no stream response")
        return self._stream_response


def _openapi(
    *responses: dict[str, Any],
    stream_response: _StreamResponse | None = None,
) -> tuple[_DingTalkOpenAPI, _OpenAPIHTTP]:
    http = _OpenAPIHTTP(
        [
            _FakeResponse({"accessToken": "token", "expireIn": 7200}),
            *(_FakeResponse(response) for response in responses),
        ],
        stream_response,
    )
    return _DingTalkOpenAPI("client", "secret", http), http


def _channel(
    *,
    only_at_reply: bool = True,
    approval_card_template_id: str = "approval.schema",
    streaming_card_template_id: str = "",
    streaming_card_key: str = "content",
) -> DingTalkChannel:
    return DingTalkChannel(
        "ding-1",
        DingTalkChannel.Credentials(
            client_id="client-id",
            client_secret="client-secret",
        ),
        DingTalkChannel.Config(
            only_at_reply=only_at_reply,
            approval_card_template_id=approval_card_template_id,
            streaming_card_template_id=streaming_card_template_id,
            streaming_card_key=streaming_card_key,
        ),
    )


def _channel_with_openapi(
    openapi: _FakeMediaOpenAPI | None = None,
    **config: Any,
) -> tuple[DingTalkChannel, _FakeMediaOpenAPI]:
    openapi = openapi or _FakeMediaOpenAPI()
    channel = _channel(**config)
    channel._openapi = cast(_DingTalkOpenAPI, openapi)
    return channel, openapi


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "conversationType": "2",
        "conversationId": "cid-group-1",
        "conversationTitle": "Engineering",
        "senderStaffId": "user-1",
        "senderNick": "Alice",
        "msgId": "message-1",
        "msgtype": "text",
        "text": {"content": " hello "},
        "isInAtList": True,
    }
    payload.update(overrides)
    return payload


async def _message_callbacks(
    channel: DingTalkChannel,
    *payloads: dict[str, Any],
) -> list[ChannelEvent]:
    received: list[ChannelEvent] = []

    async def emit(event: ChannelEvent) -> None:
        received.append(event)

    channel._emit = emit
    for payload in payloads:
        await channel._on_callback(payload)
    return received


def _message_event(
    chat_id: str = "group:cid-group-1",
    *,
    user_id: str = "user-1",
    metadata: dict[str, Any] | None = None,
) -> ChannelEvent:
    return ChannelEvent(
        channel_id="ding-1",
        channel_user_id=user_id,
        chat_id=chat_id,
        metadata=metadata or {},
    )


async def _event_stream(text: str = "hello from agent") -> AsyncIterator[dict]:
    events = [
        ReplyStartEvent(
            session_id="session-1",
            reply_id=_REPLY_ID,
            name="assistant",
        ),
        TextBlockStartEvent(reply_id=_REPLY_ID, block_id="text-1"),
        TextBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id="text-1",
            delta=text,
        ),
        TextBlockEndEvent(reply_id=_REPLY_ID, block_id="text-1"),
        ReplyEndEvent(session_id="session-1", reply_id=_REPLY_ID),
    ]
    for event in events:
        yield event.model_dump(mode="json")


async def _image_event_stream() -> AsyncIterator[dict]:
    events = [
        ReplyStartEvent(
            session_id="session-1",
            reply_id=_REPLY_ID,
            name="assistant",
        ),
        DataBlockStartEvent(
            reply_id=_REPLY_ID,
            block_id="image-1",
            media_type="image/png",
        ),
        DataBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id="image-1",
            data=base64.b64encode(b"image bytes").decode("ascii"),
            media_type="image/png",
        ),
        DataBlockEndEvent(reply_id=_REPLY_ID, block_id="image-1"),
        ReplyEndEvent(session_id="session-1", reply_id=_REPLY_ID),
    ]
    for event in events:
        yield event.model_dump(mode="json")


async def _confirmation_event_stream() -> AsyncIterator[dict]:
    events = [
        ReplyStartEvent(
            session_id="session-1",
            reply_id=_REPLY_ID,
            name="assistant",
        ),
        RequireUserConfirmEvent(
            reply_id=_REPLY_ID,
            tool_calls=[
                ToolCallBlock(
                    id="tool-1",
                    name="SendMessage",
                    input='{"target":"user:user-2","text":"hello"}',
                ),
            ],
        ),
    ]
    for event in events:
        yield event.model_dump(mode="json")


def _card_callback(
    *,
    action: str = "approve",
    user_id: str = "user-1",
    approver_id: str = "",
) -> dict[str, Any]:
    return {
        "type": "actionCallback",
        "outTrackId": "track-1",
        "userId": user_id,
        "content": json.dumps(
            {
                "cardPrivateData": {
                    "params": {
                        "action": action,
                        "toolCallId": "tool-1",
                        "chatId": "group:cid-group-1",
                        "agentId": "agent-1",
                        "sessionId": "session-1",
                        "approverId": approver_id,
                    },
                },
            },
        ),
    }


async def _confirmation_callbacks(
    channel: DingTalkChannel,
    *payloads: dict[str, Any],
) -> list[ChannelConfirmationResultEvent]:
    received: list[ChannelConfirmationResultEvent] = []

    async def emit(event: ChannelConfirmationResultEvent) -> None:
        received.append(event)

    channel._emit = emit
    for payload in payloads:
        await channel._on_card_callback(payload)
    return received


class DingTalkChannelTest(  # pylint: disable=too-many-public-methods
    IsolatedAsyncioTestCase,
):
    """DingTalk callback, lifecycle, and reply tests."""

    async def test_official_stream_client_registration(self) -> None:
        channel = _channel()

        client = channel._new_stream_client()

        self.assertIn(
            "/v1.0/im/bot/messages/get",
            client.callback_handler_map,
        )
        self.assertIn(
            "/v1.0/card/instances/callback",
            client.callback_handler_map,
        )

    async def test_official_stream_client_stops_during_retry(self) -> None:
        channel = _channel()
        client = channel._new_stream_client()

        with patch.object(client, "open_connection", return_value=None):
            listener = asyncio.create_task(client.start())
            await asyncio.sleep(0.05)
            await client.stop()
            await asyncio.wait_for(listener, timeout=1.0)

        self.assertTrue(listener.done())

    async def test_official_stream_client_propagates_cancellation(
        self,
    ) -> None:
        channel = _channel()
        client = channel._new_stream_client()

        with patch.object(client, "open_connection", return_value=None):
            listener = asyncio.create_task(client.start())
            await asyncio.sleep(0.05)
            listener.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(listener, timeout=1.0)

    async def test_public_registration_and_secret_schema(self) -> None:
        registry = ChannelTypeRegistry([DingTalkChannel])
        schema = registry.list_types()[0]

        self.assertEqual(schema.channel_type, "dingtalk")
        self.assertEqual(schema.platform_bot_id_field, "client_id")
        secret = schema.credentials_schema["properties"]["client_secret"]
        self.assertEqual(secret["format"], "password")

    async def test_group_text_callback_is_normalised(self) -> None:
        channel = _channel()
        received = await _message_callbacks(channel, _payload())

        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(event.channel_user_id, "user-1")
        self.assertEqual(event.channel_user_name, "Alice")
        self.assertEqual(event.chat_id, "group:cid-group-1")
        self.assertEqual(event.chat_name, "Engineering")
        self.assertEqual(event.channel_message_id, "message-1")
        self.assertEqual(event.message, "hello")
        self.assertEqual(event.metadata["chat_type"], "group")
        self.assertNotIn("sessionWebhook", event.metadata)
        self.assertEqual(
            await channel.chat_kind(event.chat_id),
            ChatKind.GROUP,
        )
        self.assertEqual(await channel.chat_name(event.chat_id), "Engineering")

    async def test_private_text_callback_uses_staff_id(self) -> None:
        channel = _channel()
        received = await _message_callbacks(
            channel,
            _payload(
                conversationType="1",
                conversationId="cid-private-not-used",
                conversationTitle="not-used",
            ),
        )

        self.assertEqual(received[0].chat_id, "user:user-1")
        self.assertEqual(received[0].chat_name, "")
        self.assertEqual(
            await channel.chat_kind(received[0].chat_id),
            ChatKind.PRIVATE,
        )
        self.assertEqual(await channel.chat_name("user:user-1"), "Alice")

    async def test_list_bot_chats_returns_only_observed_targets(self) -> None:
        channel = _channel()
        await _message_callbacks(
            channel,
            _payload(),
            _payload(conversationType="1", conversationTitle=""),
        )

        self.assertEqual(
            await channel.list_bot_chats(),
            [
                {
                    "chat_id": "group:cid-group-1",
                    "name": "Engineering",
                    "chat_type": "group",
                },
                {
                    "chat_id": "user:user-1",
                    "name": "Alice",
                    "chat_type": "private",
                },
            ],
        )

    async def test_group_message_without_mention_is_ignored(self) -> None:
        channel = _channel(only_at_reply=True)
        received = await _message_callbacks(
            channel,
            _payload(isInAtList=False),
        )

        self.assertEqual(received, [])

    async def test_non_text_and_invalid_target_are_ignored(self) -> None:
        channel = _channel()
        received = await _message_callbacks(
            channel,
            _payload(msgtype="picture"),
            _payload(senderStaffId="", senderId=""),
        )

        self.assertEqual(received, [])

    async def test_picture_callback_downloads_data_block(self) -> None:
        channel, media_api = _channel_with_openapi(
            _FakeMediaOpenAPI((b"png bytes", "image/png")),
        )
        received = await _message_callbacks(
            channel,
            _payload(
                msgtype="picture",
                content={"downloadCode": "picture-code"},
            ),
        )

        self.assertEqual(media_api.download_calls[0][0], "picture-code")
        block = cast(DataBlock, received[0].content[0])
        self.assertIsInstance(block, DataBlock)
        self.assertIsInstance(block.source, Base64Source)
        self.assertEqual(block.source.media_type, "image/png")
        self.assertEqual(base64.b64decode(block.source.data), b"png bytes")

    async def test_file_callback_preserves_safe_filename(self) -> None:
        channel, _ = _channel_with_openapi(
            _FakeMediaOpenAPI(
                (b"pdf bytes", "application/octet-stream"),
            ),
        )
        received = await _message_callbacks(
            channel,
            _payload(
                msgtype="file",
                content={
                    "downloadCode": "file-code",
                    "fileName": "../reports/result.pdf",
                },
            ),
        )

        block = cast(DataBlock, received[0].content[0])
        self.assertEqual(block.name, "result.pdf")
        self.assertEqual(block.source.media_type, "application/pdf")

    async def test_audio_callback_keeps_recognition_before_media(self) -> None:
        channel, _ = _channel_with_openapi(
            _FakeMediaOpenAPI((b"audio", "audio/mpeg")),
        )
        received = await _message_callbacks(
            channel,
            _payload(
                msgtype="audio",
                content={
                    "downloadCode": "audio-code",
                    "recognition": "transcribed speech",
                },
            ),
        )

        self.assertIsInstance(received[0].content[0], TextBlock)
        self.assertEqual(received[0].message, "transcribed speech")
        audio = cast(DataBlock, received[0].content[1])
        self.assertEqual(audio.source.media_type, "audio/mpeg")

    async def test_rich_text_preserves_text_image_order(self) -> None:
        channel, _ = _channel_with_openapi()
        received = await _message_callbacks(
            channel,
            _payload(
                msgtype="richText",
                content={
                    "richText": [
                        {"text": "before"},
                        {"downloadCode": "rich-image", "type": "picture"},
                        {"text": "after"},
                    ],
                },
            ),
        )

        self.assertIsInstance(received[0].content[0], TextBlock)
        self.assertIsInstance(received[0].content[1], DataBlock)
        self.assertIsInstance(received[0].content[2], TextBlock)
        self.assertEqual(received[0].message, "beforeafter")

    async def test_media_download_failure_is_visible(self) -> None:
        channel, _ = _channel_with_openapi(
            _FakeMediaOpenAPI(download=None),
        )
        received = await _message_callbacks(
            channel,
            _payload(
                msgtype="file",
                content={"downloadCode": "bad", "fileName": "bad.zip"},
            ),
        )

        self.assertIn("Unable to download", received[0].message)

    async def test_streaming_is_disabled_without_ai_card_template(
        self,
    ) -> None:
        channel = _channel(streaming_card_template_id="")

        self.assertFalse(channel.capabilities.streaming)

    async def test_default_config_streams_on_the_public_ai_card(self) -> None:
        """Streaming ships on, using the card the official SDK uses."""
        channel = DingTalkChannel(
            "ding-1",
            DingTalkChannel.Credentials(
                client_id="client-id",
                client_secret="client-secret",
            ),
            DingTalkChannel.Config(),
        )

        self.assertDictEqual(
            channel._config.model_dump(),
            {
                "only_at_reply": True,
                "show_tool_process": False,
                "show_thinking": False,
                "max_media_bytes": 10 * 1024 * 1024,
                "approval_card_template_id": "",
                "streaming_card_template_id": (
                    "8aebdfb9-28f4-4a98-98f5-396c3dde41a0.schema"
                ),
                "streaming_card_key": "content",
            },
        )
        self.assertTrue(channel.capabilities.streaming)

    async def test_send_response_streams_when_ai_card_is_configured(
        self,
    ) -> None:
        channel, media_api = _channel_with_openapi(
            streaming_card_template_id="ai-card.schema",
            streaming_card_key="answer",
        )
        event = _message_event()

        await channel.send_response(event, _event_stream())

        self.assertTrue(channel.capabilities.streaming)
        self.assertEqual(
            media_api.streaming_card_calls,
            [("group:cid-group-1", "ai-card.schema", "answer")],
        )
        self.assertGreaterEqual(len(media_api.streaming_updates), 1)
        self.assertEqual(
            media_api.streaming_updates[-1],
            (
                "stream-track-1",
                "answer",
                "hello from agent",
                True,
                False,
            ),
        )
        self.assertEqual(media_api.text_calls, [])

    async def test_streaming_card_creation_failure_falls_back_to_markdown(
        self,
    ) -> None:
        media_api = _FakeMediaOpenAPI()
        media_api.streaming_card_id = None
        channel, _ = _channel_with_openapi(
            media_api,
            streaming_card_template_id="ai-card.schema",
        )
        event = _message_event("user:user-1")

        await channel.send_response(event, _event_stream())

        self.assertEqual(
            media_api.text_calls,
            [("user:user-1", "hello from agent")],
        )

    async def test_streaming_card_update_failure_falls_back_to_markdown(
        self,
    ) -> None:
        media_api = _FakeMediaOpenAPI()
        media_api.streaming_update_success = False
        channel, _ = _channel_with_openapi(
            media_api,
            streaming_card_template_id="ai-card.schema",
        )
        event = _message_event()

        await channel.send_response(event, _event_stream())

        self.assertEqual(
            media_api.text_calls,
            [("group:cid-group-1", "hello from agent")],
        )
        self.assertTrue(media_api.streaming_updates[-1][3])
        self.assertTrue(media_api.streaming_updates[-1][4])

    async def test_long_streaming_reply_is_not_capped(self) -> None:
        """Only DingTalk decides a reply is too long, not a local guess."""
        channel, media_api = _channel_with_openapi(
            streaming_card_template_id="ai-card.schema",
        )
        event = _message_event()

        await channel.send_response(event, _event_stream("中" * 342))

        self.assertListEqual(
            media_api.streaming_card_calls,
            [("group:cid-group-1", "ai-card.schema", "content")],
        )
        self.assertListEqual(media_api.text_calls, [])

    async def test_send_response_uploads_image_data_block(self) -> None:
        channel, media_api = _channel_with_openapi()
        event = _message_event()

        await channel.send_response(event, _image_event_stream())

        self.assertEqual(
            media_api.send_calls,
            [
                (
                    "group:cid-group-1",
                    b"image bytes",
                    "image.png",
                    "image/png",
                ),
            ],
        )

    async def test_send_response_presents_tool_approval_card(self) -> None:
        channel, media_api = _channel_with_openapi()
        event = _message_event(
            metadata={"agent_id": "agent-1", "session_id": "session-1"},
        )

        await channel.send_response(event, _confirmation_event_stream())

        self.assertEqual(len(media_api.approval_calls), 1)
        chat_id, approver, template, card_data = media_api.approval_calls[0]
        self.assertEqual(chat_id, "group:cid-group-1")
        self.assertEqual(approver, "")
        self.assertEqual(template, "approval.schema")
        self.assertEqual(card_data["toolCallId"], "tool-1")
        self.assertEqual(card_data["agentId"], "agent-1")

    async def test_approval_callback_emits_resume_event_and_updates_card(
        self,
    ) -> None:
        channel, media_api = _channel_with_openapi()
        received = await _confirmation_callbacks(
            channel,
            _card_callback(action="deny"),
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].tool_call_id, "tool-1")
        self.assertEqual(received[0].chat_id, "group:cid-group-1")
        self.assertEqual(received[0].agent_id, "agent-1")
        self.assertEqual(received[0].session_id, "session-1")
        self.assertFalse(received[0].approved)
        self.assertEqual(received[0].actor, "user-1")
        self.assertEqual(media_api.card_updates[0][0], "track-1")
        self.assertEqual(media_api.card_updates[0][1]["status"], "denied")

    async def test_approval_callback_accepts_approval_aliases(self) -> None:
        for action in ("approve", "agree"):
            with self.subTest(action=action):
                channel, media_api = _channel_with_openapi()
                received = await _confirmation_callbacks(
                    channel,
                    _card_callback(action=action),
                )

                self.assertTrue(received[0].approved)
                self.assertEqual(
                    media_api.card_updates[0][1]["status"],
                    "approved",
                )

    async def test_approval_callback_rejects_another_user(self) -> None:
        channel, media_api = _channel_with_openapi()
        received = await _confirmation_callbacks(
            channel,
            _card_callback(
                user_id="unexpected-user",
                approver_id="user-1",
            ),
        )

        self.assertEqual(received, [])
        self.assertEqual(media_api.card_updates, [])

    async def test_send_file_strips_path_and_keeps_extension(self) -> None:
        channel, media_api = _channel_with_openapi()
        block = DataBlock(
            source=Base64Source(
                data=base64.b64encode(b"pdf").decode("ascii"),
                media_type="application/pdf",
            ),
            name="../../report.pdf",
        )

        sent = await channel._send_data("user:user-1", block)

        self.assertTrue(sent)
        self.assertEqual(media_api.send_calls[0][2], "report.pdf")


class DingTalkToolTest(IsolatedAsyncioTestCase):
    """DingTalk discovery and target-send tool tests."""

    async def test_channel_target_operations_reuse_openapi(self) -> None:
        media_api = _FakeMediaOpenAPI()
        media_api.search_result = [
            {
                "user_id": "user-2",
                "name": "Bob",
                "title": "Engineer",
                "department_ids": [1],
            },
        ]
        channel, _ = _channel_with_openapi(media_api)

        users = await channel.search_users("Bob", 5)
        message_sent = await channel.send_message_to(
            "user:user-2",
            "hello",
        )
        file_sent = await channel.send_file_to(
            "group:cid-2",
            b"pdf",
            "report.pdf",
        )
        image_sent = await channel.send_image_to(
            "user:user-2",
            b"png",
            "chart.png",
        )

        self.assertEqual(users[0]["name"], "Bob")
        self.assertTrue(message_sent)
        self.assertTrue(file_sent)
        self.assertTrue(image_sent)
        self.assertEqual(
            media_api.text_calls,
            [("user:user-2", "hello")],
        )
        self.assertEqual(
            media_api.send_calls[0][3],
            "application/octet-stream",
        )
        self.assertEqual(media_api.send_calls[1][3], "image/png")

    async def test_list_tools_forms_discovery_send_chain(self) -> None:
        from agentscope.app.channel._dingtalk._tools import (
            ListConversations,
            ListUsers,
            SendFile,
            SendMessage,
        )

        media_api = _FakeMediaOpenAPI()
        media_api.search_result = [
            {
                "user_id": "user-2",
                "name": "Bob",
                "title": "Engineer",
                "department_ids": [1],
            },
        ]
        channel, _ = _channel_with_openapi(media_api)
        channel._chat_names["group:cid-2"] = "Finance"
        backend = _FakeBackend({"/workspace/report.pdf": b"pdf"})
        workspace = cast(WorkspaceBase, _FakeWorkspace(backend))

        tools = await channel.list_tools(workspace)

        self.assertEqual(
            [tool.name for tool in tools],
            [
                "ListConversations",
                "ListUsers",
                "SendMessage",
                "SendFile",
                "SendImage",
            ],
        )
        conversations = await cast(ListConversations, tools[0])()
        conversation_items = json.loads(conversations.content[0].text)
        self.assertEqual(conversation_items[0]["target"], "group:cid-2")
        users = await cast(ListUsers, tools[1])("Bob", 10)
        user_items = json.loads(users.content[0].text)
        self.assertEqual(user_items[0]["target"], "user:user-2")

        message_result = await cast(SendMessage, tools[2])(
            "user:user-2",
            "hello",
        )
        file_result = await cast(SendFile, tools[3])(
            "/workspace/report.pdf",
            "group:cid-2",
        )

        self.assertIn("Sent message", message_result.content[0].text)
        self.assertIn("Sent file", file_result.content[0].text)
        self.assertEqual(backend.reads, ["/workspace/report.pdf"])

    async def test_dingtalk_tool_permissions_match_feishu_policy(self) -> None:
        channel = _channel()
        backend = _FakeBackend()
        tools = await channel.list_tools(
            cast(WorkspaceBase, _FakeWorkspace(backend)),
        )

        read_decision = await tools[0].check_permissions(
            {},
            PermissionContext(),
        )
        send_decision = await tools[2].check_permissions(
            {},
            PermissionContext(),
        )

        self.assertEqual(read_decision.behavior, PermissionBehavior.ALLOW)
        self.assertEqual(send_decision.behavior, PermissionBehavior.ASK)

    async def test_send_tools_do_not_depend_on_card_configuration(
        self,
    ) -> None:
        """Approval gates the call, not whether the tool is equipped."""
        channel = _channel(approval_card_template_id="")
        tools = await channel.list_tools(
            cast(WorkspaceBase, _FakeWorkspace(_FakeBackend())),
        )

        self.assertListEqual(
            [tool.name for tool in tools],
            [
                "ListConversations",
                "ListUsers",
                "SendMessage",
                "SendFile",
                "SendImage",
            ],
        )


class DingTalkChannelLifecycleTest(IsolatedAsyncioTestCase):
    """DingTalk delivery-without-connection and lifecycle tests."""

    async def test_reply_works_without_start_listening(self) -> None:
        """The node that delivers a reply never runs the connection loop."""
        channel = _channel()
        http = _OpenAPIHTTP(
            [
                _FakeResponse({"accessToken": "token", "expireIn": 7200}),
                _FakeResponse({}),
            ],
        )

        with patch.object(channel, "_new_http_client", return_value=http):
            await channel.send_response(_message_event(), _event_stream())
            await channel.aclose()

        self.assertListEqual(
            [(url, request["json"]) for url, request in http.posts],
            [
                (
                    "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                    {"appKey": "client-id", "appSecret": "client-secret"},
                ),
                (
                    "https://api.dingtalk.com/v1.0/robot/groupMessages/send",
                    {
                        "robotCode": "client-id",
                        "msgKey": "sampleMarkdown",
                        "msgParam": json.dumps(
                            {
                                "title": "AgentScope",
                                "text": "hello from agent",
                            },
                            ensure_ascii=False,
                        ),
                        "openConversationId": "cid-group-1",
                    },
                ),
            ],
        )
        self.assertTrue(http.closed)

    async def test_lifecycle_stops_stream_and_http_clients(self) -> None:
        channel = _channel()
        http = _FakeHTTP()
        stream = _FakeStreamClient()

        async def emit(event: ChannelEvent) -> None:
            del event

        with (
            patch.object(channel, "_new_http_client", return_value=http),
            patch.object(channel, "_new_stream_client", return_value=stream),
        ):
            listener = asyncio.create_task(channel.start_listening(emit))
            await asyncio.wait_for(stream.started.wait(), timeout=1.0)
            await asyncio.sleep(0.25)
            self.assertEqual(channel.status.state, "connected")
            listener.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await listener

        self.assertEqual(stream.stop_calls, 1)
        self.assertTrue(http.closed)
        self.assertEqual(channel.status.state, "stopped")

    async def test_lifecycle_parks_after_initialisation_failure(self) -> None:
        channel = _channel()
        http = _FakeHTTP()

        async def emit(event: ChannelEvent) -> None:
            del event

        with (
            patch.object(channel, "_new_http_client", return_value=http),
            patch.object(
                channel,
                "_new_stream_client",
                side_effect=RuntimeError("bad credentials"),
            ),
        ):
            listener = asyncio.create_task(channel.start_listening(emit))
            for _ in range(20):
                if channel.status.state == "failed":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(channel.status.state, "failed")
            self.assertEqual(channel.status.last_error, "bad credentials")
            self.assertFalse(listener.done())
            listener.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await listener

        self.assertTrue(http.closed)
        self.assertEqual(channel.status.state, "stopped")


class DingTalkOpenAPITest(IsolatedAsyncioTestCase):
    """Token, media download, upload, and target-send tests."""

    async def test_download_media_resolves_and_limits_stream(self) -> None:
        api, http = _openapi(
            {"downloadUrl": "https://media.example/file"},
            stream_response=_StreamResponse(
                [b"abc", b"def"],
                "application/pdf",
                content_length=6,
            ),
        )

        result = await api.download_media("download-code", max_bytes=6)

        self.assertEqual(result, (b"abcdef", "application/pdf"))
        self.assertEqual(http.streams[0][0], "GET")
        resolve_request = http.posts[1][1]
        self.assertEqual(resolve_request["json"]["robotCode"], "client")
        self.assertEqual(
            resolve_request["headers"]["x-acs-dingtalk-access-token"],
            "token",
        )

    async def test_download_upgrades_dingtalk_oss_url_to_https(self) -> None:
        download_url = "http://bucket.oss-cn-test.aliyuncs.com/file?sig=x"
        api, http = _openapi(
            {"downloadUrl": download_url},
            stream_response=_StreamResponse([b"image"], "image/png"),
        )

        result = await api.download_media("download-code", max_bytes=10)

        self.assertEqual(result, (b"image", "image/png"))
        self.assertEqual(
            http.streams[0][1],
            "https://bucket.oss-cn-test.aliyuncs.com/file?sig=x",
        )

    async def test_download_rejects_untrusted_http_url(self) -> None:
        api, http = _openapi(
            {"downloadUrl": "http://media.example/file"},
        )

        result = await api.download_media("download-code", max_bytes=10)

        self.assertIsNone(result)
        self.assertEqual(http.streams, [])

    async def test_download_rejects_content_length_over_limit(self) -> None:
        api, _ = _openapi(
            {"downloadUrl": "https://media.example/file"},
            stream_response=_StreamResponse(
                [b"too large"],
                "application/zip",
                content_length=9,
            ),
        )

        result = await api.download_media("download-code", max_bytes=4)

        self.assertIsNone(result)

    async def test_download_rejects_stream_over_limit(self) -> None:
        api, _ = _openapi(
            {"downloadUrl": "https://media.example/file"},
            stream_response=_StreamResponse(
                [b"abc", b"def"],
                "application/octet-stream",
            ),
        )

        result = await api.download_media("download-code", max_bytes=5)

        self.assertIsNone(result)

    async def test_upload_and_send_group_file(self) -> None:
        api, http = _openapi(
            {"errcode": 0, "media_id": "media-id"},
            {"processQueryKey": "query-key"},
        )

        sent = await api.send_media(
            "group:cid-1",
            b"pdf",
            "report.pdf",
            "application/pdf",
        )

        self.assertTrue(sent)
        self.assertEqual(http.posts[1][1]["data"], {"type": "file"})
        send_request = http.posts[2][1]["json"]
        self.assertEqual(send_request["msgKey"], "sampleFile")
        self.assertEqual(send_request["openConversationId"], "cid-1")
        self.assertEqual(
            json.loads(send_request["msgParam"]),
            {
                "mediaId": "media-id",
                "fileName": "report.pdf",
                "fileType": "pdf",
            },
        )

    async def test_upload_and_send_user_image(self) -> None:
        api, http = _openapi(
            {"errcode": 0, "media_id": "image-id"},
            {"processQueryKey": "query-key"},
        )

        sent = await api.send_media(
            "user:user-1",
            b"png",
            "image.png",
            "image/png",
        )

        self.assertTrue(sent)
        self.assertEqual(http.posts[1][1]["data"], {"type": "image"})
        send_request = http.posts[2][1]["json"]
        self.assertEqual(send_request["msgKey"], "sampleImageMsg")
        self.assertEqual(send_request["userIds"], ["user-1"])
        self.assertEqual(
            json.loads(send_request["msgParam"]),
            {"photoURL": "image-id"},
        )

    async def test_send_text_uses_group_markdown_template(self) -> None:
        api, http = _openapi(
            {"processQueryKey": "query-key"},
        )

        sent = await api.send_text("group:cid-1", "hello")

        self.assertTrue(sent)
        request = http.posts[1][1]["json"]
        self.assertEqual(request["msgKey"], "sampleMarkdown")
        self.assertEqual(request["openConversationId"], "cid-1")
        self.assertEqual(
            json.loads(request["msgParam"]),
            {"title": "AgentScope", "text": "hello"},
        )

    async def test_send_text_uses_user_markdown_template(self) -> None:
        api, http = _openapi(
            {"processQueryKey": "query-key"},
        )

        sent = await api.send_text("user:user-1", "**hello**")

        self.assertTrue(sent)
        request = http.posts[1][1]["json"]
        self.assertEqual(request["msgKey"], "sampleMarkdown")
        self.assertEqual(request["userIds"], ["user-1"])
        self.assertEqual(
            json.loads(request["msgParam"]),
            {"title": "AgentScope", "text": "**hello**"},
        )

    async def test_search_users_resolves_profile_details(self) -> None:
        api, http = _openapi(
            {"list": ["user-1", "user-2"]},
            {
                "errcode": 0,
                "result": {
                    "userid": "user-1",
                    "name": "Alice",
                    "title": "Engineer",
                    "dept_id_list": [1, 2],
                },
            },
            {"errcode": 500},
        )

        users = await api.search_users("Alice", 10)

        self.assertEqual(
            http.posts[1][1]["json"],
            {"queryWord": "Alice", "offset": 0, "size": 10},
        )
        self.assertEqual(users[0]["name"], "Alice")
        self.assertEqual(users[0]["department_ids"], [1, 2])
        self.assertEqual(users[1]["user_id"], "user-2")
        self.assertEqual(users[1]["name"], "")

    async def test_create_deliver_and_update_group_approval_card(self) -> None:
        api, http = _openapi({}, {}, {})

        out_track_id = await api.create_approval_card(
            "group:cid-1",
            "",
            "approval.schema",
            {"toolCallId": "tool-1"},
        )
        updated = await api.update_approval_card(
            cast(str, out_track_id),
            {"status": "approved"},
        )

        self.assertIsNotNone(out_track_id)
        self.assertTrue(updated)
        create_method, create_url, create_request = http.requests[0]
        self.assertEqual(create_method, "POST")
        self.assertTrue(create_url.endswith("/card/instances"))
        self.assertEqual(
            create_request["json"]["cardTemplateId"],
            "approval.schema",
        )
        self.assertEqual(create_request["json"]["callbackType"], "STREAM")
        deliver_method, deliver_url, deliver_request = http.requests[1]
        self.assertEqual(deliver_method, "POST")
        self.assertTrue(deliver_url.endswith("/card/instances/deliver"))
        self.assertEqual(
            deliver_request["json"]["openSpaceId"],
            "dtv1.card//IM_GROUP.cid-1",
        )
        self.assertEqual(
            deliver_request["json"]["imGroupOpenDeliverModel"],
            {"robotCode": "client"},
        )
        self.assertEqual(http.requests[2][0], "PUT")
        self.assertEqual(
            http.requests[2][2]["json"]["outTrackId"],
            out_track_id,
        )

    async def test_deliver_private_approval_card_to_encoded_user(self) -> None:
        api, http = _openapi({}, {})

        out_track_id = await api.create_approval_card(
            "user:user-1",
            "",
            "approval.schema",
            {"toolCallId": "tool-1"},
        )

        self.assertIsNotNone(out_track_id)
        delivery = http.requests[1][2]["json"]
        self.assertEqual(
            delivery["openSpaceId"],
            "dtv1.card//IM_ROBOT.user-1",
        )
        self.assertEqual(
            delivery["imRobotOpenDeliverModel"],
            {"spaceType": "IM_ROBOT"},
        )

    async def test_create_deliver_and_stream_group_ai_card(self) -> None:
        api, http = _openapi({}, {}, {})

        out_track_id = await api.create_streaming_card(
            "group:cid-1",
            "ai-card.schema",
            "answer",
        )
        updated = await api.stream_card(
            cast(str, out_track_id),
            "answer",
            "**complete**",
            finalize=True,
        )

        self.assertIsNotNone(out_track_id)
        self.assertTrue(updated)
        create = http.requests[0][2]["json"]
        self.assertEqual(create["cardTemplateId"], "ai-card.schema")
        self.assertEqual(create["cardData"]["cardParamMap"], {"answer": ""})
        delivery = http.requests[1][2]["json"]
        self.assertEqual(
            delivery["openSpaceId"],
            "dtv1.card//IM_GROUP.cid-1",
        )
        method, url, request = http.requests[2]
        self.assertEqual(method, "PUT")
        self.assertTrue(url.endswith("/card/streaming"))
        self.assertEqual(request["json"]["outTrackId"], out_track_id)
        self.assertEqual(request["json"]["key"], "answer")
        self.assertEqual(request["json"]["content"], "**complete**")
        self.assertTrue(request["json"]["isFull"])
        self.assertTrue(request["json"]["isFinalize"])
        self.assertEqual(
            str(UUID(request["json"]["guid"])),
            request["json"]["guid"],
        )

    async def test_unsupported_file_type_is_not_sent(self) -> None:
        api, http = _openapi(
            {"errcode": 0, "media_id": "media-id"},
        )

        sent = await api.send_media(
            "user:user-1",
            b"text",
            "notes.txt",
            "text/plain",
        )

        self.assertFalse(sent)
        self.assertEqual(http.posts, [])
