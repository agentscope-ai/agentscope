# -*- coding: utf-8 -*-
"""Unit tests for the Telegram channel without a real bot or network."""
# pylint: disable=protected-access,missing-function-docstring
# pylint: disable=too-many-public-methods
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, call, patch

import pytest
from pydantic import ValidationError

from agentscope.app.channel import TelegramChannel
from agentscope.app.channel._base import ChannelEvent, ChatKind
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.channel._telegram._channel import (
    _ApprovalCallback,
    _MAX_API_ATTEMPTS,
    _MAX_DOCUMENT_BYTES,
    _MAX_DOWNLOAD_BYTES,
    _MAX_PHOTO_BYTES,
    _PermanentTelegramError,
    _StreamPreview,
    _TelegramResult,
)
from agentscope.app.channel._telegram._markdown import _TelegramTextChunk
from agentscope.event import (
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
    URLSource,
)
from agentscope.message._block import ToolResultState
from agentscope.permission import PermissionBehavior

try:
    from telegram import Chat, Message, MessageEntity, Update, User
    from telegram.error import (
        BadRequest,
        Conflict,
        InvalidToken,
        NetworkError,
        RetryAfter,
    )
    import markdown_it
except ImportError:
    pytest.skip(
        "Telegram channel tests require agentscope[channel]",
        allow_module_level=True,
    )
else:
    del markdown_it


_BOT = User(id=123, first_name="Agent", is_bot=True, username="agent_bot")
_USER = User(id=456, first_name="Alice", is_bot=False, username="alice")


def _channel(
    *,
    only_at_reply: bool = True,
    **config_values: Any,
) -> TelegramChannel:
    channel = TelegramChannel(
        "telegram-1",
        TelegramChannel.Credentials(
            bot_id=str(_BOT.id),
            bot_token="123:secret-token",
        ),
        TelegramChannel.Config(
            only_at_reply=only_at_reply,
            **config_values,
        ),
    )
    channel._bot_user = _BOT
    return channel


def _deterministic_event(event: ChannelEvent) -> dict[str, Any]:
    """Dump an event without generated timestamps and block ids."""
    payload = event.model_dump(exclude={"received_at"})
    for block in payload["content"]:
        block.pop("id", None)
        block.pop("created_at", None)
    return payload


def _expected_text_event(
    text: str,
    *,
    chat_type: str = "private",
    message_id: int = 1,
) -> dict[str, Any]:
    """Build the complete deterministic payload for one text message."""
    private = chat_type == "private"
    return {
        "channel_id": "telegram-1",
        "channel_user_id": "456",
        "channel_user_name": "Alice",
        "chat_id": "456" if private else "-100",
        "chat_name": "Alice" if private else "Test Group",
        "channel_message_id": str(message_id),
        "content": [
            {
                "type": "text",
                "text": text,
                "finished_at": None,
            },
        ],
        "metadata": {"chat_type": chat_type},
    }


def _callback_query(
    data: str,
    *,
    chat_id: int = -100,
    chat_type: str = "supergroup",
) -> Any:
    """Build one callback query attached to a concrete Telegram chat."""
    return SimpleNamespace(
        data=data,
        from_user=_USER,
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=chat_type),
        ),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )


def _message(
    *,
    chat_type: str = "private",
    text: str | None = None,
    caption: str | None = None,
    entities: list[MessageEntity] | None = None,
    caption_entities: list[MessageEntity] | None = None,
    from_user: User = _USER,
    reply_to_message: Message | None = None,
    media_group_id: str | None = None,
    message_id: int = 1,
) -> Message:
    chat = Chat(
        id=-100 if chat_type != "private" else 456,
        type=chat_type,
        title="Test Group" if chat_type != "private" else None,
        first_name="Alice" if chat_type == "private" else None,
    )
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=from_user,
        text=text,
        caption=caption,
        entities=entities,
        caption_entities=caption_entities,
        reply_to_message=reply_to_message,
        media_group_id=media_group_id,
    )


def _mention(text: str, *, caption: bool = False, **kwargs: Any) -> Message:
    entity = MessageEntity(
        type="mention",
        offset=0,
        length=len("@agent_bot"),
    )
    if caption:
        return _message(
            caption=text,
            caption_entities=[entity],
            **kwargs,
        )
    return _message(text=text, entities=[entity], **kwargs)


def _media_message(**values: Any) -> SimpleNamespace:
    defaults = {
        "photo": (),
        "document": None,
        "audio": None,
        "voice": None,
        "video": None,
        "animation": None,
        "video_note": None,
        "sticker": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


async def _events(items: list[Any]) -> AsyncIterator[dict]:
    for item in items:
        yield item.model_dump(mode="json")


class TelegramSchemaTest(TestCase):
    """Validate registration metadata and dependency isolation."""

    def test_public_schema_and_capabilities(self) -> None:
        credentials = TelegramChannel.Credentials(
            bot_id="123",
            bot_token="123:secret",
        )
        config = TelegramChannel.Config()
        schema = TelegramChannel.Credentials.model_json_schema()

        self.assertEqual(TelegramChannel.channel_type, "telegram")
        self.assertEqual(TelegramChannel.platform_bot_id_field, "bot_id")
        self.assertNotIn("secret", repr(credentials))
        self.assertEqual(
            schema["properties"]["bot_token"]["format"],
            "password",
        )
        self.assertTrue(config.only_at_reply)
        self.assertFalse(config.show_tool_process)
        self.assertFalse(config.show_thinking)
        self.assertFalse(config.allow_public_private_chats)
        self.assertEqual(config.allowed_private_user_ids, "")
        self.assertFalse(config.allow_public_group_chats)
        self.assertEqual(config.allowed_group_chat_ids, "")
        self.assertTrue(TelegramChannel.capabilities.text)
        self.assertTrue(TelegramChannel.capabilities.image)
        self.assertTrue(TelegramChannel.capabilities.file)
        self.assertTrue(TelegramChannel.capabilities.interactive)
        self.assertTrue(TelegramChannel.capabilities.markdown)
        self.assertTrue(TelegramChannel.capabilities.streaming)
        self.assertEqual(
            TelegramChannel.capabilities.max_message_length,
            4096,
        )

    def test_private_user_allowlist_is_normalised_and_validated(self) -> None:
        config = TelegramChannel.Config(
            allowed_private_user_ids=" 789, 456\n789 123 ",
        )
        self.assertEqual(config.allowed_private_user_ids, "123,456,789")
        with self.assertRaises(ValidationError):
            TelegramChannel.Config(allowed_private_user_ids="456, not-an-id")

    def test_group_chat_allowlist_is_normalised_and_validated(self) -> None:
        config = TelegramChannel.Config(
            allowed_group_chat_ids=" -1002, -1001\n-1002 123 ",
        )
        self.assertEqual(config.allowed_group_chat_ids, "-1002,-1001,123")
        for value in ("0", "not-an-id", "+100"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    TelegramChannel.Config(allowed_group_chat_ids=value)

    def test_credentials_are_normalised_and_validated_without_leaks(
        self,
    ) -> None:
        credentials = TelegramChannel.Credentials(
            bot_id=" 123 ",
            bot_token=" 123:abc_DEF-9 ",
        )
        self.assertEqual(credentials.bot_id, "123")
        self.assertEqual(credentials.bot_token, "123:abc_DEF-9")

        for values in (
            {"bot_id": "bot", "bot_token": "123:valid"},
            {"bot_id": "123", "bot_token": "missing-colon"},
            {"bot_id": "123", "bot_token": "123:do-not-leak!"},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError) as caught:
                    TelegramChannel.Credentials(**values)
                self.assertNotIn(
                    str(values["bot_token"]),
                    str(caught.exception),
                )

    def test_module_import_does_not_load_telegram(self) -> None:
        source = (
            "import sys; "
            "import agentscope.app.channel; "
            "assert 'telegram' not in sys.modules; "
            "assert 'markdown_it' not in sys.modules"
        )
        import subprocess
        import sys

        subprocess.run(  # noqa: S603
            [sys.executable, "-c", source],
            check=True,
        )

    def test_builder_uses_separate_requests_and_plain_callback_data(
        self,
    ) -> None:
        application = _channel()._build_application()
        polling_request, api_request = application.bot._request
        polling_client = getattr(polling_request, "_client")
        api_client = getattr(api_request, "_client")
        polling_pool = getattr(getattr(polling_client, "_transport"), "_pool")
        api_pool = getattr(getattr(api_client, "_transport"), "_pool")

        self.assertIsNot(polling_request, api_request)
        self.assertEqual(polling_request.read_timeout, 40)
        self.assertEqual(
            getattr(polling_pool, "_max_connections"),
            1,
        )
        self.assertEqual(
            getattr(api_pool, "_max_connections"),
            16,
        )
        self.assertIsNone(application.bot.callback_data_cache)
        self.assertEqual(len(application.handlers[0]), 2)

    def test_missing_optional_dependency_has_clear_error(self) -> None:
        real_import = __import__

        def blocked_import(
            name: str,
            globals_: Any = None,
            locals_: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            if name.startswith("telegram"):
                raise ImportError("blocked for test")
            return real_import(name, globals_, locals_, fromlist, level)

        with patch("builtins.__import__", side_effect=blocked_import):
            with self.assertRaisesRegex(
                ImportError,
                "python-telegram-bot.*22.8",
            ):
                _channel()._build_application()

    def test_missing_markdown_dependency_has_clear_error(self) -> None:
        real_import = __import__

        def blocked_import(
            name: str,
            globals_: Any = None,
            locals_: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            if name == "markdown_it":
                raise ImportError("blocked for test")
            return real_import(name, globals_, locals_, fromlist, level)

        with patch("builtins.__import__", side_effect=blocked_import):
            with self.assertRaisesRegex(
                ImportError,
                "markdown-it-py.*4",
            ):
                _channel()._build_application()


class _FakeUpdater:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.running = False
        self.polling_kwargs: dict[str, Any] = {}

    async def start_polling(self, **kwargs: Any) -> None:
        self.calls.append("updater.start_polling")
        self.polling_kwargs = kwargs
        self.running = True

    async def stop(self) -> None:
        self.calls.append("updater.stop")
        self.running = False


class _FakeBot:
    def __init__(
        self,
        calls: list[str],
        *,
        bot_id: int = 123,
        webhook_url: str = "",
    ) -> None:
        self.calls = calls
        self.bot_id = bot_id
        self.webhook_url = webhook_url

    async def get_me(self) -> Any:
        self.calls.append("bot.get_me")
        return SimpleNamespace(id=self.bot_id, username="agent_bot")

    async def get_webhook_info(self) -> Any:
        self.calls.append("bot.get_webhook_info")
        return SimpleNamespace(url=self.webhook_url)

    async def shutdown(self) -> None:
        self.calls.append("bot.shutdown")


class _FakeApplication:
    def __init__(
        self,
        *,
        bot_id: int = 123,
        webhook_url: str = "",
        initialize_error: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.bot = _FakeBot(
            self.calls,
            bot_id=bot_id,
            webhook_url=webhook_url,
        )
        self.updater = _FakeUpdater(self.calls)
        self.running = False
        self.initialize_error = initialize_error

    async def initialize(self) -> None:
        self.calls.append("application.initialize")
        if self.initialize_error is not None:
            raise self.initialize_error

    async def start(self) -> None:
        self.calls.append("application.start")
        self.running = True

    async def stop(self) -> None:
        self.calls.append("application.stop")
        self.running = False

    async def shutdown(self) -> None:
        self.calls.append("application.shutdown")


class TelegramLifecycleTest(IsolatedAsyncioTestCase):
    """Exercise the manually owned PTB application lifecycle."""

    async def test_manual_lifecycle_and_cancellation_cleanup(self) -> None:
        channel = _channel()
        application = _FakeApplication()
        channel._build_application = lambda: application
        channel._fatal_event = asyncio.Event()

        task = asyncio.create_task(channel._run_application())
        for _ in range(20):
            if channel.status.state == "connected":
                break
            await asyncio.sleep(0)
        self.assertEqual(channel.status.state, "connected")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(
            application.calls,
            [
                "application.initialize",
                "bot.get_me",
                "bot.get_webhook_info",
                "updater.start_polling",
                "application.start",
                "updater.stop",
                "application.stop",
                "application.shutdown",
            ],
        )
        allowed = application.updater.polling_kwargs["allowed_updates"]
        self.assertEqual(
            [str(value) for value in allowed],
            ["message", "callback_query"],
        )
        self.assertFalse(
            application.updater.polling_kwargs["drop_pending_updates"],
        )

    async def test_bot_id_mismatch_is_permanent(self) -> None:
        channel = _channel()
        application = _FakeApplication(bot_id=999)
        channel._build_application = lambda: application
        channel._fatal_event = asyncio.Event()

        with self.assertRaisesRegex(_PermanentTelegramError, "does not match"):
            await channel._run_application()
        self.assertEqual(
            application.calls[-1],
            "application.shutdown",
        )

    async def test_invalid_token_is_permanent_and_closes_requests(
        self,
    ) -> None:
        channel = _channel()
        application = _FakeApplication(
            initialize_error=InvalidToken("token rejected"),
        )
        channel._build_application = lambda: application
        channel._fatal_event = asyncio.Event()

        with self.assertRaisesRegex(_PermanentTelegramError, "rejected"):
            await channel._run_application()
        self.assertEqual(
            application.calls,
            ["application.initialize", "bot.shutdown"],
        )

    async def test_webhook_conflict_is_permanent(self) -> None:
        channel = _channel()
        application = _FakeApplication(webhook_url="https://example.test/hook")
        channel._build_application = lambda: application
        channel._fatal_event = asyncio.Event()

        with self.assertRaisesRegex(_PermanentTelegramError, "active webhook"):
            await channel._run_application()
        self.assertNotIn("updater.start_polling", application.calls)
        self.assertEqual(application.calls[-1], "application.shutdown")

    async def test_polling_conflict_is_fatal(self) -> None:
        channel = _channel()
        channel._fatal_event = asyncio.Event()
        channel._on_polling_error(Conflict("another getUpdates consumer"))

        self.assertEqual(channel.status.state, "failed")
        self.assertTrue(channel._fatal_event.is_set())
        self.assertIsInstance(channel._fatal_error, Conflict)


class TelegramInboundTest(IsolatedAsyncioTestCase):
    """Exercise filtering, normalization, media, and album handling."""

    async def asyncSetUp(self) -> None:
        self.channel = _channel(
            allowed_private_user_ids=str(_USER.id),
            allowed_group_chat_ids="-100",
        )
        self.received: list[ChannelEvent] = []

        async def emit(event: ChannelEvent) -> None:
            self.received.append(event)

        self.channel._emit = emit

    async def test_allowlisted_private_text_is_received(self) -> None:
        message = _message(text="hello")
        await self.channel._on_update(Update(1, message=message), None)

        self.assertEqual(len(self.received), 1)
        self.assertEqual(
            _deterministic_event(self.received[0]),
            {
                "channel_id": "telegram-1",
                "channel_user_id": "456",
                "channel_user_name": "Alice",
                "chat_id": "456",
                "chat_name": "Alice",
                "channel_message_id": "1",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "private"},
            },
        )
        self.assertEqual(await self.channel.chat_kind("456"), ChatKind.PRIVATE)

    async def test_unknown_private_text_is_silently_ignored(self) -> None:
        channel = _channel()
        channel._emit = AsyncMock()

        await channel._on_update(
            Update(1, message=_message(text="hello")),
            None,
        )

        channel._emit.assert_not_awaited()
        self.assertEqual(channel._chat_kind_cache, {})
        self.assertEqual(channel._chat_name_cache, {})
        self.assertEqual(channel._album_messages, {})
        self.assertEqual(channel._album_tasks, {})

    async def test_unknown_private_user_is_dropped_before_media_handling(
        self,
    ) -> None:
        channel = _channel()
        channel._emit = AsyncMock()
        media = SimpleNamespace(get_file=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(id=456, type="private"),
            text=None,
            media_group_id="album-1",
            photo=(media,),
            document=None,
            audio=None,
            voice=None,
            video=None,
            animation=None,
            video_note=None,
            sticker=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=_USER,
        )

        await channel._on_update(update, None)

        channel._emit.assert_not_awaited()
        self.assertEqual(channel._album_messages, {})
        self.assertEqual(channel._album_tasks, {})
        self.assertEqual(channel._chat_kind_cache, {})
        self.assertEqual(channel._chat_name_cache, {})
        media.get_file.assert_not_awaited()

    async def test_unknown_group_is_dropped_before_media_handling(
        self,
    ) -> None:
        channel = _channel()
        channel._emit = AsyncMock()
        media = SimpleNamespace(get_file=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            media_group_id="album-1",
            photo=(media,),
            document=None,
            audio=None,
            voice=None,
            video=None,
            animation=None,
            video_note=None,
            sticker=None,
        )

        await channel._on_update(
            SimpleNamespace(effective_message=message, effective_user=_USER),
            None,
        )

        channel._emit.assert_not_awaited()
        self.assertEqual(channel._album_messages, {})
        self.assertEqual(channel._album_tasks, {})
        self.assertEqual(channel._chat_kind_cache, {})
        self.assertEqual(channel._chat_name_cache, {})
        media.get_file.assert_not_awaited()

    async def test_public_private_chat_opt_in_allows_unknown_user(
        self,
    ) -> None:
        channel = _channel(allow_public_private_chats=True)
        received: list[ChannelEvent] = []

        async def emit(event: ChannelEvent) -> None:
            received.append(event)

        channel._emit = emit
        await channel._on_update(
            Update(1, message=_message(text="hello")),
            None,
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(
            _deterministic_event(received[0]),
            {
                "channel_id": "telegram-1",
                "channel_user_id": "456",
                "channel_user_name": "Alice",
                "chat_id": "456",
                "chat_name": "Alice",
                "channel_message_id": "1",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "private"},
            },
        )

    async def test_group_chat_allowlist_and_public_opt_in_allow_messages(
        self,
    ) -> None:
        configs: tuple[dict[str, Any], ...] = (
            {"allowed_group_chat_ids": "-100"},
            {"allow_public_group_chats": True},
        )
        for config in configs:
            with self.subTest(config=config):
                channel = _channel(**config)
                channel._emit = AsyncMock()

                await channel._on_update(
                    Update(
                        1,
                        message=_mention(
                            "@agent_bot hello",
                            chat_type="supergroup",
                        ),
                    ),
                    None,
                )

                channel._emit.assert_awaited_once()
                self.assertEqual(
                    _deterministic_event(channel._emit.await_args.args[0]),
                    {
                        "channel_id": "telegram-1",
                        "channel_user_id": "456",
                        "channel_user_name": "Alice",
                        "chat_id": "-100",
                        "chat_name": "Test Group",
                        "channel_message_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "hello",
                                "finished_at": None,
                            },
                        ],
                        "metadata": {"chat_type": "supergroup"},
                    },
                )

    async def test_unknown_start_returns_id_without_emitting_an_event(
        self,
    ) -> None:
        channel = _channel()
        bot = SimpleNamespace(send_message=AsyncMock())
        channel._application = SimpleNamespace(bot=bot)
        channel._emit = AsyncMock()
        await channel._on_update(
            Update(1, message=_message(text="/start")),
            None,
        )

        channel._emit.assert_not_awaited()
        bot.send_message.assert_awaited_once_with(
            chat_id=456,
            text=(
                "Your Telegram user ID is 456. Ask the channel owner to "
                "add it to the allowed private user IDs."
            ),
        )
        self.assertEqual(channel._chat_kind_cache, {})
        self.assertEqual(channel._chat_name_cache, {})

    async def test_group_requires_mention_or_reply(self) -> None:
        ignored = _message(chat_type="supergroup", text="hello")
        mentioned = _mention(
            "@agent_bot hello",
            chat_type="supergroup",
            message_id=2,
        )
        reply = _message(from_user=_BOT, text="previous", message_id=9)
        replied = _message(
            chat_type="supergroup",
            text="follow up",
            reply_to_message=reply,
            message_id=3,
        )

        await self.channel._on_update(Update(1, message=ignored), None)
        await self.channel._on_update(Update(2, message=mentioned), None)
        await self.channel._on_update(Update(3, message=replied), None)

        self.assertEqual(
            [_deterministic_event(event) for event in self.received],
            [
                _expected_text_event(
                    "hello",
                    chat_type="supergroup",
                    message_id=2,
                ),
                _expected_text_event(
                    "follow up",
                    chat_type="supergroup",
                    message_id=3,
                ),
            ],
        )
        self.assertEqual(await self.channel.chat_kind("-100"), ChatKind.GROUP)

    async def test_group_filter_can_be_disabled(self) -> None:
        channel = _channel(only_at_reply=False, allowed_group_chat_ids="-100")
        received: list[ChannelEvent] = []

        async def emit(event: ChannelEvent) -> None:
            received.append(event)

        channel._emit = emit
        message = _message(chat_type="group", text="hello all")
        await channel._on_update(Update(1, message=message), None)
        self.assertEqual(len(received), 1)
        self.assertEqual(
            _deterministic_event(received[0]),
            _expected_text_event("hello all", chat_type="group"),
        )

    async def test_bot_messages_are_ignored(self) -> None:
        message = _message(text="loop", from_user=_BOT)
        await self.channel._on_update(Update(1, message=message), None)
        self.assertEqual(self.received, [])

    async def test_caption_mention_is_removed(self) -> None:
        message = _mention(
            "@agent_bot describe this",
            caption=True,
            chat_type="group",
        )
        event = await self.channel._normalise_messages([message])
        assert event is not None
        self.assertEqual(
            _deterministic_event(event),
            _expected_text_event("describe this", chat_type="group"),
        )

    async def test_addressed_command_triggers_group_and_keeps_command(
        self,
    ) -> None:
        command = "/help@agent_bot"
        message = _message(
            chat_type="group",
            text=f"{command} topic",
            entities=[
                MessageEntity(
                    type="bot_command",
                    offset=0,
                    length=len(command),
                ),
            ],
        )

        self.assertFalse(self.channel._gated_out(message))
        event = await self.channel._normalise_messages([message])
        assert event is not None
        self.assertEqual(
            _deterministic_event(event),
            _expected_text_event("/help topic", chat_type="group"),
        )

    async def test_media_selection_covers_supported_types(self) -> None:
        def media(**kwargs: Any) -> SimpleNamespace:
            values = {
                "mime_type": None,
                "file_name": None,
                "is_animated": False,
                "is_video": False,
            }
            values.update(kwargs)
            return SimpleNamespace(**values)

        cases = [
            ("photo", [media()], "image/jpeg", "photo.jpg"),
            (
                "document",
                media(mime_type="text/plain", file_name="a.txt"),
                "text/plain",
                "a.txt",
            ),
            ("audio", media(), "audio/mpeg", "audio.mp3"),
            ("voice", media(), "audio/ogg", "voice.ogg"),
            ("video", media(), "video/mp4", "video.mp4"),
            ("animation", media(), "video/mp4", "animation.mp4"),
            ("video_note", media(), "video/mp4", "video-note.mp4"),
            ("sticker", media(), "image/webp", "sticker.webp"),
            (
                "sticker",
                media(is_animated=True),
                "application/x-tgsticker",
                "sticker.tgs",
            ),
            ("sticker", media(is_video=True), "video/webm", "sticker.webm"),
        ]
        for attr, item, expected_mime, expected_name in cases:
            with self.subTest(attr=attr, name=expected_name):
                selected = self.channel._select_media(
                    _media_message(**{attr: item}),
                )
                self.assertEqual(selected[1:], (expected_mime, expected_name))

        smaller = media()
        largest = media()
        selected = self.channel._select_media(
            _media_message(photo=[smaller, largest]),
        )
        assert selected is not None
        self.assertIs(selected[0], largest)

    async def test_downloaded_media_is_base64_and_preserves_metadata(
        self,
    ) -> None:
        telegram_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(b"data")),
        )
        media = SimpleNamespace(
            mime_type="application/pdf",
            file_name="report.pdf",
            file_size=4,
            get_file=AsyncMock(return_value=telegram_file),
        )
        block = await self.channel._download_media(
            _media_message(document=media),
        )

        self.assertIsInstance(block, DataBlock)
        self.assertEqual(block.name, "report.pdf")
        self.assertEqual(block.source.media_type, "application/pdf")
        self.assertEqual(base64.b64decode(block.source.data), b"data")

    async def test_download_size_is_checked_before_get_file(self) -> None:
        media = SimpleNamespace(
            mime_type="application/octet-stream",
            file_name="huge.bin",
            file_size=_MAX_DOWNLOAD_BYTES + 1,
            get_file=AsyncMock(),
        )
        block = await self.channel._download_media(
            _media_message(document=media),
        )

        self.assertIsInstance(block, TextBlock)
        self.assertIn("20 MiB", block.text)
        media.get_file.assert_not_awaited()

    async def test_location_venue_and_contact_are_stable_text(self) -> None:
        location = SimpleNamespace(
            latitude=1.5,
            longitude=2.5,
            horizontal_accuracy=None,
            live_period=None,
        )
        venue = SimpleNamespace(
            title="Office",
            address="Main Road",
            location=location,
        )
        contact = SimpleNamespace(
            first_name="Alice",
            last_name="Doe",
            phone_number="+123",
            user_id=456,
            vcard="must not leak",
        )
        self.assertIn(
            "latitude: 1.5",
            self.channel._structured_text(
                SimpleNamespace(venue=None, location=location, contact=None),
            ),
        )
        self.assertIn(
            "title: Office",
            self.channel._structured_text(
                SimpleNamespace(venue=venue, location=None, contact=None),
            ),
        )
        contact_text = self.channel._structured_text(
            SimpleNamespace(venue=None, location=None, contact=contact),
        )
        self.assertIn("name: Alice Doe", contact_text)
        self.assertNotIn("vcard", contact_text)

    async def test_album_keeps_order_if_any_item_mentions_bot(self) -> None:
        first = _message(
            chat_type="group",
            caption="first",
            media_group_id="album-1",
            message_id=1,
        )
        second = _mention(
            "@agent_bot second",
            caption=True,
            chat_type="group",
            media_group_id="album-1",
            message_id=2,
        )
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="-100",
            content=[TextBlock(text="album")],
        )
        self.channel._normalise_messages = AsyncMock(return_value=event)

        with patch(
            "agentscope.app.channel._telegram._channel._ALBUM_SETTLE_SECS",
            0.01,
        ):
            self.channel._buffer_album(first)
            self.channel._buffer_album(second)
            await asyncio.sleep(0.03)

        normalised_messages = self.channel._normalise_messages.await_args.args[
            0
        ]
        self.assertEqual(
            [message.message_id for message in normalised_messages],
            [1, 2],
        )
        self.assertEqual(self.received, [event])

    async def test_album_tasks_are_cancelled_on_shutdown(self) -> None:
        message = _message(media_group_id="album-2")
        with patch(
            "agentscope.app.channel._telegram._channel._ALBUM_SETTLE_SECS",
            60,
        ):
            self.channel._buffer_album(message)
            await self.channel._cancel_albums()
        self.assertEqual(self.channel._album_tasks, {})
        self.assertEqual(self.channel._album_messages, {})

    async def test_album_caption_is_included_once(self) -> None:
        first = _message(caption="one", media_group_id="album")
        second = _message(
            caption="two",
            media_group_id="album",
            message_id=2,
        )
        event = await self.channel._normalise_messages([first, second])
        assert event is not None
        self.assertEqual(
            _deterministic_event(event),
            _expected_text_event("one"),
        )


class TelegramOutboundTest(IsolatedAsyncioTestCase):
    """Exercise streaming replies, limits, and approval callbacks."""

    async def asyncSetUp(self) -> None:
        self.channel = _channel(
            allowed_private_user_ids=str(_USER.id),
            allowed_group_chat_ids="-100",
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=99),
            ),
            send_message_draft=AsyncMock(return_value=True),
            edit_message_text=AsyncMock(return_value=True),
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
            get_chat=AsyncMock(),
        )
        self.channel._application = SimpleNamespace(bot=self.bot)
        self.bus = InMemoryMessageBus()
        self.channel.bind_message_bus(self.bus)

    async def test_text_is_sent_non_streaming_and_split(self) -> None:
        result = await self.channel.send_message_to("-100", "x" * 4097)
        self.assertTrue(result.ok)
        self.assertEqual(self.bot.send_message.await_count, 2)
        calls = self.bot.send_message.await_args_list
        self.assertEqual(len(calls[0].kwargs["text"]), 4096)
        self.assertEqual(calls[0].kwargs["chat_id"], -100)
        self.assertTrue(
            all("parse_mode" not in call.kwargs for call in calls),
        )

    async def test_image_and_file_limits(self) -> None:
        image = await self.channel.send_image_to("1", b"image", "a.png")
        file_result = await self.channel.send_file_to("1", b"file", "a.bin")
        big_image = await self.channel.send_image_to(
            "1",
            b"x" * (_MAX_PHOTO_BYTES + 1),
        )
        big_file = await self.channel.send_file_to(
            "1",
            b"x" * (_MAX_DOCUMENT_BYTES + 1),
            "huge.bin",
        )

        self.assertTrue(image.ok)
        self.assertTrue(file_result.ok)
        self.assertIn("SendFile", big_image.error)
        self.assertIn("50 MiB", big_file.error)
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_document.assert_awaited_once()

    async def test_private_response_uses_draft_then_persistent_message(
        self,
    ) -> None:
        items = [
            ReplyStartEvent(session_id="s", reply_id="r", name="agent"),
            TextBlockStartEvent(reply_id="r", block_id="t"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="done"),
            TextBlockEndEvent(reply_id="r", block_id="t"),
            ReplyEndEvent(session_id="s", reply_id="r"),
        ]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="456",
            metadata={"chat_type": "private"},
        )
        await self.channel.send_response(event, _events(items))

        self.bot.send_message_draft.assert_awaited_once()
        draft = self.bot.send_message_draft.await_args.kwargs
        self.assertEqual(draft["chat_id"], 456)
        self.assertNotEqual(draft["draft_id"], 0)
        self.assertEqual(draft["text"], "done")
        self.assertEqual(draft["parse_mode"], "HTML")
        self.bot.send_message.assert_awaited_once_with(
            chat_id=456,
            text="done",
            parse_mode="HTML",
        )
        self.bot.edit_message_text.assert_not_awaited()

    async def test_group_response_creates_and_edits_one_preview(self) -> None:
        items = [
            ReplyStartEvent(session_id="s", reply_id="r", name="agent"),
            TextBlockStartEvent(reply_id="r", block_id="t"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="a"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="b"),
            TextBlockEndEvent(reply_id="r", block_id="t"),
            ReplyEndEvent(session_id="s", reply_id="r"),
        ]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="-100",
            metadata={"chat_type": "supergroup"},
        )
        with patch(
            "agentscope.app.channel._telegram._channel.time.monotonic",
            side_effect=[1.0, 2.1, 2.2],
        ):
            await self.channel.send_response(event, _events(items))

        self.bot.send_message.assert_awaited_once_with(
            chat_id=-100,
            text="a",
            parse_mode="HTML",
        )
        self.bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100,
            message_id=99,
            text="ab",
            parse_mode="HTML",
        )
        self.bot.send_message_draft.assert_not_awaited()

    async def test_stream_updates_are_throttled(self) -> None:
        preview = _StreamPreview(mode="draft", draft_id=7)
        with patch(
            "agentscope.app.channel._telegram._channel.time.monotonic",
            side_effect=[1.0, 1.5, 2.1],
        ):
            await self.channel._update_stream_preview("456", preview, "a")
            await self.channel._update_stream_preview("456", preview, "ab")
            await self.channel._update_stream_preview("456", preview, "abc")

        self.assertEqual(self.bot.send_message_draft.await_count, 2)
        self.assertEqual(
            [
                call.kwargs["text"]
                for call in self.bot.send_message_draft.await_args_list
            ],
            ["a", "abc"],
        )

    async def test_group_preview_does_not_jump_to_short_tail_chunk(
        self,
    ) -> None:
        preview = _StreamPreview(mode="edit", draft_id=7)

        await self.channel._update_stream_preview(
            "-100",
            preview,
            "x" * 4097,
        )

        self.bot.send_message.assert_awaited_once_with(
            chat_id=-100,
            text="x" * 4096,
            parse_mode="HTML",
        )
        self.assertEqual(preview.last_html, "x" * 4096)

    async def test_preview_failure_does_not_block_final_reply(self) -> None:
        self.bot.send_message_draft.side_effect = NetworkError("offline")
        items = [
            ReplyStartEvent(session_id="s", reply_id="r", name="agent"),
            TextBlockStartEvent(reply_id="r", block_id="t"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="done"),
            TextBlockEndEvent(reply_id="r", block_id="t"),
            ReplyEndEvent(session_id="s", reply_id="r"),
        ]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="456",
            metadata={"chat_type": "private"},
        )

        await self.channel.send_response(event, _events(items))

        self.bot.send_message_draft.assert_awaited_once()
        self.bot.send_message.assert_awaited_once_with(
            chat_id=456,
            text="done",
            parse_mode="HTML",
        )

    async def test_preview_render_failure_uses_plain_final_reply(self) -> None:
        items = [
            ReplyStartEvent(session_id="s", reply_id="r", name="agent"),
            TextBlockStartEvent(reply_id="r", block_id="t"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="done"),
            TextBlockEndEvent(reply_id="r", block_id="t"),
            ReplyEndEvent(session_id="s", reply_id="r"),
        ]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="456",
            metadata={"chat_type": "private"},
        )

        with patch.object(
            self.channel,
            "_formatted_chunks",
            side_effect=ValueError("bad markdown"),
        ):
            await self.channel.send_response(event, _events(items))

        self.bot.send_message_draft.assert_not_awaited()
        self.bot.send_message.assert_awaited_once_with(
            chat_id=456,
            text="done",
        )

    async def test_bad_formatted_text_falls_back_to_plain_text(self) -> None:
        self.bot.send_message.side_effect = [
            BadRequest("can't parse entities"),
            SimpleNamespace(message_id=100),
        ]
        chunk = self.channel._formatted_chunks("**bold**")[0]

        result = await self.channel._send_formatted_chunk("456", chunk)

        self.assertTrue(result.ok)
        self.assertEqual(self.bot.send_message.await_count, 2)
        first, second = self.bot.send_message.await_args_list
        self.assertEqual(first.kwargs["parse_mode"], "HTML")
        self.assertEqual(first.kwargs["text"], "<b>bold</b>")
        self.assertNotIn("parse_mode", second.kwargs)
        self.assertEqual(second.kwargs["text"], "bold")

    async def test_long_group_final_reuses_preview_and_sends_remainder(
        self,
    ) -> None:
        text = f"**{'x' * 4097}**"
        preview = _StreamPreview(
            mode="edit",
            draft_id=7,
            message_id=99,
        )

        await self.channel._finish_streamed_text("-100", preview, text)

        self.bot.edit_message_text.assert_awaited_once()
        edit = self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edit["message_id"], 99)
        self.assertEqual(edit["parse_mode"], "HTML")
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(
            self.bot.send_message.await_args.kwargs["text"],
            "<b>x</b>",
        )

    async def test_final_text_stops_after_the_first_failed_chunk(self) -> None:
        self.channel._send_formatted_chunk = AsyncMock(
            return_value=_TelegramResult(False, "offline"),
        )
        preview = _StreamPreview(mode="none", draft_id=7)

        await self.channel._finish_streamed_text(
            "-100",
            preview,
            "x" * 4097,
        )

        self.assertEqual(
            self.channel._send_formatted_chunk.await_args_list,
            [
                call(
                    "-100",
                    _TelegramTextChunk(
                        html="x" * 4096,
                        plain="x" * 4096,
                    ),
                ),
            ],
        )

    async def test_response_image_degrades_to_document(self) -> None:
        raw = b"x" * (_MAX_PHOTO_BYTES + 1)
        block = DataBlock(
            source=Base64Source(
                data=base64.b64encode(raw).decode("ascii"),
                media_type="image/png",
            ),
            name="large.png",
        )
        self.channel._render = lambda *args, **kwargs: [block]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="1",
            metadata={"chat_type": "private"},
        )
        await self.channel.send_response(
            event,
            _events([ReplyEndEvent(session_id="s", reply_id="r")]),
        )
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_document.assert_awaited_once()

    async def test_response_ignores_url_sources(self) -> None:
        block = DataBlock(
            source=URLSource(
                url="https://example.test/image.png",
                media_type="image/png",
            ),
            name="remote.png",
        )
        self.channel._render = lambda *args, **kwargs: [block]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="1",
            metadata={"chat_type": "private"},
        )
        await self.channel.send_response(
            event,
            _events([ReplyEndEvent(session_id="s", reply_id="r")]),
        )
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_text_and_attachments_finish_before_approval(self) -> None:
        order: list[str] = []

        def record_text(*args: Any) -> _TelegramResult:
            del args
            order.append("text")
            return _TelegramResult(True)

        def record_attachment(*args: Any) -> _TelegramResult:
            del args
            order.append("attachment")
            return _TelegramResult(True)

        def record_approval(*args: Any) -> None:
            del args
            order.append("approval")

        image = DataBlock(
            source=Base64Source(
                data=base64.b64encode(b"image").decode("ascii"),
                media_type="image/png",
            ),
            name="image.png",
        )
        self.channel._render = lambda *args, **kwargs: [
            TextBlock(text="answer"),
            image,
        ]
        self.channel._send_formatted_chunk = AsyncMock(
            side_effect=record_text,
        )
        self.channel.send_image_to = AsyncMock(
            side_effect=record_attachment,
        )
        self.channel._present_confirm = AsyncMock(
            side_effect=record_approval,
        )
        confirmation = RequireUserConfirmEvent(
            reply_id="reply-1",
            tool_calls=[
                ToolCallBlock(
                    id="tool-1",
                    name="SendImage",
                    input='{"chat_id":"1","path":"image.png"}',
                ),
            ],
        )
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="456",
            metadata={"chat_type": "private"},
        )

        await self.channel.send_response(
            event,
            _events([confirmation]),
        )

        self.assertEqual(order, ["text", "attachment", "approval"])

    async def test_callback_allow_deny_and_expired_data(self) -> None:
        emitted: list[Any] = []

        async def emit(event: Any) -> None:
            emitted.append(event)

        self.channel._emit = emit
        for approved, expected_text in (
            (True, "✅ Approved"),
            (False, "🚫 Denied"),
        ):
            callback_data = await self.channel._store_approval_callback(
                _ApprovalCallback(
                    tool_call_id=f"tool-{approved}",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                    approved=approved,
                ),
            )
            query = _callback_query(callback_data)
            await self.channel._on_callback(
                SimpleNamespace(callback_query=query),
                None,
            )
            self.assertEqual(
                emitted[-1].model_dump(),
                {
                    "channel_id": "telegram-1",
                    "chat_id": "-100",
                    "channel_user_id": "456",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "tool_call_id": f"tool-{approved}",
                    "approved": approved,
                    "actor": "456",
                },
            )
            query.answer.assert_awaited_once_with("Decision received.")
            query.edit_message_text.assert_awaited_once_with(expected_text)
            stored, _ = await self.channel._load_approval_callback(
                callback_data,
            )
            self.assertIsNone(stored)

        expired = _callback_query("tampered")
        await self.channel._on_callback(
            SimpleNamespace(callback_query=expired),
            None,
        )
        self.assertEqual(len(emitted), 2)
        expired.answer.assert_awaited_once_with(
            "This approval has expired.",
            show_alert=True,
        )

    async def test_callback_emits_when_ui_operations_fail(self) -> None:
        emitted: list[Any] = []

        async def emit(event: Any) -> None:
            emitted.append(event)

        self.channel._emit = emit
        for failed_ui in ("answer", "edit_message_text"):
            callback_data = await self.channel._store_approval_callback(
                _ApprovalCallback(
                    tool_call_id=f"tool-{failed_ui}",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                    approved=True,
                ),
            )
            query = _callback_query(callback_data)
            getattr(query, failed_ui).side_effect = RuntimeError("offline")

            await self.channel._on_callback(
                SimpleNamespace(callback_query=query),
                None,
            )

            self.assertEqual(
                emitted[-1].model_dump(),
                {
                    "channel_id": "telegram-1",
                    "chat_id": "-100",
                    "channel_user_id": "456",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "tool_call_id": f"tool-{failed_ui}",
                    "approved": True,
                    "actor": "456",
                },
            )
            query.answer.assert_awaited_once_with("Decision received.")
            query.edit_message_text.assert_awaited_once_with("✅ Approved")
            stored, _ = await self.channel._load_approval_callback(
                callback_data,
            )
            self.assertIsNone(stored)

    async def test_callback_emits_before_acknowledging_telegram(self) -> None:
        order: list[str] = []

        async def emit(_event: Any) -> None:
            order.append("event")

        async def answer(*_args: Any, **_kwargs: Any) -> None:
            order.append("answer")

        self.channel._emit = emit
        callback_data = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-order",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
                approved=True,
            ),
        )
        query = _callback_query(callback_data)
        query.answer = AsyncMock(side_effect=answer)

        await self.channel._on_callback(
            SimpleNamespace(callback_query=query),
            None,
        )

        self.assertEqual(order, ["event", "answer"])

    async def test_callback_state_is_available_to_the_listener_instance(
        self,
    ) -> None:
        listener = _channel(allowed_group_chat_ids="-100")
        listener.bind_message_bus(self.bus)
        emitted: list[Any] = []

        async def emit(event: Any) -> None:
            emitted.append(event)

        listener._emit = emit
        callback_data = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-shared",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
                approved=False,
            ),
        )
        query = _callback_query(callback_data)

        await listener._on_callback(
            SimpleNamespace(callback_query=query),
            None,
        )

        self.assertEqual(len(emitted), 1)
        self.assertEqual(
            emitted[0].model_dump(),
            {
                "channel_id": "telegram-1",
                "chat_id": "-100",
                "channel_user_id": "456",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "tool_call_id": "tool-shared",
                "approved": False,
                "actor": "456",
            },
        )

    async def test_callback_chat_and_current_access_policy_are_enforced(
        self,
    ) -> None:
        cases = (
            (
                "mismatched chat",
                {"allowed_group_chat_ids": "-100,-200"},
                "-100",
                -200,
                "supergroup",
            ),
            ("disallowed group", {}, "-100", -100, "supergroup"),
            ("disallowed private", {}, "456", 456, "private"),
        )
        for label, config, stored_chat, query_chat, chat_type in cases:
            with self.subTest(label=label):
                listener = _channel(**config)
                listener.bind_message_bus(self.bus)
                listener._emit = AsyncMock()
                callback_data = await self.channel._store_approval_callback(
                    _ApprovalCallback(
                        tool_call_id=f"tool-{label}",
                        chat_id=stored_chat,
                        agent_id="agent-1",
                        session_id="session-1",
                        approved=True,
                    ),
                )
                query = _callback_query(
                    callback_data,
                    chat_id=query_chat,
                    chat_type=chat_type,
                )

                await listener._on_callback(
                    SimpleNamespace(callback_query=query),
                    None,
                )

                listener._emit.assert_not_awaited()
                query.answer.assert_awaited_once_with(
                    "This approval has expired.",
                    show_alert=True,
                )
                query.edit_message_reply_markup.assert_awaited_once_with(
                    reply_markup=None,
                )
                stored, _ = await self.channel._load_approval_callback(
                    callback_data,
                )
                self.assertIsNone(stored)

    async def test_approval_buttons_use_shared_callback_tokens(self) -> None:
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="-100",
            metadata={"agent_id": "agent-1", "session_id": "session-1"},
        )
        request = RequireUserConfirmEvent(
            reply_id="reply-1",
            tool_calls=[
                ToolCallBlock(
                    id="tool-1",
                    name="SendMessage",
                    input='{"chat_id":"1","text":"hello"}',
                ),
            ],
        )
        await self.channel._present_confirm(event, request)

        markup = self.bot.send_message.await_args.kwargs["reply_markup"]
        allow = markup.inline_keyboard[0][0].callback_data
        deny = markup.inline_keyboard[0][1].callback_data
        allow_data, _ = await self.channel._load_approval_callback(allow)
        deny_data, _ = await self.channel._load_approval_callback(deny)
        self.assertIsInstance(allow, str)
        self.assertLessEqual(len(allow.encode("utf-8")), 64)
        assert allow_data is not None
        assert deny_data is not None
        self.assertEqual(allow_data.tool_call_id, "tool-1")
        self.assertEqual(allow_data.agent_id, "agent-1")
        self.assertEqual(allow_data.session_id, "session-1")
        self.assertTrue(allow_data.approved)
        self.assertFalse(deny_data.approved)

    async def test_connection_free_client_initializes_and_closes_rest_bot(
        self,
    ) -> None:
        channel = _channel()
        bot = SimpleNamespace(
            initialize=AsyncMock(),
            shutdown=AsyncMock(),
            bot=SimpleNamespace(id=_BOT.id, username="agent_bot"),
        )
        channel._new_rest_bot = lambda: bot

        self.assertIs(await channel._bot(), bot)
        self.assertIs(await channel._bot(), bot)
        bot.initialize.assert_awaited_once()
        await channel.aclose()
        bot.shutdown.assert_awaited_once()

    async def test_chat_metadata_cache_and_empty_chat_listing(self) -> None:
        chat = Chat(id=-100, type="supergroup", title="Team")
        self.bot.get_chat.return_value = chat
        self.assertEqual(await self.channel.list_bot_chats(), [])
        self.assertEqual(await self.channel.chat_name("-100"), "Team")
        self.assertEqual(await self.channel.chat_kind("-100"), ChatKind.GROUP)
        self.bot.get_chat.assert_awaited_once_with(-100)

    async def test_outbound_response_retries_after_long_flood_wait(
        self,
    ) -> None:
        retry_delays = [31 + offset for offset in range(_MAX_API_ATTEMPTS + 1)]
        self.bot.send_message.side_effect = [
            *[
                RetryAfter(timedelta(seconds=seconds))
                for seconds in retry_delays
            ],
            SimpleNamespace(message_id=100),
        ]
        event = ChannelEvent(
            channel_id="telegram-1",
            channel_user_id="456",
            chat_id="456",
            metadata={"chat_type": "private"},
        )
        items = [
            ReplyStartEvent(session_id="s", reply_id="r", name="agent"),
            TextBlockStartEvent(reply_id="r", block_id="t"),
            TextBlockDeltaEvent(reply_id="r", block_id="t", delta="reply"),
            TextBlockEndEvent(reply_id="r", block_id="t"),
            ReplyEndEvent(session_id="s", reply_id="r"),
        ]

        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            await self.channel.send_response(event, _events(items))

        self.assertEqual(
            self.bot.send_message.await_args_list,
            [
                call(
                    chat_id=456,
                    text="reply",
                    parse_mode="HTML",
                ),
            ]
            * (len(retry_delays) + 1),
        )
        self.assertEqual(
            sleep.await_args_list,
            [call(delay) for delay in retry_delays],
        )


class TelegramToolsAndRetryTest(IsolatedAsyncioTestCase):
    """Exercise workspace tools and the narrow API retry policy."""

    async def test_tools_use_workspace_and_always_ask(self) -> None:
        channel = _channel()
        channel.send_message_to = AsyncMock(return_value=_TelegramResult(True))
        channel.send_file_to = AsyncMock(return_value=_TelegramResult(True))
        channel.send_image_to = AsyncMock(return_value=_TelegramResult(True))
        backend = SimpleNamespace(read_file=AsyncMock(return_value=b"data"))
        workspace = SimpleNamespace(get_backend=lambda: backend)
        tools = await channel.list_tools(workspace)

        self.assertEqual(
            [tool.name for tool in tools],
            ["SendMessage", "SendFile", "SendImage"],
        )
        for tool in tools:
            decision = await tool.check_permissions({}, None)
            self.assertEqual(decision.behavior, PermissionBehavior.ASK)

        message_result = await tools[0](chat_id="1", text="hello")
        file_result = await tools[1](chat_id="1", path="/workspace/a.bin")
        image_result = await tools[2](chat_id="1", path="/workspace/a.png")
        self.assertEqual(message_result.state, ToolResultState.SUCCESS)
        self.assertEqual(file_result.state, ToolResultState.SUCCESS)
        self.assertEqual(image_result.state, ToolResultState.SUCCESS)
        self.assertEqual(backend.read_file.await_count, 2)
        channel.send_file_to.assert_awaited_once_with(
            "1",
            b"data",
            "a.bin",
        )

    async def test_workspace_read_failure_is_structured(self) -> None:
        channel = _channel()
        backend = SimpleNamespace(
            read_file=AsyncMock(side_effect=FileNotFoundError("missing")),
        )
        workspace = SimpleNamespace(get_backend=lambda: backend)
        tools = await channel.list_tools(workspace)
        result = await tools[1](chat_id="1", path="/workspace/nope")
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("missing", result.content[0].text)

    async def test_network_and_retry_after_retries(self) -> None:
        channel = _channel()
        network_operation = AsyncMock(
            side_effect=[NetworkError("one"), NetworkError("two"), "ok"],
        )
        retry_after_operation = AsyncMock(
            side_effect=[RetryAfter(timedelta(seconds=0.01)), "ok"],
        )
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(
                await channel._retry_api(network_operation),
                "ok",
            )
            self.assertEqual(
                await channel._retry_api(retry_after_operation),
                "ok",
            )
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [1, 2, 0.01],
        )

    async def test_non_retryable_and_long_retry_after_is_retried(
        self,
    ) -> None:
        channel = _channel()
        bad_request = AsyncMock(side_effect=BadRequest("bad"))
        retry_delays = [31 + offset for offset in range(_MAX_API_ATTEMPTS + 1)]
        long_retry = AsyncMock(
            side_effect=[
                *[
                    RetryAfter(timedelta(seconds=seconds))
                    for seconds in retry_delays
                ],
                "ok",
            ],
        )
        with self.assertRaises(BadRequest):
            await channel._retry_api(bad_request)
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(await channel._retry_api(long_retry), "ok")
        self.assertEqual(bad_request.await_count, 1)
        self.assertEqual(
            long_retry.await_args_list,
            [call()] * (len(retry_delays) + 1),
        )
        self.assertEqual(
            sleep.await_args_list,
            [call(delay) for delay in retry_delays],
        )

    async def test_network_error_budget_and_retry_cancellation(self) -> None:
        channel = _channel()
        network_operation = AsyncMock(
            side_effect=[NetworkError("offline")] * _MAX_API_ATTEMPTS,
        )
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(NetworkError):
                await channel._retry_api(network_operation)
        self.assertEqual(network_operation.await_count, _MAX_API_ATTEMPTS)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [1, 2],
        )

        sleep_started = asyncio.Event()

        async def wait_until_cancelled(_seconds: float) -> None:
            sleep_started.set()
            await asyncio.Event().wait()

        retry_after_operation = AsyncMock(
            side_effect=RetryAfter(timedelta(seconds=60)),
        )
        with patch("asyncio.sleep", side_effect=wait_until_cancelled):
            task = asyncio.create_task(
                channel._retry_api(retry_after_operation),
            )
            await sleep_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(retry_after_operation.await_count, 1)

    async def test_token_is_redacted_from_platform_errors(self) -> None:
        channel = _channel()
        error = RuntimeError("request failed for 123:secret-token")
        self.assertNotIn("secret-token", channel._safe_error(error))
