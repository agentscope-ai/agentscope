# -*- coding: utf-8 -*-
"""The Ollama chat model implementation."""
import json
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List, Type

from pydantic import BaseModel, Field

from ..._utils._common import _generate_id
from .._base import ChatModelBase
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OllamaCredential
from ...formatter import FormatterBase, OllamaChatFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice
from ..._logging import logger

if TYPE_CHECKING:
    from ollama._types import ChatResponse as OllamaChatResponse
else:
    OllamaChatResponse = Any


class OllamaChatModel(ChatModelBase):
    """The Ollama chat model."""

    class Parameters(BaseModel):
        """The parameters for the Ollama chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description="Whether to enable thinking"
            " (for models like qwen3, deepseek-r1).",
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            le=2,
        )

    type: Literal["ollama_chat"] = "ollama_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OllamaCredential | None = None,
        model: str = "",
        parameters: "OllamaChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 32768,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Ollama chat model.

        Args:
            credential (`OllamaCredential | None`, defaults to `None`):
                The Ollama connection settings. When ``None``, a default
                ``OllamaCredential`` (localhost) will be used.
            model (`str`):
                The Ollama model name, e.g. ``llama3.3`` or ``qwen3:14b``.
            parameters (`OllamaChatModel.Parameters | None`, defaults to \
            `None`):
                The Ollama API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the Ollama API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `32768`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the Ollama API. When ``None``, an
                ``OllamaChatFormatter`` instance will be used.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``ollama.AsyncClient``
                and onward to the underlying ``httpx.AsyncClient``
                (e.g. ``timeout``, ``headers``, ``verify``).
        """
        resolved_credential = credential or OllamaCredential()

        super().__init__(
            credential=resolved_credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )

        self.formatter = formatter or OllamaChatFormatter()
        self.client_kwargs = client_kwargs or {}

        # Models that rejected the ``tools`` field, so later calls skip it
        self._models_without_tools: set[str] = set()

        import ollama

        self.client: ollama.AsyncClient = ollama.AsyncClient(
            host=self.credential.host,
            **self.client_kwargs,
        )

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        import httpx

        # Local service: retry transient transport-layer failures only.
        # ollama.ResponseError wraps server-side errors regardless of cause
        # (incl. 4xx like "model not found"), so we don't retry on it.
        return (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the Ollama chat API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                A list of message dicts with ``role`` and ``content`` keys.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas.
            tool_choice (`ToolChoice | None`, optional):
                Not supported by Ollama yet (ignored with warning).
            **generate_kwargs (`Any`):
                Extra keyword arguments forwarded to the Ollama API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                A ``ChatResponse`` when streaming is disabled, or an async
                generator of ``ChatResponse`` objects when streaming is
                enabled.
        """
        formatted_messages = await self.formatter.format(messages)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": formatted_messages,
            "stream": self.stream,
        }

        options: dict[str, Any] = {}
        if self.parameters.max_tokens is not None:
            options["num_predict"] = self.parameters.max_tokens
        if self.parameters.temperature is not None:
            options["temperature"] = self.parameters.temperature
        if options:
            kwargs["options"] = options

        kwargs["think"] = self.parameters.thinking_enable

        kwargs.update(generate_kwargs)

        fmt_tools, _ = self._format_tools(tools, tool_choice)

        if fmt_tools and model_name not in self._models_without_tools:
            kwargs["tools"] = fmt_tools

        import ollama

        start_datetime = datetime.now()
        try:
            response = await self._chat(kwargs)
        except ollama.ResponseError as e:
            if "tools" not in kwargs or not _is_tools_unsupported_error(e):
                raise
            logger.warning(
                "Ollama model %s does not support tools, retrying without "
                "tools. Tool schemas will not be sent to this model again.",
                model_name,
            )
            self._models_without_tools.add(model_name)
            kwargs.pop("tools")
            response = await self._chat(kwargs)

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return await self._parse_completion_response(start_datetime, response)

    async def _chat(self, kwargs: dict[str, Any]) -> Any:
        """Send a chat request to Ollama.

        In streaming mode the Ollama client only raises request errors
        (e.g. HTTP 400) once the stream is iterated, so the first chunk is
        fetched here to surface them before the stream is handed back.

        Args:
            kwargs (`dict[str, Any]`):
                The keyword arguments forwarded to ``ollama.AsyncClient.chat``.

        Returns:
            `Any`:
                The Ollama response object, or an async iterator over the
                stream chunks when streaming is enabled.
        """
        response = await self.client.chat(**kwargs)
        if not self.stream:
            return response

        try:
            first_chunk = await anext(response)
        except StopAsyncIteration:
            first_chunk = None

        async def _replay() -> AsyncGenerator[Any, None]:
            if first_chunk is not None:
                yield first_chunk
            async for chunk in response:
                yield chunk

        return _replay()

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, None]:
        """Validate, filter tools, and warn if tool_choice is set.

        Ollama does not support ``tool_choice`` natively. When
        ``tool_choice.tools`` is specified the schemas list is filtered to
        only those tools. Any ``tool_choice.mode`` value is ignored with a
        warning.

        Args:
            tools (`list[dict] | None`, optional):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, None]`:
                A tuple of (filtered_tools, None) — tool_choice is always
                ``None`` since Ollama does not support it.
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.tools:
                allowed = set(tool_choice.tools)
                tools = [t for t in tools if t["function"]["name"] in allowed]

        if tool_choice:
            logger.warning(
                "Ollama ignores tool_choice.mode; "
                "tool_choice.tools is still applied to filter tool schemas.",
            )

        return tools, None

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the Ollama streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Any`):
                The Ollama async stream object.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()

        async for chunk in response:
            delta_res = ChatResponse(
                content=[],
                is_last=False,
                id=response_id,
            )

            msg = chunk.message

            # Thinking
            if getattr(msg, "thinking", None):
                delta_res.append_thinking(
                    block_id=thinking_id,
                    thinking=msg.thinking,
                )

            # Text
            if msg.content:
                delta_res.append_text(
                    block_id=text_id,
                    text=msg.content,
                )

            # Tool call
            for idx, tool_call in enumerate(msg.tool_calls or []):
                delta_res.append_tool_call(
                    block_id=f"{idx}_{tool_call.function.name}",
                    name=tool_call.function.name,
                    input=json.dumps(
                        tool_call.function.arguments,
                        ensure_ascii=False,
                    ),
                )

            current_time = (datetime.now() - start_datetime).total_seconds()
            delta_res.usage = ChatUsage(
                input_tokens=getattr(chunk, "prompt_eval_count", 0) or 0,
                output_tokens=getattr(chunk, "eval_count", 0) or 0,
                time=current_time,
            )

            yield delta_res

    async def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: OllamaChatResponse,
    ) -> ChatResponse:
        """Parse the Ollama non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`OllamaChatResponse`):
                The Ollama chat response object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        message_thinking = getattr(response.message, "thinking", None)
        if message_thinking:
            content_blocks.append(ThinkingBlock(thinking=message_thinking))

        if response.message.content:
            content_blocks.append(TextBlock(text=response.message.content))

        for idx, tool_call in enumerate(response.message.tool_calls or []):
            content_blocks.append(
                ToolCallBlock(
                    id=f"{idx}_{tool_call.function.name}",
                    name=tool_call.function.name,
                    input=json.dumps(
                        tool_call.function.arguments,
                        ensure_ascii=False,
                    ),
                ),
            )

        usage = None
        prompt_eval = getattr(response, "prompt_eval_count", None)
        eval_count = getattr(response, "eval_count", None)
        if prompt_eval is not None and eval_count is not None:
            usage = ChatUsage(
                input_tokens=prompt_eval,
                output_tokens=eval_count,
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        return ChatResponse(
            id=getattr(response, "id", None) or _generate_id(),
            content=content_blocks,
            is_last=True,
            usage=usage,
        )


def _is_tools_unsupported_error(error: Exception) -> bool:
    """Whether an Ollama error says the model does not support tools, e.g.
    ``registry.ollama.ai/library/deepseek-r1:1.5b does not support tools``.
    """
    status_code = getattr(error, "status_code", None)
    message = str(getattr(error, "error", error))
    return status_code == 400 and "does not support tools" in message
