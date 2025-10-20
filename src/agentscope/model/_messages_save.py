# -*- coding: utf-8 -*-
"""Messages save utilities for chat models.

This module provides functionality to save model conversations for data collection.
Each record corresponds to a single model invocation and contains the input messages 
and available tools.

Design goals:
- Minimal dependencies and simple file IO
- Async-friendly API (but uses sync file writes for portability)
- Safe to call frequently; append-only JSONL
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ._model_response import ChatResponse
from .._logging import logger


@dataclass
class MessagesRecord:
    """A single messages data record to be written as JSONL.

    Notes
    -----
    - "messages" and "tools" are stored as JSON-encoded strings to make the
      downstream ingestion consistent with common data pipelines that expect
      string fields in tabular formats.
    """

    messages: list[dict]
    tools: list[dict] | None
    metadata: dict[str, Any] | None = None

    def to_jsonl(self) -> str:
        payload = {
            "messages": json.dumps(self.messages, ensure_ascii=False),
            "tools": json.dumps(self.tools or [], ensure_ascii=False),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, ensure_ascii=False)


class MessagesDataCollector:
    """Append-only JSONL data collector for messages records.

    Parameters
    ----------
    output_path:
        The file path to write JSONL lines to. Parent directories will be
        created if not present.
    enable_collection:
        Global switch to enable/disable writing.
    """

    def __init__(
        self,
        output_path: str,
        enable_collection: bool = True,
    ) -> None:
        self.output_path = output_path
        self.enable_collection = enable_collection

        # Ensure directory exists early to fail fast on permission issues
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)

    async def collect(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a single model call as one JSONL line.

        This function is async for ergonomic symmetry, but uses synchronous
        file IO for portability and simplicity (atomic append behavior on POSIX
        systems).
        """
        if not self.enable_collection:
            return

        record = MessagesRecord(
            messages=messages,
            tools=tools or [],
            metadata={
                **(metadata or {}),
                "collected_at": datetime.utcnow().isoformat() + "Z",
            },
        )

        line = record.to_jsonl()
        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class MessagesSaveMixin:
    """Mixin class to add messages saving functionality to chat models.
    
    This mixin provides the core functionality for saving model conversations
    to JSONL files. It should be mixed into ChatModelBase or its subclasses.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._messages_collector: MessagesDataCollector | None = None

    def _setup_messages_save(self, save_messages: bool, save_path: str | None) -> None:
        """Setup messages saving functionality.
        
        Args:
            save_messages: Whether to enable messages saving
            save_path: Path to save messages data (required if save_messages is True)
        """
        if save_messages:
            if not save_path:
                raise ValueError("save_path must be provided when save_messages is True")
            self._messages_collector = MessagesDataCollector(
                output_path=save_path, 
                enable_collection=True
            )

    def _build_complete_conversation(
        self, 
        input_messages: list[dict], 
        response: ChatResponse, 
        tools: list[dict] | None
    ) -> list[dict]:
        """Build complete conversation including assistant response."""
        from ..message import TextBlock, ToolUseBlock, ThinkingBlock
        
        # Start with input messages
        complete_messages = input_messages.copy()
        
        # Extract assistant response content
        assistant_content = []
        tool_calls = []
        reasoning_content = []
        
        for block in response.content:
            # Ensure we are handling a mapping-like block and surface content on failure
            assert isinstance(block, dict), f"Unexpected block type: {type(block)} value={block!r}"
            block_type = block.get("type")
            if block_type == "text":
                # Coerce None to empty string; other types to string to be safe
                val = block.get("text")
                assistant_content.append("") if val is None else assistant_content.append(str(val))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": block["input"]
                    }
                })
            elif block_type == "thinking":
                # Store thinking content separately, coerced to string
                val = block.get("thinking")
                reasoning_content.append("") if val is None else reasoning_content.append(str(val))
        
        # Create assistant message
        assistant_message = {
            "role": "assistant",
            "content": "\n".join(assistant_content) if assistant_content else None
        }
        
        # Add reasoning content if any
        if reasoning_content:
            assistant_message["reasoning_content"] = "\n".join(reasoning_content)
        
        # Add tool calls if any
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        
        complete_messages.append(assistant_message)
        
        return complete_messages

    async def _save_messages_if_enabled(
        self,
        messages: list[dict],
        response: ChatResponse,
        tools: list[dict] | None,
        tool_choice: str | None = None,
        stream: bool = False,
    ) -> None:
        """Save messages if collection is enabled."""
        if not self._messages_collector:
            return

        try:
            # Build complete conversation with assistant response
            complete_messages = self._build_complete_conversation(
                messages, response, tools
            )
            await self._messages_collector.collect(
                messages=complete_messages,
                tools=tools or [],
                metadata={
                    "model_name": self.model_name,
                    "tool_choice": tool_choice,
                    "stream": stream,
                },
            )
        except Exception as e:
            logger.warning("Messages save failed: %s", e)


def enable_messages_save(
    model: "ChatModelBase",
    save_path: str,
) -> "ChatModelBase":
    """Enable messages saving for a model.
    
    Args:
        model: The chat model to enable saving for
        save_path: Path to save messages data
        
    Returns:
        The same model instance with messages saving enabled
    """
    if hasattr(model, '_setup_messages_save'):
        model._setup_messages_save(save_messages=True, save_path=save_path)
    else:
        raise ValueError(f"Model {type(model)} does not support messages saving")
    return model
