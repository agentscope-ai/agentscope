# -*- coding: utf-8 -*-
"""Project AgentScope streaming events onto ACP session updates."""

import json
from time import monotonic
from typing import Any

from acp import (
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_agent_thought,
    update_tool_call,
)

from ..event import (
    AgentEvent,
    DataBlockStartEvent,
    TextBlockDeltaEvent,
    ThinkingBlockDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
)
from ..message import ToolResultState

_TOOL_UPDATE_INTERVAL = 1.0


def parse_input(value: str) -> Any:
    """Preserve invalid or incomplete JSON as text for display."""
    try:
        return json.loads(value)
    except ValueError:
        return value


class EventProjector:
    """Keep tool input and output snapshots for one ACP prompt."""

    def __init__(self) -> None:
        self._inputs: dict[str, str] = {}
        self._outputs: dict[str, list[str]] = {}
        self._last_output: dict[str, float] = {}

    def project(self, event: AgentEvent) -> Any | None:
        """Convert a supported event without duplicating final messages."""
        if isinstance(event, TextBlockDeltaEvent):
            return update_agent_message(text_block(event.delta))
        if isinstance(event, ThinkingBlockDeltaEvent):
            return update_agent_thought(text_block(event.delta))
        if isinstance(event, DataBlockStartEvent):
            return update_agent_message(
                text_block(f"[Binary output: {event.media_type}]"),
            )
        if isinstance(event, ToolCallStartEvent):
            self._inputs[event.tool_call_id] = ""
            return start_tool_call(
                event.tool_call_id,
                event.tool_call_name,
                status="pending",
            )
        if isinstance(event, ToolCallDeltaEvent):
            self._inputs[event.tool_call_id] = (
                self._inputs.get(event.tool_call_id, "") + event.delta
            )
        elif isinstance(event, ToolCallEndEvent):
            return update_tool_call(
                event.tool_call_id,
                raw_input=parse_input(
                    self._inputs.get(event.tool_call_id, ""),
                ),
            )
        elif isinstance(event, ToolResultStartEvent):
            self._outputs[event.tool_call_id] = []
            self._last_output.pop(event.tool_call_id, None)
            return update_tool_call(
                event.tool_call_id,
                status="in_progress",
            )
        elif isinstance(event, ToolResultTextDeltaEvent):
            # Interruption may emit a result without a preceding START.
            self._outputs.setdefault(event.tool_call_id, []).append(
                event.delta,
            )
            return self._progress_update(event.tool_call_id)
        elif isinstance(event, ToolResultDataDeltaEvent):
            # The text-only MVP makes omitted binary output explicit.
            self._outputs.setdefault(event.tool_call_id, []).append(
                f"\n[Binary tool output: {event.media_type}]",
            )
            return self._progress_update(event.tool_call_id)
        elif isinstance(event, ToolResultEndEvent):
            output = "".join(self._outputs.pop(event.tool_call_id, []))
            self._last_output.pop(event.tool_call_id, None)
            return update_tool_call(
                event.tool_call_id,
                status=(
                    "completed"
                    if event.state == ToolResultState.SUCCESS
                    else "failed"
                ),
                content=[tool_content(text_block(output))],
                raw_output=output,
            )
        return None

    def _progress_update(self, tool_call_id: str) -> Any | None:
        """Throttle replacement snapshots, allowing the first immediately.

        Updates are event-driven; there is no timer to flush a trailing chunk.
        Buffered output waits for another output event or tool completion.
        """
        now = monotonic()
        previous = self._last_output.get(tool_call_id)
        if previous is not None and now - previous < _TOOL_UPDATE_INTERVAL:
            return None
        self._last_output[tool_call_id] = now
        output = "".join(self._outputs[tool_call_id])
        return update_tool_call(
            tool_call_id,
            content=[tool_content(text_block(output))],
            raw_output=output,
        )
