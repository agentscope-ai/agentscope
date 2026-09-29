# -*- coding: utf-8 -*-
"""The AI SDK (Vercel ``ai``) UI message stream middleware class.

Converts the AgentScope event stream into the "UI message stream" protocol that
the Vercel AI SDK's ``useChat`` consumes: newline-delimited ``data:`` frames,
each a ``{"type": ...}`` chunk, plus the ``x-vercel-ai-ui-message-stream: 1``
response header that tells the client to parse the body with that protocol.

Mapping (``AgentEvent`` → chunk):

* ``ReplyStartEvent`` → ``start``
* ``TextBlock*`` → ``text-start`` / ``text-delta`` / ``text-end``
* ``ThinkingBlock*`` → ``reasoning-start`` / ``reasoning-delta`` /
  ``reasoning-end``
* ``ToolCallStart/Delta/End`` → ``tool-input-start`` / ``tool-input-delta`` /
  ``tool-input-available`` (fragments buffered, then parsed)
* ``ToolResultStart/TextDelta/DataDelta`` → ``data-agentscope`` (the UI
  message stream has no chunk equivalent for these)
* ``ToolResultEndEvent`` → ``tool-output-available``
* ``ReplyEndEvent`` → ``finish`` (``error`` when the reply failed)
* anything else → ``data-agentscope`` carrying the raw event

Two protocol constraints shape this adapter, and both are worth stating rather
than hiding:

1. **Tool arguments arrive as JSON fragments.** The UI message stream wants a
   single ``tool-input-available`` carrying the *parsed* object, so fragments
   are buffered per ``(reply_id, tool_call_id)`` and parsed at
   ``ToolCallEndEvent``. Unparseable fragments emit an ``error`` chunk — never
   a malformed tool input.

2. **``_convert_to_protocol`` returns one dict per event**, so this adapter can
   emit exactly one frame per event. Two consequences follow, both inherited
   from the same constraint the AG-UI adapter documents for its two-level
   reasoning structure: a failed reply can emit ``error`` *or* the trailing
   ``finish``, not both; and ``TextBlockEndEvent``'s ``text`` (which can differ
   from the concatenated deltas for truncated replies) is dropped, because the
   UI message stream has no replace-text chunk. Widening the base hook to allow
   0..N chunks would let both cases be exact.
"""

from typing import TYPE_CHECKING, Any

import json

from starlette.types import ASGIApp
from starlette.requests import Request
from starlette.responses import Response

from ._base import ProtocolMiddlewareBase
from ....event import (
    AgentEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ExceedMaxItersEvent,
    ExternalExecutionResultEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from ....types import ReplyFinishedReason

if TYPE_CHECKING:
    from typing import Callable

#: The header the AI SDK checks to decide whether the body is a UI message
#: stream. Its presence is what makes ``useChat`` parse ``data:`` frames.
UI_MESSAGE_STREAM_HEADER = "x-vercel-ai-ui-message-stream"

#: Name used for events that have no chunk in the UI message stream protocol.
CUSTOM_DATA_PART = "data-agentscope"


class AISDKProtocolMiddleware(ProtocolMiddlewareBase):
    """Convert AgentScope events into the AI SDK UI message stream protocol."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the AI SDK protocol middleware.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)
        # Per-instance state, mirroring AGUIProtocolMiddleware: safe under
        # typical single-stream usage, not across concurrent requests.  Use
        # contextvars if concurrency is needed.
        self._tool_inputs: dict[tuple[str, str], list[str]] = {}
        self._tool_names: dict[tuple[str, str], str] = {}
        self._tool_outputs: dict[tuple[str, str], list[str]] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: "Callable",
    ) -> Response:
        """Convert the stream and advertise the protocol to the client."""
        response = await super().dispatch(request, call_next)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            response.headers[UI_MESSAGE_STREAM_HEADER] = "1"
        return response

    # pylint: disable=too-many-return-statements
    def _convert_to_protocol(self, event: AgentEvent) -> dict:
        """Convert one AgentScope event into one UI message stream chunk."""
        # --- reply lifecycle ------------------------------------------------
        if isinstance(event, ReplyStartEvent):
            return {"type": "start", "messageId": event.reply_id}

        if isinstance(event, ReplyEndEvent):
            if event.finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
                return {
                    "type": "error",
                    "errorText": "The agent exceeded the maximum "
                    "reasoning-acting iterations",
                }
            if event.error is not None:
                return {
                    "type": "error",
                    "errorText": str(event.error),
                }
            return {"type": "finish"}

        # --- text -----------------------------------------------------------
        if isinstance(event, TextBlockStartEvent):
            return {"type": "text-start", "id": event.block_id}

        if isinstance(event, TextBlockDeltaEvent):
            return {
                "type": "text-delta",
                "id": event.block_id,
                "delta": event.delta,
            }

        if isinstance(event, TextBlockEndEvent):
            # ``event.text`` may carry the final text when it differs from the
            # concatenated deltas; the UI message stream has no replace-text
            # chunk, so it cannot be forwarded (see module docstring).
            return {"type": "text-end", "id": event.block_id}

        # --- reasoning ------------------------------------------------------
        if isinstance(event, ThinkingBlockStartEvent):
            return {"type": "reasoning-start", "id": event.block_id}

        if isinstance(event, ThinkingBlockDeltaEvent):
            return {
                "type": "reasoning-delta",
                "id": event.block_id,
                "delta": event.delta,
            }

        if isinstance(event, ThinkingBlockEndEvent):
            return {"type": "reasoning-end", "id": event.block_id}

        # --- tool calls -----------------------------------------------------
        if isinstance(event, ToolCallStartEvent):
            key = (event.reply_id, event.tool_call_id)
            self._tool_names[key] = event.tool_call_name
            self._tool_inputs[key] = []
            return {
                "type": "tool-input-start",
                "toolCallId": event.tool_call_id,
                "toolName": event.tool_call_name,
            }

        if isinstance(event, ToolCallDeltaEvent):
            key = (event.reply_id, event.tool_call_id)
            self._tool_inputs.setdefault(key, []).append(event.delta)
            return {
                "type": "tool-input-delta",
                "toolCallId": event.tool_call_id,
                "inputTextDelta": event.delta,
            }

        if isinstance(event, ToolCallEndEvent):
            key = (event.reply_id, event.tool_call_id)
            raw = "".join(self._tool_inputs.pop(key, []))
            tool_name = self._tool_names.pop(key, "")
            return self._tool_input_available(
                event.tool_call_id,
                tool_name,
                raw,
            )

        # --- tool results ---------------------------------------------------
        if isinstance(event, ToolResultStartEvent):
            key = (event.reply_id, event.tool_call_id)
            self._tool_outputs[key] = []
            return self._custom("tool_result_start", event)

        if isinstance(event, ToolResultTextDeltaEvent):
            key = (event.reply_id, event.tool_call_id)
            self._tool_outputs.setdefault(key, []).append(event.delta)
            return self._custom("tool_result_text_delta", event)

        if isinstance(event, ToolResultDataDeltaEvent):
            return self._custom("tool_result_data_delta", event)

        if isinstance(event, ToolResultEndEvent):
            key = (event.reply_id, event.tool_call_id)
            output = "".join(self._tool_outputs.pop(key, []))
            return {
                "type": "tool-output-available",
                "toolCallId": event.tool_call_id,
                "output": output or str(event.state),
            }

        # --- everything else ------------------------------------------------
        if isinstance(event, ModelCallEndEvent):
            return self._custom("model_call_end", event)

        if isinstance(event, ModelCallStartEvent):
            return self._custom("model_call_start", event)

        if isinstance(event, ExceedMaxItersEvent):
            return self._custom("exceed_max_iters", event)

        if isinstance(
            event,
            (
                DataBlockStartEvent,
                DataBlockDeltaEvent,
                DataBlockEndEvent,
                RequireUserConfirmEvent,
                RequireExternalExecutionEvent,
                UserConfirmResultEvent,
                ExternalExecutionResultEvent,
            ),
        ):
            return self._custom(_event_name(event), event)

        return self._custom("unknown", event)

    # -- helpers -----------------------------------------------------------
    def _tool_input_available(
        self,
        tool_call_id: str,
        tool_name: str,
        raw: str,
    ) -> dict:
        """Emit the parsed tool input, or an error chunk if it is not JSON."""
        try:
            parsed: Any = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            return {
                "type": "error",
                "errorText": "Tool call "
                f"{tool_name or tool_call_id} produced arguments that are "
                f"not valid JSON: {exc}",
            }

        return {
            "type": "tool-input-available",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "input": parsed,
        }

    @staticmethod
    def _custom(name: str, event: AgentEvent) -> dict:
        """Wrap an event with no chunk equivalent as a custom data part."""
        return {
            "type": CUSTOM_DATA_PART,
            "data": {"event": name, **event.model_dump(exclude_none=True)},
        }


def _event_name(event: AgentEvent) -> str:
    """Snake-case the pydantic event class name for the data part."""
    name = type(event).__name__.removesuffix("Event")
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)
