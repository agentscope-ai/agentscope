# -*- coding: utf-8 -*-
"""Message stream management module.

Implements a multi-producer multi-consumer message stream queue for passing
real-time messages between users and agents. Messages use AgentScope's Msg
format and support streaming push (incremental TextBlock/AudioBlock).
"""

import asyncio
import base64
from enum import Enum
from typing import Optional, AsyncGenerator, Literal, Dict
from collections import defaultdict

from agentscope._logging import logger
from agentscope.message import Msg, TextBlock, AudioBlock, Base64Source


class MsgEvent(Enum):
    """Message event types (passed via metadata)."""

    DATA = "data"  # Data message (default)
    SPEECH_END = "speech_end"  # User finished speaking (manual recording mode)
    RESPONSE_START = "response_start"  # Agent starts responding
    RESPONSE_END = "response_end"  # Agent response ended


def create_text_msg(
    name: str,
    text: str,
    role: Literal["assistant", "user", "system"] = "assistant",
    is_partial: bool = True,
    event: MsgEvent = MsgEvent.DATA,
) -> Msg:
    """Create a text message.

    Args:
        name (`str`):
            The sender's name.
        text (`str`):
            The text content (incremental).
        role (`Literal["assistant", "user", "system"]`, defaults to
        `"assistant"`):
            The role of the sender.
        is_partial (`bool`, defaults to `True`):
            Whether this is a partial message (streaming).
        event (`MsgEvent`, defaults to `MsgEvent.DATA`):
            The event type.

    Returns:
        `Msg`:
            The created message object.
    """
    return Msg(
        name=name,
        role=role,
        content=[TextBlock(type="text", text=text)] if text else [],
        metadata={
            "is_partial": is_partial,
            "event": event.value,
        },
    )


def create_audio_msg(
    name: str,
    audio_data: bytes,
    sample_rate: int = 24000,
    role: Literal["assistant", "user", "system"] = "assistant",
    is_partial: bool = True,
    event: MsgEvent = MsgEvent.DATA,
) -> Msg:
    """Create an audio message.

    Args:
        name (`str`):
            The sender's name.
        audio_data (`bytes`):
            The audio data (PCM bytes, incremental).
        sample_rate (`int`, defaults to `24000`):
            The audio sample rate in Hz.
        role (`Literal["assistant", "user", "system"]`, defaults to
        `"assistant"`):
            The role of the sender.
        is_partial (`bool`, defaults to `True`):
            Whether this is a partial message (streaming).
        event (`MsgEvent`, defaults to `MsgEvent.DATA`):
            The event type.

    Returns:
        `Msg`:
            The created message object.
    """
    audio_block = AudioBlock(
        type="audio",
        source=Base64Source(
            type="base64",
            media_type=f"audio/pcm;rate={sample_rate}",
            data=base64.b64encode(audio_data).decode("ascii"),
        ),
    )
    return Msg(
        name=name,
        role=role,
        content=[audio_block],
        metadata={
            "is_partial": is_partial,
            "event": event.value,
            "sample_rate": sample_rate,
        },
    )


def create_event_msg(
    name: str,
    event: MsgEvent,
    role: Literal["assistant", "user", "system"] = "assistant",
) -> Msg:
    """Create an event message (no actual data).

    Args:
        name (`str`):
            The sender's name.
        event (`MsgEvent`):
            The event type.
        role (`Literal["assistant", "user", "system"]`, defaults to
        `"assistant"`):
            The role of the sender.

    Returns:
        `Msg`:
            The created message object.
    """
    return Msg(
        name=name,
        role=role,
        content=[],
        metadata={
            "is_partial": False,
            "event": event.value,
        },
    )


def get_audio_from_msg(msg: Msg) -> Optional[bytes]:
    """Extract audio data from a message.

    Args:
        msg (`Msg`):
            The message object.

    Returns:
        `Optional[bytes]`:
            The audio data as bytes, or None if no audio found.
    """
    for block in msg.content:
        # AudioBlock is TypedDict, check using dict methods
        if isinstance(block, dict) and block.get("type") == "audio":
            source = block.get("source")
            if source and isinstance(source, dict) and source.get("data"):
                return base64.b64decode(source["data"])
    return None


def get_text_from_msg(msg: Msg) -> Optional[str]:
    """Extract text data from a message.

    Args:
        msg (`Msg`):
            The message object.

    Returns:
        `Optional[str]`:
            The text content, or None if no text found.
    """
    for block in msg.content:
        # TextBlock is TypedDict, check using dict methods
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text")
    return None


def get_event_from_msg(msg: Msg) -> MsgEvent:
    """Get event type from a message.

    Args:
        msg (`Msg`):
            The message object.

    Returns:
        `MsgEvent`:
            The event type. Returns MsgEvent.DATA if not specified.
    """
    event_str = msg.metadata.get("event", MsgEvent.DATA.value)
    try:
        return MsgEvent(event_str)
    except ValueError:
        return MsgEvent.DATA


def is_partial_msg(msg: Msg) -> bool:
    """Check if this is a partial message (streaming).

    Args:
        msg (`Msg`):
            The message object.

    Returns:
        `bool`:
            True if this is a partial message, False otherwise.
    """
    return msg.metadata.get("is_partial", False)


class MsgStream:
    """Message stream for multi-producer multi-consumer communication.

    Supports multiple producers pushing messages to the stream and multiple
    consumers subscribing to receive messages.

    Examples:
        Producer pushing streaming text:

        .. code-block:: python

            stream = MsgStream()

            # Producer pushing streaming text
            await stream.push(create_text_msg(
                name="assistant",
                text="Hello",  # Incremental text
                is_partial=True,
            ))

            # Producer pushing streaming audio
            await stream.push(create_audio_msg(
                name="assistant",
                audio_data=pcm_bytes,  # Incremental audio
                is_partial=True,
            ))

        Consumer subscribing and receiving messages:

        .. code-block:: python

            # Consumer subscribing and receiving messages
            async for msg in stream.subscribe("agent", exclude_names=[
            "agent"]):
                text = get_text_from_msg(msg)
                audio = get_audio_from_msg(msg)
                event = get_event_from_msg(msg)
    """

    def __init__(self, max_size: int = 1000) -> None:
        """Initialize the message stream.

        Args:
            max_size (`int`, defaults to `1000`):
                Maximum queue capacity to prevent memory overflow.
        """
        self._max_size = max_size
        self._subscribers: Dict[str, asyncio.Queue[Optional[Msg]]] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._stats: Dict[str, int] = defaultdict(int)

    async def push(self, msg: Msg) -> None:
        """Push a message to the stream.

        The message will be distributed to all active subscribers.
        If a subscriber's queue is full, the oldest message will be dropped.

        Args:
            msg (`Msg`):
                The message object to push.
        """
        if self._closed:
            logger.warning("MsgStream is closed, cannot push data")
            return

        async with self._lock:
            for subscriber_id, queue in self._subscribers.items():
                try:
                    if queue.qsize() >= self._max_size:
                        try:
                            queue.get_nowait()
                            self._stats["dropped"] += 1
                        except asyncio.QueueEmpty:
                            pass
                    await queue.put(msg)
                    self._stats["pushed"] += 1
                except Exception as e:
                    logger.error(
                        "Error pushing to subscriber %s: %s",
                        subscriber_id,
                        e,
                    )

    async def subscribe(
        self,
        subscriber_id: str,
        filter_names: Optional[list[str]] = None,
        exclude_names: Optional[list[str]] = None,
        filter_roles: Optional[list[str]] = None,
        exclude_roles: Optional[list[str]] = None,
    ) -> AsyncGenerator[Msg, None]:
        """Subscribe to the message stream.

        Args:
            subscriber_id (`str`):
                Unique identifier for the subscriber.
            filter_names (`Optional[list[str]]`, defaults to `None`):
                Only receive messages from these senders.
            exclude_names (`Optional[list[str]]`, defaults to `None`):
                Exclude messages from these senders (commonly used to
                exclude self).
            filter_roles (`Optional[list[str]]`, defaults to `None`):
                Only receive messages from these roles.
            exclude_roles (`Optional[list[str]]`, defaults to `None`):
                Exclude messages from these roles.

        Yields:
            `Msg`:
                Message objects that match the filter criteria.
        """
        queue: asyncio.Queue[Optional[Msg]] = asyncio.Queue(
            maxsize=self._max_size,
        )

        async with self._lock:
            if subscriber_id in self._subscribers:
                logger.warning(
                    "Subscriber %s already exists, replacing",
                    subscriber_id,
                )
            self._subscribers[subscriber_id] = queue
            logger.info("Subscriber %s registered", subscriber_id)

        try:
            while not self._closed:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.1)

                    if msg is None:
                        break

                    # Filter by sender names
                    if (
                        filter_names is not None
                        and msg.name not in filter_names
                    ):
                        continue
                    if exclude_names is not None and msg.name in exclude_names:
                        continue

                    # Filter by roles
                    if (
                        filter_roles is not None
                        and msg.role not in filter_roles
                    ):
                        continue
                    if exclude_roles is not None and msg.role in exclude_roles:
                        continue

                    self._stats["consumed"] += 1
                    yield msg

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    logger.info("Subscriber %s cancelled", subscriber_id)
                    break
        finally:
            async with self._lock:
                if subscriber_id in self._subscribers:
                    del self._subscribers[subscriber_id]
                    logger.info("Subscriber %s unregistered", subscriber_id)

    async def unsubscribe(self, subscriber_id: str) -> None:
        """Unsubscribe from the message stream.

        Args:
            subscriber_id (`str`):
                The subscriber ID to remove.
        """
        async with self._lock:
            if subscriber_id in self._subscribers:
                del self._subscribers[subscriber_id]
                logger.info("Subscriber %s unsubscribed", subscriber_id)

    async def register_queue(
        self,
        subscriber_id: str,
        queue: asyncio.Queue[Optional[Msg]],
    ) -> None:
        """Register a custom queue as a subscriber.

        This is a low-level method for advanced use cases where you need
        direct queue access instead of using the subscribe() generator.

        Args:
            subscriber_id (`str`):
                Unique identifier for the subscriber.
            queue (`asyncio.Queue[Optional[Msg]]`):
                The queue to register.
        """
        async with self._lock:
            self._subscribers[subscriber_id] = queue
            logger.info("Queue registered for subscriber %s", subscriber_id)

    async def unregister_queue(self, subscriber_id: str) -> None:
        """Unregister a previously registered queue.

        Args:
            subscriber_id (`str`):
                The subscriber ID to remove.
        """
        async with self._lock:
            if subscriber_id in self._subscribers:
                del self._subscribers[subscriber_id]
                logger.info(
                    "Queue unregistered for subscriber %s",
                    subscriber_id,
                )

    async def broadcast_event(
        self,
        name: str,
        event: MsgEvent,
        role: Literal["assistant", "user", "system"] = "assistant",
    ) -> None:
        """Broadcast an event message to all subscribers.

        Args:
            name (`str`):
                The sender's name.
            event (`MsgEvent`):
                The event type to broadcast.
            role (`Literal["assistant", "user", "system"]`, defaults to
            `"assistant"`):
                The role of the sender.
        """
        msg = create_event_msg(name=name, event=event, role=role)
        await self.push(msg)

    def get_subscriber_count(self) -> int:
        """Get the current number of subscribers.

        Returns:
            `int`:
                The number of active subscribers.
        """
        return len(self._subscribers)

    def get_stats(self) -> Dict[str, int]:
        """Get statistics about message flow.

        Returns:
            `Dict[str, int]`:
                Statistics including 'pushed', 'dropped', and 'consumed'
                counts.
        """
        return dict(self._stats)

    async def close(self) -> None:
        """Close the message stream.

        This will notify all subscribers and clean up resources.
        """
        self._closed = True

        async with self._lock:
            for queue in self._subscribers.values():
                try:
                    await queue.put(None)
                except Exception:
                    pass
            self._subscribers.clear()

        logger.info("MsgStream closed. Stats: %s", self._stats)

    @property
    def is_closed(self) -> bool:
        """Check if the stream is closed.

        Returns:
            `bool`:
                True if the stream is closed, False otherwise.
        """
        return self._closed
