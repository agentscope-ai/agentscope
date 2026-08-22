# -*- coding: utf-8 -*-
"""AgentEvent → ACP ``session/update`` translation (DESIGN.md §10, §11).

One :class:`Translator` per session. Correlation is carried by ids the
core already mints (receipt invariants a/c):

- streamed text/thinking/data blocks: ``block_id`` → ``messageId``,
- tool call/result lifecycle: ``tool_call_id`` → ``toolCallId``,
- the turn: ``reply_id`` (no direct ACP field — the turn *is* the open
  ``session/prompt`` request).

Events with no clean ACP mapping (model-call boundaries, hint blocks,
custom events) translate to nothing; ``usage_update`` is omitted
entirely because ACP requires both ``used`` and ``size`` (context
window) and AgentScope only reports per-call token counts.
"""
import base64
import json
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from acp import (
    audio_block,
    image_block,
    resource_link_block,
    text_block,
    tool_content,
)
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallStart,
    ToolCallProgress,
)

from agentscope.event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    TextBlockDeltaEvent,
    ThinkingBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import ToolResultState

from .bridge import OpRegistry

SessionUpdate: TypeAlias = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress
)

# Presentation hint only: AgentScope builtin tool name → ACP ToolKind.
_TOOL_KIND = {
    "Read": "read",
    "Grep": "search",
    "Glob": "search",
    "Write": "edit",
    "Edit": "edit",
    "Bash": "execute",
    "PowerShell": "execute",
}

# ToolResultState → (ACP ToolCallStatus, receipt taxonomy label).
# ACP only has completed/failed; the finer-grained taxonomy label
# (invariant e) rides along in _meta.
_RESULT_STATUS = {
    ToolResultState.SUCCESS: ("completed", "completed"),
    ToolResultState.ERROR: ("failed", "failed"),
    ToolResultState.INTERRUPTED: ("failed", "interrupted"),
    ToolResultState.DENIED: ("failed", "denied"),
}


@dataclass
class _ToolCallBuffer:
    """Streaming state for one in-flight tool call."""

    name: str
    args: str = ""
    result_text: str = ""
    data_blocks: dict[str, list[str]] = field(default_factory=dict)
    data_media_types: dict[str, str] = field(default_factory=dict)
    data_urls: dict[str, str] = field(default_factory=dict)


def _parse_args(raw: str) -> Any:
    """Best-effort JSON parse of accumulated tool arguments."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


class Translator:
    """Stateful AgentEvent → session/update mapper for one session."""

    def __init__(self, ops: OpRegistry) -> None:
        self._ops = ops
        self._tool_calls: dict[str, _ToolCallBuffer] = {}
        # Streamed assistant-level data blocks (images/audio), buffered
        # until DATA_BLOCK_END because partial base64 is not renderable.
        self._data: dict[str, list[str]] = {}
        self._data_media_types: dict[str, str] = {}

    def translate(self, event: Any) -> list[SessionUpdate]:
        """Map one AgentEvent to zero or more session updates.

        Events with no handler have no per-event ACP mapping:

        - ReplyStart/ReplyEnd, ExceedMaxIters: turn boundaries — the
          server derives the stopReason from
          ``ReplyEndEvent.finished_reason``.
        - ModelCallStart/End: no faithful ``usage_update`` possible (no
          context-window size signal), so nothing is emitted.
        - HintBlockEvent: runtime-state injection hints are internal
          system-reminder text; rendering them would leak into the UI.
        - RequireUserConfirm/RequireExternalExecution and the resume
          inputs: handled by the server's permission bridge.
        - CustomEvent: no stable mapping.
        """
        handler = self._HANDLERS.get(type(event))
        if handler is None:
            return []
        return handler(self, event)

    def _text_delta(self, event: TextBlockDeltaEvent) -> list[SessionUpdate]:
        return [
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=text_block(event.delta),
                message_id=event.block_id,
            ),
        ]

    def _thinking_delta(
        self,
        event: ThinkingBlockDeltaEvent,
    ) -> list[SessionUpdate]:
        return [
            AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=text_block(event.delta),
                message_id=event.block_id,
            ),
        ]

    def _data_delta(self, event: DataBlockDeltaEvent) -> list[SessionUpdate]:
        self._data.setdefault(event.block_id, []).append(event.data)
        self._data_media_types[event.block_id] = event.media_type
        return []

    def _data_end(self, event: DataBlockEndEvent) -> list[SessionUpdate]:
        chunks = self._data.pop(event.block_id, [])
        media_type = self._data_media_types.pop(event.block_id, "")
        block = _data_to_content(chunks, media_type)
        if block is None:
            return []
        return [
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=block,
                message_id=event.block_id,
            ),
        ]

    def _tool_call_start(
        self,
        event: ToolCallStartEvent,
    ) -> list[SessionUpdate]:
        self._tool_calls[event.tool_call_id] = _ToolCallBuffer(
            name=event.tool_call_name,
        )
        return [
            ToolCallStart(
                session_update="tool_call",
                tool_call_id=event.tool_call_id,
                title=event.tool_call_name,
                kind=_TOOL_KIND.get(event.tool_call_name, "other"),
                status="pending",
            ),
        ]

    def _tool_call_delta(
        self,
        event: ToolCallDeltaEvent,
    ) -> list[SessionUpdate]:
        buf = self._tool_calls.get(event.tool_call_id)
        if buf is not None:
            buf.args += event.delta
        # ACP has no argument-delta update; hold until TOOL_CALL_END.
        return []

    def _tool_call_end(self, event: ToolCallEndEvent) -> list[SessionUpdate]:
        buf = self._tool_calls.get(event.tool_call_id)
        if buf is None:
            return []
        # Arguments are complete: register the pending operation so
        # the OpBindingMiddleware can claim it at execution time
        # (invariant c), and surface the final rawInput.
        self._ops.register_pending(
            event.tool_call_id,
            buf.name,
            buf.args,
        )
        return [
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=event.tool_call_id,
                raw_input=_parse_args(buf.args),
            ),
        ]

    def _tool_result_start(
        self,
        event: ToolResultStartEvent,
    ) -> list[SessionUpdate]:
        # Execution began (permission, if any, was resolved).
        self._tool_calls.setdefault(
            event.tool_call_id,
            _ToolCallBuffer(name=event.tool_call_name),
        )
        return [
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=event.tool_call_id,
                status="in_progress",
            ),
        ]

    def _tool_result_text_delta(
        self,
        event: ToolResultTextDeltaEvent,
    ) -> list[SessionUpdate]:
        buf = self._tool_calls.get(event.tool_call_id)
        if buf is None:
            return []
        buf.result_text += event.delta
        # ``content`` replaces the collection on every update, so
        # each update carries the accumulated text.
        return [
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=event.tool_call_id,
                content=[tool_content(text_block(buf.result_text))],
            ),
        ]

    def _tool_result_data_delta(
        self,
        event: ToolResultDataDeltaEvent,
    ) -> list[SessionUpdate]:
        buf = self._tool_calls.get(event.tool_call_id)
        if buf is None:
            return []
        # ``data`` and ``url`` are mutually exclusive: the core emits
        # data=None with url=... for URL-sourced DataBlocks. URLs are
        # one-shot, base64 payloads stream in chunks.
        if event.data is None:
            if event.url is not None:
                buf.data_urls[event.block_id] = event.url
            return []
        buf.data_blocks.setdefault(event.block_id, []).append(event.data)
        buf.data_media_types[event.block_id] = event.media_type
        return []

    def _tool_result_end(
        self,
        event: ToolResultEndEvent,
    ) -> list[SessionUpdate]:
        buf = self._tool_calls.pop(event.tool_call_id, None)
        self._ops.discard_pending(event.tool_call_id)
        status, taxonomy = _RESULT_STATUS.get(
            event.state,
            ("failed", "failed"),
        )
        content = []
        raw_output: Any = None
        if buf is not None:
            if buf.result_text:
                content.append(tool_content(text_block(buf.result_text)))
                raw_output = buf.result_text
            for block_id, chunks in buf.data_blocks.items():
                block = _data_to_content(
                    chunks,
                    buf.data_media_types.get(block_id, ""),
                )
                if block is not None:
                    content.append(tool_content(block))
            for url in buf.data_urls.values():
                content.append(
                    tool_content(resource_link_block(name=url, uri=url)),
                )
        return [
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=event.tool_call_id,
                status=status,
                content=content or None,
                raw_output=raw_output,
                field_meta={"agentscope": {"result_state": taxonomy}},
            ),
        ]

    _HANDLERS: dict[type, Any] = {
        TextBlockDeltaEvent: _text_delta,
        ThinkingBlockDeltaEvent: _thinking_delta,
        DataBlockDeltaEvent: _data_delta,
        DataBlockEndEvent: _data_end,
        ToolCallStartEvent: _tool_call_start,
        ToolCallDeltaEvent: _tool_call_delta,
        ToolCallEndEvent: _tool_call_end,
        ToolResultStartEvent: _tool_result_start,
        ToolResultTextDeltaEvent: _tool_result_text_delta,
        ToolResultDataDeltaEvent: _tool_result_data_delta,
        ToolResultEndEvent: _tool_result_end,
    }


def _data_to_content(chunks: list[str], media_type: str) -> Any:
    """Assemble buffered base64 chunks into an ACP content block."""
    chunks = [c for c in chunks if c is not None]
    if not chunks:
        return None
    data = "".join(chunks)
    if media_type.startswith("image/"):
        return image_block(data, media_type)
    if media_type.startswith("audio/"):
        return audio_block(data, media_type)
    # Other media: degrade to a short textual stand-in rather than
    # dropping the information entirely.
    try:
        size = len(base64.b64decode(data, validate=False))
    except (ValueError, TypeError):
        size = len(data)
    return text_block(f"[{media_type or 'binary'} data, {size} bytes]")
