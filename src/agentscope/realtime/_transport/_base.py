# -*- coding: utf-8 -*-
"""The transport base class."""
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import AsyncIterator

from pydantic import BaseModel

from .._playout import PlayoutPosition


class AudioFrame(BaseModel):
    """A chunk of upstream user audio."""

    pcm: bytes
    """PCM16 mono at the transport's :attr:`input_sample_rate`."""


class ControlFrameType(StrEnum):
    """The non-audio things a remote client can say.

    Each one is the wire form of a public :class:`RealtimeAgent` method,
    so that a browser can do what a Python caller does directly. Traffic
    a transport handles for itself — handshakes, playback reports — never
    reaches the agent and has no member here.
    """

    TEXT = "text"
    """A typed user turn."""

    USER_CONFIRM = "user_confirm"
    """The outcome of a tool permission prompt."""

    INTERRUPT = "interrupt"
    """The user pressed stop, as opposed to speaking over the reply."""

    EXTERNAL_EXECUTION_RESULT = "external_execution_result"
    """The result of a tool the client ran itself."""


class ControlFrame(BaseModel):
    """One upstream control event."""

    type: ControlFrameType
    data: dict = {}


TransportFrame = AudioFrame | ControlFrame


class TransportBase(ABC):
    """Connects a :class:`RealtimeAgent` to where audio comes from and
    goes to.

    A transport is a browser connection, a local sound card, or a queue
    the caller drives — the agent cannot tell the difference. It is owned
    by whoever created it, not by the agent; :meth:`RealtimeAgent.run`
    only borrows it.
    """

    input_sample_rate: int
    """The rate of the PCM delivered by :meth:`incoming`."""

    output_sample_rate: int
    """The rate :meth:`send_audio` expects."""

    async def __aenter__(self) -> "TransportBase":
        """Start on entry; the transport's owner is whoever entered."""
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close on exit."""
        await self.close()

    @abstractmethod
    async def start(self) -> None:
        """Open the transport and begin capturing."""

    @abstractmethod
    async def close(self) -> None:
        """Release every resource held by the transport."""

    @abstractmethod
    async def incoming(self) -> AsyncIterator[TransportFrame]:
        """Yield upstream frames until the transport closes."""
        raise NotImplementedError(f"{type(self).__name__}.incoming")
        yield  # pylint: disable=unreachable

    @abstractmethod
    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Queue assistant audio for playback.

        Args:
            pcm (`bytes`):
                PCM16 mono at :attr:`output_sample_rate`.
            item_id (`str`):
                The assistant item this audio belongs to, so that playback
                position can be attributed to it.
        """

    @abstractmethod
    async def send_event(self, event: dict) -> None:
        """Send one control event downstream."""

    @abstractmethod
    async def clear_audio(self) -> PlayoutPosition:
        """Cut playback short and report how much was actually heard.

        Fades out briefly to avoid an audible click, then discards queued
        and in-flight audio.

        Clearing and reporting are one call on purpose: the returned
        position is what the caller must feed to
        :meth:`VoiceBackendBase.truncate`, so the two cannot drift apart
        and the truncation cannot be forgotten.

        Returns:
            `PlayoutPosition`: What the user heard before the cut.
        """

    @abstractmethod
    def playout(self) -> PlayoutPosition:
        """The current playback position, without interrupting."""
