# -*- coding: utf-8 -*-
"""The DeepSeek chat model implementation."""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ..._utils._common import _generate_id
from ...credential import DeepSeekCredential
from ...formatter import FormatterBase, DeepSeekChatFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any


class DeepSeekChatModel(ChatModelBase):
    """The DeepSeek chat model."""

    class Parameters(BaseModel):
        """The parameters for the DeepSeek chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description=(
                "Whether to enable thinking mode. When enabled, the model "
                "outputs a chain-of-thought reasoning before the final "
                "answer via the reasoning_content field."
            ),
        )

        reasoning_effort: Literal["high", "max"] | None = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "Controls the depth of reasoning in thinking mode. "
                "Supported values: high (default), max. "
                "For compatibility, low/medium map to high, "
                "xhigh maps to max."
            ),
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            le=2,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

    type: Literal["deepseek_chat"] = "deepseek_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: DeepSeekCredential,
        model: str,
        parameters: "DeepSeekChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 65536,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the DeepSeek chat model.

        Args:
            credential (`DeepSeekCredential`):
                The DeepSeek credential used to authenticate API calls.
            model (`str`):
                The DeepSeek model name, e.g. ``deepseek-chat``.
            parameters (`DeepSeekChatModel.Parameters | None`, defaults to \
            `None`):
                The DeepSeek API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the DeepSeek API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `65536`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the DeepSeek API. When ``None``, a
                ``DeepSeekChatFormatter`` instance will be used.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``openai.AsyncClient``
                (e.g. ``timeout``, ``default_headers``, ``http_client``).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )
        self.formatter = formatter or DeepSeekChatFormatter()
        self.client_kwargs = client_kwargs or {}

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        import openai

        return (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the DeepSeek chat completions API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                A list of message dicts with ``role`` and ``content`` keys.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas.
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            **generate_kwargs (`Any`):
                Extra keyword arguments forwarded to the API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                A ``ChatResponse`` when streaming is disabled, or an async
                generator of ``ChatResponse`` objects when streaming is
                enabled.
        """
        import openai

        client = openai.AsyncClient(
            **{
                "api_key": self.credential.api_key.get_secret_value(),
                "base_url": self.credential.base_url,
                **self.client_kwargs,
            },
        )

        formatted_messages = await self.formatter.format(messages)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": formatted_messages,
            "stream": self.stream,
        }

        if self.parameters.max_tokens is not None:
            kwargs["max_tokens"] = self.parameters.max_tokens

        if self.parameters.temperature is not None:
            kwargs["temperature"] = self.parameters.temperature

        if self.parameters.top_p is not None:
            kwargs["top_p"] = self.parameters.top_p

        if self.parameters.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.parameters.reasoning_effort

        kwargs.update(generate_kwargs)

        thinking_type = (
            "enabled" if self.parameters.thinking_enable else "disabled"
        )
        kwargs.setdefault("extra_body", {})
        kwargs["extra_body"].setdefault("thinking", {})
        kwargs["extra_body"]["thinking"].setdefault("type", thinking_type)

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)

        if fmt_tools:
            kwargs["tools"] = fmt_tools

        if fmt_tool_choice is not None:
            kwargs["tool_choice"] = fmt_tool_choice

        if self.stream:
            kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()
        response = await client.chat.completions.create(**kwargs)

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the DeepSeek streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                The OpenAI-compatible async stream object.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        usage = None
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()
        # The mapping from index to tool call id
        tool_call_mapping: dict = OrderedDict()

        async with response as stream:
            async for chunk in stream:
                delta_res = ChatResponse(
                    content=[],
                    is_last=False,
                    id=response_id,
                )

                # Update the response ID if exists
                response_id = getattr(chunk, "id", None) or response_id
                delta_res.id = response_id

                if chunk.usage:
                    u = chunk.usage
                    usage = ChatUsage(
                        input_tokens=u.prompt_tokens,
                        output_tokens=u.completion_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        cache_input_tokens=getattr(
                            u,
                            "prompt_cache_hit_tokens",
                            0,
                        ),
                    )

                if not chunk.choices:
                    # DeepSeek emits a trailing usage-only chunk with no
                    # choices; forward it as an empty-content delta so the
                    # base class ``__call__`` can absorb ``usage`` into
                    # ``acc_res``. The empty delta itself is filtered out
                    # of the surfaced stream by ``_stream``.
                    if usage is not None:
                        delta_res.usage = usage
                        yield delta_res
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Thinking block
                if getattr(delta, "reasoning_content", None):
                    delta_res.append_thinking(
                        block_id=thinking_id,
                        thinking=delta.reasoning_content,
                    )

                # Text
                if getattr(delta, "content", None):
                    delta_res.append_text(
                        block_id=text_id,
                        text=delta.content,
                    )

                # Tool call
                for tool_call in getattr(delta, "tool_calls", None) or []:
                    index = tool_call.index
                    fn = getattr(tool_call, "function", None)
                    delta_name = getattr(fn, "name", None) if fn else None
                    delta_args = getattr(fn, "arguments", None) if fn else None

                    # Record the id and name in case following deltas
                    # don't provide them
                    if index not in tool_call_mapping:
                        tool_call_mapping[index] = (
                            tool_call.id,
                            delta_name or "unknown",
                        )

                    stored_id, stored_name = tool_call_mapping[index]

                    delta_res.append_tool_call(
                        block_id=tool_call.id or stored_id,
                        name=delta_name or stored_name,
                        input=delta_args or "",
                    )

                if delta_res.content or usage:
                    delta_res.usage = usage
                    yield delta_res

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: ChatCompletion,
    ) -> ChatResponse:
        """Parse the DeepSeek non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                The OpenAI-compatible chat completion object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if response.choices:
            choice = response.choices[0]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if isinstance(reasoning, str) and reasoning:
                content_blocks.append(ThinkingBlock(thinking=reasoning))

            if choice.message.content:
                content_blocks.append(TextBlock(text=choice.message.content))

            for tool_call in choice.message.tool_calls or []:
                content_blocks.append(
                    ToolCallBlock(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        input=tool_call.function.arguments,
                    ),
                )

        usage = None
        if response.usage:
            u = response.usage
            usage = ChatUsage(
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                cache_input_tokens=getattr(
                    u,
                    "prompt_cache_hit_tokens",
                    0,
                ),
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

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, str | dict | None]:
        """Validate, filter, and format tools and tool_choice for the DeepSeek
        API.

        When ``tool_choice.tools`` is specified the schemas list is filtered
        to only those tools. When ``tool_choice.mode`` is a specific tool name
        (str) the model is forced to call exactly that tool without needing to
        filter the list, preserving prompt-cache efficiency.

        Args:
            tools (`list[dict] | None`, optional):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, str | dict | None]`:
                A tuple of (formatted_tools, formatted_tool_choice).
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.tools:
                allowed = set(tool_choice.tools)
                tools = [t for t in tools if t["function"]["name"] in allowed]

        if not tool_choice:
            return tools, None

        mode = tool_choice.mode

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            return tools, {"type": "function", "function": {"name": mode}}

        return tools, mode

    def _get_disable_thinking_kwargs(self) -> dict:
        """DeepSeek uses ``thinking.type=disabled`` in extra_body."""
        return {
            "extra_body": {"thinking": {"type": "disabled"}},
        }
