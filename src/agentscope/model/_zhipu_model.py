# -*- coding: utf-8 -*-
"""Model wrapper for Zhipu AI models."""
from collections import OrderedDict
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    Literal,
    Type,
)

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..message import ToolUseBlock, TextBlock, ThinkingBlock
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from zai.types.chat.chat_completion import ChatCompletion
else:
    ChatCompletion = "zai.types.chat.chat_completion.ChatCompletion"
    ChatCompletionChunk = "zai.types.chat.chat_completion_chunk.ChatCompletionChunk"


class ZhipuChatModel(ChatModelBase):
    """The Zhipu AI chat model class in agentscope."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = True,
        base_url: str | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Zhipu AI chat model.

        Args:
            model_name (`str`):
                The name of the model, e.g., "glm-4", "glm-4-plus", "glm-4v".
            api_key (`str | None`, default `None`):
                The API key for Zhipu AI. If not specified, it will be read
                from the environment variable `ZHIPUAI_API_KEY`.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            base_url (`str | None`, default `None`):
                The base URL for Zhipu AI API. If not specified, uses the
                default URL.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
            optional):
                The extra keyword arguments used in Zhipu AI API generation,
                e.g. `temperature`, `max_tokens`, `top_p`, etc.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the Zhipu AI client.
        """

        try:
            from zai import ZhipuAiClient
        except ImportError as e:
            raise ImportError(
                "The package zai-sdk is not found. Please install it by "
                'running command `pip install zai-sdk`',
            ) from e

        super().__init__(model_name, stream)

        self.client = ZhipuAiClient(
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "any", "required"]
        | str
        | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from Zhipu AI chat completions API by the given
        arguments.

        Args:
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required, and `name` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "any", "required"] | str \
                | None`, default `None`):
                Controls which (if any) tool is called by the model.
                Can be "auto", "none", or specific tool name.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.
            **kwargs (`Any`):
                The keyword arguments for Zhipu AI chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please refer to
                the Zhipu AI API documentation for more details.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Zhipu AI chat completions API.
        """

        # Validate messages
        if not isinstance(messages, list):
            raise ValueError(
                "Zhipu AI `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for Zhipu AI API.",
            )

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            self._validate_tool_choice(tool_choice, tools)
            kwargs["tool_choice"] = self._format_tool_choice(tool_choice)

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            # Convert BaseModel to JSON schema for response format
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": structured_model.__name__,
                    "schema": structured_model.model_json_schema(),
                },
            }
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)

        start_datetime = datetime.now()
        response = self.client.chat.completions.create(**kwargs)

        if self.stream:
            return self._parse_zhipu_stream_completion_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = self._parse_zhipu_completion_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    async def _parse_zhipu_stream_completion_response(
        self,
        start_datetime: datetime,
        response: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given a Zhipu AI streaming completion response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Any`):
                Zhipu AI streaming response to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            AsyncGenerator[ChatResponse, None]:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.
        """
        accumulated_text = ""
        accumulated_thinking = ""
        tool_calls = OrderedDict()  # Store tool calls
        metadata = None

        for chunk in response:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Handle text content
            if delta.content:
                accumulated_text += delta.content

            # Handle thinking/reasoning content
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                accumulated_thinking += str(delta.reasoning_content)

            # Handle tool calls
            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    tool_id = tool_call.id or f"call_{tool_call.index}"
                    if tool_id not in tool_calls:
                        tool_calls[tool_id] = {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": tool_call.function.name or "",
                            "input": "",
                        }

                    if tool_call.function.name:
                        tool_calls[tool_id]["name"] = tool_call.function.name
                    if tool_call.function.arguments:
                        tool_calls[tool_id]["input"] += tool_call.function.arguments

            # Calculate usage statistics
            current_time = (datetime.now() - start_datetime).total_seconds()
            usage = None
            if hasattr(chunk, 'usage') and chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                    time=current_time,
                )

            # Create content blocks
            contents: list = []

            # Add thinking block if present
            if accumulated_thinking:
                contents.append(
                    ThinkingBlock(
                        type="thinking",
                        thinking=accumulated_thinking,
                    ),
                )

            if accumulated_text:
                contents.append(TextBlock(type="text", text=accumulated_text))
                if structured_model:
                    metadata = _json_loads_with_repair(accumulated_text)

            # Add tool call blocks
            for tool_call in tool_calls.values():
                try:
                    input_data = tool_call["input"]
                    if isinstance(input_data, str) and input_data:
                        input_data = _json_loads_with_repair(input_data)
                    contents.append(
                        ToolUseBlock(
                            type=tool_call["type"],
                            id=tool_call["id"],
                            name=tool_call["name"],
                            input=input_data,
                        ),
                    )
                except Exception as e:
                    logger.warning(f"Error parsing tool call input: {e}")

            # Generate response when there's content or at final chunk
            if choice.finish_reason or contents:
                res = ChatResponse(
                    content=contents,
                    usage=usage,
                    metadata=metadata,
                )
                yield res

    def _parse_zhipu_completion_response(
        self,
        start_datetime: datetime,
        response: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given a Zhipu AI chat completion response object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Any`):
                Zhipu AI ChatCompletion object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks and usage.
        """
        content_blocks: List[TextBlock | ToolUseBlock] = []
        metadata = None

        if not response.choices:
            return ChatResponse(content=[], usage=None, metadata=None)

        choice = response.choices[0]
        message = choice.message

        # Handle text content
        if message.content:
            content_blocks.append(
                TextBlock(
                    type="text",
                    text=message.content,
                ),
            )
            if structured_model:
                metadata = _json_loads_with_repair(message.content)

        # Handle tool calls
        if message.tool_calls:
            for tool_call in message.tool_calls:
                try:
                    input_data = tool_call.function.arguments
                    if isinstance(input_data, str):
                        input_data = _json_loads_with_repair(input_data)

                    content_blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=tool_call.id,
                            name=tool_call.function.name,
                            input=input_data,
                        ),
                    )
                except Exception as e:
                    logger.warning(f"Error parsing tool call: {e}")

        # Calculate usage
        usage = None
        if hasattr(response, 'usage') and response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        parsed_response = ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=metadata,
        )

        return parsed_response

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schemas to the Zhipu AI format."""
        return schemas

    def _format_tool_choice(
        self,
        tool_choice: str,
    ) -> str | dict[str, Any]:
        """Format tool choice to Zhipu AI format."""
        if tool_choice in ["auto", "none"]:
            return tool_choice
        elif tool_choice == "any":
            # Zhipu AI doesn't support "any", use "auto" instead
            logger.warning(
                "Zhipu AI doesn't support tool_choice='any', using 'auto' instead."
            )
            return "auto"
        elif tool_choice == "required":
            # Zhipu AI doesn't support "required", use "auto" instead
            logger.warning(
                "Zhipu AI doesn't support tool_choice='required', using 'auto' instead."
            )
            return "auto"
        else:
            # Specific tool name
            return {
                "type": "function",
                "function": {"name": tool_choice}
            }

