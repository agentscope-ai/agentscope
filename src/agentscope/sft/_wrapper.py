# -*- coding: utf-8 -*-
"""ChatModel wrapper to collect SFT data per model invocation.

Usage
-----
>>> from agentscope.model import OpenAIChatModel
>>> from agentscope.sft import SFTDataCollector, ChatModelSFTWrapper
>>> base = OpenAIChatModel(model_name="gpt-4", api_key="sk-...", stream=False)
>>> collector = SFTDataCollector(output_path="sft.jsonl", enable_collection=True)
>>> model = ChatModelSFTWrapper(base_model=base, collector=collector)
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Type, Literal

from ..model import ChatModelBase, ChatResponse
from ..types import JSONSerializableObject
from .._logging import logger
from ._collector import SFTDataCollector


class ChatModelSFTWrapper(ChatModelBase):
    """A transparent wrapper that records inputs per model call.

    The wrapper forwards all calls to the underlying model and then appends a
    JSONL record via the provided collector. Streaming and non-streaming modes
    are supported. No modification is made to the original response.
    """

    def __init__(
        self,
        base_model: ChatModelBase,
        collector: SFTDataCollector | None,
    ) -> None:
        super().__init__(model_name=base_model.model_name, stream=base_model.stream)
        self._base = base_model
        self._collector = collector

    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "any", "required"] | str | None = None,
        structured_model: Type[Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        # Forward the call
        result = await self._base(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            structured_model=structured_model,
            **kwargs,
        )

        # Streaming mode: wrap the async generator to collect once at the end
        if self._base.stream and hasattr(result, "__aiter__"):
            async def _streaming_wrapper() -> AsyncGenerator[ChatResponse, None]:
                final_resp: ChatResponse | None = None
                async for chunk in result:  # type: ignore[misc]
                    final_resp = chunk
                    yield chunk
                try:
                    if self._collector and final_resp:
                        # Build complete conversation with assistant response
                        complete_messages = self._build_complete_conversation(
                            messages, final_resp, tools
                        )
                        await self._collector.collect(
                            messages=complete_messages,
                            tools=tools or [],
                            metadata={
                                "model_name": self.model_name,
                                "tool_choice": tool_choice,
                                "stream": True,
                            },
                        )
                except Exception as e:
                    logger.warning("SFT collection failed (stream): %s", e)

            return _streaming_wrapper()

        # Non-streaming: collect immediately
        try:
            if self._collector:
                # Build complete conversation with assistant response
                complete_messages = self._build_complete_conversation(
                    messages, result, tools
                )
                await self._collector.collect(
                    messages=complete_messages,
                    tools=tools or [],
                    metadata={
                        "model_name": self.model_name,
                        "tool_choice": tool_choice,
                        "stream": False,
                    },
                )
        except Exception as e:
            logger.warning("SFT collection failed: %s", e)

        return result

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


def wrap_model_with_sft(
    model: ChatModelBase,
    collector: SFTDataCollector | None,
) -> ChatModelBase:
    """Return the model wrapped with SFT collection if collector is provided."""
    if collector is None:
        return model
    return ChatModelSFTWrapper(base_model=model, collector=collector)


