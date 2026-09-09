# -*- coding: utf-8 -*-
"""Per-turn latency metrics."""
from pydantic import BaseModel, Field


class TurnMetrics(BaseModel):
    """The four timestamps of one turn, and the delays derived from them.

    All timestamps are :func:`time.monotonic` readings, in seconds.
    """

    user_speech_end_at: float | None = None
    """The user stopped speaking."""

    turn_committed_at: float | None = None
    """Endpointing settled and the turn was handed to the backend."""

    backend_first_audio_at: float | None = None
    """The backend produced its first audio byte."""

    first_audio_played_at: float | None = None
    """That audio actually reached the speaker. Reported by the transport,
    and the only timestamp that reflects what the user perceives."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def endpointing_delay(self) -> float | None:
        """Time spent deciding the user had finished."""
        return self._delta(self.user_speech_end_at, self.turn_committed_at)

    @property
    def backend_ttfb(self) -> float | None:
        """Time the backend took to start answering."""
        return self._delta(
            self.turn_committed_at,
            self.backend_first_audio_at,
        )

    @property
    def transport_delay(self) -> float | None:
        """Time between audio leaving the server and reaching the ear."""
        return self._delta(
            self.backend_first_audio_at,
            self.first_audio_played_at,
        )

    @property
    def e2e_latency(self) -> float | None:
        """The user stopped speaking until they heard an answer."""
        return self._delta(
            self.user_speech_end_at,
            self.first_audio_played_at,
        )

    @staticmethod
    def _delta(start: float | None, end: float | None) -> float | None:
        """The gap between two optional timestamps."""
        if start is None or end is None:
            return None
        return end - start
