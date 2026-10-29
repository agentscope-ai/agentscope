# -*- coding: utf-8 -*-
"""Unit tests for the Telegram channel without a real bot or network."""
# pylint: disable=protected-access,missing-function-docstring
# pylint: disable=too-many-public-methods
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, call, patch

import pytest
from pydantic import ValidationError

from utils import AnyString, AnyValue

from agentscope.app.channel import TelegramChannel
from agentscope.app.channel._base import (
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChatKind,
)
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.channel._telegram._channel import (
    _ApprovalCallback,
    _LISTENER_LOCK_PREFIX,
    _MAX_API_ATTEMPTS,
    _MAX_DOCUMENT_BYTES,
    _MAX_PHOTO_BYTES,
    _TelegramDeliveryUnknown,
    _TelegramRetryBudgetExceeded,
    _PermanentTelegramError,
    _RetryBudget,
    _StreamPreview,
    _TelegramResult,
)
from agentscope.app.channel._telegram._markdown import _TelegramTextChunk
from agentscope.app.channel._telegram._inbound import (
    _MAX_DOWNLOAD_BYTES,
    TelegramInboundNormalizer,
)
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
from agentscope.permission import PermissionBehavior, PermissionDecision

try:
    from telegram import Chat, Message, MessageEntity, Update, User
    from telegram.error import (
        BadRequest,
        Conflict,
        InvalidToken,
        NetworkError,
        RetryAfter,
        TimedOut,
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


def _callback_query(
    data: str,
    *,
    chat_id: int = -100,
    chat_type: str = "supergroup",
    from_user: User = _USER,
) -> Any:
    """Build one callback query attached to a concrete Telegram chat."""
    return SimpleNamespace(
        data=data,
        from_user=from_user,
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
        self.assertEqual(
            config.model_dump(),
            {
                "only_at_reply": True,
                "show_tool_process": False,
                "show_thinking": False,
            },
        )
        self.assertEqual(
            TelegramChannel.capabilities.model_dump(),
            {
                "text": True,
                "markdown": True,
                "image": True,
                "file": True,
                "interactive": True,
                "streaming": True,
                "max_message_length": 4096,
                "wiki": False,
            },
        )

    def test_credentials_are_normalised_and_validated_without_leaks(
        self,
    ) -> None:
        credentials = TelegramChannel.Credentials(
            bot_id=" 123 ",
            bot_token=" 123:abc_DEF-9 ",
        )
        self.assertEqual(
            credentials.model_dump(),
            {"bot_id": "123", "bot_token": "123:abc_DEF-9"},
        )

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
        self.assertEqual(
            application.updater.polling_kwargs,
            {
                "timeout": 30,
                "bootstrap_retries": 0,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
                "error_callback": channel._on_polling_error,
            },
        )

    async def test_bot_id_mismatch_is_permanent(self) -> None:
        channel = _channel()
        application = _FakeApplication(bot_id=999)
        channel._build_application = lambda: application
        channel._fatal_event = asyncio.Event()

        with self.assertRaisesRegex(_PermanentTelegramError, "does not match"):
            await channel._run_application()
        self.assertEqual(
            application.calls,
            [
                "application.initialize",
                "bot.get_me",
                "application.shutdown",
            ],
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
        self.assertEqual(
            application.calls,
            [
                "application.initialize",
                "bot.get_me",
                "bot.get_webhook_info",
                "application.shutdown",
            ],
        )

    async def test_polling_conflict_requests_reconnect(self) -> None:
        channel = _channel()
        channel._fatal_event = asyncio.Event()
        channel._on_polling_error(Conflict("another getUpdates consumer"))

        self.assertEqual(channel.status.state, "retrying")
        self.assertTrue(channel._fatal_event.is_set())
        self.assertIsInstance(channel._fatal_error, Conflict)

    async def test_listener_lease_serializes_replicas(self) -> None:
        bus = InMemoryMessageBus()
        first = _channel()
        second = _channel()
        first.bind_message_bus(bus)
        second.bind_message_bus(bus)
        entered = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []

        async def hold_first() -> None:
            async with first._listener_lease():
                order.append("first")
                entered.set()
                await release.wait()

        async def enter_second() -> None:
            await entered.wait()
            async with second._listener_lease():
                order.append("second")

        first_task = asyncio.create_task(hold_first())
        second_task = asyncio.create_task(enter_second())
        await entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(order, ["first"])
        release.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(order, ["first", "second"])

    async def test_listener_lease_is_released_on_cancellation(self) -> None:
        bus = InMemoryMessageBus()
        channel = _channel()
        channel.bind_message_bus(bus)
        entered = asyncio.Event()

        async def hold_lease() -> None:
            async with channel._listener_lease():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold_lease())
        await entered.wait()
        lock_key = f"{_LISTENER_LOCK_PREFIX}{channel.channel_id}"
        self.assertTrue(await bus.is_locked(lock_key))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(await bus.is_locked(lock_key))

    async def test_conflict_retries_beyond_the_old_attempt_limit(self) -> None:
        channel = _channel()
        channel._run_application = AsyncMock(
            side_effect=Conflict("another getUpdates consumer"),
        )
        delays: list[float] = []

        async def stop_after_three(delay: float) -> None:
            delays.append(delay)
            if len(delays) == 3:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=stop_after_three):
            with self.assertRaises(asyncio.CancelledError):
                await channel.start_listening(AsyncMock())

        self.assertEqual(
            channel._run_application.await_args_list,
            [call()] * 3,
        )
        self.assertEqual(delays, [1.0, 2.0, 4.0])


class TelegramInboundTest(IsolatedAsyncioTestCase):
    """Exercise filtering, normalization, media, and album handling."""

    async def asyncSetUp(self) -> None:
        self.channel = _channel()
        self.received: list[ChannelEvent] = []

        async def emit(event: ChannelEvent) -> None:
            self.received.append(event)

        self.channel._emit = emit
        self.inbound = TelegramInboundNormalizer(self.channel)
        self.channel._inbound = self.inbound

    async def test_private_text_is_received(self) -> None:
        message = _message(text="hello")
        await self.channel._on_update(Update(1, message=message), None)

        self.assertEqual(len(self.received), 1)
        self.assertEqual(
            self.received[0].model_dump(),
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
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "private"},
                "received_at": AnyString(),
            },
        )
        self.assertEqual(await self.channel.chat_kind("456"), ChatKind.PRIVATE)

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
            [event.model_dump() for event in self.received],
            [
                {
                    "channel_id": "telegram-1",
                    "channel_user_id": "456",
                    "channel_user_name": "Alice",
                    "chat_id": "-100",
                    "chat_name": "Test Group",
                    "channel_message_id": "2",
                    "content": [
                        {
                            "type": "text",
                            "text": "hello",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {"chat_type": "supergroup"},
                    "received_at": AnyString(),
                },
                {
                    "channel_id": "telegram-1",
                    "channel_user_id": "456",
                    "channel_user_name": "Alice",
                    "chat_id": "-100",
                    "chat_name": "Test Group",
                    "channel_message_id": "3",
                    "content": [
                        {
                            "type": "text",
                            "text": "follow up",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {"chat_type": "supergroup"},
                    "received_at": AnyString(),
                },
            ],
        )
        self.assertEqual(await self.channel.chat_kind("-100"), ChatKind.GROUP)

    async def test_group_filter_can_be_disabled(self) -> None:
        channel = _channel(only_at_reply=False)
        received: list[ChannelEvent] = []

        async def emit(event: ChannelEvent) -> None:
            received.append(event)

        channel._emit = emit
        message = _message(chat_type="group", text="hello all")
        await channel._on_update(Update(1, message=message), None)
        self.assertEqual(len(received), 1)
        self.assertEqual(
            received[0].model_dump(),
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
                        "text": "hello all",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "group"},
                "received_at": AnyString(),
            },
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
        event = await self.inbound.normalise_messages([message])
        assert event is not None
        self.assertEqual(
            event.model_dump(),
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
                        "text": "describe this",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "group"},
                "received_at": AnyString(),
            },
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

        self.assertFalse(self.inbound.gated_out(message))
        event = await self.inbound.normalise_messages([message])
        assert event is not None
        self.assertEqual(
            event.model_dump(),
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
                        "text": "/help topic",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "group"},
                "received_at": AnyString(),
            },
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
                selected = self.inbound.select_media(
                    _media_message(**{attr: item}),
                )
                self.assertEqual(
                    selected,
                    (
                        item[-1] if isinstance(item, list) else item,
                        expected_mime,
                        expected_name,
                    ),
                )

        smaller = media(file_id="small-photo")
        largest = media(file_id="large-photo")
        selected = self.inbound.select_media(
            _media_message(photo=[smaller, largest]),
        )
        self.assertEqual(selected, (largest, "image/jpeg", "photo.jpg"))

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
        block = await self.inbound.download_media(
            _media_message(document=media),
        )

        self.assertIsInstance(block, DataBlock)
        self.assertEqual(
            block.model_dump(),
            {
                "type": "data",
                "id": AnyString(),
                "source": {
                    "type": "base64",
                    "data": base64.b64encode(b"data").decode("ascii"),
                    "media_type": "application/pdf",
                },
                "name": "report.pdf",
                "created_at": AnyString(),
                "finished_at": None,
            },
        )

    async def test_download_size_is_checked_before_get_file(self) -> None:
        media = SimpleNamespace(
            mime_type="application/octet-stream",
            file_name="huge.bin",
            file_size=_MAX_DOWNLOAD_BYTES + 1,
            get_file=AsyncMock(),
        )
        block = await self.inbound.download_media(
            _media_message(document=media),
        )

        self.assertEqual(
            block.model_dump(),
            {
                "type": "text",
                "text": (
                    "[Telegram attachment omitted: huge.bin exceeds the "
                    "20 MiB Bot API download limit.]"
                ),
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
            },
        )
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
        self.assertEqual(
            self.inbound.structured_text(
                SimpleNamespace(venue=None, location=location, contact=None),
            ),
            "[Telegram location]\nlatitude: 1.5\nlongitude: 2.5",
        )
        self.assertEqual(
            self.inbound.structured_text(
                SimpleNamespace(venue=venue, location=None, contact=None),
            ),
            "[Telegram venue]\n"
            "title: Office\n"
            "address: Main Road\n"
            "latitude: 1.5\n"
            "longitude: 2.5",
        )
        self.assertEqual(
            self.inbound.structured_text(
                SimpleNamespace(venue=None, location=None, contact=contact),
            ),
            "[Telegram contact]\n"
            "name: Alice Doe\n"
            "phone_number: +123\n"
            "user_id: 456",
        )

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
        self.inbound.normalise_messages = AsyncMock(return_value=event)

        with patch(
            "agentscope.app.channel._telegram._inbound._ALBUM_SETTLE_SECS",
            0.01,
        ):
            self.inbound.buffer_album(first)
            self.inbound.buffer_album(second)
            await asyncio.sleep(0.03)

        self.inbound.normalise_messages.assert_awaited_once_with(
            [first, second],
        )
        self.assertEqual(self.received, [event])

    async def test_album_tasks_are_cancelled_on_shutdown(self) -> None:
        message = _message(media_group_id="album-2")
        with patch(
            "agentscope.app.channel._telegram._inbound._ALBUM_SETTLE_SECS",
            60,
        ):
            self.inbound.buffer_album(message)
            await self.channel._cancel_albums()
        self.assertEqual(self.inbound.album_tasks, {})
        self.assertEqual(self.inbound.album_messages, {})

    async def test_album_download_is_cancelled_and_close_is_idempotent(
        self,
    ) -> None:
        started = asyncio.Event()

        async def blocked_download(_message: Any) -> None:
            started.set()
            await asyncio.Future()

        self.inbound.download_media = AsyncMock(side_effect=blocked_download)
        with patch(
            "agentscope.app.channel._telegram._inbound._ALBUM_SETTLE_SECS",
            0.01,
        ):
            self.inbound.buffer_album(
                _message(media_group_id="album-downloading"),
            )
            await asyncio.wait_for(started.wait(), timeout=1.0)
            await self.inbound.aclose()
            await self.inbound.aclose()

        self.assertEqual(self.inbound.album_tasks, {})
        self.assertEqual(self.inbound.album_messages, {})
        self.inbound.buffer_album(
            _message(media_group_id="album-after-close"),
        )
        self.assertEqual(self.inbound.album_tasks, {})

    async def test_album_caption_is_included_once(self) -> None:
        first = _message(caption="one", media_group_id="album")
        second = _message(
            caption="two",
            media_group_id="album",
            message_id=2,
        )
        event = await self.inbound.normalise_messages([first, second])
        assert event is not None
        self.assertEqual(
            event.model_dump(),
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
                        "text": "one",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {"chat_type": "private"},
                "received_at": AnyString(),
            },
        )


class TelegramOutboundTest(IsolatedAsyncioTestCase):
    """Exercise streaming replies, limits, and approval callbacks."""

    async def asyncSetUp(self) -> None:
        self.channel = _channel()
        self.bot = SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=99),
            ),
            send_message_draft=AsyncMock(return_value=True),
            edit_message_text=AsyncMock(return_value=True),
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
            get_chat=AsyncMock(
                side_effect=lambda chat_id: Chat(
                    id=chat_id,
                    type="private" if chat_id == 456 else "supergroup",
                    first_name="Alice" if chat_id == 456 else None,
                    title="Test Group" if chat_id != 456 else None,
                ),
            ),
        )
        self.channel._application = SimpleNamespace(bot=self.bot)
        self.bus = InMemoryMessageBus()
        self.channel.bind_message_bus(self.bus)

    async def test_text_is_sent_non_streaming_and_split(self) -> None:
        result = await self.channel.send_message_to("-100", "x" * 4097)
        self.assertEqual(result, _TelegramResult(True))
        self.assertEqual(
            self.bot.send_message.await_args_list,
            [
                call(chat_id=-100, text="x" * 4096),
                call(chat_id=-100, text="x"),
            ],
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

        self.assertEqual(image, _TelegramResult(True))
        self.assertEqual(file_result, _TelegramResult(True))
        self.assertEqual(
            big_image,
            _TelegramResult(
                False,
                "image exceeds Telegram's 10 MiB photo limit; use SendFile",
            ),
        )
        self.assertEqual(
            big_file,
            _TelegramResult(False, "file exceeds Telegram's 50 MiB limit"),
        )
        self.assertEqual(
            [
                call(
                    *item.args,
                    **{
                        **item.kwargs,
                        "photo": item.kwargs["photo"].getvalue(),
                    },
                )
                for item in self.bot.send_photo.await_args_list
            ],
            [call(chat_id=1, photo=b"image", filename="a.png")],
        )
        self.assertEqual(
            [
                call(
                    *item.args,
                    **{
                        **item.kwargs,
                        "document": item.kwargs["document"].getvalue(),
                    },
                )
                for item in self.bot.send_document.await_args_list
            ],
            [call(chat_id=1, document=b"file", filename="a.bin")],
        )

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

        draft_id = self.bot.send_message_draft.await_args.kwargs["draft_id"]
        self.assertIsInstance(draft_id, int)
        self.assertNotEqual(draft_id, 0)
        self.bot.send_message_draft.assert_awaited_once_with(
            chat_id=456,
            draft_id=AnyValue(),
            text="done",
            parse_mode="HTML",
        )
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
            side_effect=[float(value * 4) for value in range(100)],
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

        self.assertEqual(
            self.bot.send_message_draft.await_args_list,
            [
                call(chat_id=456, draft_id=7, text="a", parse_mode="HTML"),
                call(chat_id=456, draft_id=7, text="abc", parse_mode="HTML"),
            ],
        )
        self.assertEqual(
            preview,
            _StreamPreview(
                mode="draft",
                draft_id=7,
                last_update=2.1,
                last_html="abc",
            ),
        )

    async def test_group_stream_updates_use_three_second_cadence(self) -> None:
        preview = _StreamPreview(
            mode="edit",
            draft_id=7,
            last_update=10.0,
        )
        with patch(
            "agentscope.app.channel._telegram._channel.time.monotonic",
            side_effect=[13.0, 13.2],
        ):
            await self.channel._update_stream_preview("-100", preview, "early")
            await self.channel._update_stream_preview("-100", preview, "ready")

        self.bot.send_message.assert_awaited_once_with(
            chat_id=-100,
            text="ready",
            parse_mode="HTML",
        )

    async def test_throttled_preview_does_not_render_markdown(self) -> None:
        preview = _StreamPreview(
            mode="draft",
            draft_id=7,
            last_update=10.0,
        )
        with (
            patch(
                "agentscope.app.channel._telegram._channel.time.monotonic",
                return_value=10.1,
            ),
            patch.object(self.channel, "_formatted_chunks") as formatted,
        ):
            await self.channel._update_stream_preview(
                "456",
                preview,
                "new text",
            )
        formatted.assert_not_called()

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
        self.assertEqual(
            preview,
            _StreamPreview(
                mode="edit",
                draft_id=7,
                message_id=99,
                last_update=AnyValue(),
                last_html="x" * 4096,
            ),
        )

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

        self.bot.send_message_draft.assert_awaited_once_with(
            chat_id=456,
            draft_id=AnyValue(),
            text="done",
            parse_mode="HTML",
        )
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

        self.assertEqual(result, _TelegramResult(True))
        self.assertEqual(
            self.bot.send_message.await_args_list,
            [
                call(chat_id=456, text="<b>bold</b>", parse_mode="HTML"),
                call(chat_id=456, text="bold"),
            ],
        )

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

        self.bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100,
            message_id=99,
            text=f"<b>{'x' * 4096}</b>",
            parse_mode="HTML",
        )
        self.bot.send_message.assert_awaited_once_with(
            chat_id=-100,
            text="<b>x</b>",
            parse_mode="HTML",
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

        self.channel._send_formatted_chunk.assert_awaited_once_with(
            "-100",
            _TelegramTextChunk(
                html="x" * 4096,
                plain="x" * 4096,
            ),
            _RetryBudget(deadline=AnyValue()),
            preview,
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
        self.assertEqual(
            [
                call(
                    *item.args,
                    **{
                        **item.kwargs,
                        "document": item.kwargs["document"].getvalue(),
                    },
                )
                for item in self.bot.send_document.await_args_list
            ],
            [call(chat_id=1, document=raw, filename="large.png")],
        )

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

        def record_text(*args: Any, **kwargs: Any) -> _TelegramResult:
            del args, kwargs
            order.append("text")
            return _TelegramResult(True)

        def record_attachment(*args: Any, **kwargs: Any) -> _TelegramResult:
            del args, kwargs
            order.append("attachment")
            return _TelegramResult(True)

        def record_approval(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
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
        self.channel._send_formatted_chunk.assert_awaited_once_with(
            "456",
            _TelegramTextChunk(html="answer", plain="answer"),
            _RetryBudget(deadline=AnyValue()),
            _StreamPreview(mode="draft", draft_id=AnyValue()),
        )
        self.channel.send_image_to.assert_awaited_once_with(
            "456",
            b"image",
            "image.png",
            budget=_RetryBudget(deadline=AnyValue()),
            pace=_StreamPreview(mode="draft", draft_id=AnyValue()),
        )
        self.channel._present_confirm.assert_awaited_once_with(
            event,
            confirmation,
            _StreamPreview(mode="draft", draft_id=AnyValue()),
        )

    async def test_approval_buttons_share_one_callback_payload(self) -> None:
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
        with (
            patch(
                "agentscope.app.channel._telegram._channel."
                "secrets.token_urlsafe",
                return_value="approval-token",
            ),
            patch.object(
                self.bus,
                "registry_set",
                wraps=self.bus.registry_set,
            ) as registry_set,
        ):
            await self.channel._present_confirm(event, request)
        self.assertEqual(
            [
                call(
                    *item.args,
                    **{
                        **item.kwargs,
                        "reply_markup": item.kwargs["reply_markup"].to_dict(),
                    },
                )
                for item in self.bot.send_message.await_args_list
            ],
            [
                call(
                    chat_id=-100,
                    text=(
                        "🛡️ Tool execution requires approval\n"
                        "Tool: SendMessage\n"
                        'Arguments: {"chat_id":"1","text":"hello"}'
                    ),
                    reply_markup={
                        "inline_keyboard": [
                            [
                                {
                                    "text": "✅ Allow",
                                    "callback_data": "as:a:approval-token",
                                },
                                {
                                    "text": "❌ Deny",
                                    "callback_data": "as:d:approval-token",
                                },
                            ],
                        ],
                    },
                ),
            ],
        )
        registry_set.assert_awaited_once_with(
            "agentscope:channel:approval:telegram-1:approval-token",
            "payload",
            json.dumps(
                {
                    "tool_call_id": "tool-1",
                    "chat_id": "-100",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "submitted": False,
                    "approved": None,
                },
                separators=(",", ":"),
            ),
            ttl_secs=86400,
        )
        self.assertEqual(
            await self.channel._load_approval_callback("as:a:approval-token"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
                "approval-token",
                True,
            ),
        )
        self.assertEqual(
            await self.channel._load_approval_callback("as:d:approval-token"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
                "approval-token",
                False,
            ),
        )

    async def test_callback_submit_and_failure_paths(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.channel._emit = AsyncMock(side_effect=RuntimeError("offline"))
        failed = _callback_query(f"as:d:{token}")
        await self.channel._on_callback(
            SimpleNamespace(callback_query=failed),
            None,
        )
        self.assertEqual(
            await self.channel._load_approval_callback(f"as:d:{token}"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
                token,
                False,
            ),
        )
        failed.answer.assert_awaited_once_with(
            "Could not confirm submission. Please retry.",
            show_alert=True,
        )

        self.channel._emit = AsyncMock()
        succeeded = _callback_query(f"as:a:{token}")
        await self.channel._on_callback(
            SimpleNamespace(callback_query=succeeded),
            None,
        )
        self.channel._emit.assert_awaited_once_with(
            ChannelConfirmationResultEvent(
                channel_id="telegram-1",
                chat_id="-100",
                channel_user_id="456",
                agent_id="agent-1",
                session_id="session-1",
                tool_call_id="tool-1",
                approved=True,
                actor="456",
            ),
        )
        succeeded.answer.assert_awaited_once_with("Decision submitted.")
        succeeded.edit_message_text.assert_awaited_once_with(
            "✅ Approval submitted",
        )

    async def test_callback_reads_payload_once_inside_token_lock(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.assertLessEqual(len(f"as:a:{token}".encode("utf-8")), 64)
        self.channel._emit = AsyncMock()
        with patch.object(
            self.bus,
            "registry_get",
            wraps=self.bus.registry_get,
        ) as registry_get:
            await self.channel._on_callback(
                SimpleNamespace(
                    callback_query=_callback_query(f"as:a:{token}"),
                ),
                None,
            )

        registry_get.assert_awaited_once_with(
            f"agentscope:channel:approval:telegram-1:{token}",
            "payload",
        )

    async def test_wrong_chat_does_not_consume_callback(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.channel._emit = AsyncMock()
        query = _callback_query(f"as:a:{token}", chat_id=-200)
        await self.channel._on_callback(
            SimpleNamespace(callback_query=query),
            None,
        )
        self.channel._emit.assert_not_awaited()
        self.assertEqual(
            await self.channel._load_approval_callback(f"as:a:{token}"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
                token,
                True,
            ),
        )

    async def test_bot_operator_does_not_consume_callback(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.channel._emit = AsyncMock()
        query = _callback_query(f"as:a:{token}", from_user=_BOT)

        await self.channel._on_callback(
            SimpleNamespace(callback_query=query),
            None,
        )

        self.channel._emit.assert_not_awaited()

        self.assertEqual(
            await self.channel._load_approval_callback(f"as:a:{token}"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
                token,
                True,
            ),
        )

    async def test_callback_emits_when_ui_operations_fail(self) -> None:
        for failed_ui in ("answer", "edit_message_text"):
            with self.subTest(failed_ui=failed_ui):
                self.channel._emit = AsyncMock()
                token = await self.channel._store_approval_callback(
                    _ApprovalCallback(
                        tool_call_id=f"tool-{failed_ui}",
                        chat_id="-100",
                        agent_id="agent-1",
                        session_id="session-1",
                    ),
                )
                query = _callback_query(f"as:a:{token}")
                getattr(query, failed_ui).side_effect = RuntimeError("offline")

                await self.channel._on_callback(
                    SimpleNamespace(callback_query=query),
                    None,
                )

                self.channel._emit.assert_awaited_once_with(
                    ChannelConfirmationResultEvent(
                        channel_id="telegram-1",
                        chat_id="-100",
                        channel_user_id="456",
                        agent_id="agent-1",
                        session_id="session-1",
                        tool_call_id=f"tool-{failed_ui}",
                        approved=True,
                        actor="456",
                    ),
                )
                query.answer.assert_awaited_once_with("Decision submitted.")
                query.edit_message_text.assert_awaited_once_with(
                    "✅ Approval submitted",
                )
                self.assertEqual(
                    await self.channel._load_approval_callback(
                        f"as:a:{token}",
                    ),
                    (None, token, True),
                )

    async def test_concurrent_decisions_submit_once(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.channel._emit = AsyncMock()
        await asyncio.gather(
            self.channel._on_callback(
                SimpleNamespace(
                    callback_query=_callback_query(f"as:a:{token}"),
                ),
                None,
            ),
            self.channel._on_callback(
                SimpleNamespace(
                    callback_query=_callback_query(f"as:d:{token}"),
                ),
                None,
            ),
        )
        # Either concurrent decision may win, but all other fields and the
        # single emission are deterministic.
        approved = self.channel._emit.await_args.args[0].approved
        self.assertIsInstance(approved, bool)
        self.channel._emit.assert_awaited_once_with(
            ChannelConfirmationResultEvent(
                channel_id="telegram-1",
                chat_id="-100",
                channel_user_id="456",
                agent_id="agent-1",
                session_id="session-1",
                tool_call_id="tool-1",
                approved=approved,
                actor="456",
            ),
        )

    async def test_delete_failure_leaves_terminal_receipt(self) -> None:
        token = await self.channel._store_approval_callback(
            _ApprovalCallback(
                tool_call_id="tool-1",
                chat_id="-100",
                agent_id="agent-1",
                session_id="session-1",
            ),
        )
        self.channel._emit = AsyncMock()
        self.channel._delete_approval_callback = AsyncMock(
            side_effect=RuntimeError("delete failed"),
        )
        await self.channel._on_callback(
            SimpleNamespace(
                callback_query=_callback_query(f"as:a:{token}"),
            ),
            None,
        )
        self.assertEqual(
            await self.channel._load_approval_callback(f"as:d:{token}"),
            (
                _ApprovalCallback(
                    tool_call_id="tool-1",
                    chat_id="-100",
                    agent_id="agent-1",
                    session_id="session-1",
                    submitted=True,
                    approved=True,
                ),
                token,
                False,
            ),
        )

        retry = _callback_query(f"as:d:{token}")
        await self.channel._on_callback(
            SimpleNamespace(callback_query=retry),
            None,
        )
        self.channel._emit.assert_awaited_once_with(
            ChannelConfirmationResultEvent(
                channel_id="telegram-1",
                chat_id="-100",
                channel_user_id="456",
                agent_id="agent-1",
                session_id="session-1",
                tool_call_id="tool-1",
                approved=True,
                actor="456",
            ),
        )
        retry.edit_message_text.assert_awaited_once_with(
            "✅ Approval submitted",
        )

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
        bot.initialize.assert_awaited_once_with()
        await channel.aclose()
        bot.shutdown.assert_awaited_once_with()

    async def test_chat_metadata_cache_and_empty_chat_listing(self) -> None:
        chat = Chat(id=-100, type="supergroup", title="Team")
        self.bot.get_chat.side_effect = None
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
        waits = [item.args[0] for item in sleep.await_args_list]
        self.assertAlmostEqual(waits[0], retry_delays[0], delta=0.1)
        self.assertEqual(
            sleep.await_args_list,
            [call(AnyValue()), *[call(delay) for delay in retry_delays[1:]]],
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
            self.assertEqual(
                decision,
                PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=(
                        "Sending content to a Telegram chat requires approval."
                    ),
                ),
            )

        message_result = await tools[0](chat_id="1", text="hello")
        file_result = await tools[1](chat_id="1", path="/workspace/a.bin")
        image_result = await tools[2](chat_id="1", path="/workspace/a.png")
        for result, expected_text in (
            (message_result, "Sent message to 1."),
            (file_result, "Sent file a.bin to 1."),
            (image_result, "Sent image a.png to 1."),
        ):
            self.assertEqual(
                result.model_dump(),
                {
                    "content": [
                        {
                            "type": "text",
                            "text": expected_text,
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": ToolResultState.SUCCESS,
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
            )
        self.assertEqual(
            backend.read_file.await_args_list,
            [call("/workspace/a.bin"), call("/workspace/a.png")],
        )
        channel.send_message_to.assert_awaited_once_with("1", "hello")
        channel.send_file_to.assert_awaited_once_with(
            "1",
            b"data",
            "a.bin",
        )
        channel.send_image_to.assert_awaited_once_with(
            "1",
            b"data",
            "a.png",
        )

    async def test_workspace_read_failure_is_structured(self) -> None:
        channel = _channel()
        backend = SimpleNamespace(
            read_file=AsyncMock(side_effect=FileNotFoundError("missing")),
        )
        workspace = SimpleNamespace(get_backend=lambda: backend)
        tools = await channel.list_tools(workspace)
        result = await tools[1](chat_id="1", path="/workspace/nope")
        self.assertEqual(
            result.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "SendFile could not read '/workspace/nope': "
                            "missing"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": ToolResultState.ERROR,
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

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
            sleep.await_args_list,
            [call(1), call(2), call(0.01)],
        )
        self.assertEqual(network_operation.await_args_list, [call()] * 3)
        self.assertEqual(retry_after_operation.await_args_list, [call()] * 2)

    async def test_retry_after_cannot_cross_operation_budget(self) -> None:
        channel = _channel()
        operation = AsyncMock(
            side_effect=RetryAfter(timedelta(seconds=400)),
        )
        with patch("asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(_TelegramRetryBudgetExceeded):
                await channel._retry_api(operation)
        sleep.assert_not_awaited()
        operation.assert_awaited_once_with()

    async def test_retry_after_delays_share_one_operation_budget(self) -> None:
        channel = _channel()
        operation = AsyncMock(
            side_effect=[
                RetryAfter(timedelta(seconds=200)),
                RetryAfter(timedelta(seconds=150)),
            ],
        )
        clock = 0.0

        def monotonic() -> float:
            return clock

        async def advance_clock(seconds: float) -> None:
            nonlocal clock
            clock += seconds

        budget = _RetryBudget(deadline=300.0)
        with (
            patch(
                "agentscope.app.channel._telegram._channel.time.monotonic",
                side_effect=monotonic,
            ),
            patch("asyncio.sleep", side_effect=advance_clock) as sleep,
        ):
            with self.assertRaises(_TelegramRetryBudgetExceeded):
                await channel._retry_api(operation, budget=budget)

        self.assertEqual(operation.await_args_list, [call(), call()])
        sleep.assert_awaited_once_with(200.0)

    async def test_side_effect_timeout_is_not_retried(self) -> None:
        channel = _channel()
        operation = AsyncMock(side_effect=TimedOut("read timed out"))

        with self.assertRaises(_TelegramDeliveryUnknown):
            await channel._retry_api(operation, side_effecting=True)

        operation.assert_awaited_once_with()

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
        bad_request.assert_awaited_once_with()
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
            with self.assertRaises(_TelegramDeliveryUnknown):
                await channel._retry_api(network_operation)
        self.assertEqual(
            network_operation.await_args_list,
            [call()] * _MAX_API_ATTEMPTS,
        )
        self.assertEqual(
            sleep.await_args_list,
            [call(1), call(2)],
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
        retry_after_operation.assert_awaited_once_with()

    async def test_token_is_redacted_from_platform_errors(self) -> None:
        channel = _channel()
        error = RuntimeError("request failed for 123:secret-token")
        self.assertEqual(
            channel._safe_error(error),
            "request failed for <redacted>",
        )
