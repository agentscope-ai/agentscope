# -*- coding: utf-8 -*-
"""Session-scoped estimate of the provider-side context occupancy.

Realtime sessions keep the working context on the provider's side; the
local ``state.context`` transcript is only a mirror of what has been
observed. This module tracks an *estimate* of how many tokens that
server-side context occupies, together with where the estimate came from
and whether it can still be trusted.

Deliberately NOT part of this module: compression scheduling. The
tracker only answers "what do we currently think the context occupies,
and how reliable is that number" — deciding when to act on it belongs
to the compression layer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Literal

from ..._utils._common import _estimate_tokens
from ...message import Msg


def estimate_context_tokens(messages: Sequence[Msg]) -> int:
    """Estimate the provider-facing transcript size from local messages.

    Only fields that describe the conversation are included. Runtime metadata,
    timestamps and permission bookkeeping are excluded so they do not consume
    an artificial share of the context budget.
    """
    if not messages:
        return 0

    transcript = [
        {
            "role": message.role,
            "name": message.name,
            "content": [
                block.model_dump(
                    mode="json",
                    exclude={
                        "created_at",
                        "finished_at",
                        "metadata",
                        "suggested_rules",
                    },
                )
                for block in message.content
            ],
        }
        for message in messages
    ]
    return _estimate_tokens(
        json.dumps(
            transcript,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


class UsageProvenance(str, Enum):
    """Where a context-usage estimate came from."""

    PROVIDER = "provider-reported"
    """The provider stated the number in a completion event."""

    ESTIMATED = "estimated"
    """Derived locally because the provider reported nothing usable."""


@dataclass
class ContextUsageObservation:
    """One trusted-enough estimate of the server-side context size."""

    estimate_tokens: int
    provenance: UsageProvenance
    observed_at: float
    is_stale: bool = False
    stale_reason: str | None = None
    pending_local_appends: int = 0


class ContextUsageTracker:
    """Track an estimate of the server-side context occupancy.

    Design constraints this class encodes (see issue #2566 discussion):

    - Provider ``input_tokens`` semantics differ per provider — the value
      may cover the full context processed for that response (OpenAI-style
      cumulative) or only the delta. The tracker therefore NEVER
      accumulates per-response input tokens; it keeps the latest
      observation and marks it stale as local content grows past it.
    - A missing input-token report must stay distinguishable from a report
      of zero. Output tokens alone cannot establish context occupancy.
    - The estimate is independent from :class:`TurnMetrics`: those are
      per-turn latency counters, this is session-level context state.
    """

    def __init__(self) -> None:
        self._observation: ContextUsageObservation | None = None
        self._local_appends_since_observation = 0

    @property
    def observation(self) -> ContextUsageObservation | None:
        """The latest observation, or ``None`` before the first one."""
        return self._observation

    @property
    def estimate_tokens(self) -> int | None:
        """Latest estimate, or ``None`` when nothing has been observed."""
        if self._observation is None:
            return None
        return self._observation.estimate_tokens

    @property
    def provenance(
        self,
    ) -> Literal[UsageProvenance.PROVIDER, UsageProvenance.ESTIMATED] | None:
        """Describe whether the current count is reported or estimated."""
        if self._observation is None:
            return None
        return self._observation.provenance

    @property
    def is_stale(self) -> bool:
        """True when the estimate can no longer be trusted as current."""
        if self._observation is None:
            return True
        return self._observation.is_stale

    @property
    def local_appends_since_observation(self) -> int:
        """Count local transcript appends since the last observation."""
        return self._local_appends_since_observation

    def observe_provider_report(
        self,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Fold one provider usage report into the estimate.

        Without input tokens, the previous observation is kept but marked
        stale — output usage alone cannot establish context occupancy.
        """
        del output_tokens  # Output tokens do not establish input occupancy.
        if input_tokens is None:
            self.mark_stale(
                "provider omitted input usage on response completion",
            )
            return

        # Input is the context-bearing side; output text may be retained by
        # some providers but billed-only audio (e.g. Qwen-Audio speech out)
        # must not be added — so output never grows the estimate here.
        self._observation = ContextUsageObservation(
            estimate_tokens=input_tokens,
            provenance=UsageProvenance.PROVIDER,
            observed_at=monotonic(),
        )
        self._local_appends_since_observation = 0

    def observe_estimate(self, estimate_tokens: int) -> None:
        """Replace the observation with a conservative transcript estimate.

        A fallback must not lower a previously observed provider count: the
        local transcript may omit provider-side instructions or cached state.
        """
        previous = (
            self._observation.estimate_tokens
            if self._observation is not None
            else 0
        )
        self._observation = ContextUsageObservation(
            estimate_tokens=max(0, estimate_tokens, previous),
            provenance=UsageProvenance.ESTIMATED,
            observed_at=monotonic(),
        )
        self._local_appends_since_observation = 0

    def note_local_append(self, count: int = 1) -> None:
        """Record content appended to the local transcript after the
        last observation. Accumulated appends sharpen the stale reason
        but do not fabricate an estimate."""
        if count <= 0 or self._observation is None:
            return
        self._local_appends_since_observation += count
        self._observation.is_stale = True
        self._observation.stale_reason = (
            f"{self._local_appends_since_observation} local appends "
            "since observation"
        )

    def mark_stale(self, reason: str) -> None:
        """Invalidate the current observation (interrupt, session
        recreation, truncation — anything that makes the last number
        unreliable)."""
        if self._observation is None:
            return
        self._observation.is_stale = True
        self._observation.stale_reason = reason

    def mark_baseline_untrusted(self, injected_messages: int) -> None:
        """Call after a (re)connect that injected prior history.

        The provider has not yet reported usage for the injected
        context, so whatever was observed before cannot be trusted as
        the new session's baseline.
        """
        self.mark_stale(
            f"history re-injected on connect ({injected_messages} messages); "
            "awaiting first provider usage report",
        )
