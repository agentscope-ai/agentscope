# -*- coding: utf-8 -*-
"""SSE envelope state machine.

Translates AgentScope ``EventType`` events into the frontend's
streaming envelope protocol. Tracks per-request state (text blocks,
reasoning blocks, tool calls) and emits the correct event sequence
that the frontend expects.

One instance per ``Runtime.run()`` invocation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

logger = logging.getLogger(__name__)


def _gen_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Envelope:
    """SSE envelope generation + state machine.

    Yields schema objects (``dict``) identical to what the frontend
    SSE parser expects. Each object carries a ``sequence_number`` so
    the frontend can detect gaps.

    The envelope translates these AgentScope events:
    - ``TEXT_BLOCK_START/DELTA/END`` → text message chunks
    - ``THINKING_BLOCK_START/DELTA/END`` → reasoning bubbles
    - ``TOOL_CALL_START/DELTA/END`` → plugin call card
    - ``TOOL_RESULT_START/TEXT_DELTA/END`` → plugin output card
    - ``MODEL_CALL_END`` → usage info
    """

    def __init__(self, session_id: str = "") -> None:
        self._response: dict[str, Any] = {
            "object": "response",
            "id": f"response_{uuid.uuid4().hex}",
            "created_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            ),
            "session_id": session_id,
            "status": "created",
            "output": [],
            "usage": {},
        }
        self._message_id = _gen_id()
        self._completed_message: dict[str, Any] = {
            "id": self._message_id,
            "object": "message",
            "type": "message",
            "role": "assistant",
            "name": "assistant",
            "content": [],
            "status": "in_progress",
        }
        self._message_started = False
        self._text_blocks: Dict[str, Dict[str, Any]] = {}
        self._reasoning_blocks: Dict[str, Dict[str, Any]] = {}
        self._tool_calls: Dict[str, Dict[str, Any]] = {}
        self._seq_counter = 0
        self._error_text: str | None = None
        self._finalized = False

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    def _tag_seq(self, obj: dict) -> dict:
        obj["sequence_number"] = self._next_seq()
        return obj

    # ------------------------------------------------------------------
    # Response lifecycle
    # ------------------------------------------------------------------

    async def emit_response_created(self) -> AsyncGenerator[dict, None]:
        self._response["status"] = "created"
        yield self._tag_seq(dict(self._response))
        self._response["status"] = "in_progress"
        yield self._tag_seq(dict(self._response))

    # ------------------------------------------------------------------
    # Text message finalize helper
    # ------------------------------------------------------------------

    def _should_finalize_text_message(self) -> bool:
        return self._message_started and len(
            self._completed_message["content"],
        ) > 0

    async def _finalize_text_message(self) -> AsyncGenerator[dict, None]:
        self._completed_message["status"] = "completed"
        self._response["output"].append(dict(self._completed_message))
        yield self._tag_seq(dict(self._completed_message))
        # Start a fresh message
        self._message_id = _gen_id()
        self._completed_message = {
            "id": self._message_id,
            "object": "message",
            "type": "message",
            "role": "assistant",
            "name": "assistant",
            "content": [],
            "status": "in_progress",
        }
        self._message_started = False
        self._text_blocks = {}

    # ------------------------------------------------------------------
    # Event translation
    # ------------------------------------------------------------------

    async def translate_event(
        self,
        event: Any,
    ) -> AsyncGenerator[dict, None]:
        """Translate an AgentScope event into real-time envelope dicts."""
        evt_type = getattr(event, "type", None)
        if hasattr(evt_type, "value"):
            evt_type = evt_type.value

        # Try to import EventType; fall back gracefully
        try:
            from agentscope.event import EventType
        except ImportError:
            EventType = None  # type: ignore

        # === TEXT BLOCK ===
        if self._match(evt_type, "text_block_start"):
            if not self._message_started:
                yield self._tag_seq(dict(self._completed_message))
                self._message_started = True
            block_id = getattr(event, "block_id", "default")
            index = len(self._text_blocks)
            self._text_blocks[block_id] = {"index": index, "text": ""}

        elif self._match(evt_type, "text_block_delta"):
            if not self._message_started:
                yield self._tag_seq(dict(self._completed_message))
                self._message_started = True
            block_id = getattr(event, "block_id", "default")
            delta = getattr(event, "delta", "") or ""
            state = self._text_blocks.setdefault(
                block_id,
                {"index": len(self._text_blocks), "text": ""},
            )
            state["text"] += delta
            chunk = {
                "type": "text",
                "text": delta,
                "delta": True,
                "index": state["index"],
                "msg_id": self._message_id,
            }
            yield self._tag_seq(chunk)

        elif self._match(evt_type, "text_block_end"):
            block_id = getattr(event, "block_id", "default")
            state = self._text_blocks.get(block_id)
            if state is None:
                return
            final = {
                "type": "text",
                "text": state["text"],
                "delta": False,
                "index": state["index"],
                "msg_id": self._message_id,
            }
            yield self._tag_seq(final)
            self._completed_message["content"].append(
                {"type": "text", "text": state["text"], "index": state["index"]},
            )

        # === THINKING BLOCK ===
        elif self._match(evt_type, "thinking_block_start"):
            if self._should_finalize_text_message():
                async for obj in self._finalize_text_message():
                    yield obj
            block_id = getattr(event, "block_id", "default")
            r_msg_id = _gen_id()
            r_envelope = {
                "id": r_msg_id,
                "object": "message",
                "type": "reasoning",
                "role": "assistant",
                "name": "assistant",
                "content": [],
                "status": "in_progress",
            }
            self._reasoning_blocks[block_id] = {
                "msg_id": r_msg_id,
                "envelope": r_envelope,
                "text": "",
            }
            yield self._tag_seq(dict(r_envelope))

        elif self._match(evt_type, "thinking_block_delta"):
            block_id = getattr(event, "block_id", "default")
            delta = getattr(event, "delta", "") or ""
            state = self._reasoning_blocks.get(block_id)
            if state is None:
                if self._should_finalize_text_message():
                    async for obj in self._finalize_text_message():
                        yield obj
                r_msg_id = _gen_id()
                r_envelope = {
                    "id": r_msg_id,
                    "object": "message",
                    "type": "reasoning",
                    "role": "assistant",
                    "name": "assistant",
                    "content": [],
                    "status": "in_progress",
                }
                state = {
                    "msg_id": r_msg_id,
                    "envelope": r_envelope,
                    "text": "",
                }
                self._reasoning_blocks[block_id] = state
                yield self._tag_seq(dict(r_envelope))
            state["text"] += delta
            chunk = {
                "type": "text",
                "text": delta,
                "delta": True,
                "index": 0,
                "msg_id": state["msg_id"],
            }
            yield self._tag_seq(chunk)

        elif self._match(evt_type, "thinking_block_end"):
            block_id = getattr(event, "block_id", "default")
            state = self._reasoning_blocks.get(block_id)
            if state is None:
                return
            final = {
                "type": "text",
                "text": state["text"],
                "delta": False,
                "index": 0,
                "msg_id": state["msg_id"],
            }
            yield self._tag_seq(final)
            state["envelope"]["content"].append(
                {"type": "text", "text": state["text"]},
            )
            state["envelope"]["status"] = "completed"
            self._response["output"].append(dict(state["envelope"]))
            yield self._tag_seq(dict(state["envelope"]))

        # === TOOL CALL ===
        elif self._match(evt_type, "tool_call_start"):
            if self._should_finalize_text_message():
                async for obj in self._finalize_text_message():
                    yield obj
            call_id = getattr(event, "tool_call_id", _gen_id("call"))
            msg_id = _gen_id()
            plugin_call = {
                "id": msg_id,
                "object": "message",
                "type": "plugin_call",
                "role": "assistant",
                "name": "assistant",
                "content": [],
                "status": "in_progress",
            }
            stub = {
                "type": "data",
                "data": {
                    "call_id": call_id,
                    "name": getattr(event, "tool_call_name", ""),
                    "arguments": "",
                },
                "delta": True,
                "index": 0,
                "msg_id": msg_id,
            }
            yield self._tag_seq(dict(plugin_call))
            yield self._tag_seq(stub)
            self._tool_calls[call_id] = {
                "name": getattr(event, "tool_call_name", ""),
                "argument_fragments": [],
                "message": plugin_call,
                "output_text_acc": "",
            }

        elif self._match(evt_type, "tool_call_delta"):
            call_id = getattr(event, "tool_call_id", "")
            state = self._tool_calls.get(call_id)
            if state is None:
                return
            delta = getattr(event, "delta", "") or ""
            state["argument_fragments"].append(delta)
            chunk = {
                "type": "data",
                "data": {"arguments": delta},
                "delta": True,
                "index": 0,
                "msg_id": state["message"]["id"],
            }
            yield self._tag_seq(chunk)

        elif self._match(evt_type, "tool_call_end"):
            call_id = getattr(event, "tool_call_id", "")
            state = self._tool_calls.get(call_id)
            if state is None:
                return
            arguments = "".join(state.pop("argument_fragments", []))
            final = {
                "type": "data",
                "data": {
                    "call_id": call_id,
                    "name": state["name"],
                    "arguments": arguments,
                },
                "delta": False,
            }
            state["message"]["content"].append(final)
            yield self._tag_seq(final)
            self._response["output"].append(dict(state["message"]))
            yield self._tag_seq(dict(state["message"]))

        # === TOOL RESULT ===
        elif self._match(evt_type, "tool_result_start"):
            call_id = getattr(event, "tool_call_id", "")
            state = self._tool_calls.get(call_id)
            if state is None:
                state = {
                    "name": getattr(event, "tool_call_name", ""),
                    "argument_fragments": [],
                    "output_text_acc": "",
                }
                self._tool_calls[call_id] = state
            out_msg_id = _gen_id()
            out_msg = {
                "id": out_msg_id,
                "object": "message",
                "type": "plugin_call_output",
                "role": "tool",
                "name": "assistant",
                "content": [],
                "status": "in_progress",
            }
            stub = {
                "type": "data",
                "data": {
                    "call_id": call_id,
                    "name": state["name"],
                    "output": "",
                },
                "delta": False,
                "index": 0,
                "msg_id": out_msg_id,
            }
            yield self._tag_seq(out_msg)
            yield self._tag_seq(stub)
            state["output_message"] = out_msg
            state["output_text_acc"] = ""

        elif self._match(evt_type, "tool_result_text_delta"):
            call_id = getattr(event, "tool_call_id", "")
            state = self._tool_calls.get(call_id)
            if state is None:
                return
            state["output_text_acc"] += getattr(event, "delta", "") or ""
            chunk = {
                "type": "data",
                "data": {
                    "call_id": call_id,
                    "name": state["name"],
                    "output": state["output_text_acc"],
                },
                "delta": True,
                "index": 0,
                "msg_id": state.get("output_message", {}).get("id", ""),
            }
            yield self._tag_seq(chunk)

        elif self._match(evt_type, "tool_result_end"):
            call_id = getattr(event, "tool_call_id", "")
            state = self._tool_calls.get(call_id)
            if state is None:
                return
            final = {
                "type": "data",
                "data": {
                    "call_id": call_id,
                    "name": state["name"],
                    "output": state.get("output_text_acc", ""),
                },
                "delta": False,
            }
            out_msg = state.get("output_message", {})
            if out_msg:
                out_msg["content"].append(final)
                out_msg["status"] = "completed"
                self._response["output"].append(dict(out_msg))
                yield self._tag_seq(final)
                yield self._tag_seq(dict(out_msg))

        # === MODEL CALL END ===
        elif self._match(evt_type, "model_call_end"):
            input_tokens = getattr(event, "input_tokens", 0)
            output_tokens = getattr(event, "output_tokens", 0)
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            self._response["usage"] = usage

        else:
            # Unknown event — log and skip
            logger.debug("envelope: unhandled event type=%s", evt_type)

    @staticmethod
    def _match(evt_type: Any, name: str) -> bool:
        """Match event type by string suffix (case-insensitive).

        AgentScope ``EventType`` values are uppercase (e.g.
        ``"TEXT_BLOCK_START"``); the envelope uses lowercase names
        internally.  Compare case-insensitively so both work.
        """
        if evt_type is None:
            return False
        if isinstance(evt_type, str):
            val = evt_type
        else:
            val = getattr(evt_type, "value", "")
        val = val.lower()
        return val == name or val.endswith(name)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(self) -> AsyncGenerator[dict, None]:
        yield self._tag_seq(dict(self._response))

    # ------------------------------------------------------------------
    # Slash command short-circuit
    # ------------------------------------------------------------------

    async def from_text(self, text: str) -> AsyncGenerator[dict, None]:
        """Translate a plain text response (e.g. slash command) into
        a full envelope sequence."""
        if not self._message_started:
            yield self._tag_seq(dict(self._completed_message))
            self._message_started = True
        chunk = {
            "type": "text",
            "text": text,
            "delta": False,
            "index": 0,
            "msg_id": self._message_id,
        }
        yield self._tag_seq(chunk)
        self._completed_message["content"].append(
            {"type": "text", "text": text},
        )
        self._completed_message["status"] = "completed"
        self._response["output"].append(dict(self._completed_message))
        yield self._tag_seq(dict(self._completed_message))
        self._response["status"] = "completed"
        self._response["completed_at"] = datetime.now(
            timezone.utc,
        ).isoformat(timespec="seconds")
        yield self._tag_seq(dict(self._response))
        self._finalized = True

    # ------------------------------------------------------------------
    # Error / Cancel / Finalize
    # ------------------------------------------------------------------

    async def error_envelope(
        self,
        error_text: str,
        error_code: str = "error",
    ) -> AsyncGenerator[dict, None]:
        self._error_text = error_text
        async for obj in self._finalize_response():
            yield obj

    async def cancel_envelope(self) -> AsyncGenerator[dict, None]:
        async for obj in self._finalize_response():
            yield obj

    async def finalize(self) -> AsyncGenerator[dict, None]:
        if self._finalized:
            return
        async for obj in self._finalize_response():
            yield obj

    async def _finalize_response(self) -> AsyncGenerator[dict, None]:
        if self._finalized:
            return
        if self._message_started:
            # Back-fill partial text
            if not self._completed_message["content"]:
                for state in self._text_blocks.values():
                    text = state.get("text", "")
                    if text:
                        self._completed_message["content"].append(
                            {"type": "text", "text": text},
                        )
            if self._completed_message["content"]:
                self._completed_message["status"] = "completed"
                self._response["output"].append(
                    dict(self._completed_message),
                )
                yield self._tag_seq(dict(self._completed_message))
        if self._error_text:
            self._response["status"] = "failed"
            self._response["error"] = {
                "code": "error",
                "message": self._error_text,
            }
        else:
            self._response["status"] = "completed"
        self._response["completed_at"] = datetime.now(
            timezone.utc,
        ).isoformat(timespec="seconds")
        yield self._tag_seq(dict(self._response))
        self._finalized = True

    @property
    def response(self) -> dict:
        return self._response


__all__ = ["Envelope"]
