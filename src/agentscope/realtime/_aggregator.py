# -*- coding: utf-8 -*-
"""Turn aggregation."""
import time

from pydantic import BaseModel, Field

_TRAILING = " 。，,.!?！？、"


class TurnAggregatorConfig(BaseModel):
    """How raw transcripts are collapsed into clean user turns."""

    merge_window_ms: int = Field(default=800, ge=0)
    """A transcript arriving this soon after the previous turn continues
    it rather than starting a new one."""

    backchannels: frozenset[str] = frozenset()
    """Acknowledgements that never constitute a turn. Language specific,
    so empty by default."""

    min_chars: int = Field(default=1, ge=0)


class TurnAggregator:
    """Collapses the transcripts a provider emits into clean user turns.

    Turn boundaries in speech are inferred, and inferred wrongly often
    enough to matter: one sentence gets split across two turns when the
    speaker pauses, and a stray "mm-hmm" gets promoted to a turn of its
    own. Neither is visible to the provider once it has answered, but both
    end up in our own context, where they outlive the call.
    """

    def __init__(self, config: TurnAggregatorConfig | None = None) -> None:
        """Initialize the aggregator.

        Args:
            config (`TurnAggregatorConfig | None`, optional):
                Merge window and backchannel list.
        """
        self.config = config or TurnAggregatorConfig()
        self._last_at: float | None = None
        self._merges = False

    def take(self, transcript: str) -> str | None:
        """Accept a settled transcript and decide whether it is a turn.

        Args:
            transcript (`str`):
                The transcript the provider settled on.

        Returns:
            `str | None`:
                The turn text, or ``None`` for an empty transcript or a
                bare acknowledgement.
        """
        text = transcript.strip()
        if len(text) < self.config.min_chars:
            return None
        if text.strip(_TRAILING) in self.config.backchannels:
            return None

        now = time.monotonic()
        self._merges = (
            self._last_at is not None
            and (now - self._last_at) * 1000 <= self.config.merge_window_ms
        )
        self._last_at = now
        return text

    def merges_with_previous(self) -> bool:
        """Whether the last :meth:`take` continued the previous turn."""
        return self._merges

    def reset(self) -> None:
        """Forget the previous turn, e.g. on reconnect."""
        self._last_at = None
        self._merges = False
