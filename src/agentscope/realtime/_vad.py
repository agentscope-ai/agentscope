# -*- coding: utf-8 -*-
"""Voice activity detection."""
from abc import ABC, abstractmethod
from enum import StrEnum


class SpeechTransition(StrEnum):
    """A transition in voice activity, as detected by a VAD. Not an event
    class; the model-side ``SpeechStartedEvent`` is what a provider reports."""

    STARTED = "started"
    ENDED = "ended"


class VADBase(ABC):
    """Voice activity detection over a stream of PCM16 mono audio.

    Implementations are stateful: ``push`` is called with consecutive
    chunks and returns a transition only on the frame where it happens.
    """

    sample_rate: int
    """The PCM sample rate the implementation expects."""

    @abstractmethod
    def push(self, pcm: bytes) -> SpeechTransition | None:
        """Feed one chunk of audio and report a transition, if any.

        This is an edge detector, not a level query: it returns a value
        only on the chunk where speech starts or stops. Chunk sizes are
        whatever the transport produces, while models want a fixed frame
        (Silero v5 wants 512 samples), so implementations buffer and slice
        internally and report at most one transition per call.

        Args:
            pcm (`bytes`):
                PCM16 mono audio at :attr:`sample_rate`.

        Returns:
            `SpeechTransition | None`:
                The transition detected on this chunk, or ``None``.
        """

    @abstractmethod
    def reset(self) -> None:
        """Drop internal state. Called whenever the audio stream has a
        gap — a reconnect, or the microphone being unmuted — since the
        silence counters carried across it would be stale."""
