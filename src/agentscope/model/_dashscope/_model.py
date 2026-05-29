# -*- coding: utf-8 -*-
"""The DashScope chat model class (OpenAI-compatible implementation)."""
import warnings
from collections import OrderedDict
from datetime import datetime
from typing import Any, AsyncGenerator, List, Literal, Type, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse, StructuredResponse
from .._model_usage import ChatUsage
from ...credential import DashScopeCredential
from ...formatter import FormatterBase, DashScopeChatFormatter
from ...message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
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
            title="Thinking budget",
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

    type: Literal["dashscope_chat"] = "dashscope_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str,
        parameters: "DashScopeChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
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
            context_size=context_size,
        )
        self.formatter = formatter or DashScopeChatFormatter()
        self.client_kwargs = client_kwargs or {}

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

        request_kwargs.update(kwargs)

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            request_kwargs["tools"] = fmt_tools
            if not self.parameters.parallel_tool_calls:
                request_kwargs["parallel_tool_calls"] = False
        if fmt_tool_choice is not None:
            request_kwargs["tool_choice"] = fmt_tool_choice

        extra_body: dict[str, Any] = {}
        if self.parameters.thinking_enable is not None:
            extra_body["enable_thinking"] = self.parameters.thinking_enable
        if self.parameters.thinking_budget is not None:
            extra_body["thinking_budget"] = self.parameters.thinking_budget
        if self.parameters.top_k is not None:
            extra_body["top_k"] = self.parameters.top_k

        if extra_body:
            request_kwargs.setdefault("extra_body", {})
            request_kwargs["extra_body"].update(extra_body)

        if self.stream:
            request_kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()
        response = await client.chat.completions.create(**request_kwargs)

        if self.stream:
            audio_cfg = request_kwargs.get("audio")
            audio_fmt = (
                audio_cfg.get("format", "wav")
                if isinstance(audio_cfg, dict)
                else "wav"
            )
            return self._parse_stream_response(
                start_datetime,
                response,
                audio_fmt,
            )

        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
        audio_format: str = "wav",
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the DashScope streaming response (OpenAI-compatible format).

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                The OpenAI-compatible async stream object.
            audio_format (`str`, defaults to ``"wav"``):
                The audio format requested (used to set the media type on
                the output ``DataBlock``).

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        usage = None
        response_id: str | None = None
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        acc_tool_calls: OrderedDict = OrderedDict()
        acc_audio_data: str = ""

        async with response as stream:
            async for chunk in stream:
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

                response_id = response_id or getattr(chunk, "id", None)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                delta_thinking = (
                    getattr(delta, "reasoning_content", None) or ""
                )
                delta_text = getattr(delta, "content", None) or ""

                # Collect audio output from Omni models (delta.audio.data)
                delta_audio = getattr(delta, "audio", None)
                if delta_audio is not None:
                    if isinstance(delta_audio, dict):
                        audio_chunk = delta_audio.get("data", "")
                    else:
                        audio_chunk = getattr(delta_audio, "data", "") or ""
                    if audio_chunk:
                        acc_audio_data += audio_chunk

                acc_thinking.thinking += delta_thinking
                acc_text.text += delta_text

                delta_tool_call_blocks: List[ToolCallBlock] = []
                for tool_call in getattr(delta, "tool_calls", None) or []:
                    idx = tool_call.index
                    args = (
                        tool_call.function.arguments
                        if tool_call.function
                        else ""
                    ) or ""
                    if idx in acc_tool_calls:
                        acc_tool_calls[idx]["input"] += args
                    else:
                        acc_tool_calls[idx] = {
                            "id": tool_call.id or "",
                            "name": (
                                tool_call.function.name
                                if tool_call.function
                                else ""
                            ),
                            "input": args,
                        }
                    tc = acc_tool_calls[idx]
                    delta_tool_call_blocks.append(
                        ToolCallBlock(
                            id=tc["id"],
                            name=tc["name"],
                            input=args,
                        ),
                    )

                delta_contents: List[
                    TextBlock | ToolCallBlock | ThinkingBlock
                ] = []
                if delta_thinking:
                    delta_contents.append(
                        ThinkingBlock(
                            id=acc_thinking.id,
                            thinking=delta_thinking,
                        ),
                    )
                if delta_text:
                    delta_contents.append(
                        TextBlock(id=acc_text.id, text=delta_text),
                    )
                delta_contents.extend(delta_tool_call_blocks)

                if delta_contents:
                    _kwargs: dict[str, Any] = {
                        "content": delta_contents,
                        "usage": usage,
                        "is_last": False,
                    }
                    if response_id:
                        _kwargs["id"] = response_id
                    yield ChatResponse(**_kwargs)

        final_contents: List[
            TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
        ] = []
        if acc_thinking.thinking:
            final_contents.append(acc_thinking)
        if acc_text.text:
            final_contents.append(acc_text)
        for tc in acc_tool_calls.values():
            final_contents.append(
                ToolCallBlock(
                    id=tc["id"],
                    name=tc["name"],
                    input=tc["input"],
                ),
            )
        if acc_audio_data:
            final_contents.append(
                DataBlock(
                    source=Base64Source(
                        data=acc_audio_data,
                        media_type=f"audio/{audio_format}",
                    ),
                ),
            )

        _final_kwargs: dict[str, Any] = {
            "content": final_contents,
            "usage": usage,
            "is_last": True,
        }
        if response_id:
            _final_kwargs["id"] = response_id
        yield ChatResponse(**_final_kwargs)

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

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> StructuredResponse:
        """DashScope-specific override for structured output.

        DashScope rejects ``tool_choice="required"`` or an object-form
        ``tool_choice`` when thinking mode is enabled. In that case we
        default ``tool_choice`` to ``"auto"`` and rely on the base class's
        injected system-reminder prompt to guide the model. When thinking
        is disabled, this falls through to the base implementation.

        See: https://help.aliyun.com/en/model-studio/qwen-function-calling

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[Msg]`):
                The context for the LLM to generate the structured output.
            structured_model (`Type[BaseModel] | dict`):
                A Pydantic model class or a JSON schema dict describing the
                required output structure.
            tool_choice (`ToolChoice | None`, defaults to `None`):
                The tool_choice forwarded to ``_call_api``. When ``None``
                and thinking mode is enabled, it is downgraded to
                ``ToolChoice(mode="auto")``; otherwise the base default
                (force the structured-output tool) is used.
            **kwargs (`Any`):
                Additional keyword arguments forwarded to ``_call_api``.

        Returns:
            `StructuredResponse`:
                The structured response whose ``content`` is the validated
                output dict matching ``structured_model``.
        """
        if tool_choice is None and self.parameters.thinking_enable:
            tool_choice = ToolChoice(mode="auto")
        return await super()._call_api_with_structured_output(
            model_name=model_name,
            messages=messages,
            structured_model=structured_model,
            tool_choice=tool_choice,
            **kwargs,
        )
