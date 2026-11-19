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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
import io
import json
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
    from telegram import Update
    from telegram.ext import Application, CallbackContext

    from ...message_bus import MessageBus
    from ....tool import ToolBase
    from ....workspace import WorkspaceBase
    from ._markdown import _TelegramTextChunk
    from ._inbound import TelegramInboundNormalizer


_POLL_TIMEOUT_SECS = 30
_POLL_READ_TIMEOUT_SECS = 40
_PRIVATE_STREAM_MIN_INTERVAL_SECS = 1.0
_GROUP_STREAM_MIN_INTERVAL_SECS = 3.1
_CONNECT_BACKOFF_RESET_SECS = 60.0
_MAX_API_ATTEMPTS = 3
_API_RETRY_BUDGET_SECS = 300.0
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_MAX_TEXT_LENGTH = 4096
_APPROVAL_CALLBACK_PREFIX = "as:"
_APPROVAL_CALLBACK_TTL_SECS = 24 * 60 * 60
_LISTENER_LOCK_TTL_SECS = 60
_LISTENER_LOCK_PREFIX = "agentscope:telegram:listener:lock:"
_APPROVAL_LOCK_TTL_SECS = 60
_APPROVAL_LOCK_PREFIX = "agentscope:telegram:approval:lock:"

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
    submitted: bool = False
    approved: bool | None = None

    def to_json(self) -> str:
        """Serialize the callback payload for the shared message bus."""
        return json.dumps(
            {
                "tool_call_id": self.tool_call_id,
                "chat_id": self.chat_id,
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "submitted": self.submitted,
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
        if not isinstance(raw, dict):
            return None
        fields = ("tool_call_id", "chat_id", "agent_id", "session_id")
        if any(not isinstance(raw.get(field), str) for field in fields):
            return None
        if not raw["tool_call_id"] or not raw["chat_id"]:
            return None
        submitted = raw.get("submitted", False)
        approved = raw.get("approved")
        if not isinstance(submitted, bool):
            return None
        if approved is not None and not isinstance(approved, bool):
            return None
        if submitted and approved is None:
            return None
        return cls(
            tool_call_id=raw["tool_call_id"],
            chat_id=raw["chat_id"],
            agent_id=raw["agent_id"],
            session_id=raw["session_id"],
            submitted=submitted,
            approved=approved,
        )


@dataclass(frozen=True)
class _TelegramResult:
    """Sanitised result returned to Agent-callable delivery tools."""

    ok: bool
    error: str = ""
    failure: str = ""


@dataclass
class _RetryBudget:
    """Deadline and retry state shared by one logical API delivery."""

    deadline: float
    network_failures: int = 0

    @classmethod
    def start(cls) -> "_RetryBudget":
        """Start a fresh logical-operation budget."""
        return cls(time.monotonic() + _API_RETRY_BUDGET_SECS)


class _TelegramRetryBudgetExceeded(TimeoutError):
    """A Telegram operation cannot retry within its delivery budget."""


class _TelegramDeliveryUnknown(RuntimeError):
    """A network failure left the operation's delivery outcome unknown."""


@dataclass
class _StreamPreview:
    """Mutable state for one best-effort streamed reply preview."""

    mode: str
    draft_id: int
    message_id: int | None = None
    last_update: float | None = None
    retry_not_before: float = 0.0
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
        self.status = ChannelStatus()
        self._application: "Application | None" = None
        self._rest_bot: Any = None
        self._rest_bot_lock = asyncio.Lock()
        self._message_bus: "MessageBus | None" = None
        self._bot_user: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fatal_event: asyncio.Event | None = None
        self._fatal_error: BaseException | None = None
        self._chat_name_cache: dict[str, str] = {}
        self._chat_kind_cache: dict[str, ChatKind] = {}
        self._inbound: "TelegramInboundNormalizer | None" = None
        self._connected_since: float | None = None

    @property
    def channel_id(self) -> str:
        """The unique channel instance ID."""
        return self._channel_id

    def bind_message_bus(self, message_bus: "MessageBus") -> None:
        """Bind shared state for listener coordination and callbacks."""
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
        backoff = 1.0
        try:
            while True:
                self._fatal_event = asyncio.Event()
                self._fatal_error = None
                try:
                    async with self._listener_lease():
                        self.status.state = "connecting"
                        await self._run_application()
                    if self._fatal_error is not None:
                        raise self._fatal_error
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
                    if (
                        self._connected_since is not None
                        and time.monotonic() - self._connected_since
                        >= _CONNECT_BACKOFF_RESET_SECS
                    ):
                        backoff = 1.0
                    self._connected_since = None
                    self.status.state = "retrying"
                    self.status.last_error = self._safe_error(error)
                    logger.warning(
                        "Telegram channel '%s' will reconnect in %.1fs: %s",
                        self._channel_id,
                        backoff,
                        self.status.last_error,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            await self._cancel_albums()
            self.status.state = "stopped"

    @asynccontextmanager
    async def _listener_lease(self) -> AsyncIterator[None]:
        """Hold the Telegram-private per-channel polling lease."""
        if self._message_bus is None:
            yield
            return
        key = f"{_LISTENER_LOCK_PREFIX}{self._channel_id}"
        async with self._message_bus.acquire_lock(
            key,
            ttl_secs=_LISTENER_LOCK_TTL_SECS,
        ):
            yield

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
            from ._inbound import TelegramInboundNormalizer

            self._inbound = TelegramInboundNormalizer(self)

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
            self._connected_since = time.monotonic()
            assert self._fatal_event is not None
            await self._fatal_event.wait()
        except InvalidToken as error:
            raise _PermanentTelegramError(
                "Telegram rejected the configured bot token.",
            ) from error
        except Conflict as error:
            raise Conflict(
                "Another instance is already polling updates for this bot.",
            ) from error
        except (BadRequest, Forbidden) as error:
            raise _PermanentTelegramError(self._safe_error(error)) from error
        finally:
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
                        await self._cancel_albums()
                    finally:
                        try:
                            if initialized:
                                await application.shutdown()
                            else:
                                # Application shutdown is a no-op when bot
                                # initialisation fails; close its requests.
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
                "'python-telegram-bot>=22.8' and "
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
        """Reconnect after competing pollers; PTB retries other failures."""
        try:
            from telegram.error import Conflict
        except ImportError:
            return
        self.status.last_error = self._safe_error(error)
        if isinstance(error, Conflict):
            self.status.state = "retrying"
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

    def _ensure_inbound(self) -> "TelegramInboundNormalizer":
        """Return the normalizer for the listener or direct tests."""
        if self._inbound is None:
            from ._inbound import TelegramInboundNormalizer

            self._inbound = TelegramInboundNormalizer(self)
        return self._inbound

    async def _on_update(
        self,
        update: "Update",
        _context: "CallbackContext",
    ) -> None:
        """Delegate one update to the run-scoped inbound normalizer."""
        await self._ensure_inbound().handle(update)

    async def _cancel_albums(self) -> None:
        inbound, self._inbound = self._inbound, None
        if inbound is not None:
            await inbound.aclose()

    # -- Outbound replies and approvals -------------------------------

    async def _new_stream_preview(
        self,
        event: ChannelEvent,
    ) -> _StreamPreview:
        """Choose native private-chat drafts or editable messages."""
        kind = await self.chat_kind(event.chat_id)
        return _StreamPreview(
            mode="draft" if kind == ChatKind.PRIVATE else "edit",
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
        from telegram.error import RetryAfter

        now = time.monotonic()
        interval = (
            _PRIVATE_STREAM_MIN_INTERVAL_SECS
            if preview.mode == "draft"
            else _GROUP_STREAM_MIN_INTERVAL_SECS
        )
        if now < preview.retry_not_before or (
            preview.last_update is not None
            and now - preview.last_update < interval
        ):
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
            if chunk.html == preview.last_html:
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
                except BadRequest as error:
                    if not self._is_format_error(error):
                        raise
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
                except BadRequest as error:
                    if not self._is_format_error(error):
                        raise
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
                    if "message is not modified" in str(error).casefold():
                        pass
                    elif self._is_format_error(error):
                        await bot.edit_message_text(
                            chat_id=self._target_chat_id(chat_id),
                            message_id=preview.message_id,
                            text=chunk.plain,
                        )
                    else:
                        raise
            preview.last_html = chunk.html
            preview.last_update = now
        except RetryAfter as error:
            preview.retry_not_before = max(
                preview.retry_not_before,
                time.monotonic() + self._retry_after_seconds(error),
            )
            logger.debug(
                "Telegram channel '%s' cooled down one streaming preview",
                self._channel_id,
            )
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
            result = await self.send_message_to(
                chat_id,
                text,
                budget=_RetryBudget.start(),
                pace=preview,
            )
            if not result.ok:
                logger.warning(
                    "Telegram channel '%s' failed to send its plain-text "
                    "fallback (%s): %s",
                    self._channel_id,
                    result.failure or "api_error",
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
                budget = _RetryBudget.start()
                result = await self._edit_formatted_chunk(
                    chat_id,
                    preview.message_id,
                    chunks[0],
                    budget,
                    preview,
                )
                if result.ok:
                    first_unsent = 1
                elif result.failure != "edit_target_missing":
                    logger.warning(
                        "Telegram channel '%s' could not finalise its "
                        "preview (%s): %s",
                        self._channel_id,
                        result.failure or "api_error",
                        result.error,
                    )
                    return
                else:
                    logger.warning(
                        "Telegram channel '%s' could not finalise its "
                        "preview (%s): %s",
                        self._channel_id,
                        result.failure or "api_error",
                        result.error,
                    )
        for chunk in chunks[first_unsent:]:
            budget = (
                budget
                if first_unsent == 0
                and preview.mode == "edit"
                and preview.message_id is not None
                else _RetryBudget.start()
            )
            result = await self._send_formatted_chunk(
                chat_id,
                chunk,
                budget,
                preview,
            )
            if not result.ok:
                logger.warning(
                    "Telegram channel '%s' failed to send text (%s): %s",
                    self._channel_id,
                    result.failure or "api_error",
                    result.error,
                )
                break

    async def _send_formatted_chunk(
        self,
        chat_id: str,
        chunk: "_TelegramTextChunk",
        budget: _RetryBudget | None = None,
        preview: _StreamPreview | None = None,
    ) -> _TelegramResult:
        """Send HTML and retry once as plain text on formatting errors."""
        from telegram.error import BadRequest

        budget = budget or _RetryBudget.start()
        preview = preview or _StreamPreview(mode="draft", draft_id=1)
        try:
            bot = await self._bot()
            await self._wait_for_delivery_window(preview, budget)
            try:
                await self._retry_api(
                    lambda: bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.html,
                        parse_mode="HTML",
                    ),
                    budget=budget,
                    pace=preview,
                    side_effecting=True,
                )
            except BadRequest as error:
                if not self._is_format_error(error):
                    raise
                await self._wait_for_delivery_window(preview, budget)
                await self._retry_api(
                    lambda: bot.send_message(
                        chat_id=self._target_chat_id(chat_id),
                        text=chunk.plain,
                    ),
                    budget=budget,
                    pace=preview,
                    side_effecting=True,
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return self._delivery_failure(error)

    async def _edit_formatted_chunk(
        self,
        chat_id: str,
        message_id: int,
        chunk: "_TelegramTextChunk",
        budget: _RetryBudget | None = None,
        preview: _StreamPreview | None = None,
    ) -> _TelegramResult:
        """Finalise an editable preview with formatted/plain fallback."""
        from telegram.error import BadRequest

        budget = budget or _RetryBudget.start()
        preview = preview or _StreamPreview(mode="edit", draft_id=1)
        try:
            bot = await self._bot()
            await self._wait_for_delivery_window(preview, budget)
            try:
                await self._retry_api(
                    lambda: bot.edit_message_text(
                        chat_id=self._target_chat_id(chat_id),
                        message_id=message_id,
                        text=chunk.html,
                        parse_mode="HTML",
                    ),
                    budget=budget,
                    pace=preview,
                    side_effecting=True,
                )
            except BadRequest as error:
                lowered = str(error).casefold()
                if "message is not modified" in lowered:
                    return _TelegramResult(True)
                if "message to edit not found" in lowered:
                    return _TelegramResult(
                        False,
                        self._safe_error(error),
                        "edit_target_missing",
                    )
                if not self._is_format_error(error):
                    raise
                await self._wait_for_delivery_window(preview, budget)
                await self._retry_api(
                    lambda: bot.edit_message_text(
                        chat_id=self._target_chat_id(chat_id),
                        message_id=message_id,
                        text=chunk.plain,
                    ),
                    budget=budget,
                    pace=preview,
                    side_effecting=True,
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return self._delivery_failure(error)

    async def _wait_for_delivery_window(
        self,
        preview: _StreamPreview,
        budget: _RetryBudget,
    ) -> None:
        """Respect known flood limits and the group edit cadence."""
        target = preview.retry_not_before
        if preview.mode == "edit" and preview.last_update is not None:
            target = max(
                target,
                preview.last_update + _GROUP_STREAM_MIN_INTERVAL_SECS,
            )
        delay = max(0.0, target - time.monotonic())
        remaining = budget.deadline - time.monotonic()
        if delay >= remaining:
            raise _TelegramRetryBudgetExceeded(
                "Telegram retry delay exceeds the delivery budget",
            )
        if delay:
            await asyncio.sleep(delay)
        preview.last_update = time.monotonic()

    @staticmethod
    def _is_format_error(error: BaseException) -> bool:
        text = str(error).casefold()
        return any(
            marker in text
            for marker in (
                "can't parse entities",
                "unsupported start tag",
                "can't find end tag",
            )
        )

    def _delivery_failure(self, error: BaseException) -> _TelegramResult:
        if isinstance(error, _TelegramRetryBudgetExceeded):
            failure = "budget_exhausted"
        elif isinstance(error, _TelegramDeliveryUnknown):
            failure = "delivery_unknown"
        else:
            failure = "api_error"
        return _TelegramResult(False, self._safe_error(error), failure)

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
                    budget=_RetryBudget.start(),
                    pace=preview,
                )
            elif len(attachment_bytes) <= _MAX_DOCUMENT_BYTES:
                result = await self.send_file_to(
                    event.chat_id,
                    attachment_bytes,
                    name,
                    budget=_RetryBudget.start(),
                    pace=preview,
                )
            else:
                result = _TelegramResult(
                    False,
                    f"attachment {name!r} exceeds Telegram's 50 MiB limit",
                )
            if not result.ok:
                logger.warning(
                    "Telegram channel '%s' failed to send attachment (%s): %s",
                    self._channel_id,
                    result.failure or "api_error",
                    result.error,
                )
                await self.send_message_to(
                    event.chat_id,
                    f"Could not send attachment: {result.error}",
                    budget=_RetryBudget.start(),
                    pace=preview,
                )

        if confirm is not None:
            await self._present_confirm(event, confirm, preview)

    async def _present_confirm(
        self,
        event: ChannelEvent,
        request: RequireUserConfirmEvent,
        pace: _StreamPreview | None = None,
    ) -> None:
        """Send one approval card backed by one shared callback payload."""
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
            try:
                token = await self._store_approval_callback(
                    _ApprovalCallback(
                        tool_call_id=tool.id,
                        chat_id=event.chat_id,
                        agent_id=str(event.metadata.get("agent_id", "")),
                        session_id=str(event.metadata.get("session_id", "")),
                    ),
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
                            callback_data=(
                                f"{_APPROVAL_CALLBACK_PREFIX}a:{token}"
                            ),
                        ),
                        InlineKeyboardButton(
                            "❌ Deny",
                            callback_data=(
                                f"{_APPROVAL_CALLBACK_PREFIX}d:{token}"
                            ),
                        ),
                    ],
                ],
            )
            try:
                budget = _RetryBudget.start()
                if pace is not None:
                    await self._wait_for_delivery_window(pace, budget)
                await self._retry_api(
                    partial(
                        bot.send_message,
                        chat_id=self._target_chat_id(event.chat_id),
                        text=(
                            "🛡️ Tool execution requires approval\n"
                            f"Tool: {tool.name}\n"
                            f"Arguments: {str(tool.input)[:800]}"
                        ),
                        reply_markup=keyboard,
                    ),
                    budget=budget,
                    pace=pace,
                    side_effecting=True,
                )
            except Exception as error:  # pylint: disable=broad-except
                try:
                    await self._delete_approval_callback(token)
                except Exception:  # pylint: disable=broad-except
                    logger.debug("Could not retire an unsent approval token")
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
        return token

    async def _load_approval_callback(
        self,
        raw_data: Any,
    ) -> tuple[_ApprovalCallback | None, str | None, bool | None]:
        """Parse a decision and load its shared callback payload."""
        token, decision = self._parse_approval_callback(raw_data)
        if not token or self._message_bus is None:
            return None, token or None, None
        payload = await self._message_bus.registry_get(
            MessageBusKeys.channel_approval_callback(self._channel_id, token),
            "payload",
        )
        data = (
            _ApprovalCallback.from_json(payload)
            if payload is not None
            else None
        )
        return data, token, decision

    @staticmethod
    def _parse_approval_callback(
        raw_data: Any,
    ) -> tuple[str | None, bool | None]:
        """Parse a compact callback without reading shared state."""
        if not isinstance(raw_data, str):
            return None, None
        parts = raw_data.split(":", maxsplit=2)
        if len(parts) != 3 or parts[0] != "as" or parts[1] not in ("a", "d"):
            return None, None
        token = parts[2]
        if not token:
            return None, None
        return token, parts[1] == "a"

    async def _mark_approval_submitted(
        self,
        token: str,
        data: _ApprovalCallback,
        approved: bool,
    ) -> None:
        """Persist a terminal receipt before best-effort deletion."""
        assert self._message_bus is not None
        submitted = _ApprovalCallback(
            tool_call_id=data.tool_call_id,
            chat_id=data.chat_id,
            agent_id=data.agent_id,
            session_id=data.session_id,
            submitted=True,
            approved=approved,
        )
        await self._message_bus.registry_set(
            MessageBusKeys.channel_approval_callback(self._channel_id, token),
            "payload",
            submitted.to_json(),
            ttl_secs=_APPROVAL_CALLBACK_TTL_SECS,
        )

    async def _delete_approval_callback(self, token: str) -> None:
        """Retire callback state after its decision reached the gateway."""
        if self._message_bus is not None:
            await self._message_bus.registry_del(
                MessageBusKeys.channel_approval_callback(
                    self._channel_id,
                    token,
                ),
                "payload",
            )

    @staticmethod
    def _is_callback_allowed(query: Any, data: _ApprovalCallback) -> bool:
        message = getattr(query, "message", None)
        chat = getattr(message, "chat", None)
        user = getattr(query, "from_user", None)
        return bool(
            chat is not None
            and str(chat.id) == data.chat_id
            and str(chat.type) in ("private", "group", "supergroup")
            and user is not None
            and not getattr(user, "is_bot", False),
        )

    async def _expire_callback(self, query: Any) -> None:
        """Best-effort UI cleanup for an expired or unknown callback."""
        try:
            await query.answer(
                "This approval is no longer pending.",
                show_alert=True,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not answer an expired Telegram approval")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not freeze an expired Telegram approval")

    async def _answer_callback_failure(self, query: Any) -> None:
        """Keep a callback retryable when shared state or delivery fails."""
        try:
            await query.answer(
                "Could not confirm submission. Please retry.",
                show_alert=True,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not report a Telegram approval failure")

    async def _on_callback(
        self,
        update: "Update",
        _context: "CallbackContext",
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        token, _decision = self._parse_approval_callback(query.data)
        if token is None or self._message_bus is None:
            await self._expire_callback(query)
            return

        submitted: bool | None = None
        try:
            lock_key = f"{_APPROVAL_LOCK_PREFIX}{self._channel_id}:{token}"
            async with self._message_bus.acquire_lock(
                lock_key,
                ttl_secs=_APPROVAL_LOCK_TTL_SECS,
            ):
                data, _token, decision = await self._load_approval_callback(
                    query.data,
                )
                if data is None or decision is None:
                    submitted = None
                elif not self._is_callback_allowed(query, data):
                    try:
                        await query.answer(
                            "This approval belongs to another chat.",
                            show_alert=True,
                        )
                    except Exception:  # pylint: disable=broad-except
                        logger.debug("Could not reject a foreign callback")
                    return
                elif data.submitted:
                    submitted = data.approved
                elif self._emit is None:
                    raise RuntimeError(
                        "Telegram approval delivery is unavailable",
                    )
                else:
                    actor_id = str(query.from_user.id)
                    await self._emit(
                        ChannelConfirmationResultEvent(
                            channel_id=self._channel_id,
                            chat_id=data.chat_id,
                            channel_user_id=actor_id,
                            agent_id=data.agent_id,
                            session_id=data.session_id,
                            tool_call_id=data.tool_call_id,
                            approved=decision,
                            actor=actor_id,
                        ),
                    )
                    await self._mark_approval_submitted(
                        token,
                        data,
                        decision,
                    )
                    submitted = decision
                if submitted is not None:
                    try:
                        await self._delete_approval_callback(token)
                    except Exception:  # pylint: disable=broad-except
                        logger.debug(
                            "Could not retire a submitted Telegram approval",
                        )
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not deliver approval: %s",
                self._channel_id,
                self._safe_error(error),
            )
            await self._answer_callback_failure(query)
            return

        if submitted is None:
            await self._expire_callback(query)
            return
        try:
            await query.answer("Decision submitted.")
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not answer a Telegram approval callback")
        try:
            await query.edit_message_text(
                "✅ Approval submitted" if submitted else "🚫 Denial submitted",
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not freeze a Telegram approval message")

    # -- Agent-callable delivery --------------------------------------

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
        channel_user_id: str | None = None,
    ) -> list["ToolBase"]:
        """Return Telegram delivery tools for the session workspace."""
        del channel_user_id
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
        *,
        budget: _RetryBudget | None = None,
        pace: _StreamPreview | None = None,
    ) -> _TelegramResult:
        """Send plain text, splitting it at Telegram's hard limit."""
        if not text:
            return _TelegramResult(False, "message text is empty")
        try:
            bot = await self._bot()
            for part in self._split_long_message(text):
                part_budget = budget or _RetryBudget.start()
                if pace is not None:
                    await self._wait_for_delivery_window(pace, part_budget)
                await self._retry_api(
                    partial(
                        bot.send_message,
                        chat_id=self._target_chat_id(chat_id),
                        text=part,
                    ),
                    budget=part_budget,
                    pace=pace,
                    side_effecting=True,
                )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return self._delivery_failure(error)

    async def send_file_to(
        self,
        chat_id: str,
        data: bytes,
        file_name: str,
        *,
        budget: _RetryBudget | None = None,
        pace: _StreamPreview | None = None,
    ) -> _TelegramResult:
        """Send bytes as a Telegram document."""
        if len(data) > _MAX_DOCUMENT_BYTES:
            return _TelegramResult(
                False,
                "file exceeds Telegram's 50 MiB limit",
            )
        try:
            bot = await self._bot()
            budget = budget or _RetryBudget.start()
            if pace is not None:
                await self._wait_for_delivery_window(pace, budget)
            await self._retry_api(
                lambda: bot.send_document(
                    chat_id=self._target_chat_id(chat_id),
                    document=io.BytesIO(data),
                    filename=file_name or "file",
                ),
                budget=budget,
                pace=pace,
                side_effecting=True,
            )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return self._delivery_failure(error)

    async def send_image_to(
        self,
        chat_id: str,
        data: bytes,
        file_name: str = "image",
        *,
        budget: _RetryBudget | None = None,
        pace: _StreamPreview | None = None,
    ) -> _TelegramResult:
        """Send bytes as an inline Telegram photo."""
        if len(data) > _MAX_PHOTO_BYTES:
            return _TelegramResult(
                False,
                "image exceeds Telegram's 10 MiB photo limit; use SendFile",
            )
        try:
            bot = await self._bot()
            budget = budget or _RetryBudget.start()
            if pace is not None:
                await self._wait_for_delivery_window(pace, budget)
            await self._retry_api(
                lambda: bot.send_photo(
                    chat_id=self._target_chat_id(chat_id),
                    photo=io.BytesIO(data),
                    filename=file_name or "image",
                ),
                budget=budget,
                pace=pace,
                side_effecting=True,
            )
            return _TelegramResult(True)
        except Exception as error:  # pylint: disable=broad-except
            return self._delivery_failure(error)

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
        *,
        budget: _RetryBudget | None = None,
        pace: _StreamPreview | None = None,
        side_effecting: bool = False,
    ) -> _T:
        from telegram.error import (
            BadRequest,
            NetworkError,
            RetryAfter,
            TimedOut,
        )

        budget = budget or _RetryBudget.start()
        while True:
            remaining = budget.deadline - time.monotonic()
            if remaining <= 0:
                raise _TelegramRetryBudgetExceeded(
                    "Telegram API retry budget was exhausted",
                )
            try:
                try:
                    async with asyncio.timeout(remaining):
                        return await operation()
                except TimeoutError as error:
                    raise _TelegramDeliveryUnknown(
                        "Telegram API request timed out; delivery is unknown",
                    ) from error
            except RetryAfter as error:
                seconds = self._retry_after_seconds(error)
                if pace is not None:
                    pace.retry_not_before = max(
                        pace.retry_not_before,
                        time.monotonic() + seconds,
                    )
                if seconds >= budget.deadline - time.monotonic():
                    raise _TelegramRetryBudgetExceeded(
                        "Telegram flood-limit wait exceeds the delivery "
                        "budget",
                    ) from error
                await asyncio.sleep(max(0.0, seconds))
            except NetworkError as error:
                # PTB models BadRequest as a NetworkError subclass even
                # though retrying a malformed Bot API request cannot help.
                if isinstance(error, BadRequest):
                    raise
                if side_effecting and isinstance(error, TimedOut):
                    raise _TelegramDeliveryUnknown(
                        "Telegram API request timed out; delivery is unknown",
                    ) from error
                budget.network_failures += 1
                if budget.network_failures >= _MAX_API_ATTEMPTS:
                    raise _TelegramDeliveryUnknown(
                        "Telegram API request failed; delivery is unknown",
                    ) from error
                delay = 2 ** (budget.network_failures - 1)
                if delay >= budget.deadline - time.monotonic():
                    raise _TelegramRetryBudgetExceeded(
                        "Telegram network retry exceeds the delivery budget",
                    ) from error
                await asyncio.sleep(delay)

    @staticmethod
    def _retry_after_seconds(error: BaseException) -> float:
        delay = getattr(error, "retry_after", 0.0)
        return max(
            0.0,
            delay.total_seconds()
            if isinstance(delay, timedelta)
            else float(delay),
        )

    def _safe_error(self, error: BaseException) -> str:
        text = str(error) or type(error).__name__
        if self._bot_token:
            text = text.replace(self._bot_token, "<redacted>")
        return text
