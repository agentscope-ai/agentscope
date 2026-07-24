# -*- coding: utf-8 -*-
"""The DashScope chat model class (OpenAI-compatible implementation)."""
import base64
import warnings
from collections import OrderedDict
from datetime import datetime
from typing import Any, AsyncGenerator, List, Literal, Type, TYPE_CHECKING

from pydantic import BaseModel, Field

from ..._utils._audio import _build_streaming_wav_header
from ..._utils._common import _generate_id
from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import DashScopeCredential
from ...formatter import FormatterBase, DashScopeChatFormatter
from ...message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any


class DashScopeChatModel(ChatModelBase):
    """The DashScope chat model (OpenAI-compatible implementation).

    This implementation uses the OpenAI Python SDK to call DashScope's
    OpenAI-compatible endpoint (``compatible-mode/v1``), which supports
    both text-only and multimodal (image/video) inputs through the same
    unified API.
    """

    class Parameters(BaseModel):
        """The parameters for DashScope LLM API."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description="The thinking enable for the LLM output.",
        )

        thinking_budget: int | None = Field(
            default=None,
            title="Thinking Budget",
            description="The thinking budget for the LLM output.",
            gt=0,
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            lt=2,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

        top_k: int | None = Field(
            default=None,
            title="Top K",
            description="The top K value for the LLM output.",
            gt=0,
            le=100,
        )

        parallel_tool_calls: bool = Field(
            default=True,
            title="Parallel Tool Calls",
            description="If enable parallel tool calls for the LLM output.",
        )

        voice: str | None = Field(
            default=None,
            title="Voice",
            description=(
                "Voice for audio output on omni-style models (e.g. "
                "``qwen3.5-omni-plus``). Setting this implicitly asks the "
                "model to speak its response — ``modalities`` is filled in "
                "automatically. Supported voices vary by model — see the "
                "model card's ``voice.suggestions``. Any value the API "
                "accepts works — the suggestions are convenience-only. "
                "Leave unset for text-only "
                "responses."
            ),
        )

    type: Literal["dashscope_chat"] = "dashscope_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str,
        parameters: "DashScopeChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 131072,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the DashScope chat model.

        Args:
            credential (`DashScopeCredential`):
                The DashScope credential used to authenticate API calls.
            model (`str`):
                The DashScope model name, e.g. ``qwen-plus``.
            parameters (`DashScopeChatModel.Parameters | None`, defaults to \
            `None`):
                The DashScope API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the DashScope API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `131072`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the DashScope API. When ``None``, a
                ``DashScopeChatFormatter`` instance will be used.
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
        self.formatter = formatter or DashScopeChatFormatter()
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
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the DashScope chat completions API via OpenAI-compatible
        endpoint.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                The Msg objects that will be formatted and sent to the API.
            tools (`list[dict] | None`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`ToolChoice | None`, default `None`):
                Controls which (if any) tool is called by the model.
            **kwargs (`Any`):
                The keyword arguments for DashScope chat completions API,
                e.g. ``temperature``, ``max_tokens``, ``top_p``, etc.
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

        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": formatted_messages,
            "stream": self.stream,
        }

        if self.parameters.max_tokens is not None:
            request_kwargs["max_tokens"] = self.parameters.max_tokens

        if self.parameters.temperature is not None:
            request_kwargs["temperature"] = self.parameters.temperature

        if self.parameters.top_p is not None:
            request_kwargs["top_p"] = self.parameters.top_p

        if self.parameters.voice is not None:
            # Requesting audio output implies ``modalities`` must include
            # ``"audio"``; set it automatically so callers don't have to.
            # ``format`` is forced to ``pcm16``: omni streaming delivers raw
            # PCM upstream regardless of the requested format, and we wrap
            # it as WAV in ``_parse_stream_response`` before yielding.
            request_kwargs["audio"] = {
                "voice": self.parameters.voice,
                "format": "pcm16",
            }
            request_kwargs["modalities"] = ["text", "audio"]

        request_kwargs.update(kwargs)

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            request_kwargs["tools"] = fmt_tools
            if not self.parameters.parallel_tool_calls:
                request_kwargs["parallel_tool_calls"] = False
        if fmt_tool_choice is not None:
            request_kwargs["tool_choice"] = fmt_tool_choice

        request_kwargs.setdefault("extra_body", {})
        eb = request_kwargs["extra_body"]

        if self.parameters.thinking_enable is not None:
            eb.setdefault(
                "enable_thinking",
                self.parameters.thinking_enable,
            )
        if self.parameters.thinking_budget is not None:
            eb.setdefault(
                "thinking_budget",
                self.parameters.thinking_budget,
            )
        if self.parameters.top_k is not None:
            eb.setdefault("top_k", self.parameters.top_k)

        if self.stream:
            request_kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()
        response = await client.chat.completions.create(**request_kwargs)

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the DashScope streaming response (OpenAI-compatible format).

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
        # ``True`` once the first audio chunk has been prefixed with a
        # streaming WAV header and yielded.
        audio_header_sent: bool = False

        usage = None
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()
        audio_id = _generate_id()
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
                    ptd = getattr(u, "prompt_tokens_details", None)
                    if ptd and hasattr(ptd, "cached_tokens"):
                        cache_read = ptd.cached_tokens or 0
                    else:
                        cache_read = 0
                    usage = ChatUsage(
                        input_tokens=u.prompt_tokens or 0,
                        output_tokens=u.completion_tokens or 0,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        cache_input_tokens=cache_read,
                    )

                if not chunk.choices:
                    if usage is not None:
                        delta_res.usage = usage
                        yield delta_res
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Thinking
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

                # Data block
                if getattr(delta, "audio", None):
                    delta_audio = getattr(delta, "audio", None)
                    if isinstance(delta_audio, dict):
                        audio_chunk = delta_audio.get("data", "")
                    else:
                        audio_chunk = getattr(delta_audio, "data", "")

                    if audio_chunk:
                        pcm_bytes = base64.b64decode(audio_chunk)
                        if not audio_header_sent:
                            payload = _build_streaming_wav_header() + pcm_bytes
                            audio_header_sent = True
                        else:
                            payload = pcm_bytes
                        # ``append_data_block`` expects the raw incremental
                        # media bytes and handles base64 encoding internally
                        # (see ``ChatResponse.append_data_block``); passing an
                        # already base64-encoded string here would result in
                        # double-encoding.
                        delta_res.append_data_block(
                            block_id=audio_id,
                            data=payload,
                            media_type="audio/wav",
                        )

                if delta_res.content or usage:
                    delta_res.usage = usage
                    yield delta_res

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: ChatCompletion,
    ) -> ChatResponse:
        """Parse the DashScope non-streaming response (OpenAI-compatible
        format).

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
            ptd = getattr(u, "prompt_tokens_details", None)
            if ptd and hasattr(ptd, "cached_tokens"):
                cache_read = ptd.cached_tokens or 0
            else:
                cache_read = 0
            usage = ChatUsage(
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                cache_input_tokens=cache_read,
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
        """Validate and format tools and tool_choice for DashScope.

        DashScope supports "auto", "none", and "required" modes in
        OpenAI-compatible format. When ``tool_choice.tools`` is specified
        the schemas list is filtered to only those tools. When
        ``tool_choice.mode`` is a specific tool name (str) the model is
        forced to call exactly that tool.

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

        fmt_tools = None
        if tools:
            for value in tools:
                if (
                    not isinstance(value, dict)
                    or "type" not in value
                    or value["type"] != "function"
                    or "function" not in value
                ):
                    raise ValueError(
                        f"Each schema must be a dict with 'type' as "
                        f"'function' and 'function' key, got {value}",
                    )
            fmt_tools = tools

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice.mode

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            return fmt_tools, {
                "type": "function",
                "function": {"name": mode},
            }

        if mode == "required":
            warnings.warn(
                f"'{mode}' is not fully supported by DashScope API. "
                "It will be converted to 'auto'.",
                DeprecationWarning,
                stacklevel=2,
            )
            return fmt_tools, "auto"

        return fmt_tools, mode

    def _get_disable_thinking_kwargs(self) -> dict:
        """DashScope uses ``enable_thinking=False`` in extra_body."""
        return {"extra_body": {"enable_thinking": False}}
