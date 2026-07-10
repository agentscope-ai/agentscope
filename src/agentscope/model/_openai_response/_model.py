# -*- coding: utf-8 -*-
"""The OpenAI Responses API chat model implementation."""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, List, TYPE_CHECKING, Type

from pydantic import BaseModel, Field

from ..._utils._common import _generate_id
from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OpenAICredential
from ...formatter import FormatterBase, OpenAIResponseFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.responses import Response
    from openai.types.responses import ResponseStreamEvent
    from openai import AsyncStream
else:
    Response = Any
    ResponseStreamEvent = Any
    AsyncStream = Any

# kwargs accepted by Chat Completions but NOT by the Responses API.
_RESPONSES_UNSUPPORTED_KWARGS = frozenset({"modalities", "audio"})


class OpenAIResponseModel(ChatModelBase):
    """The OpenAI Responses API chat model.

    Compared with the Chat Completions API, the Responses API provides
    first-class streaming events for reasoning / thinking, text output
    and function-call arguments, which makes it a natural fit for models
    that expose chain-of-thought reasoning (e.g. ``o3``, ``o4-mini``).
    """

    class Parameters(BaseModel):
        """The parameters for the OpenAI Response API model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of output tokens.",
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

    type: Literal["openai_response"] = "openai_response"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OpenAICredential,
        model: str,
        parameters: "OpenAIResponseModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 200000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the OpenAI Responses API chat model.

        Args:
            credential (`OpenAICredential`):
                The OpenAI credential used to authenticate API calls.
            model (`str`):
                The OpenAI model name, e.g. ``o3`` or ``o4-mini``.
            parameters (`OpenAIResponseModel.Parameters | None`, defaults \
            to `None`):
                The OpenAI Responses API parameters. When ``None``, the
                default parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the OpenAI Responses API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `200000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the OpenAI Responses API. When ``None``, an
                ``OpenAIResponseFormatter`` instance will be used.
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
        self.formatter = formatter or OpenAIResponseFormatter()
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
        """Call the OpenAI Responses API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                A list of input items for the Responses API.
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

        api_kwargs: dict[str, Any] = {
            "model": model_name,
            "input": formatted_messages,
            "stream": self.stream,
        }

        if self.parameters.max_tokens is not None:
            api_kwargs["max_output_tokens"] = self.parameters.max_tokens

        if self.parameters.temperature is not None:
            api_kwargs["temperature"] = self.parameters.temperature

        if (
            self.parameters.thinking_enable
            and self.parameters.reasoning_effort
        ):
            api_kwargs["reasoning"] = {
                "effort": self.parameters.reasoning_effort,
            }

        # The Responses API does not yet support audio output
        # (modalities / audio params).  Strip them so callers that
        # mistakenly pass Chat-Completions-style audio kwargs don't
        # trigger a TypeError.
        # https://developers.openai.com/api/docs/guides/migrate-to-responses
        api_kwargs.update(
            {
                k: v
                for k, v in generate_kwargs.items()
                if k not in _RESPONSES_UNSUPPORTED_KWARGS
            },
        )

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            api_kwargs["tools"] = fmt_tools
        if fmt_tool_choice is not None:
            api_kwargs["tool_choice"] = fmt_tool_choice

        start_datetime = datetime.now()
        response = await client.responses.create(**api_kwargs)

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: "AsyncStream[ResponseStreamEvent]",
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the OpenAI Responses API streaming response.

        Each event yields only the *delta* content produced by that event so
        that the base ``ChatModelBase.__call__`` can accumulate the full
        response. Because the Responses API only returns the upstream
        response id on the final ``response.completed`` event, we assign a
        locally-generated ``response_id`` up-front so every delta chunk
        carries a stable id.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream[ResponseStreamEvent]`):
                The OpenAI Responses API async stream object.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``.
                The base class ``__call__`` accumulates them and emits the
                final ``is_last=True`` chunk.
        """
        usage: ChatUsage | None = None
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()
        # Mapping from Responses API item id (fc_xxx) to (call_id, name)
        # so subsequent argument deltas can be routed to the right tool
        # call block.
        tool_call_mapping: dict = OrderedDict()

        async for event in response:
            event_type = event.type
            delta_res = ChatResponse(
                content=[],
                is_last=False,
                id=response_id,
            )

            if event_type == "response.reasoning_summary_text.delta":
                # Reasoning summary text is NOT emitted by all models.
                # As of 2026-05, o1 and o4-mini do not stream reasoning
                # summary deltas. This handler exists for forward
                # compatibility with models that do expose it.
                delta_res.append_thinking(
                    event.delta,
                    block_id=thinking_id,
                )

            elif event_type == "response.output_text.delta":
                delta_res.append_text(event.delta, block_id=text_id)

            elif event_type == "response.output_item.added":
                item = event.item
                if getattr(item, "type", None) == "function_call":
                    # item.call_id (call_xxx) is used as ToolCallBlock.id
                    # so the formatter can echo it back as
                    # function_call.call_id / function_call_output.call_id.
                    # Only record the mapping here — do NOT emit an
                    # empty-input delta so downstream consumers don't see
                    # a leading no-op chunk. The block is created on the
                    # first argument delta below.
                    tool_call_mapping[item.id] = (
                        item.call_id,
                        getattr(item, "name", "") or "unknown",
                    )

            elif event_type == "response.function_call_arguments.delta":
                item_id = event.item_id
                if item_id in tool_call_mapping:
                    call_id, name = tool_call_mapping[item_id]
                    delta_res.append_tool_call(
                        block_id=call_id,
                        name=name,
                        input=event.delta or "",
                    )

            elif event_type == "response.completed":
                resp = event.response
                if resp.usage:
                    u = resp.usage
                    details = getattr(u, "input_tokens_details", None)
                    usage = ChatUsage(
                        input_tokens=u.input_tokens,
                        output_tokens=u.output_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        cache_input_tokens=getattr(
                            details,
                            "cached_tokens",
                            0,
                        )
                        if details
                        else 0,
                    )
                # Attach reasoning item id metadata from the completed
                # response so the formatter can echo it back in
                # multi-turn history. The Responses API requires every
                # function_call item to be accompanied by its preceding
                # reasoning item (see the function-calling guide); the
                # reasoning item may have an empty summary when the model
                # does not expose it (e.g. o1/o4-mini as of 2026-05).
                for output_item in getattr(resp, "output", []):
                    if getattr(output_item, "type", None) == "reasoning":
                        reasoning_item_id = getattr(output_item, "id", None)
                        if reasoning_item_id:
                            delta_res.append_thinking(
                                thinking="",
                                block_id=thinking_id,
                                reasoning_item_id=reasoning_item_id,
                            )

            if delta_res.content or usage:
                delta_res.usage = usage
                yield delta_res

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: "Response",
    ) -> ChatResponse:
        """Parse the OpenAI Responses API non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Response`):
                The OpenAI Responses API response object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        for item in response.output:
            item_type = getattr(item, "type", None)

            if item_type == "reasoning":
                reasoning_item_id = getattr(item, "id", None)
                combined_summary = " ".join(
                    getattr(s, "text", "")
                    for s in getattr(item, "summary", [])
                    if getattr(s, "text", "")
                )
                if combined_summary:
                    content_blocks.append(
                        ThinkingBlock(
                            type="thinking",
                            thinking=combined_summary,
                            reasoning_item_id=reasoning_item_id,
                        ),
                    )

            elif item_type == "message":
                for part in getattr(item, "content", []):
                    if getattr(part, "type", None) == "output_text":
                        content_blocks.append(
                            TextBlock(type="text", text=part.text),
                        )

            elif item_type == "function_call":
                content_blocks.append(
                    ToolCallBlock(
                        id=item.call_id,
                        name=item.name,
                        input=getattr(item, "arguments", "") or "{}",
                    ),
                )

        usage = None
        if response.usage:
            u = response.usage
            details = getattr(u, "input_tokens_details", None)
            usage = ChatUsage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
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

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, str | dict | None]:
        """Validate and format tools and tool_choice for the Responses API.

        The full ``tools`` list is always sent unchanged to maximise prompt
        cache hits.  When ``tool_choice.tools`` restricts the callable
        subset, the ``allowed_tools`` tool_choice format is used instead of
        filtering the schemas list.

        Args:
            tools (`list[dict] | None`, optional):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, str | dict | None]`:
                A tuple of ``(formatted_tools, formatted_tool_choice)``
                ready for the Responses API.
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)

        fmt_tools = None
        if tools:
            fmt_tools = [
                {"type": "function", **tool["function"]} for tool in tools
            ]

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice.mode

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            return fmt_tools, {"type": "function", "name": mode}

        if tool_choice.tools:
            return fmt_tools, {
                "type": "allowed_tools",
                "mode": mode,
                "tools": [
                    {"type": "function", "name": name}
                    for name in tool_choice.tools
                ],
            }

        return fmt_tools, mode
