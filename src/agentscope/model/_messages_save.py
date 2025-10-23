# -*- coding: utf-8 -*-
"""Messages save utilities for chat models.

This module provides functionality to save model conversations for data collection.
Each record corresponds to a single model invocation and contains the input messages 
and available tools in their original format.

Design Philosophy:
- AgentScope focuses on data collection, not transformation
- Save raw messages as-is for maximum flexibility
- Transformation to specific SFT formats happens downstream
- Hook-based mechanism for instance-level control

Usage Example:
    from agentscope.model import DashScopeChatModel, enable_messages_save
    
    # Create model instances
    model_a = DashScopeChatModel(model_name="qwen-turbo", api_key="xxx")
    model_b = DashScopeChatModel(model_name="qwen-turbo", api_key="xxx")
    
    # Only enable saving for model_a
    enable_messages_save(model_a, save_path="./model_a_messages.jsonl")
    
    # model_a will save messages, model_b will not
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, AsyncGenerator, TYPE_CHECKING

from ._model_response import ChatResponse
from .._logging import logger

if TYPE_CHECKING:
    from ._model_base import ChatModelBase


@dataclass
class MessagesRecord:
    """A single messages data record to be written as JSONL.
    
    The messages are stored in their original format without any transformation.
    This preserves maximum information and flexibility for downstream processing.
    """

    messages: list[dict]
    tools: list[dict] | None
    metadata: dict[str, Any] | None = None

    def to_jsonl(self) -> str:
        """Convert record to JSONL format.
        
        Note: messages and tools are JSON-encoded as strings for compatibility
        with common data pipeline tools.
        """
        payload = {
            "messages": json.dumps(self.messages, ensure_ascii=False),
            "tools": json.dumps(self.tools or [], ensure_ascii=False),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, ensure_ascii=False)


class MessagesDataCollector:
    """Append-only JSONL data collector for raw messages.
    
    This collector saves messages in their original format without any
    transformation. The downstream SFT framework is responsible for converting
    the data to its required format.
    
    Parameters
    ----------
    output_path : str | None
        The file path to write JSONL lines to. If None, only in-memory storage.
        Parent directories will be created if not present.
    enable_collection : bool
        Global switch to enable/disable writing.
    custom_tags : dict | None
        Custom tags to add to metadata for all collected messages.
        
    Design Notes
    ------------
    - No normalization or transformation is performed
    - Messages are saved exactly as they appear in the API
    - Reasoning content, tool calls, and other fields are preserved as-is
    - This provides maximum flexibility for downstream processing
    - Can save to file, memory, or both
    """

    def __init__(
        self,
        output_path: str | None = None,
        enable_collection: bool = True,
        custom_tags: dict | None = None,
    ) -> None:
        self.output_path = output_path
        self.enable_collection = enable_collection
        self.custom_tags = custom_tags or {}
        self.history: list[dict] = []  # In-memory history

        # Ensure directory exists if output_path is provided
        if self.output_path:
            output_dir = os.path.dirname(os.path.abspath(self.output_path))
            if output_dir:  # Only create if there's a directory component
                os.makedirs(output_dir, exist_ok=True)

    async def collect(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a single model call as one JSONL line and/or to memory.

        This function is async for ergonomic symmetry, but uses synchronous
        file IO for portability and simplicity (atomic append behavior on POSIX
        systems).
        
        Args:
            messages: List of message dictionaries to save (in original format)
            tools: List of tool dictionaries, if any
            metadata: Additional metadata to include in the record
        """
        if not self.enable_collection:
            return

        # Merge custom tags into metadata
        full_metadata = {
            **(metadata or {}),
            **self.custom_tags,  # Add custom tags
            "collected_at": datetime.utcnow().isoformat() + "Z",
        }

        record = MessagesRecord(
            messages=messages,
            tools=tools or [],
            metadata=full_metadata,
        )

        # Save to memory
        self.history.append({
            "messages": messages,
            "tools": tools or [],
            "metadata": full_metadata,
        })

        # Save to file if output_path is provided
        if self.output_path:
            line = record.to_jsonl()
            with open(self.output_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def _build_complete_conversation(
    input_messages: list[dict],
    response: ChatResponse,
) -> list[dict]:
    """Build complete conversation including assistant response.
    
    Extracts information from ChatResponse and constructs the assistant message
    in the standard format with content, tool_calls, and reasoning_content fields.
    
    Args:
        input_messages: The original input messages
        response: The ChatResponse from the model
        
    Returns:
        Complete conversation including the assistant's response
    """
    # Start with input messages
    complete_messages = input_messages.copy()
    
    # Extract assistant response content
    assistant_content = []
    tool_calls = []
    reasoning_content = []
    
    for block in response.content:
        # Ensure we are handling a mapping-like block
        assert isinstance(block, dict), (
            f"Unexpected block type: {type(block)} value={block!r}"
        )
        block_type = block.get("type")
        
        if block_type == "text":
            val = block.get("text")
            if val is not None:
                assistant_content.append(str(val))
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
            val = block.get("thinking")
            if val is not None:
                reasoning_content.append(str(val))
    
    # Create assistant message
    assistant_message = {
        "role": "assistant",
        "content": "\n".join(assistant_content) if assistant_content else None
    }
    
    # Add reasoning content if any (keep as separate field)
    if reasoning_content:
        assistant_message["reasoning_content"] = "\n".join(reasoning_content)
    
    # Add tool calls if any
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls
    
    complete_messages.append(assistant_message)
    
    return complete_messages


def _create_saving_class(original_class: type) -> type:
    """Create a subclass that wraps __call__ to add message saving.
    
    This dynamically creates a new class that inherits from the original class
    and overrides __call__ to add message saving logic.
    
    Args:
        original_class: The original model class
        
    Returns:
        A new class with message saving enabled
    """
    # Get the original __call__ method (before any decorators)
    original_call = original_class.__call__
    
    async def __call_with_save__(self, *args, **kwargs):
        """Override __call__ to add message saving."""
        # Call the original method
        result = await original_call(self, *args, **kwargs)
        
        # Get the collector from the instance
        collector = getattr(self, '_messages_collector', None)
        if not collector:
            return result
        
        # Extract parameters
        messages = args[0] if args else kwargs.get('messages', [])
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        
        if isinstance(result, AsyncGenerator):
            # Streaming response - wrap the generator
            async def _save_wrapper():
                final_resp = None
                async for chunk in result:
                    final_resp = chunk
                    yield chunk
                
                # Save messages after streaming completes
                if final_resp is not None:
                    try:
                        complete_messages = _build_complete_conversation(
                            messages, final_resp
                        )
                        await collector.collect(
                            messages=complete_messages,
                            tools=tools or [],
                            metadata={
                                "model_name": self.model_name,
                                "tool_choice": tool_choice,
                                "stream": True,
                            },
                        )
                    except Exception as e:
                        logger.warning("Messages save failed: %s", e)
            
            return _save_wrapper()
        else:
            # Non-streaming response
            try:
                complete_messages = _build_complete_conversation(messages, result)
                await collector.collect(
                    messages=complete_messages,
                    tools=tools or [],
                    metadata={
                        "model_name": self.model_name,
                        "tool_choice": tool_choice,
                        "stream": False,
                    },
                )
            except Exception as e:
                logger.warning("Messages save failed: %s", e)
            
            return result
    
    # Create a new class dynamically
    new_class = type(
        f"{original_class.__name__}WithMessagesSave",
        (original_class,),
        {
            "__call__": __call_with_save__,
            "_is_messages_save_enabled": True,
        }
    )
    
    return new_class


def enable_messages_save(
    model: "ChatModelBase",
    save_path: str | None = None,
    tags: dict | None = None,
) -> "ChatModelBase":
    """Enable messages saving for a specific model instance.
    
    This function uses a dynamic subclass approach to add message saving to a
    model instance without modifying other instances of the same class.
    
    Args:
        model: The chat model instance to enable saving for
        save_path: Path to save messages data (JSONL format). If None, only 
            saves to memory (model.messages_call_history)
        tags: Custom tags (key-value pairs) to add to metadata for all messages.
            Example: {"experiment": "exp1", "version": "v1.0"}
        
    Returns:
        The same model instance with messages saving enabled
        
    Example:
        >>> from agentscope.model import DashScopeChatModel, enable_messages_save
        >>> 
        >>> # Save to file with custom tags
        >>> model_a = DashScopeChatModel(model_name="qwen-turbo", api_key="xxx")
        >>> enable_messages_save(
        ...     model_a, 
        ...     save_path="./model_a.jsonl",
        ...     tags={"experiment": "exp1", "user": "alice"}
        ... )
        >>> 
        >>> # Only save to memory, no file
        >>> model_b = DashScopeChatModel(model_name="qwen-turbo", api_key="xxx")
        >>> enable_messages_save(model_b)  # No save_path
        >>> 
        >>> # Access history: model_b.messages_call_history
        >>> # Export history: model_b.export_messages_call_history("output.jsonl")
        
    Notes:
        - This can be called multiple times to change settings
        - The mechanism doesn't interfere with tracing or other functionality
        - Messages are saved in their original format without transformation
        - This works by changing the instance's __class__ to a dynamic subclass
        - History is always stored in model.messages_call_history
    """
    from ._model_base import ChatModelBase
    
    if not isinstance(model, ChatModelBase):
        raise ValueError(
            f"Model must be a ChatModelBase instance, got {type(model)}"
        )
    
    # Create or update the collector
    model._messages_collector = MessagesDataCollector(
        output_path=save_path,
        enable_collection=True,
        custom_tags=tags,
    )
    
    # Check if already enabled for this instance
    if not getattr(model.__class__, '_is_messages_save_enabled', False):
        # Change the instance's class to a saving-enabled subclass
        original_class = model.__class__
        saving_class = _create_saving_class(original_class)
        model.__class__ = saving_class
    
    # Add export method if not already present
    if not hasattr(model, 'export_messages_call_history'):
        def export_messages_call_history(path: str) -> int:
            """Export messages call history to a JSONL file.
            
            Args:
                path: Output file path
                
            Returns:
                Number of records exported
            """
            if not hasattr(model, '_messages_collector'):
                logger.warning("No messages collector found")
                return 0
            
            history = model._messages_collector.history
            if not history:
                logger.warning("No messages in history")
                return 0
            
            # Ensure directory exists
            output_dir = os.path.dirname(os.path.abspath(path))
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            # Write to file
            with open(path, "w", encoding="utf-8") as f:
                for record in history:
                    # Convert to MessagesRecord format
                    msg_record = MessagesRecord(
                        messages=record["messages"],
                        tools=record["tools"],
                        metadata=record["metadata"],
                    )
                    f.write(msg_record.to_jsonl() + "\n")
            
            logger.info(f"Exported {len(history)} records to {path}")
            return len(history)
        
        model.export_messages_call_history = export_messages_call_history
    
    # Add property for accessing history
    @property
    def messages_call_history(self):
        """Access the messages call history."""
        if hasattr(self, '_messages_collector'):
            return self._messages_collector.history
        return []
    
    # Bind the property to the instance's class
    if not hasattr(model.__class__, 'messages_call_history'):
        model.__class__.messages_call_history = messages_call_history
    
    log_msg = f"Messages saving enabled for model instance: {model.model_name}"
    if save_path:
        log_msg += f" -> file: {save_path}"
    if tags:
        log_msg += f", tags: {tags}"
    log_msg += ", memory: model.messages_call_history"
    
    logger.info(log_msg)
    
    return model
