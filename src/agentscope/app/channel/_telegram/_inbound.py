# -*- coding: utf-8 -*-
"""Telegram inbound message filtering and normalisation."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import base64
from typing import Any, TYPE_CHECKING

from ...._logging import logger
from ....message import Base64Source, DataBlock, TextBlock
from .._base import ChannelEvent

if TYPE_CHECKING:
    from telegram import Message, Update

    from ._channel import TelegramChannel


_ALBUM_SETTLE_SECS = 0.8
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_SUPPORTED_CHAT_TYPES = ("private", "group", "supergroup")


class TelegramInboundNormalizer:
    """Own inbound normalisation and all delayed album work for one run."""

    def __init__(self, channel: "TelegramChannel") -> None:
        self._channel = channel
        self._closed = False
        self._user_name_cache: dict[str, str] = {}
        self._album_messages: dict[
            tuple[str, str, str],
            list["Message"],
        ] = {}
        self._album_tasks: dict[
            tuple[str, str, str],
            asyncio.Task[None],
        ] = {}
        self._all_album_tasks: set[asyncio.Task[None]] = set()

    @property
    def album_messages(self) -> dict[tuple[str, str, str], list["Message"]]:
        """Expose buffered albums for focused lifecycle tests."""
        return self._album_messages

    @property
    def album_tasks(
        self,
    ) -> dict[tuple[str, str, str], asyncio.Task[None]]:
        """Expose current settle tasks for focused lifecycle tests."""
        return self._album_tasks

    async def handle(self, update: "Update") -> None:
        """Filter, normalise and emit one Telegram update."""
        if self._closed:
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or user.is_bot:
            return
        if str(message.chat.type) not in _SUPPORTED_CHAT_TYPES:
            return
        try:
            self._channel._remember_chat(message.chat)
            if message.media_group_id and self.downloadable(message):
                self.buffer_album(message)
                return
            if self.gated_out(message):
                return
            event = await self.normalise_messages([message])
            if event is not None and not self._closed:
                await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Telegram channel '%s' failed to process a message",
                self._channel.channel_id,
            )

    async def _emit(self, event: ChannelEvent) -> None:
        emit = self._channel._emit
        if emit is not None:
            await emit(event)
            self._channel.status.state = "connected"
            self._channel.status.last_error = ""

    def buffer_album(self, message: "Message") -> None:
        """Debounce album parts and retain every task until it finishes."""
        if self._closed:
            return
        user_id = str(message.from_user.id) if message.from_user else ""
        key = (str(message.chat_id), user_id, str(message.media_group_id))
        self._album_messages.setdefault(key, []).append(message)
        previous = self._album_tasks.get(key)
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(
            self._flush_album(key),
            name=f"telegram-album:{message.media_group_id}",
        )
        self._album_tasks[key] = task
        self._all_album_tasks.add(task)
        task.add_done_callback(self._all_album_tasks.discard)

    async def _flush_album(self, key: tuple[str, str, str]) -> None:
        task = asyncio.current_task()
        try:
            await asyncio.sleep(_ALBUM_SETTLE_SECS)
            messages = self._album_messages.pop(key, [])
            if not messages or all(self.gated_out(msg) for msg in messages):
                return
            event = await self.normalise_messages(messages)
            if event is not None and not self._closed:
                await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Telegram channel '%s' failed to process an album",
                self._channel.channel_id,
            )
        finally:
            if self._album_tasks.get(key) is task:
                self._album_tasks.pop(key, None)

    async def aclose(self) -> None:
        """Idempotently reject new work and await cancellation of albums."""
        if self._closed and not self._all_album_tasks:
            return
        self._closed = True
        tasks = list(self._all_album_tasks)
        self._album_tasks.clear()
        self._album_messages.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._all_album_tasks.clear()

    async def normalise_messages(
        self,
        messages: list["Message"],
    ) -> ChannelEvent | None:
        """Convert one message or album into one AgentScope event."""
        first = messages[0]
        content: list[TextBlock | DataBlock] = []
        for message in messages:
            block = await self.download_media(message)
            if block is not None:
                content.append(block)

        if len(messages) == 1:
            structured = self.structured_text(first)
            if structured:
                content.append(TextBlock(text=structured))

        text = ""
        for message in messages:
            raw = message.text or message.caption or ""
            if raw:
                text = self.strip_bot_mention(message, raw).strip()
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
        return ChannelEvent(
            channel_id=self._channel.channel_id,
            channel_user_id=user_id,
            channel_user_name=user_name,
            chat_id=str(chat.id),
            chat_name=self._channel._chat_display_name(chat),
            channel_message_id=str(first.message_id),
            content=content,
            metadata={"chat_type": str(chat.type)},
        )

    def gated_out(self, message: "Message") -> bool:
        """Apply group mention/reply filtering."""
        chat_type = str(message.chat.type)
        if chat_type not in ("group", "supergroup"):
            return False
        if not self._channel._config.only_at_reply:
            return False
        reply = message.reply_to_message
        if (
            reply is not None
            and reply.from_user is not None
            and str(reply.from_user.id) == self._channel._bot_id
        ):
            return False
        return not self.mentions_bot(message)

    def mentions_bot(self, message: "Message") -> bool:
        """Return whether Telegram entities address this bot."""
        username = str(
            getattr(self._channel._bot_user, "username", "") or "",
        )
        for entity, value in self.parsed_entities(message).items():
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
                and str(mentioned_user.id) == self._channel._bot_id
            ):
                return True
        return False

    def strip_bot_mention(self, message: "Message", text: str) -> str:
        """Strip the bot target while retaining an addressed command."""
        username = str(
            getattr(self._channel._bot_user, "username", "") or "",
        )
        for entity, value in self.parsed_entities(message).items():
            entity_type = str(entity.type)
            mentioned_user = getattr(entity, "user", None)
            is_bot = (
                entity_type == "mention"
                and username
                and value.casefold() == f"@{username}".casefold()
            ) or (
                entity_type == "text_mention"
                and mentioned_user is not None
                and str(mentioned_user.id) == self._channel._bot_id
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
    def parsed_entities(message: "Message") -> dict[Any, str]:
        """Read text or caption entities with Telegram's UTF-16 rules."""
        if message.text:
            return message.parse_entities()
        if message.caption:
            return message.parse_caption_entities()
        return {}

    @staticmethod
    def downloadable(message: "Message") -> bool:
        """Return whether a message carries a downloadable attachment."""
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

    async def download_media(
        self,
        message: "Message",
    ) -> TextBlock | DataBlock | None:
        """Download one attachment or return an explanatory text block."""
        selected = self.select_media(message)
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
            telegram_file = await self._channel._retry_api(media.get_file)
            raw = bytes(
                await self._channel._retry_api(
                    telegram_file.download_as_bytearray,
                ),
            )
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Telegram channel '%s' could not download %s: %s",
                self._channel.channel_id,
                name,
                self._channel._safe_error(error),
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
    def select_media(message: "Message") -> tuple[Any, str, str] | None:
        """Choose Telegram's best attachment and stable metadata."""
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
            return TelegramInboundNormalizer.select_sticker(message.sticker)
        return None

    @staticmethod
    def select_sticker(sticker: Any) -> tuple[Any, str, str]:
        """Choose a MIME type and filename for a Telegram sticker."""
        if sticker.is_animated:
            return sticker, "application/x-tgsticker", "sticker.tgs"
        if sticker.is_video:
            return sticker, "video/webm", "sticker.webm"
        return sticker, "image/webp", "sticker.webp"

    @staticmethod
    def structured_text(message: "Message") -> str:
        """Render location, venue and contact data without leaking vCards."""
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
            name = " ".join(
                part
                for part in (contact.first_name, contact.last_name or "")
                if part
            )
            lines = [
                "[Telegram contact]",
                f"name: {name}",
                f"phone_number: {contact.phone_number}",
            ]
            if contact.user_id is not None:
                lines.append(f"user_id: {contact.user_id}")
            return "\n".join(lines)
        return ""


__all__ = ["TelegramInboundNormalizer"]
