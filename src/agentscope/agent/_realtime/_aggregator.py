# -*- coding: utf-8 -*-
"""Turn aggregation."""
import time

_TRAILING = " 。，,.!?！？、"


class TurnAggregator:
    """Collapses the transcripts a provider emits into clean user turns.

    Turn boundaries in speech are inferred, and inferred wrongly often
    enough to matter: one sentence gets split across two turns when the
    speaker pauses, and a stray "mm-hmm" gets promoted to a turn of its
    own. Neither is visible to the provider once it has answered, but both
    end up in our own context, where they outlive the call.
    """

    def __init__(
        self,
        merge_window_ms: int = 800,
        backchannels: frozenset[str] = frozenset(),
        min_chars: int = 1,
    ) -> None:
        """Initialize the aggregator.

        Args:
            merge_window_ms (`int`, defaults to `800`):
                A transcript arriving this soon after the previous turn
                continues it rather than starting a new one.
            backchannels (`frozenset[str]`, optional):
                Acknowledgements that never constitute a turn. Language
                specific, so empty by default.
            min_chars (`int`, defaults to `1`):
                Transcripts shorter than this are dropped.
        """
        self.merge_window_ms = merge_window_ms
        self.backchannels = frozenset(backchannels)
        self.min_chars = min_chars
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
        if len(text) < self.min_chars:
            return None
        if text.strip(_TRAILING) in self.backchannels:
            return None

        now = time.monotonic()
        self._merges = (
            self._last_at is not None
            and (now - self._last_at) * 1000 <= self.merge_window_ms
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
