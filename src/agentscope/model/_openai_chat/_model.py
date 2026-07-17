# -*- coding: utf-8 -*-
"""The OpenAI Chat Completions model implementation."""
import warnings
import base64
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List, Type

from pydantic import BaseModel, Field

from ..._utils._audio import _build_streaming_wav_header
from ..._utils._common import _generate_id, _flatten_json_schema
from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse, StructuredResponse
from .._model_usage import ChatUsage
from ...credential import OpenAICredential
from ...formatter import FormatterBase, OpenAIChatFormatter
from ...message import (
    Msg,
    ThinkingBlock,
    ToolCallBlock,
    TextBlock,
    DataBlock,
    Base64Source,
)
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any


class OpenAIChatModel(ChatModelBase):
    """The OpenAI Chat Completions model."""

    class Parameters(BaseModel):
        """The parameters for the OpenAI Chat model."""

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
                "Whether to enable reasoning for reasoning models "
                "(e.g. o3, o4-mini, gpt-5.5). Use reasoning_effort to "
                "control the depth of reasoning."
            ),
        )

        reasoning_effort: (
            Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
        ) = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "Controls the depth of reasoning for reasoning models "
                "(e.g. o3, o4-mini, gpt-5.5). Supported values are "
                "model-dependent and may include: none, minimal, low, "
                "medium, high, xhigh."
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

        parallel_tool_calls: bool = Field(
            default=True,
            title="Parallel Tool Calls",
            description="Whether to enable parallel tool calls.",
        )

        voice: str | None = Field(
            default=None,
            title="Voice",
            description=(
                "Voice for audio output on omni-style models (e.g. "
                "``gpt-audio-mini``). Setting this implicitly asks the "
                "model to speak its response — ``modalities`` is filled in "
                "automatically. Supported voices vary by model — see the "
                "model card's ``voice.suggestions``. Any value the API "
                "accepts works — the suggestions are convenience-only. "
                "Leave unset for text-only "
                "responses."
            ),
        )

    type: Literal["openai_chat"] = "openai_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OpenAICredential,
        model: str,
        parameters: "OpenAIChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 128000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the OpenAI chat model.

        Args:
            credential (`OpenAICredential`):
                The OpenAI credential used to authenticate API calls.
            model (`str`):
                The OpenAI model name, e.g. ``gpt-4.1``.
            parameters (`OpenAIChatModel.Parameters | None`, defaults to \
            `None`):
                The OpenAI Chat API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the OpenAI API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `128000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the OpenAI API. When ``None``, an
                ``OpenAIChatFormatter`` instance will be used.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``openai.AsyncClient``
                (e.g. ``timeout``, ``default_headers``, ``http_client``).
            extra_body (`dict[str, Any] | None`, defaults to `None`):
                Additional request body fields forwarded to
                OpenAI-compatible APIs.
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
        self.formatter = formatter or OpenAIChatFormatter()
        self.client_kwargs = client_kwargs or {}
        self.extra_body = dict(extra_body) if extra_body is not None else None

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
        """Call the OpenAI Chat Completions API.

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
                "organization": self.credential.organization,
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
            kwargs["max_completion_tokens"] = self.parameters.max_tokens

        if self.parameters.temperature is not None:
            kwargs["temperature"] = self.parameters.temperature

        if self.parameters.top_p is not None:
            kwargs["top_p"] = self.parameters.top_p

        if (
            self.parameters.thinking_enable
            and self.parameters.reasoning_effort
        ):
            kwargs["reasoning_effort"] = self.parameters.reasoning_effort

        if self.parameters.voice is not None:
            # Requesting audio output implies ``modalities`` must include
            # ``"audio"``; set it automatically so callers don't have to.
            # ``format`` is forced to ``pcm16``: OpenAI streaming only
            # supports ``pcm16`` (other formats raise 400), and we re-wrap
            # as WAV downstream so the frontend receives a playable block.
            kwargs["audio"] = {
                "voice": self.parameters.voice,
                "format": "pcm16",
            }
            kwargs["modalities"] = ["text", "audio"]

        if self.extra_body is not None:
            kwargs["extra_body"] = dict(self.extra_body)

        kwargs.update(generate_kwargs)

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)

        if fmt_tools:
            kwargs["tools"] = fmt_tools
            if not self.parameters.parallel_tool_calls:
                kwargs["parallel_tool_calls"] = False

        if fmt_tool_choice is not None:
            kwargs["tool_choice"] = fmt_tool_choice

        if self.stream:
            kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()
        response = await client.chat.completions.create(**kwargs)

        audio_cfg = kwargs.get("audio")
        audio_fmt = (
            audio_cfg.get("format", "wav")
            if isinstance(audio_cfg, dict)
            else "wav"
        )

        if self.stream:
            # Streaming wire format is always ``pcm16`` (forced above) and we
            # re-wrap it as WAV before yielding, so downstream sees ``wav``.
            return self._parse_stream_response(
                start_datetime,
                response,
            )

        return self._parse_completion_response(
            start_datetime,
            response,
            audio_fmt,
        )

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the OpenAI Chat streaming response.

        Upstream sends raw PCM16 (24kHz, 16-bit mono — OpenAI's only
        streaming-supported audio format). We prefix the first audio
        chunk with a streaming WAV header so the frontend can start
        playback immediately; downstream accumulation is handled by
        ``ChatResponse.append_data_block``.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                The OpenAI async stream object.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``.
                The base class ``__call__`` accumulates them and emits the
                final ``is_last=True`` chunk.
        """
        # ``True`` once the first audio chunk has been prefixed with a
        # streaming WAV header and yielded.
        audio_header_sent: bool = False

        usage = None
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()
        audio_id: str = _generate_id()
        # The mapping from index to (tool call id, tool call name)
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
                    details = getattr(u, "prompt_tokens_details", None)
                    usage = ChatUsage(
                        input_tokens=u.prompt_tokens,
                        output_tokens=u.completion_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        cache_input_tokens=getattr(
                            details,
                            "cached_tokens",
                            0,
                        )
                        if details
                        else 0,
                    )

                if not chunk.choices:
                    if delta_res.content or usage:
                        delta_res.usage = usage
                        yield delta_res
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Thinking
                delta_thinking = getattr(delta, "reasoning_content", None)
                if not isinstance(delta_thinking, str):
                    delta_thinking = getattr(delta, "reasoning", None)
                if isinstance(delta_thinking, str) and delta_thinking:
                    delta_res.append_thinking(
                        block_id=thinking_id,
                        thinking=delta_thinking,
                    )

                # Text
                delta_text = getattr(delta, "content", None) or ""

                # Audio (delta.audio.data / delta.audio.transcript). Omni
                # models deliver text via ``delta.audio.transcript`` rather
                # than ``delta.content``; fold it into ``delta_text`` so the
                # agent's streaming pipeline emits ``TextBlockDelta`` events
                # alongside the audio chunks.
                delta_audio = getattr(delta, "audio", None)
                if delta_audio is not None:
                    if isinstance(delta_audio, dict):
                        audio_chunk = delta_audio.get("data", "") or ""
                        transcript_chunk = (
                            delta_audio.get("transcript", "") or ""
                        )
                    else:
                        audio_chunk = getattr(delta_audio, "data", "") or ""
                        transcript_chunk = (
                            getattr(delta_audio, "transcript", "") or ""
                        )
                    if transcript_chunk:
                        delta_text += transcript_chunk
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

                if delta_text:
                    delta_res.append_text(
                        block_id=text_id,
                        text=delta_text,
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
        audio_format: str = "wav",
    ) -> ChatResponse:
        """Parse the OpenAI Chat non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                The OpenAI chat completion object.
            audio_format (`str`, defaults to ``"wav"``):
                The audio format requested (used to set the media type on
                the output ``DataBlock``).

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[
            TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
        ] = []

        if response.choices:
            choice = response.choices[0]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if not isinstance(reasoning, str):
                reasoning = getattr(choice.message, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                content_blocks.append(ThinkingBlock(thinking=reasoning))

            if choice.message.content:
                content_blocks.append(TextBlock(text=choice.message.content))

            # Extract audio output (message.audio.data /
            # message.audio.transcript)
            audio_obj = getattr(choice.message, "audio", None)
            if audio_obj is not None:
                if isinstance(audio_obj, dict):
                    audio_data = audio_obj.get("data", "")
                    audio_transcript = audio_obj.get("transcript", "")
                else:
                    audio_data = getattr(audio_obj, "data", "") or ""
                    audio_transcript = (
                        getattr(audio_obj, "transcript", "") or ""
                    )
                if not choice.message.content and audio_transcript:
                    content_blocks.append(TextBlock(text=audio_transcript))
                if audio_data:
                    content_blocks.append(
                        DataBlock(
                            source=Base64Source(
                                data=audio_data,
                                media_type=f"audio/{audio_format}",
                            ),
                        ),
                    )

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
            details = getattr(u, "prompt_tokens_details", None)
            usage = ChatUsage(
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                cache_input_tokens=getattr(
                    details,
                    "cached_tokens",
                    0,
                )
                if details
                else 0,
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

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> StructuredResponse:
        """OpenAI-compatible override for structured output.

        Some third-party providers that expose an OpenAI-compatible API (e.g.
        DeepSeek via DashScope) reject forced ``tool_choice`` when thinking
        mode is active.  When such a ``BadRequestError`` is encountered, this
        method automatically retries with ``tool_choice="auto"``.
        """
        import openai

        try:
            return await super()._call_api_with_structured_output(
                model_name=model_name,
                messages=messages,
                structured_model=structured_model,
                tool_choice=tool_choice,
                **kwargs,
            )
        except openai.BadRequestError as e:
            if "tool_choice" not in str(e):
                raise
            # Thinking mode rejects forced tool_choice; fall back to auto
            warnings.warn(
                f"Forced tool_choice rejected by provider ({e}), "
                "retrying with tool_choice='auto'.",
                stacklevel=2,
            )
            return await super()._call_api_with_structured_output(
                model_name=model_name,
                messages=messages,
                structured_model=structured_model,
                tool_choice=ToolChoice(mode="auto"),
                **kwargs,
            )

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, str | dict | None]:
        """Validate, filter, and format tools and tool_choice for the OpenAI
        Chat Completions API.

        When ``tool_choice.tools`` is specified the schemas list is filtered
        to only those tools. When ``tool_choice.mode`` is a specific tool name
        (str) the model is forced to call exactly that tool without needing to
        filter the list, preserving prompt-cache efficiency.

        Tool parameter schemas are flattened (``$ref`` / ``$defs`` resolved
        inline) so that providers which do not support JSON Schema references
        (e.g. GLM-5.x via OpenCode Go) receive a self-contained schema.

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

        if tools:
            tools = self._flatten_tool_schemas(tools)

        if not tool_choice:
            return tools, None

        mode = tool_choice.mode

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            return tools, {"type": "function", "function": {"name": mode}}

        return tools, mode

    @staticmethod
    def _flatten_tool_schemas(
        tools: list[dict],
    ) -> list[dict]:
        """Inline ``$ref`` / ``$defs`` in each tool's parameter schema.

        Args:
            tools (`list[dict]`):
                The list of tool dicts, each with a ``"function"`` key
                containing the tool name, description and ``"parameters"``
                JSON schema.

        Returns:
            `list[dict]`:
                A new list where each tool's ``parameters`` schema has all
                local ``$ref`` / ``$defs`` resolved inline.  Tools whose
                schema contained no references are returned unchanged (same
                object identity).
        """
        result = []
        for tool in tools:
            func = tool.get("function")
            if not isinstance(func, dict):
                result.append(tool)
                continue
            params = func.get("parameters")
            if not isinstance(params, dict):
                result.append(tool)
                continue
            flat = _flatten_json_schema(params)
            if flat is not params:
                tool = {**tool, "function": {**func, "parameters": flat}}
            result.append(tool)
        return result
