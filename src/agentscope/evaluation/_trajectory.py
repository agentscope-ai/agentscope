# -*- coding: utf-8 -*-
"""Data contracts for event-based trajectory evaluation."""
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..event import AgentEvent
from ..message import Msg, Usage
from ..types import ErrorInfo, ReplyFinishedReason


class EvaluationCase(BaseModel):
    """One task fixture for a single logical reply, including its resumes."""

    id: str
    """Case identifier, unique within one evaluation run."""

    inputs: list[Msg]
    """Initial task messages; the execution callback owns agent setup."""

    reference: dict[str, Any] | None = None
    """JSON-serializable expectations interpreted by the chosen evaluators.
    None means no reference was supplied, not an automatically correct task.
    Custom evaluators may instead own a task-specific scoring predicate.
    """

    tool_schemas: list[dict] | None = None
    """Schemas for checking observed arguments. None means unavailable;
    an empty list supplies no schemas. Expected or forbidden tool use belongs
    in reference data or an evaluator-owned predicate, not this schema list.
    """


class Trajectory(BaseModel):
    """Observed execution of one reply, with explicit collection limits.

    Events do not expose internal model retries, the actual fallback model
    or whether a reported zero token count represents unavailable usage.
    This record must not be presented as complete billing or attempt data.
    """

    schema_version: Literal[1] = 1
    """Version of the serialized trajectory contract."""

    case_id: str
    """The originating evaluation case identifier."""

    inputs: list[Msg]
    """A copy of the original task messages."""

    session_id: str | None = None
    """Session bound by the first observed ReplyStartEvent, if available."""

    reply_id: str | None = None
    """Logical reply identifier shared across human-input resumes."""

    events: list[AgentEvent] = Field(default_factory=list)
    """Copied events in observation order, deduplicated by event ID."""

    reply: Msg | None = None
    """Message rebuilt from events, including intermediate tool blocks."""

    output: Msg | None = None
    """Final message explicitly yielded by the stream. None is not failure."""

    finished_reason: ReplyFinishedReason | None = None
    """Observed Agent termination reason, independent of stream closure."""

    awaiting_input: bool = False
    """Whether observed confirmation or external-execution requests remain."""

    collection_status: Literal[
        "collecting",
        "exhausted",
        "cancelled",
        "error",
    ] = "collecting"
    """Current or last subscription segment status, not task success."""

    collection_error: ErrorInfo | None = None
    """Collection-layer error, separate from an Agent error in reply."""

    duration_ms: float | None = None
    """Observed event lifecycle duration, including human waiting time.
    None indicates insufficient or inconsistent timestamps. This is not
    pure model or tool execution latency.
    """

    reported_usage: Usage | None = None
    """Sum of unique ModelCallEndEvent usage, or None when none was seen.
    Never add reply.usage or output.usage again. Compression calls and
    internal retries may be missing; a reported zero remains unverified.
    """

    usage_verified: Literal[False] = False
    """Event-only collection cannot verify complete or actual token usage."""

    diagnostics: list[str] = Field(default_factory=list)
    """Missing boundaries, orphaned deltas or other observation gaps."""


class MetricResult(BaseModel):
    """One metric and the evidence available for interpreting its value."""

    name: str
    """Metric name, distinguishing execution success from semantic quality."""

    value: float | None = None
    """Measured value, or None when the metric cannot be evaluated."""

    sample_count: int = Field(default=0, ge=0)
    """Number of eligible observations; zero does not imply a perfect rate."""

    reason: str | None = None
    """Required explanation from the evaluator when value is None."""

    details: dict[str, Any] = Field(default_factory=dict)
    """JSON-serializable evidence, exclusions, numerator and denominator."""


class EvaluationResult(BaseModel):
    """A case trajectory and its evaluator results, ready for reporting."""

    case_id: str
    """Identifier matching trajectory.case_id."""

    trajectory: Trajectory
    """The observed trajectory, which can still be waiting or incomplete."""

    metrics: list[MetricResult] = Field(default_factory=list)
    """Scores and explicit not-evaluable results for this case."""
