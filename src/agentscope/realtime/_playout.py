# -*- coding: utf-8 -*-
"""Playback position of assistant audio."""
from pydantic import BaseModel, Field


class PlayoutPosition(BaseModel):
    """How much assistant audio actually left the speaker — the only sound
    basis for truncating a barge-in."""

    item_id: str
    """The assistant item this position refers to."""

    played_ms: int = Field(default=0, ge=0)
    """Milliseconds of audio actually played out."""

    first_played_at: float | None = None
    """:func:`time.monotonic` when the first sample of this item reached
    the speaker — the only timestamp that reflects what the user hears."""
