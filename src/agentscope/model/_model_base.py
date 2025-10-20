# -*- coding: utf-8 -*-
"""The chat model base class."""

from abc import abstractmethod
from typing import AsyncGenerator, Any

from ._model_response import ChatResponse

TOOL_CHOICE_MODES = ["auto", "none", "any", "required"]


class ChatModelBase:
    """Base class for chat models."""

    model_name: str
    """The model name"""

    stream: bool
    """Is the model output streaming or not"""

    def __init__(
        self,
        model_name: str,
        stream: bool,
        save_messages: bool = False,
        save_path: str | None = None,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
            save_messages (`bool`, optional):
                Whether to save model conversations to file. Defaults to False.
            save_path (`str | None`, optional):
                Path to save messages data. Required if save_messages is True.
        """
        self.model_name = model_name
        self.stream = stream
        
        # Setup messages saving if enabled
        if save_messages:
            if not save_path:
                raise ValueError("save_path must be provided when save_messages is True")
            from ._messages_save import MessagesDataCollector
            self._messages_collector = MessagesDataCollector(
                output_path=save_path, 
                enable_collection=True
            )
        else:
            self._messages_collector = None

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        pass

    def _validate_tool_choice(
        self,
        tool_choice: str,
        tools: list[dict] | None,
    ) -> None:
        """
        Validate tool_choice parameter.

        Args:
            tool_choice (`str`):
                Tool choice mode or function name
            tools (`list[dict] | None`):
                Available tools list
        Raises:
            TypeError: If tool_choice is not string
            ValueError: If tool_choice is invalid
        """
        if not isinstance(tool_choice, str):
            raise TypeError(
                f"tool_choice must be str, got {type(tool_choice)}",
            )
        if tool_choice in TOOL_CHOICE_MODES:
            return

        available_functions = [tool["function"]["name"] for tool in tools]

        if tool_choice not in available_functions:
            all_options = TOOL_CHOICE_MODES + available_functions
            raise ValueError(
                f"Invalid tool_choice '{tool_choice}'. "
                f"Available options: {', '.join(sorted(all_options))}",
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
            from .._logging import logger
            logger.warning("Messages save failed: %s", e)
