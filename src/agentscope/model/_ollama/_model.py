# -*- coding: utf-8 -*-
"""The Ollama chat model implementation."""
import json
import uuid
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, Field

from .._base import ChatModelBase
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OllamaCredential
from ...formatter import FormatterBase, OllamaChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice
from ...tracing import trace_llm
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
        context_size: int = 32768,
        formatter: FormatterBase | None = None,
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
            context_size (`int`, defaults to `32768`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the Ollama API. When ``None``, an
                ``OllamaChatFormatter`` instance will be used.
        """
        super().__init__(
            model=model,
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.credential = credential or OllamaCredential()
        self.parameters = parameters or self.Parameters()
        self.formatter = formatter or OllamaChatFormatter()

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
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
        import ollama

        client = ollama.AsyncClient(host=self.credential.host)

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

        if self.parameters.thinking_enable:
            kwargs["think"] = True

        kwargs.update(generate_kwargs)

        if tools:
            kwargs["tools"] = tools

        if tool_choice:
            logger.warning("Ollama does not support tool_choice yet, ignored.")

        start_datetime = datetime.now()
        response = await client.chat(**kwargs)

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return await self._parse_completion_response(start_datetime, response)

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
        # All delta should have the same block identifier.
        # Ollama does not return a request id, so we generate one upfront
        # to keep it stable.
        response_id = getattr(response, "id", None) or uuid.uuid4().hex
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        acc_tool_calls: dict = {}
        usage = None

        async for chunk in response:
            delta_content: list = []
            msg = chunk.message

            chunk_thinking = getattr(msg, "thinking", None)
            if chunk_thinking:
                acc_thinking.thinking += chunk_thinking
                delta_content.append(
                    ThinkingBlock(id=acc_thinking.id, thinking=chunk_thinking),
                )

            if msg.content:
                acc_text.text += msg.content
                delta_content.append(
                    TextBlock(id=acc_text.id, text=msg.content),
                )

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

            current_time = (datetime.now() - start_datetime).total_seconds()
            usage = ChatUsage(
                input_tokens=getattr(chunk, "prompt_eval_count", 0) or 0,
                output_tokens=getattr(chunk, "eval_count", 0) or 0,
                time=current_time,
            )

            if delta_content:
                yield ChatResponse(
                    id=response_id,
                    content=delta_content,
                    is_last=False,
                    usage=usage,
                )

        final_content: list = []
        if acc_thinking.thinking:
            final_content.append(acc_thinking)
        if acc_text.text:
            final_content.append(acc_text)
        for tool_id, tc in acc_tool_calls.items():
            final_content.append(
                ToolCallBlock(id=tool_id, name=tc["name"], input=tc["input"]),
            )

        yield ChatResponse(
            id=response_id,
            content=final_content,
            is_last=True,
            usage=usage,
        )

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
                    input=json.dumps(tool_call.function.arguments),
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
            id=getattr(response, "id", None) or uuid.uuid4().hex,
            content=content_blocks,
            is_last=True,
            usage=usage,
        )
