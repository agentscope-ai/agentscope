# -*- coding: utf-8 -*-
"""Model wrapper for Ollama models."""
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    AsyncIterator,
    Literal,
    Type,
)
from collections import OrderedDict

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..message import ToolUseBlock, TextBlock, ThinkingBlock
from ..tracing import trace_llm


if TYPE_CHECKING:
    from ollama._types import ChatResponse as OllamaChatResponse
else:
    OllamaChatResponse = "ollama._types.ChatResponse"


class OllamaChatModel(ChatModelBase):
    """The Ollama chat model class in agentscope."""

    def __init__(
        self,
        model_name: str,
        stream: bool = False,
        options: dict = None,
        keep_alive: str = "5m",
        enable_thinking: bool | None = None,
        host: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Ollama chat model.

        Args:
           model_name (`str`):
               The name of the model.
           stream (`bool`, default `True`):
               Streaming mode or not.
           options (`dict`, default `None`):
               Additional parameters to pass to the Ollama API. These can
               include temperature etc.
           keep_alive (`str`, default `"5m"`):
               Duration to keep the model loaded in memory. The format is a
               number followed by a unit suffix (s for seconds, m for minutes
               , h for hours).
           enable_thinking (`bool | None`, default `None`)
               Whether enable thinking or not, only for models such as qwen3,
               deepseek-r1, etc. For more details, please refer to
               https://ollama.com/search?c=thinking
           host (`str | None`, default `None`):
               The host address of the Ollama server. If None, uses the
               default address (typically http://localhost:11434).
           **kwargs (`Any`):
               Additional keyword arguments to pass to the base chat model
               class.
        """

        try:
            import ollama
        except ImportError as e:
            raise ImportError(
                "The package ollama is not found. Please install it by "
                'running command `pip install "ollama>=0.1.7"`',
            ) from e

        super().__init__(model_name, stream)

        self.client = ollama.AsyncClient(
            host=host,
            **kwargs,
        )
        self.options = options
        self.keep_alive = keep_alive
        self.think = enable_thinking

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
        """Get the response from Ollama chat completions API by the given
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
                 Can be "auto", "none", "any", "required", or specific tool
                 name.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.
            **kwargs (`Any`):
                The keyword arguments for Ollama chat completions API,
                e.g. `think`etc. Please refer to the Ollama API
                documentation for more details.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Ollama chat completions API.
        """

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream,
            "options": self.options,
            "keep_alive": self.keep_alive,
            **kwargs,
        }

        if self.think is not None and "think" not in kwargs:
            kwargs["think"] = self.think

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            logger.warning("Ollama does not support tool_choice yet, ignored.")

        if structured_model:
            kwargs["format"] = structured_model.model_json_schema()

        start_datetime = datetime.now()
        response = await self.client.chat(**kwargs)

        if self.stream:
            return self._parse_ollama_stream_completion_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = await self._parse_ollama_completion_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    async def _parse_ollama_stream_completion_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator[OllamaChatResponse],
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given an Ollama streaming completion response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncIterator[OllamaChatResponse]`):
                Ollama streaming response async iterator to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            AsyncGenerator[ChatResponse, None]:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.

        """
        accumulated_text = ""
        acc_thinking_content = ""
        tool_calls = OrderedDict()  # Store tool calls
        metadata: dict | None = None

        async for chunk in response:
            # Handle text content
            msg = chunk.message
            acc_thinking_content += msg.thinking or ""
            accumulated_text += msg.content or ""

            # Handle tool calls
            for idx, tool_call in enumerate(msg.tool_calls or []):
                function = tool_call.function
                tool_id = f"{idx}_{function.name}"
                tool_calls[tool_id] = {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": function.name,
                    "input": function.arguments,
                }
            # Calculate usage statistics
            current_time = (datetime.now() - start_datetime).total_seconds()
            usage = ChatUsage(
                input_tokens=getattr(chunk, "prompt_eval_count", 0) or 0,
                output_tokens=getattr(chunk, "eval_count", 0) or 0,
                time=current_time,
            )
            # Create content blocks
            contents: list = []

            if acc_thinking_content:
                contents.append(
                    ThinkingBlock(
                        type="thinking",
                        thinking=acc_thinking_content,
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
                    if isinstance(input_data, str):
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
                    print(f"Error parsing tool call input: {e}")

            # Generate response when there's new content or at final chunk
            if chunk.done and contents:
                res = ChatResponse(
                    content=contents,
                    usage=usage,
                    metadata=metadata,
                )
                yield res

    async def _parse_ollama_completion_response(
        self,
        start_datetime: datetime,
        response: OllamaChatResponse,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given an Ollama chat completion response object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`OllamaChatResponse`):
                Ollama OllamaChatResponse object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks and usage.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.
        """
        content_blocks: List[TextBlock | ToolUseBlock | ThinkingBlock] = []
        metadata: dict | None = None

        if response.message.thinking:
            content_blocks.append(
                ThinkingBlock(
                    type="thinking",
                    thinking=response.message.thinking,
                ),
            )

        if response.message.content:
            content_blocks.append(
                TextBlock(
                    type="text",
                    text=response.message.content,
                ),
            )
            if structured_model:
                metadata = _json_loads_with_repair(
                    response.message.content,
                )

        for idx, tool_call in enumerate(response.message.tool_calls or []):
            content_blocks.append(
                ToolUseBlock(
                    type="tool_use",
                    id=f"{idx}_{tool_call.function.name}",
                    name=tool_call.function.name,
                    input=tool_call.function.arguments,
                ),
            )

        usage = None
        if "prompt_eval_count" in response and "eval_count" in response:
            usage = ChatUsage(
                input_tokens=response.get("prompt_eval_count", 0),
                output_tokens=response.get("eval_count", 0),
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
        """Format the tools JSON schemas to the Ollama format."""
        return schemas
