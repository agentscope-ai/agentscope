# -*- coding: utf-8 -*-
"""Side-channel collection of one logical reply's events."""
from datetime import datetime
from typing import Literal

from ..event import AgentEvent, EventType
from ..message import Msg, Usage
from ..types import ErrorInfo, ReplyFinishedReason
from ._trajectory import EvaluationCase, Trajectory


class TrajectoryCollector:
    """Observe a logical reply across human-input resumes.

    This collector never controls Agent execution. Snapshots own their data
    and remain available after subscription cancellation.
    """

    def __init__(self, case: EvaluationCase) -> None:
        """Copy a case's input without creating or running an Agent."""
        self._trajectory = Trajectory(
            case_id=case.id,
            inputs=[msg.model_copy(deep=True) for msg in case.inputs],
        )
        self._event_ids: set[str] = set()
        self._awaiting: set[str] = set()
        self._started_at: datetime | None = None
        self._last_at: datetime | None = None
        self._invalid_timestamps = False

    def record(self, item: AgentEvent | Msg) -> None:
        """Copy an event or final message, rejecting other logical replies.

        Replayed event IDs are ignored. Distinct calls to the same tool stay
        distinct. Raw orphan events are preserved with diagnostics. Activity
        after an observed terminal event is retained for inspection but does
        not reopen the reply or contribute to its usage or rebuilt message.
        """
        if not isinstance(item, (AgentEvent, Msg)):
            raise TypeError("Expected an AgentEvent or Msg.")
        trajectory = self._trajectory
        reply_id = (
            item.id
            if isinstance(item, Msg)
            else getattr(
                item,
                "reply_id",
                None,
            )
        )
        session_id = getattr(item, "session_id", None)
        if trajectory.reply_id is not None and reply_id not in (
            None,
            trajectory.reply_id,
        ):
            raise ValueError("Cannot collect events from another reply.")
        if trajectory.session_id is not None and session_id not in (
            None,
            trajectory.session_id,
        ):
            raise ValueError("Cannot collect events from another session.")
        if isinstance(item, Msg):
            trajectory.reply_id = reply_id
            trajectory.output = item.model_copy(deep=True)
            if trajectory.finished_reason is None:
                trajectory.finished_reason = item.finished_reason
            if trajectory.finished_reason is not None:
                self._awaiting.clear()
                trajectory.awaiting_input = False
            return
        if item.id in self._event_ids:
            return
        event = item.model_copy(deep=True)
        self._event_ids.add(event.id)
        trajectory.events.append(event)
        if trajectory.finished_reason is not None:
            if event.type != EventType.CUSTOM:
                self._diagnose(
                    "New event after reply termination: " + event.id,
                )
            return

        trajectory.collection_status = "collecting"
        trajectory.collection_error = None
        if event.type == EventType.CUSTOM:
            return
        trajectory.reply_id = reply_id
        if session_id is not None:
            trajectory.session_id = session_id
        if event.type == EventType.REPLY_START:
            if trajectory.reply is None:
                trajectory.reply = Msg(
                    id=event.reply_id,
                    name=event.name,
                    role=event.role,
                    content=[],
                    created_at=event.created_at,
                )
            else:
                trajectory.reply.name = event.name
            self._update_time(event.created_at, start=True)
        else:
            if trajectory.reply is None:
                self._diagnose("Missing ReplyStartEvent.")
                trajectory.reply = Msg(
                    id=event.reply_id,
                    name="",
                    role="assistant",
                    content=[],
                    created_at=event.created_at,
                )
            self._update_time(event.created_at)
            self._diagnose_orphan(event)
            # append_event can update an embedded external result. Preserve
            # the independently recorded raw event.
            trajectory.reply.append_event(event.model_copy(deep=True))

        if event.type == EventType.MODEL_CALL_END:
            usage = Usage(
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cache_input_tokens=event.cache_input_tokens,
                cache_creation_input_tokens=event.cache_creation_input_tokens,
            )
            if trajectory.reported_usage is None:
                trajectory.reported_usage = usage
            else:
                for field in (
                    "input_tokens",
                    "output_tokens",
                    "cache_input_tokens",
                    "cache_creation_input_tokens",
                ):
                    setattr(
                        trajectory.reported_usage,
                        field,
                        getattr(trajectory.reported_usage, field)
                        + getattr(usage, field),
                    )
        self._update_waiting(event)
        if event.type == EventType.REPLY_END:
            trajectory.finished_reason = ReplyFinishedReason(
                event.finished_reason,
            )
            self._awaiting.clear()
        trajectory.awaiting_input = bool(self._awaiting)

    def _diagnose(self, message: str) -> None:
        """Keep each diagnostic once."""
        if message not in self._trajectory.diagnostics:
            self._trajectory.diagnostics.append(message)

    def _diagnose_orphan(self, event: AgentEvent) -> None:
        """Detect deltas that Msg.append_event would otherwise only log."""
        block_type = None
        block_id = getattr(event, "block_id", None)
        kind = str(event.type)
        if kind.startswith("TOOL_CALL_") and not kind.endswith("START"):
            block_type, block_id = "tool_call", event.tool_call_id
        elif kind.startswith("TOOL_RESULT_") and not kind.endswith("START"):
            block_type, block_id = "tool_result", event.tool_call_id
        elif kind.endswith(("_DELTA", "_END")):
            block_type = {
                "TEXT": "text",
                "THINKING": "thinking",
                "DATA": "data",
            }.get(kind.split("_", maxsplit=1)[0])
        reply = self._trajectory.reply
        if (
            block_type
            and reply is not None
            and not any(
                block.type == block_type and block.id == block_id
                for block in reply.content
            )
        ):
            self._diagnose(f"Orphaned {event.type}: {block_id}")

    def _update_waiting(self, event: AgentEvent) -> None:
        """Track pending outside interaction by call ID."""
        if event.type in (
            EventType.REQUIRE_USER_CONFIRM,
            EventType.REQUIRE_EXTERNAL_EXECUTION,
        ):
            self._awaiting.update(call.id for call in event.tool_calls)
        elif event.type == EventType.USER_CONFIRM_RESULT:
            self._awaiting.difference_update(
                result.tool_call.id for result in event.confirm_results
            )
        elif event.type == EventType.EXTERNAL_EXECUTION_RESULT:
            self._awaiting.difference_update(
                result.id for result in event.execution_results
            )
        elif event.type in (
            EventType.TOOL_RESULT_START,
            EventType.TOOL_RESULT_END,
        ):
            self._awaiting.discard(event.tool_call_id)

    def _update_time(self, value: str, start: bool = False) -> None:
        """Measure observed lifecycle time; invalid timestamps stay unknown."""
        if self._invalid_timestamps:
            return
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if self._last_at is not None and timestamp < self._last_at:
                raise ValueError("Non-monotonic event timestamps.")
            if start and self._started_at is None:
                self._started_at = timestamp
            elif self._started_at is not None:
                self._trajectory.duration_ms = (
                    timestamp - self._started_at
                ).total_seconds() * 1000
            self._last_at = timestamp
        except (TypeError, ValueError):
            self._invalid_timestamps = True
            self._trajectory.duration_ms = None
            self._diagnose("Inconsistent event timestamps; duration unknown.")

    def end_segment(
        self,
        reason: Literal["exhausted", "cancelled", "error"],
        error: ErrorInfo | None = None,
    ) -> None:
        """End observation without inventing an Agent termination event.

        Exhaustion after a confirmation request leaves the reply waiting.
        Subscription cancellation alone does not imply Agent interruption.
        """
        if reason not in ("exhausted", "cancelled", "error"):
            raise ValueError("Invalid collection segment outcome.")
        if error is not None and reason != "error":
            raise ValueError("Collection errors require reason='error'.")
        self._trajectory.collection_status = reason
        self._trajectory.collection_error = (
            error.model_copy(deep=True) if error is not None else None
        )

    def snapshot(self) -> Trajectory:
        """Copy observations without closing the collection segment."""
        return self._trajectory.model_copy(deep=True)
