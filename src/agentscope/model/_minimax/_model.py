# -*- coding: utf-8 -*-
"""The MiniMax chat model implementation.

MiniMax officially recommends using the Anthropic-compatible API for calls
(see https://platform.minimax.io/docs/api-reference/text-anthropic-api).
This module routes ``MiniMaxChatModel`` through the ``anthropic`` SDK
against MiniMax's ``/anthropic`` endpoint, matching the behaviour of
:class:`agentscope.model.AnthropicChatModel` so users get the same
``thinking_enable`` / ``thinking_budget`` controls, the same tool-call
semantics, and a thinking block preserved across turns.
"""

from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse, StructuredResponse
from .._model_usage import ChatUsage
from ...credential import MiniMaxCredential
from ...formatter import FormatterBase, MiniMaxChatFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from anthropic.types.message import Message
    from anthropic import AsyncStream
else:
    Message = Any
    AsyncStream = Any


class MiniMaxChatModel(ChatModelBase):
    """The MiniMax chat model.

    Calls MiniMax via its officially recommended Anthropic-compatible API
    (``https://api.minimax.io/anthropic``) using the ``anthropic`` SDK.
    Supports extended thinking via the ``thinking_enable`` /
    ``thinking_budget`` parameters in the same way as
    :class:`agentscope.model.AnthropicChatModel`.
    """

    type: Literal["minimax_chat"] = "minimax_chat"
    """The type of the chat model."""

    class Parameters(BaseModel):
        """The parameters for the MiniMax chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description=(
                "The maximum number of tokens to generate in the chat "
                "completion."
            ),
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

    def __init__(
        self,
        credential: MiniMaxCredential,
        model: str,
        parameters: "MiniMaxChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 512000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the MiniMax chat model.

        Args:
            credential (`MiniMaxCredential`):
                The MiniMax credential used to authenticate API calls.
            model (`str`):
                The MiniMax model name, e.g. ``MiniMax-M3``.
            parameters (`MiniMaxChatModel.Parameters | None`, defaults to \
            `None`):
                The MiniMax API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the MiniMax API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `512000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the MiniMax Anthropic-compatible API. When
                ``None``, a ``MiniMaxChatFormatter`` instance will be used.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to
                ``anthropic.AsyncAnthropic`` (e.g. ``timeout``,
                ``default_headers``, ``http_client``, ``auth_token``).
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
        self.formatter = formatter or MiniMaxChatFormatter()
        self.client_kwargs = client_kwargs or {}

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        import anthropic

        return (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call MiniMax's Anthropic-compatible chat completions API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas.
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            **generate_kwargs (`Any`):
                The keyword arguments for the Anthropic-compatible API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                A ``ChatResponse`` when streaming is disabled, or an async
                generator of ``ChatResponse`` objects when streaming is
                enabled.
        """
        import anthropic

        client = anthropic.AsyncAnthropic(
            **{
                "api_key": self.credential.api_key.get_secret_value(),
                "base_url": self.credential.base_url,
                **self.client_kwargs,
            },
        )

        # The Anthropic-compatible endpoint requires `max_tokens`; fall back
        # to a safe default when the user hasn't configured one explicitly.
        max_tokens = self.parameters.max_tokens or 8192

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "stream": self.stream,
            **generate_kwargs,
        }

        # Extended thinking — only set when explicitly enabled. The
        # Anthropic-compatible API requires `max_tokens > budget_tokens`
        # strictly, so auto-expand max_tokens if the user-supplied budget
        # is too large.
        if self.parameters.thinking_enable and "thinking" not in kwargs:
            budget = self.parameters.thinking_budget or (max_tokens // 2)
            if budget >= max_tokens:
                max_tokens = budget + 1024
                kwargs["max_tokens"] = max_tokens
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools:
            kwargs["tools"] = fmt_tools
        if fmt_tool_choice is not None:
            kwargs["tool_choice"] = fmt_tool_choice

        formatted_messages = await self.formatter.format(messages)

        # Extract the system message — Anthropic-compatible APIs expect it
        # as a top-level `system` parameter, not in the messages array.
        if formatted_messages and formatted_messages[0]["role"] == "system":
            kwargs["system"] = formatted_messages[0]["content"]
            formatted_messages = formatted_messages[1:]

        kwargs["messages"] = formatted_messages

        start_datetime = datetime.now()

        response = await client.messages.create(**kwargs)

        if self.stream:
            return self._parse_stream_response(
                start_datetime,
                response,
            )

        return await self._parse_completion_response(
            start_datetime,
            response,
        )

    async def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: Message,
    ) -> ChatResponse:
        """Parse a non-streaming Anthropic-compatible response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Message`):
                Anthropic-compatible Message object to parse.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True`` containing
                the extracted content blocks and usage.
        """
        content_blocks: List[ThinkingBlock | TextBlock | ToolCallBlock] = []

        if hasattr(response, "content") and response.content:
            for content_block in response.content:
                block_type = getattr(content_block, "type", None)
                if block_type == "thinking":
                    content_blocks.append(
                        ThinkingBlock(
                            thinking=content_block.thinking,
                            signature=getattr(content_block, "signature", "")
                            or "",
                        ),
                    )
                elif block_type == "text":
                    content_blocks.append(
                        TextBlock(text=content_block.text),
                    )
                elif block_type == "tool_use":
                    import json

                    content_blocks.append(
                        ToolCallBlock(
                            id=content_block.id,
                            name=content_block.name,
                            input=json.dumps(content_block.input),
                        ),
                    )

        usage = None
        if response.usage:
            u = response.usage
            usage = ChatUsage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                cache_creation_input_tokens=getattr(
                    u,
                    "cache_creation_input_tokens",
                    0,
                ),
                cache_input_tokens=getattr(
                    u,
                    "cache_read_input_tokens",
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

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse a streaming Anthropic-compatible response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                Anthropic-compatible AsyncStream object to parse.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True`` containing the
                fully accumulated content blocks and usage.
        """
        usage = None
        response_id: str | None = None
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        thinking_signature = ""
        acc_tool_calls: OrderedDict = OrderedDict()

        async for event in response:
            delta_content: list = []

            if event.type == "message_start":
                message = event.message
                if response_id is None:
                    response_id = getattr(message, "id", None)
                if message.usage:
                    u = message.usage
                    usage = ChatUsage(
                        input_tokens=u.input_tokens,
                        output_tokens=getattr(u, "output_tokens", 0),
                        time=(datetime.now() - start_datetime).total_seconds(),
                        cache_creation_input_tokens=getattr(
                            u,
                            "cache_creation_input_tokens",
                            0,
                        ),
                        cache_input_tokens=getattr(
                            u,
                            "cache_read_input_tokens",
                            0,
                        ),
                    )

            elif event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    block_index = event.index
                    tool_block = event.content_block
                    acc_tool_calls[block_index] = {
                        "id": tool_block.id,
                        "name": tool_block.name,
                        "input": "",
                    }

            elif event.type == "content_block_delta":
                block_index = event.index
                delta = event.delta
                if delta.type == "text_delta":
                    acc_text.text += delta.text
                    delta_content.append(
                        TextBlock(id=acc_text.id, text=delta.text),
                    )
                elif delta.type == "thinking_delta":
                    acc_thinking.thinking += delta.thinking
                    delta_content.append(
                        ThinkingBlock(
                            id=acc_thinking.id,
                            thinking=delta.thinking,
                        ),
                    )
                elif delta.type == "signature_delta":
                    thinking_signature = delta.signature
                elif (
                    delta.type == "input_json_delta"
                    and block_index in acc_tool_calls
                ):
                    fragment = delta.partial_json or ""
                    acc_tool_calls[block_index]["input"] += fragment
                    tc = acc_tool_calls[block_index]
                    delta_content.append(
                        ToolCallBlock(
                            id=tc["id"],
                            name=tc["name"],
                            input=fragment,
                        ),
                    )

            elif event.type == "message_delta":
                if event.usage and usage:
                    usage.output_tokens = event.usage.output_tokens

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
        if acc_thinking.thinking:
            acc_thinking.signature = thinking_signature
            final_content.append(acc_thinking)
        if acc_text.text:
            final_content.append(acc_text)
        for tc in acc_tool_calls.values():
            final_content.append(
                ToolCallBlock(
                    id=tc["id"],
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

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, dict | None]:
        """Validate and format tools and tool_choice for the
        Anthropic-compatible API.

        Converts OpenAI-style tool schemas to Anthropic's flat format and
        maps tool_choice modes to Anthropic's type-based format. When
        ``tool_choice.tools`` is specified the schemas list is filtered to
        only those tools. When ``tool_choice.mode`` is a specific tool name
        (str) the model is forced to call exactly that tool without needing
        to filter the list, preserving prompt-cache efficiency.

        Args:
            tools (`list[dict] | None`, optional):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, dict | None]`:
                A tuple of (formatted_tools, formatted_tool_choice).
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.tools:
                allowed = set(tool_choice.tools)
                tools = [t for t in tools if t["function"]["name"] in allowed]

        fmt_tools = None
        if tools:
            fmt_tools = []
            for schema in tools:
                assert (
                    "function" in schema
                ), f"Invalid schema: {schema}, expect key 'function'."
                assert "name" in schema["function"], (
                    f"Invalid schema: {schema}, expect key 'name' in "
                    "'function' field."
                )
                fmt_tools.append(
                    {
                        "name": schema["function"]["name"],
                        "description": schema["function"].get(
                            "description",
                            "",
                        ),
                        "input_schema": schema["function"].get(
                            "parameters",
                            {},
                        ),
                    },
                )

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice.mode

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            # mode is a specific tool name — force call it
            return fmt_tools, {"type": "tool", "name": mode}

        type_mapping = {
            "auto": {"type": "auto"},
            "none": {"type": "none"},
            "required": {"type": "any"},
        }
        return fmt_tools, type_mapping[mode]

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> StructuredResponse:
        """MiniMax-specific override for structured output.

        Mirrors :class:`AnthropicChatModel`'s behaviour: when thinking is
        enabled, force ``tool_choice="auto"`` because the
        Anthropic-compatible API rejects any forcing form (``"any"`` or a
        specific tool) while extended thinking is on.

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
