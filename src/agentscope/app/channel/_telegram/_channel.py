# -*- coding: utf-8 -*-
"""Telegram channel implemented with python-telegram-bot long polling.

The PTB application is embedded in AgentScope's asyncio lifecycle.  It never
uses ``run_polling`` or owns the process event loop.  Platform updates are
normalised into channel events; AgentScope remains responsible for routing,
sessions, persistence, and permission decisions.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
import io
import json
import re
import secrets
import time
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    TYPE_CHECKING,
    TypeVar,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...._logging import logger
from ....event import ReplyEndEvent, RequireUserConfirmEvent
from ....message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from ...message_bus import MessageBusKeys
from .._base import (
    ChannelBase,
    ChannelCapability,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelStatus,
    ChatKind,
    _EVENT_ADAPTER,
)

if TYPE_CHECKING:
    from telegram import Message, Update
    from telegram.ext import Application, CallbackContext

    from ...message_bus import MessageBus
    from ....tool import ToolBase
    from ....workspace import WorkspaceBase
    from ._markdown import _TelegramTextChunk


_POLL_TIMEOUT_SECS = 30
_POLL_READ_TIMEOUT_SECS = 40
_ALBUM_SETTLE_SECS = 0.8
_STREAM_MIN_INTERVAL_SECS = 1.0
_MAX_CONNECT_ATTEMPTS = 2
_MAX_API_ATTEMPTS = 3
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_MAX_TEXT_LENGTH = 4096
_APPROVAL_CALLBACK_PREFIX = "as:approval:"
_APPROVAL_CALLBACK_TTL_SECS = 24 * 60 * 60

_T = TypeVar("_T")


class _PermanentTelegramError(RuntimeError):
    """A configuration error that requires editing the channel."""


@dataclass(frozen=True)
class _ApprovalCallback:
    """Approval data stored in the shared callback registry."""

    tool_call_id: str
    chat_id: str
    agent_id: str
    session_id: str
    approved: bool

    def to_json(self) -> str:
        """Serialize the callback payload for the shared message bus."""
        return json.dumps(
            {
                "tool_call_id": self.tool_call_id,
                "chat_id": self.chat_id,
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "approved": self.approved,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "_ApprovalCallback | None":
        """Return a validated callback payload, or ``None`` if malformed."""
        try:
            raw = json.loads(value)
        except (TypeError, ValueError):
            return None
        if not isinstance(raw, dict) or not isinstance(
            raw.get("approved"),
            bool,
        ):
            return None
        fields = ("tool_call_id", "chat_id", "agent_id", "session_id")
        if any(not isinstance(raw.get(field), str) for field in fields):
            return None
        if not raw["tool_call_id"] or not raw["chat_id"]:
            return None
        return cls(
            tool_call_id=raw["tool_call_id"],
            chat_id=raw["chat_id"],
            agent_id=raw["agent_id"],
            session_id=raw["session_id"],
            approved=raw["approved"],
        )


@dataclass(frozen=True)
class _TelegramResult:
    """Sanitised result returned to Agent-callable delivery tools."""

    ok: bool
    error: str = ""


@dataclass
class _StreamPreview:
    """Mutable state for one best-effort streamed reply preview."""

    mode: str
    draft_id: int
    message_id: int | None = None
    last_update: float | None = None
    last_html: str = ""
    disabled: bool = False


class TelegramChannel(ChannelBase):
    """Telegram Bot API channel using a single long-polling consumer."""

    channel_type = "telegram"
    display_name = "Telegram"
    description = "Telegram bot for private chats and groups."
    icon_url = "https://www.google.com/s2/favicons?domain=telegram.org&sz=128"
    platform_bot_id_field = "bot_id"

    class Credentials(BaseModel):
        """Telegram bot identity and secret token."""

        model_config = ConfigDict(hide_input_in_errors=True)

        bot_id: str = Field(
            title="Bot ID",
            description="Numeric Telegram bot ID returned by getMe.",
        )
        bot_token: str = Field(
            title="Bot Token",
            description="Telegram Bot API token issued by BotFather.",
            repr=False,
            json_schema_extra={"format": "password"},
        )

        @field_validator("bot_id")
        @classmethod
        def _validate_bot_id(cls, value: str) -> str:
            value = value.strip()
            if not value.isdigit() or int(value) <= 0:
                raise ValueError("bot_id must be a positive numeric ID")
            return value

        @field_validator("bot_token")
        @classmethod
        def _validate_bot_token(cls, value: str) -> str:
            value = value.strip()
            prefix, separator, secret = value.partition(":")
            if (
                not separator
                or not prefix.isdigit()
                or int(prefix) <= 0
                or not secret
                or any(
                    character not in "abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                    for character in secret
                )
            ):
                raise ValueError("bot_token has an invalid Telegram format")
            return value

    class Config(BaseModel):
        """Telegram platform behavior."""

        only_at_reply: bool = Field(
            default=True,
            title="Reply only when mentioned",
            description=(
                "In groups, reply only when mentioned or when a user "
                "replies to the bot"
            ),
        )
        show_tool_process: bool = Field(
            default=False,
            title="Show tool process",
            description="Show tool calls and results inline in the reply",
        )
        show_thinking: bool = Field(
            default=False,
            title="Show thinking",
            description="Show model reasoning inline in the reply",
        )
        allow_public_private_chats: bool = Field(
            default=False,
            title="Allow public private chats",
            description=(
                "Allow any Telegram user to start a private chat with this "
                "bot"
            ),
        )
        allowed_private_user_ids: str = Field(
            default="",
            title="Allowed private user IDs",
            description=(
                "Comma-, whitespace-, or newline-separated Telegram user "
                "IDs allowed to use private chats"
            ),
        )

        @field_validator("allowed_private_user_ids")
        @classmethod
        def _validate_allowed_private_user_ids(cls, value: str) -> str:
            tokens = [
                token for token in re.split(r"[,\s]+", value.strip()) if token
            ]
            ids: set[int] = set()
            for token in tokens:
                if re.fullmatch(r"[0-9]+", token) is None or int(token) <= 0:
                    raise ValueError(
                        "allowed_private_user_ids must contain only "
                        "positive numeric Telegram user IDs",
                    )
                ids.add(int(token))
            return ",".join(str(user_id) for user_id in sorted(ids))

    capabilities = ChannelCapability(
        text=True,
        markdown=True,
        image=True,
        file=True,
        interactive=True,
        streaming=True,
        max_message_length=_MAX_TEXT_LENGTH,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "TelegramChannel.Credentials",
        config: "TelegramChannel.Config",
    ) -> None:
        self._channel_id = channel_id
        self._bot_id = credentials.bot_id.strip()
        self._bot_token = credentials.bot_token
        self._config = config
        self._allowed_private_user_ids = frozenset(
            int(user_id)
            for user_id in config.allowed_private_user_ids.split(",")
            if user_id
        )
        self.status = ChannelStatus()
        self._application: "Application | None" = None
        self._rest_bot: Any = None
        self._rest_bot_lock = asyncio.Lock()
        self._message_bus: "MessageBus | None" = None
        self._bot_user: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fatal_event: asyncio.Event | None = None
        self._fatal_error: BaseException | None = None
        self._user_name_cache: dict[str, str] = {}
        self._chat_name_cache: dict[str, str] = {}
        self._chat_kind_cache: dict[str, ChatKind] = {}
        self._album_messages: dict[tuple[str, str, str], list["Message"]] = {}
        self._album_tasks: dict[tuple[str, str, str], asyncio.Task] = {}

    @property
    def channel_id(self) -> str:
        """The unique channel instance ID."""
        return self._channel_id

    def bind_message_bus(self, message_bus: "MessageBus") -> None:
        """Bind shared state used by approval callbacks across processes."""
        self._message_bus = message_bus

    async def aclose(self) -> None:
        """Close the lazy REST bot owned by a connection-free client."""
        async with self._rest_bot_lock:
            bot, self._rest_bot = self._rest_bot, None
        if bot is not None:
            await bot.shutdown()

    # -- Lifecycle -----------------------------------------------------

    async def start_listening(
        self,
        emit: Callable[
            [ChannelEvent | ChannelConfirmationResultEvent],
            Awaitable[None],
        ],
    ) -> None:
        """Run one PTB application under AgentScope's asyncio lifecycle."""
        self._emit = emit
        self._loop = asyncio.get_running_loop()
        self.status.state = "connecting"
        attempts = 0
        backoff = 1.0
        try:
            while True:
                self._fatal_event = asyncio.Event()
                self._fatal_error = None
                try:
                    await self._run_application()
                    if self._fatal_error is not None:
                        raise _PermanentTelegramError(
                            self._safe_error(self._fatal_error),
                        )
                    raise RuntimeError("Telegram polling stopped unexpectedly")
                except (ImportError, _PermanentTelegramError) as error:
                    self.status.state = "failed"
                    self.status.last_error = self._safe_error(error)
                    logger.error(
                        "Telegram channel '%s' stopped: %s",
                        self._channel_id,
                        self.status.last_error,
                    )
                    while True:
                        await asyncio.sleep(30.0)
                except Exception as error:  # pylint: disable=broad-except
                    attempts += 1
                    self.status.state = "retrying"
                    self.status.last_error = self._safe_error(error)
                    logger.warning(
                        "Telegram channel '%s' failed to connect (%d/%d): %s",
                        self._channel_id,
                        attempts,
                        _MAX_CONNECT_ATTEMPTS,
                        self.status.last_error,
                    )
                    if attempts >= _MAX_CONNECT_ATTEMPTS:
                        self.status.state = "failed"
                        while True:
                            await asyncio.sleep(30.0)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            await self._cancel_albums()
            self.status.state = "stopped"

    async def _run_application(self) -> None:
        """Build, initialise, poll, and always shut down one PTB app."""
        application = self._build_application()
        from telegram.error import (
            BadRequest,
            Conflict,
            Forbidden,
            InvalidToken,
        )

        self._application = application
        initialized = False
        try:
            await application.initialize()
            initialized = True
            me = await self._retry_api(application.bot.get_me)
            actual_id = str(me.id)
            if actual_id != self._bot_id:
                raise _PermanentTelegramError(
                    f"Configured bot_id {self._bot_id!r} does not match "
                    f"Telegram bot ID {actual_id!r}.",
                )
            self._bot_user = me

            webhook = await self._retry_api(application.bot.get_webhook_info)
            if getattr(webhook, "url", ""):
                raise _PermanentTelegramError(
                    "This bot has an active webhook. Remove it before "
                    "starting AgentScope long polling.",
                )

            updater = application.updater
            if updater is None:
                raise _PermanentTelegramError(
                    "The Telegram application has no polling updater.",
                )
            from telegram.constants import UpdateType

            await updater.start_polling(
                timeout=_POLL_TIMEOUT_SECS,
                bootstrap_retries=0,
                allowed_updates=[
                    UpdateType.MESSAGE,
                    UpdateType.CALLBACK_QUERY,
                ],
                drop_pending_updates=False,
                error_callback=self._on_polling_error,
            )
            await application.start()
            self.status.state = "connected"
            self.status.last_error = ""
            assert self._fatal_event is not None
            await self._fatal_event.wait()
        except InvalidToken as error:
            raise _PermanentTelegramError(
                "Telegram rejected the configured bot token.",
            ) from error
        except Conflict as error:
            raise _PermanentTelegramError(
                "Another instance is already polling updates for this bot.",
            ) from error
        except (BadRequest, Forbidden) as error:
            raise _PermanentTelegramError(self._safe_error(error)) from error
        finally:
            await self._cancel_albums()
            updater = application.updater
            try:
                if updater is not None and updater.running:
                    await updater.stop()
            finally:
                try:
                    if application.running:
                        await application.stop()
                finally:
                    try:
                        if initialized:
                            await application.shutdown()
                        else:
                            # Bot.initialize opens both HTTPXRequest instances
                            # before getMe validates the token. Application
                            # shutdown is a no-op when initialization fails,
                            # so close the Bot explicitly.
                            await application.bot.shutdown()
                    finally:
                        self._application = None

    def _build_application(self) -> "Application":
        """Create the PTB application without importing PTB at module load."""
        try:
            import markdown_it
            from telegram.ext import (
                ApplicationBuilder,
                CallbackQueryHandler,
                filters,
                MessageHandler,
            )
        except ImportError as error:
            raise ImportError(
                "TelegramChannel requires 'agentscope[channel]' or both "
                "'python-telegram-bot[callback-data]>=22.8,<23.0' and "
                "'markdown-it-py>=4,<5'.",
            ) from error
        del markdown_it

        api_request = self._new_api_request()
        from telegram.request import HTTPXRequest

        polling_request = HTTPXRequest(
            connection_pool_size=1,
            connect_timeout=10.0,
            read_timeout=_POLL_READ_TIMEOUT_SECS,
            write_timeout=10.0,
            pool_timeout=5.0,
        )
        application = (
            ApplicationBuilder()
            .token(self._bot_token)
            .request(api_request)
            .get_updates_request(polling_request)
            .build()
        )
        message_filter = (
            filters.TEXT
            | filters.PHOTO
            | filters.Document.ALL
            | filters.AUDIO
            | filters.VOICE
            | filters.VIDEO
            | filters.ANIMATION
            | filters.VIDEO_NOTE
            | filters.Sticker.ALL
            | filters.LOCATION
            | filters.VENUE
            | filters.CONTACT
        ) & ~filters.StatusUpdate.ALL
        application.add_handler(
            MessageHandler(message_filter, self._on_update),
        )
        application.add_handler(CallbackQueryHandler(self._on_callback))
        return application

    @staticmethod
    def _new_api_request() -> Any:
        """Build the shared request settings for outbound Bot API calls."""
        from telegram.request import HTTPXRequest

        return HTTPXRequest(
            connection_pool_size=16,
            connect_timeout=10.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=10.0,
            media_write_timeout=60.0,
        )

    def _new_rest_bot(self) -> Any:
        """Build an unconnected Bot API client for a ChannelClients node."""
        from telegram import Bot

        return Bot(token=self._bot_token, request=self._new_api_request())

    async def _bot(self) -> Any:
        """Return a Bot API client whether or not this instance listens.

        A channel worker owns the polling ``Application``. A process running
        an agent owns only a connection-free ``ChannelClients`` instance, so
        it initializes a short-lived REST-capable bot lazily instead.
        """
        application = self._application
        if application is not None:
            return application.bot
        if self._rest_bot is not None:
            return self._rest_bot
        async with self._rest_bot_lock:
            if self._rest_bot is not None:
                return self._rest_bot
            bot = self._new_rest_bot()
            try:
                await bot.initialize()
                actual_id = str(bot.bot.id)
                if actual_id != self._bot_id:
                    raise _PermanentTelegramError(
                        f"Configured bot_id {self._bot_id!r} does not match "
                        f"Telegram bot ID {actual_id!r}.",
                    )
            except Exception:
                await bot.shutdown()
                raise
            self._rest_bot = bot
            self._bot_user = bot.bot
            return bot

    def _on_polling_error(self, error: BaseException) -> None:
        """Mark competing pollers as fatal; PTB retries transient errors."""
        try:
            from telegram.error import Conflict
        except ImportError:
            return
        self.status.last_error = self._safe_error(error)
        if isinstance(error, Conflict):
            self.status.state = "failed"
            self._fatal_error = error
            if self._fatal_event is not None:
                self._fatal_event.set()
            return
        self.status.state = "retrying"
        if self._loop is not None:
            self._loop.call_later(5.0, self._restore_connected_status)

    def _restore_connected_status(self) -> None:
        application = self._application
        if (
            application is not None
            and application.running
            and self._fatal_error is None
        ):
            self.status.state = "connected"
            self.status.last_error = ""

    # -- Inbound messages ---------------------------------------------

    async def _on_update(
        self,
        update: "Update",
        _context: "CallbackContext",
    ) -> None:
        """Normalise a supported Telegram message and emit it."""
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or user.is_bot:
            return
        try:
            if not self._is_private_user_allowed(message, user):
                await self._notify_unapproved_private_user(message, user)
                return
            self._remember_chat(message.chat)
            if message.media_group_id and self._downloadable(message):
                self._buffer_album(message)
                return
            if self._gated_out(message):
                return
            event = await self._normalise_messages([message])
            if event is not None and self._emit is not None:
                await self._emit(event)
                self.status.state = "connected"
                self.status.last_error = ""
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Telegram channel '%s' failed to process a message",
                self._channel_id,
            )

    def _is_private_user_allowed(self, message: "Message", user: Any) -> bool:
        """Return whether a sender may enter a private chat with the bot."""
        if str(message.chat.type) != "private":
            return True
        if user is None:
            return False
        return (
            self._config.allow_public_private_chats
            or int(user.id) in self._allowed_private_user_ids
        )

    @staticmethod
    def _is_start_command(message: "Message") -> bool:
        """Whether a private message is a plain Telegram ``/start`` command."""
        parts = (message.text or "").split(maxsplit=1)
        return bool(parts) and parts[0].casefold() == "/start"

    async def _notify_unapproved_private_user(
        self,
        message: "Message",
        user: Any,
    ) -> None:
        """Give only ``/start`` senders their id without invoking an agent."""
        if not self._is_start_command(message):
            return
        try:
            bot = await self._bot()
            await self._retry_api(
                lambda: bot.send_message(
                    chat_id=self._target_chat_id(str(message.chat.id)),
                    text=(
                        f"Your Telegram user ID is {user.id}. Ask the "
                        "channel owner to add it to the allowed private "
                        "user IDs."
                    ),
                ),
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.debug(
                "Telegram channel '%s' could not notify an unapproved "
                "private user: %s",
                self._channel_id,
                self._safe_error(error),
            )

    def _buffer_album(self, message: "Message") -> None:
        user_id = str(message.from_user.id) if message.from_user else ""
        key = (str(message.chat_id), user_id, str(message.media_group_id))
        self._album_messages.setdefault(key, []).append(message)
        previous = self._album_tasks.get(key)
        if previous is not None:
            previous.cancel()
        self._album_tasks[key] = asyncio.create_task(
            self._flush_album(key),
            name=f"telegram-album:{message.media_group_id}",
        )

    async def _flush_album(self, key: tuple[str, str, str]) -> None:
        task = asyncio.current_task()
        try:
            await asyncio.sleep(_ALBUM_SETTLE_SECS)
            messages = self._album_messages.pop(key, [])
            first = messages[0] if messages else None
            if (
                first is None
                or not self._is_private_user_allowed(first, first.from_user)
                or all(self._gated_out(msg) for msg in messages)
            ):
                return
            event = await self._normalise_messages(messages)
            if event is not None and self._emit is not None:
                await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Telegram channel '%s' failed to process an album",
                self._channel_id,
            )
        finally:
            if self._album_tasks.get(key) is task:
                self._album_tasks.pop(key, None)

    async def _cancel_albums(self) -> None:
        tasks = list(self._album_tasks.values())
        self._album_tasks.clear()
        self._album_messages.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _normalise_messages(
        self,
        messages: list["Message"],
    ) -> ChannelEvent | None:
        first = messages[0]
        content: list[TextBlock | DataBlock] = []
        for message in messages:
            block = await self._download_media(message)
            if block is not None:
                content.append(block)

        if len(messages) == 1:
            structured = self._structured_text(first)
            if structured:
                content.append(TextBlock(text=structured))

        text = ""
        for message in messages:
            raw = message.text or message.caption or ""
            if raw:
                text = self._strip_bot_mention(message, raw).strip()
                if text:
                    break
        if text:
            content.append(TextBlock(text=text))
        if not content:
            return None

        user = first.from_user
        chat = first.chat
        user_id = str(user.id) if user else ""
        user_name = ""
        if user is not None:
            user_name = self._user_name_cache.get(user_id, "")
            if not user_name:
                user_name = user.full_name or user.username or user_id
                self._user_name_cache[user_id] = user_name
        chat_id = str(chat.id)
        return ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=user_id,
            channel_user_name=user_name,
            chat_id=chat_id,
            chat_name=self._chat_display_name(chat),
            channel_message_id=str(first.message_id),
            content=content,
            metadata={"chat_type": str(chat.type)},
        )

    def _gated_out(self, message: "Message") -> bool:
        chat_type = str(message.chat.type)
        if chat_type not in ("group", "supergroup"):
            return False
        if not self._config.only_at_reply:
            return False
        reply = message.reply_to_message
        if (
            reply is not None
            and reply.from_user is not None
            and str(reply.from_user.id) == self._bot_id
        ):
            return False
        return not self._mentions_bot(message)

    def _mentions_bot(self, message: "Message") -> bool:
        username = str(getattr(self._bot_user, "username", "") or "")
        for entity, value in self._parsed_entities(message).items():
            entity_type = str(entity.type)
            if (
                entity_type == "mention"
                and username
                and value.casefold() == f"@{username}".casefold()
            ):
                return True
            if (
                entity_type == "bot_command"
                and username
                and value.casefold().endswith(f"@{username}".casefold())
            ):
                return True
            mentioned_user = getattr(entity, "user", None)
            if (
                entity_type == "text_mention"
                and mentioned_user is not None
                and str(mentioned_user.id) == self._bot_id
            ):
                return True
        return False

    def _strip_bot_mention(self, message: "Message", text: str) -> str:
        username = str(getattr(self._bot_user, "username", "") or "")
        for entity, value in self._parsed_entities(message).items():
            entity_type = str(entity.type)
            mentioned_user = getattr(entity, "user", None)
            is_bot = (
                entity_type == "mention"
                and username
                and value.casefold() == f"@{username}".casefold()
            ) or (
                entity_type == "text_mention"
                and mentioned_user is not None
                and str(mentioned_user.id) == self._bot_id
            )
            if is_bot:
                text = text.replace(value, "")
            elif (
                entity_type == "bot_command"
                and username
                and value.casefold().endswith(f"@{username}".casefold())
            ):
                suffix_length = len(username) + 1
                text = text.replace(value, value[:-suffix_length], 1)
        return text

    @staticmethod
    def _parsed_entities(message: "Message") -> dict[Any, str]:
        if message.text:
            return message.parse_entities()
        if message.caption:
            return message.parse_caption_entities()
        return {}

    @staticmethod
    def _downloadable(message: "Message") -> bool:
        return bool(message.photo) or any(
            getattr(message, attr, None) is not None
            for attr in (
                "document",
                "audio",
                "voice",
                "video",
                "animation",
                "video_note",
                "sticker",
            )
        )

    async def _download_media(
        self,
        message: "Message",
    ) -> TextBlock | DataBlock | None:
        selected = self._select_media(message)
        if selected is None:
            return None
        media, media_type, name = selected
        size = getattr(media, "file_size", None)
        if size is not None and size > _MAX_DOWNLOAD_BYTES:
            return TextBlock(
                text=(
                    f"[Telegram attachment omitted: {name} exceeds the "
                    "20 MiB Bot API download limit.]"
                ),
            )
        try:
            telegram_file = await self._retry_api(media.get_file)
            raw = bytes(
                await self._retry_api(telegram_file.download_as_bytearray),
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not download %s: %s",
                self._channel_id,
                name,
                self._safe_error(error),
            )
            return TextBlock(
                text=f"[Telegram attachment unavailable: {name}.]",
            )
        return DataBlock(
            source=Base64Source(
                data=base64.b64encode(raw).decode("ascii"),
                media_type=media_type,
            ),
            name=name,
        )

    @staticmethod
    def _select_media(message: "Message") -> tuple[Any, str, str] | None:
        if message.photo:
            return message.photo[-1], "image/jpeg", "photo.jpg"
        if message.document:
            item = message.document
            return (
                item,
                item.mime_type or "application/octet-stream",
                item.file_name or "document",
            )
        if message.audio:
            item = message.audio
            return (
                item,
                item.mime_type or "audio/mpeg",
                item.file_name or "audio.mp3",
            )
        if message.voice:
            item = message.voice
            return item, item.mime_type or "audio/ogg", "voice.ogg"
        if message.video:
            item = message.video
            return (
                item,
                item.mime_type or "video/mp4",
                item.file_name or "video.mp4",
            )
        if message.animation:
            item = message.animation
            return (
                item,
                item.mime_type or "video/mp4",
                item.file_name or "animation.mp4",
            )
        if message.video_note:
            return message.video_note, "video/mp4", "video-note.mp4"
        if message.sticker:
            return TelegramChannel._select_sticker(message.sticker)
        return None

    @staticmethod
    def _select_sticker(sticker: Any) -> tuple[Any, str, str]:
        """Choose a MIME type and filename for a Telegram sticker."""
        if sticker.is_animated:
            return sticker, "application/x-tgsticker", "sticker.tgs"
        if sticker.is_video:
            return sticker, "video/webm", "sticker.webm"
        return sticker, "image/webp", "sticker.webp"

    @staticmethod
    def _structured_text(message: "Message") -> str:
        if message.venue:
            venue = message.venue
            return "\n".join(
                [
                    "[Telegram venue]",
                    f"title: {venue.title}",
                    f"address: {venue.address}",
                    f"latitude: {venue.location.latitude}",
                    f"longitude: {venue.location.longitude}",
                ],
            )
        if message.location:
            location = message.location
            lines = [
                "[Telegram location]",
                f"latitude: {location.latitude}",
                f"longitude: {location.longitude}",
            ]
            if location.horizontal_accuracy is not None:
                lines.append(
                    f"horizontal_accuracy: {location.horizontal_accuracy}",
                )
            if location.live_period is not None:
                lines.append(f"live_period: {location.live_period}")
            return "\n".join(lines)
        if message.contact:
            contact = message.contact
            name_parts = (contact.first_name, contact.last_name or "")
            name = " ".join(part for part in name_parts if part)
            lines = [
                "[Telegram contact]",
                f"name: {name}",
                f"phone_number: {contact.phone_number}",
            ]
            if contact.user_id is not None:
                lines.append(f"user_id: {contact.user_id}")
            return "\n".join(lines)
        return ""

    # -- Outbound replies and approvals -------------------------------

    async def _new_stream_preview(
        self,
        event: ChannelEvent,
    ) -> _StreamPreview:
        """Choose native private-chat drafts or editable messages."""
        chat_type = str(event.metadata.get("chat_type", ""))
        if not chat_type:
            kind = self._chat_kind_cache.get(event.chat_id)
            if kind is None:
                kind = await self.chat_kind(event.chat_id)
            chat_type = "private" if kind == ChatKind.PRIVATE else ""
        return _StreamPreview(
            mode="draft" if chat_type == "private" else "edit",
            draft_id=secrets.randbelow(2**31 - 1) + 1,
        )

    def _has_streamable_content(self, reply: Msg) -> bool:
        """Avoid previewing the base class' unfinished empty fallback."""
        for block in reply.content:
            if isinstance(block, TextBlock) and block.text:
                return True
            if (
                isinstance(block, ThinkingBlock)
                and self._config.show_thinking
                and block.thinking
            ):
                return True
            if (
                isinstance(block, ToolCallBlock)
                and self._config.show_tool_process
            ):
                return True
            if (
                isinstance(block, ToolResultBlock)
                and self._config.show_tool_process
                and isinstance(block.output, str)
                and block.output
            ):
                return True
        return False

    @staticmethod
    def _text_from_blocks(blocks: list[TextBlock | DataBlock]) -> str:
        """Join text blocks while leaving attachments on their own path."""
        return "".join(
            block.text for block in blocks if isinstance(block, TextBlock)
        )

    @staticmethod
    def _formatted_chunks(text: str) -> list["_TelegramTextChunk"]:
        """Lazily render common Markdown to Telegram-safe HTML chunks."""
        from ._markdown import _telegram_markdown_chunks

        return _telegram_markdown_chunks(text, _MAX_TEXT_LENGTH)

    async def _update_stream_preview(
        self,
        chat_id: str,
        preview: _StreamPreview,
        text: str,
    ) -> None:
        """Best-effort preview update that can never block final delivery."""
        if preview.disabled or not text:
            return
        try:
            chunks = self._formatted_chunks(text)
            if not chunks:
                return
            # Keep the one editable group preview stable once the reply
            # crosses Telegram's 4096-character boundary. Showing the last
            # chunk would make a nearly full preview suddenly shrink to the
            # short tail; final delivery sends the remaining chunks.
            chunk = chunks[0]
            now = time.monotonic()
            if chunk.html == preview.last_html or (
                preview.last_update is not None
                and now - preview.last_update < _STREAM_MIN_INTERVAL_SECS
            ):
                return

            bot = await self._bot()
            from telegram.error import BadRequest

            if preview.mode == "draft":
                try:
                    await bot.send_message_draft(
                        chat_id=self._target_chat_id(chat_id),
                        draft_id=preview.draft_id,
                        text=chunk.html,
                        parse_mode="HTML",
                    )
                except BadRequest:
                    await bot.send_message_draft(
                        chat_id=self._target_chat_id(chat_id),
                        draft_id=preview.draft_id,
                        text=chunk.plain,
                    )
            elif preview.message_id is None:
                try:
                    message = await bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.html,
                        parse_mode="HTML",
                    )
                except BadRequest:
                    message = await bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.plain,
                    )
                preview.message_id = int(message.message_id)
            else:
                try:
                    await bot.edit_message_text(
                        chat_id=self._target_chat_id(chat_id),
                        message_id=preview.message_id,
                        text=chunk.html,
                        parse_mode="HTML",
                    )
                except BadRequest as error:
                    if "message is not modified" not in str(error).casefold():
                        await bot.edit_message_text(
                            chat_id=self._target_chat_id(chat_id),
                            message_id=preview.message_id,
                            text=chunk.plain,
                        )
            preview.last_html = chunk.html
            preview.last_update = now
        except Exception as error:  # pylint: disable=broad-except
            preview.disabled = True
            logger.debug(
                "Telegram channel '%s' disabled one streaming preview: %s",
                self._channel_id,
                self._safe_error(error),
            )

    async def _finish_streamed_text(
        self,
        chat_id: str,
        preview: _StreamPreview,
        text: str,
    ) -> None:
        """Persist all final chunks, reusing an editable group preview."""
        try:
            chunks = self._formatted_chunks(text)
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not format its final reply; "
                "sending plain text instead: %s",
                self._channel_id,
                self._safe_error(error),
            )
            result = await self.send_message_to(chat_id, text)
            if not result.ok:
                logger.warning(
                    "Telegram channel '%s' failed to send its plain-text "
                    "fallback: %s",
                    self._channel_id,
                    result.error,
                )
            return
        if not chunks:
            return
        first_unsent = 0
        if preview.mode == "edit" and preview.message_id is not None:
            if len(chunks) == 1 and preview.last_html == chunks[0].html:
                first_unsent = 1
            else:
                result = await self._edit_formatted_chunk(
                    chat_id,
                    preview.message_id,
                    chunks[0],
                )
                if result.ok:
                    first_unsent = 1
                else:
                    logger.warning(
                        "Telegram channel '%s' could not finalise its "
                        "preview: %s",
                        self._channel_id,
                        result.error,
                    )
        for chunk in chunks[first_unsent:]:
            result = await self._send_formatted_chunk(chat_id, chunk)
            if not result.ok:
                logger.warning(
                    "Telegram channel '%s' failed to send text: %s",
                    self._channel_id,
                    result.error,
                )
                break

    async def _send_formatted_chunk(
        self,
        chat_id: str,
        chunk: "_TelegramTextChunk",
    ) -> _TelegramResult:
        """Send HTML and retry once as plain text on formatting errors."""
        from telegram.error import BadRequest

        try:
            bot = await self._bot()
            try:
                await self._retry_api(
                    lambda: bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.html,
                        parse_mode="HTML",
                    ),
                )
            except BadRequest:
                await self._retry_api(
                    lambda: bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.plain,
                    ),
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return _TelegramResult(False, self._safe_error(error))

    async def _edit_formatted_chunk(
        self,
        chat_id: str,
        message_id: int,
        chunk: "_TelegramTextChunk",
    ) -> _TelegramResult:
        """Finalise an editable preview with formatted/plain fallback."""
        from telegram.error import BadRequest

        try:
            bot = await self._bot()
            try:
                await self._retry_api(
                    lambda: bot.edit_message_text(
                        chat_id=self._target_chat_id(chat_id),
                        message_id=message_id,
                        text=chunk.html,
                        parse_mode="HTML",
                    ),
                )
            except BadRequest as error:
                if "message is not modified" in str(error).casefold():
                    return _TelegramResult(True)
                await self._retry_api(
                    lambda: bot.edit_message_text(
                        chat_id=self._target_chat_id(chat_id),
                        message_id=message_id,
                        text=chunk.plain,
                    ),
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return _TelegramResult(False, self._safe_error(error))

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Stream a formatted preview, then persist the complete reply."""
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        preview = await self._new_stream_preview(event)
        async for event_payload in events:
            evt = _EVENT_ADAPTER.validate_python(event_payload)
            if isinstance(evt, RequireUserConfirmEvent):
                confirm = evt
                break
            reply_id = getattr(evt, "reply_id", None)
            if reply_id is not None:
                if reply is None:
                    reply = Msg(name="assistant", role="assistant", content=[])
                    reply.id = reply_id
                reply.append_event(evt)
            if isinstance(evt, ReplyEndEvent):
                break

            if reply is not None and self._has_streamable_content(reply):
                current_blocks = self._render(
                    reply,
                    show_thinking=self._config.show_thinking,
                    show_tool_process=self._config.show_tool_process,
                )
                current_text = self._text_from_blocks(current_blocks)
                await self._update_stream_preview(
                    event.chat_id,
                    preview,
                    current_text,
                )

        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        await self._finish_streamed_text(
            event.chat_id,
            preview,
            self._text_from_blocks(blocks),
        )

        for block in blocks:
            if isinstance(block, TextBlock):
                continue
            if not isinstance(block.source, Base64Source):
                continue
            try:
                attachment_bytes = base64.b64decode(
                    block.source.data,
                    validate=True,
                )
            except (binascii.Error, ValueError):
                logger.warning("Telegram reply contained invalid base64 data")
                continue
            media_type = block.source.media_type or ""
            name = block.name or "attachment"
            image_small_enough = len(attachment_bytes) <= _MAX_PHOTO_BYTES
            inline_image = (
                media_type.startswith("image/") and image_small_enough
            )
            if inline_image:
                result = await self.send_image_to(
                    event.chat_id,
                    attachment_bytes,
                    name,
                )
            elif len(attachment_bytes) <= _MAX_DOCUMENT_BYTES:
                result = await self.send_file_to(
                    event.chat_id,
                    attachment_bytes,
                    name,
                )
            else:
                result = _TelegramResult(
                    False,
                    f"attachment {name!r} exceeds Telegram's 50 MiB limit",
                )
            if not result.ok:
                await self.send_message_to(
                    event.chat_id,
                    f"Could not send attachment: {result.error}",
                )

        if confirm is not None:
            await self._present_confirm(event, confirm)

    async def _present_confirm(
        self,
        event: ChannelEvent,
        request: RequireUserConfirmEvent,
    ) -> None:
        try:
            bot = await self._bot()
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not prepare approval "
                "delivery: %s",
                self._channel_id,
                self._safe_error(error),
            )
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        for tool in request.tool_calls:
            base = {
                "tool_call_id": tool.id,
                "chat_id": event.chat_id,
                "agent_id": str(event.metadata.get("agent_id", "")),
                "session_id": str(event.metadata.get("session_id", "")),
            }
            try:
                allow_callback = await self._store_approval_callback(
                    _ApprovalCallback(**base, approved=True),
                )
                deny_callback = await self._store_approval_callback(
                    _ApprovalCallback(**base, approved=False),
                )
            except Exception as error:  # pylint: disable=broad-except
                logger.warning(
                    "Telegram channel '%s' could not store approval state: %s",
                    self._channel_id,
                    self._safe_error(error),
                )
                continue
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Allow",
                            callback_data=allow_callback,
                        ),
                        InlineKeyboardButton(
                            "❌ Deny",
                            callback_data=deny_callback,
                        ),
                    ],
                ],
            )
            text = (
                "🛡️ Tool execution requires approval\n"
                f"Tool: {tool.name}\n"
                f"Arguments: {str(tool.input)[:800]}"
            )
            try:
                await self._retry_api(
                    partial(
                        bot.send_message,
                        chat_id=self._target_chat_id(event.chat_id),
                        text=text,
                        reply_markup=keyboard,
                    ),
                )
            except Exception as error:  # pylint: disable=broad-except
                logger.warning(
                    "Telegram channel '%s' could not send approval: %s",
                    self._channel_id,
                    self._safe_error(error),
                )

    async def _store_approval_callback(
        self,
        data: _ApprovalCallback,
    ) -> str:
        """Persist one compact callback token for a cross-process card."""
        if self._message_bus is None:
            raise RuntimeError(
                "Telegram approval callback storage is unavailable",
            )
        token = secrets.token_urlsafe(18)
        await self._message_bus.registry_set(
            MessageBusKeys.channel_approval_callback(self._channel_id, token),
            "payload",
            data.to_json(),
            ttl_secs=_APPROVAL_CALLBACK_TTL_SECS,
        )
        return f"{_APPROVAL_CALLBACK_PREFIX}{token}"

    async def _load_approval_callback(
        self,
        raw_data: Any,
    ) -> tuple[_ApprovalCallback | None, str | None]:
        """Load a callback payload sent by a connection-free client."""
        if not isinstance(raw_data, str) or not raw_data.startswith(
            _APPROVAL_CALLBACK_PREFIX,
        ):
            return None, None
        token = raw_data.removeprefix(_APPROVAL_CALLBACK_PREFIX)
        if not token or self._message_bus is None:
            return None, token or None
        payload = await self._message_bus.registry_get(
            MessageBusKeys.channel_approval_callback(self._channel_id, token),
            "payload",
        )
        return (
            _ApprovalCallback.from_json(payload)
            if payload is not None
            else None,
            token,
        )

    async def _delete_approval_callback(self, token: str) -> None:
        """Retire callback state after its decision reached the gateway."""
        if self._message_bus is None:
            return
        await self._message_bus.registry_del(
            MessageBusKeys.channel_approval_callback(self._channel_id, token),
            "payload",
        )

    async def _expire_callback(self, query: Any) -> None:
        """Best-effort UI cleanup for an expired or unknown callback."""
        try:
            await query.answer("This approval has expired.", show_alert=True)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not answer an expired Telegram approval")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not freeze an expired Telegram approval")

    async def _on_callback(
        self,
        update: "Update",
        _context: "CallbackContext",
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            data, token = await self._load_approval_callback(query.data)
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not load approval state: %s",
                self._channel_id,
                self._safe_error(error),
            )
            data, token = None, None
        if data is None:
            await self._expire_callback(query)
            return

        actor_id = str(query.from_user.id) if query.from_user else ""
        if self._emit is not None:
            await self._emit(
                ChannelConfirmationResultEvent(
                    channel_id=self._channel_id,
                    chat_id=data.chat_id,
                    channel_user_id=actor_id,
                    agent_id=data.agent_id,
                    session_id=data.session_id,
                    tool_call_id=data.tool_call_id,
                    approved=data.approved,
                    actor=actor_id,
                ),
            )
            try:
                if token is not None:
                    await self._delete_approval_callback(token)
            except Exception:  # pylint: disable=broad-except
                logger.debug("Could not retire a Telegram approval callback")

        try:
            await query.answer("Decision received.")
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not answer a Telegram approval callback")
        try:
            await query.edit_message_text(
                "✅ Approved" if data.approved else "🚫 Denied",
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not freeze a Telegram approval message")

    # -- Agent-callable delivery --------------------------------------

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        from ._tools import SendFile, SendImage, SendMessage

        backend = workspace.get_backend()
        return [
            SendMessage(self, backend),
            SendFile(self, backend),
            SendImage(self, backend),
        ]

    async def send_message_to(
        self,
        chat_id: str,
        text: str,
    ) -> _TelegramResult:
        """Send plain text, splitting it at Telegram's hard limit."""
        if not text:
            return _TelegramResult(False, "message text is empty")
        try:
            bot = await self._bot()
            for part in self._split_long_message(text):
                await self._retry_api(
                    partial(
                        bot.send_message,
                        chat_id=self._target_chat_id(chat_id),
                        text=part,
                    ),
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return _TelegramResult(False, self._safe_error(error))

    async def send_file_to(
        self,
        chat_id: str,
        data: bytes,
        file_name: str,
    ) -> _TelegramResult:
        """Send bytes as a Telegram document."""
        if len(data) > _MAX_DOCUMENT_BYTES:
            return _TelegramResult(
                False,
                "file exceeds Telegram's 50 MiB limit",
            )
        try:
            bot = await self._bot()
            await self._retry_api(
                lambda: bot.send_document(
                    chat_id=self._target_chat_id(chat_id),
                    document=io.BytesIO(data),
                    filename=file_name or "file",
                ),
            )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return _TelegramResult(False, self._safe_error(error))

    async def send_image_to(
        self,
        chat_id: str,
        data: bytes,
        file_name: str = "image",
    ) -> _TelegramResult:
        """Send bytes as an inline Telegram photo."""
        if len(data) > _MAX_PHOTO_BYTES:
            return _TelegramResult(
                False,
                "image exceeds Telegram's 10 MiB photo limit; use SendFile",
            )
        try:
            bot = await self._bot()
            await self._retry_api(
                lambda: bot.send_photo(
                    chat_id=self._target_chat_id(chat_id),
                    photo=io.BytesIO(data),
                    filename=file_name or "image",
                ),
            )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return _TelegramResult(False, self._safe_error(error))

    # -- Platform metadata and helpers --------------------------------

    async def list_bot_chats(self) -> list[dict]:
        """Telegram has no API that enumerates every chat a bot belongs to."""
        return []

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        cached = self._chat_kind_cache.get(chat_id)
        if cached is not None:
            return cached
        chat = await self._get_chat(chat_id)
        if chat is None:
            return None
        self._remember_chat(chat)
        return self._chat_kind_cache.get(chat_id)

    async def chat_name(self, chat_id: str) -> str:
        cached = self._chat_name_cache.get(chat_id)
        if cached:
            return cached
        chat = await self._get_chat(chat_id)
        if chat is None:
            return ""
        self._remember_chat(chat)
        return self._chat_name_cache.get(chat_id, "")

    async def _get_chat(self, chat_id: str) -> Any:
        try:
            bot = await self._bot()
            return await self._retry_api(
                lambda: bot.get_chat(self._target_chat_id(chat_id)),
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.debug(
                "Telegram channel '%s' could not resolve chat %s: %s",
                self._channel_id,
                chat_id,
                self._safe_error(error),
            )
            return None

    def _remember_chat(self, chat: Any) -> None:
        chat_id = str(chat.id)
        chat_type = str(chat.type)
        if chat_type == "private":
            self._chat_kind_cache[chat_id] = ChatKind.PRIVATE
        elif chat_type in ("group", "supergroup", "channel"):
            self._chat_kind_cache[chat_id] = ChatKind.GROUP
        name = self._chat_display_name(chat)
        if name:
            self._chat_name_cache[chat_id] = name

    @staticmethod
    def _chat_display_name(chat: Any) -> str:
        return (
            getattr(chat, "title", None)
            or getattr(chat, "full_name", None)
            or getattr(chat, "username", None)
            or ""
        )

    @staticmethod
    def _target_chat_id(chat_id: str) -> int | str:
        stripped = str(chat_id).strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return stripped

    async def _retry_api(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        from telegram.error import BadRequest, NetworkError, RetryAfter

        for attempt in range(_MAX_API_ATTEMPTS):
            try:
                return await operation()
            except RetryAfter as error:
                delay = error.retry_after
                seconds = (
                    delay.total_seconds()
                    if isinstance(delay, timedelta)
                    else float(delay)
                )
                exhausted = attempt + 1 >= _MAX_API_ATTEMPTS
                if exhausted:
                    raise
                await asyncio.sleep(max(0.0, seconds))
            except NetworkError as error:
                # PTB models BadRequest as a NetworkError subclass even
                # though retrying a malformed Bot API request cannot help.
                if isinstance(error, BadRequest):
                    raise
                if attempt + 1 >= _MAX_API_ATTEMPTS:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError("unreachable Telegram retry state")

    def _safe_error(self, error: BaseException) -> str:
        text = str(error) or type(error).__name__
        if self._bot_token:
            text = text.replace(self._bot_token, "<redacted>")
        return text
