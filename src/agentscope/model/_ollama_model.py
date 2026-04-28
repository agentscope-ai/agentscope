# -*- coding: utf-8 -*-
"""Model wrapper for Ollama models."""
import json
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    AsyncIterator,
    Literal,
)

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from .._logging import logger
from ..formatter import FormatterBase, OllamaChatFormatter
from ..message import ToolCallBlock, TextBlock, ThinkingBlock
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from ollama._types import ChatResponse as OllamaChatResponse
else:
    OllamaChatResponse = "ollama._types.ChatResponse"


class OllamaChatModel(ChatModelBase):
    """The Ollama chat model class in agentscope."""

    class ThinkingConfig(BaseModel):
        """Configuration for thinking in Ollama chat models."""

        enable_thinking: bool

    def __init__(
        self,
        model_name: str,
        context_length: int,
        stream: bool = False,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        thinking_config: ThinkingConfig | None = None,
        options: dict = None,
        keep_alive: str = "5m",
        host: str | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the Ollama chat model.

        Args:
            model_name (`str`):
                The name of the model.
            context_length (`int `):
                The context length of the model, which will be used in
                context compression
            stream (`bool`, default `True`):
                Streaming mode or not.
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
            thinking_config (`ThinkingConfig | None`, default `None`)
                Configuration for thinking, only for models such as qwen3,
                deepseek-r1, etc.
            options (`dict`, default `None`):
                Additional parameters to pass to the Ollama API. These can
                include temperature etc.
            keep_alive (`str`, default `"5m"`):
                Duration to keep the model loaded in memory. The format is a
                number followed by a unit suffix (s for seconds, m for minutes
                , h for hours).
            host (`str | None`, default `None`):
                The host address of the Ollama server. If None, uses the
                default address (typically http://localhost:11434).
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the Ollama client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in Ollama API generation.
        """

        try:
            import ollama
        except ImportError as e:
            raise ImportError(
                "The package ollama is not found. Please install it by "
                'running command `pip install "ollama>=0.1.7"`',
            ) from e

        super().__init__(
            model_name=model_name,
            stream=stream,
            context_length=context_length,
            max_retries=max_retries,
            fallback_model_name=fallback_model_name,
            formatter=formatter or OllamaChatFormatter(),
        )

        self.client = ollama.AsyncClient(
            host=host,
            **(client_kwargs or {}),
        )
        self.options = options
        self.keep_alive = keep_alive
        self.thinking_config = thinking_config
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from Ollama chat completions API by the given
        arguments.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required, and `name` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "required"] | str \
                | None`, default `None`):
                Ollama doesn't support `tool_choice` argument yet.
            **kwargs (`Any`):
                The keyword arguments for Ollama chat completions API,
                e.g. `think`etc. Please refer to the Ollama API
                documentation for more details.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Ollama chat completions API.
        """

        kwargs = {
            "model": model_name,
            "messages": messages,
            "stream": self.stream,
            "options": self.options,
            "keep_alive": self.keep_alive,
            **self.generate_kwargs,
            **kwargs,
        }

        if self.thinking_config and "think" not in kwargs:
            kwargs["think"] = self.thinking_config.enable_thinking

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            logger.warning("Ollama does not support tool_choice yet, ignored.")

        start_datetime = datetime.now()
        response = await self.client.chat(**kwargs)

        if self.stream:
            return self._parse_ollama_stream_completion_response(
                start_datetime,
                response,
            )

        parsed_response = await self._parse_ollama_completion_response(
            start_datetime,
            response,
        )

        return parsed_response

    async def _parse_ollama_stream_completion_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator[OllamaChatResponse],
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given an Ollama streaming completion response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncIterator[OllamaChatResponse]`):
                Ollama streaming response async iterator to parse.

        Returns:
            AsyncGenerator[ChatResponse, None]:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.

        """
        # Accumulated state
        acc_text = ""
        acc_thinking = ""
        acc_tool_calls: dict = {}  # tool_id -> {name, input}
        response_id: str | None = None
        usage = None

        async for chunk in response:
            delta_content: list = []
            msg = chunk.message

            # Handle thinking delta
            if msg.thinking:
                acc_thinking += msg.thinking
                delta_content.append(ThinkingBlock(thinking=msg.thinking))

            # Handle text delta
            if msg.content:
                acc_text += msg.content
                delta_content.append(TextBlock(text=msg.content))

            # Handle tool calls
            for idx, tool_call in enumerate(msg.tool_calls or []):
                function = tool_call.function
                tool_id = f"{idx}_{function.name}"
                input_str = json.dumps(function.arguments)
                acc_tool_calls[tool_id] = {
                    "name": function.name,
                    "input": input_str,
                }
                delta_content.append(
                    ToolCallBlock(
                        id=tool_id,
                        name=function.name,
                        input=input_str,
                    ),
                )

            if response_id is None:
                response_id = getattr(chunk, "id", None)

            # Calculate usage
            current_time = (datetime.now() - start_datetime).total_seconds()
            usage = ChatUsage(
                input_tokens=getattr(chunk, "prompt_eval_count", 0) or 0,
                output_tokens=getattr(chunk, "eval_count", 0) or 0,
                time=current_time,
            )

            if delta_content:
                _kwargs: dict[str, Any] = {
                    "content": delta_content,
                    "is_last": False,
                    "usage": usage,
                }
                if response_id:
                    _kwargs["id"] = response_id
                yield ChatResponse(**_kwargs)

        # Build final accumulated content
        final_content: list = []
        if acc_thinking:
            final_content.append(ThinkingBlock(thinking=acc_thinking))
        if acc_text:
            final_content.append(TextBlock(text=acc_text))
        for tool_id, tc in acc_tool_calls.items():
            final_content.append(
                ToolCallBlock(
                    id=tool_id,
                    name=tc["name"],
                    input=tc["input"],
                ),
            )

        _final_kwargs: dict[str, Any] = {
            "content": final_content,
            "is_last": True,
            "usage": usage,
        }
        if response_id:
            _final_kwargs["id"] = response_id
        yield ChatResponse(**_final_kwargs)

    async def _parse_ollama_completion_response(
        self,
        start_datetime: datetime,
        response: OllamaChatResponse,
    ) -> ChatResponse:
        """Given an Ollama chat completion response object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`OllamaChatResponse`):
                Ollama OllamaChatResponse object to parse.

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks and usage.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if response.message.thinking:
            content_blocks.append(
                ThinkingBlock(thinking=response.message.thinking),
            )

        if response.message.content:
            content_blocks.append(
                TextBlock(text=response.message.content),
            )

        for idx, tool_call in enumerate(response.message.tool_calls or []):
            content_blocks.append(
                ToolCallBlock(
                    id=f"{idx}_{tool_call.function.name}",
                    name=tool_call.function.name,
                    input=json.dumps(tool_call.function.arguments),
                ),
            )

        usage = None
        if "prompt_eval_count" in response and "eval_count" in response:
            usage = ChatUsage(
                input_tokens=response.get("prompt_eval_count", 0),
                output_tokens=response.get("eval_count", 0),
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        resp_kwargs: dict[str, Any] = {
            "content": content_blocks,
            "is_last": True,
            "usage": usage,
        }
        response_id = getattr(response, "id", None)
        if response_id:
            resp_kwargs["id"] = response_id

        return ChatResponse(**resp_kwargs)

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schemas to the Ollama format."""
        return schemas
