# -*- coding: utf-8 -*-
"""The Anthropic chat model implementation."""
import json
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse, StructuredResponse
from .._model_usage import ChatUsage
from ..._utils._common import _generate_id
from ...credential import AnthropicCredential
from ...formatter import FormatterBase, AnthropicChatFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from anthropic.types.message import Message
    from anthropic import AsyncStream
else:
    Message = Any
    AsyncStream = Any


class AnthropicChatModel(ChatModelBase):
    """The Anthropic chat model."""

    type: Literal["anthropic_chat"] = "anthropic_chat"
    """The type of the chat model."""

    class Parameters(BaseModel):
        """The parameters for the Anthropic chat model."""

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
            title="Thinking Budget",
            description="The thinking budget for the LLM output.",
            gt=0,
        )

    def __init__(
        self,
        credential: AnthropicCredential,
        model: str,
        parameters: "AnthropicChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 200000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Anthropic chat model.

        Args:
            credential (`AnthropicCredential`):
                The Anthropic credential used to authenticate API calls.
            model (`str`):
                The Anthropic model name, e.g. ``claude-opus-4-7``.
            parameters (`AnthropicChatModel.Parameters | None`, defaults to \
            `None`):
                The Anthropic API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the Anthropic API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `200000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the Anthropic API. When ``None``, an
                ``AnthropicChatFormatter`` instance will be used.
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
        self.formatter = formatter or AnthropicChatFormatter()
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
        """Get the response from Anthropic chat completions API by the given
        arguments.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required, and `name` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas.
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            **generate_kwargs (`Any`):
                The keyword arguments for Anthropic chat completions API.

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

        # Anthropic requires max_tokens; fall back to a safe default when
        # the user hasn't configured one explicitly.
        max_tokens = self.parameters.max_tokens or 8192

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "stream": self.stream,
            **generate_kwargs,
        }

        # Anthropic extended thinking — only set when explicitly enabled.
        # Anthropic requires max_tokens > budget_tokens strictly.
        if self.parameters.thinking_enable and "thinking" not in kwargs:
            budget = self.parameters.thinking_budget or (max_tokens // 2)
            if budget >= max_tokens:
                # Auto-expand max_tokens to satisfy the strict inequality.
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

        # Extract the system message
        if formatted_messages and formatted_messages[0]["role"] == "system":
            kwargs["system"] = formatted_messages[0]["content"]
            formatted_messages = formatted_messages[1:]

        kwargs["messages"] = formatted_messages

        start_datetime = datetime.now()

        response = await client.messages.create(**kwargs)

        if self.stream:
            return self._parse_anthropic_stream_completion_response(
                start_datetime,
                response,
            )

        # Non-streaming response
        parsed_response = await self._parse_anthropic_completion_response(
            start_datetime,
            response,
        )

        return parsed_response

    async def _parse_anthropic_completion_response(
        self,
        start_datetime: datetime,
        response: Message,
    ) -> ChatResponse:
        """Given an Anthropic Message object, extract the content blocks and
        usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Message`):
                Anthropic Message object to parse.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True`` containing
                the extracted content blocks and usage.
        """
        content_blocks: List[ThinkingBlock | TextBlock | ToolCallBlock] = []

        if hasattr(response, "content") and response.content:
            for content_block in response.content:
                if (
                    hasattr(content_block, "type")
                    and content_block.type == "thinking"
                ):
                    thinking_block = ThinkingBlock(
                        thinking=content_block.thinking,
                        signature=getattr(
                            content_block,
                            "signature",
                            "",
                        )
                        or "",
                    )
                    content_blocks.append(thinking_block)

                elif (
                    hasattr(content_block, "type")
                    and content_block.type == "redacted_thinking"
                ):
                    content_blocks.append(
                        ThinkingBlock(
                            thinking="",
                            redacted_thinking_data=getattr(
                                content_block,
                                "data",
                                "",
                            ),
                        ),
                    )

                elif (
                    hasattr(content_block, "type")
                    and content_block.type == "text"
                ):
                    content_blocks.append(
                        TextBlock(text=content_block.text),
                    )

                elif (
                    hasattr(content_block, "type")
                    and content_block.type == "tool_use"
                ):
                    content_blocks.append(
                        ToolCallBlock(
                            id=content_block.id,
                            name=content_block.name,
                            input=json.dumps(
                                content_block.input,
                                ensure_ascii=False,
                            ),
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
                )
                or 0,
                cache_input_tokens=getattr(
                    u,
                    "cache_read_input_tokens",
                    0,
                )
                or 0,
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

    async def _parse_anthropic_stream_completion_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given an Anthropic streaming response, extract the content blocks
        and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                Anthropic AsyncStream object to parse.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True`` containing the
                fully accumulated content blocks and usage.
        """

        usage = None
        response_id: str = _generate_id()
        text_id: str = _generate_id()
        thinking_id: str = _generate_id()
        # The mapping from index to tool call id
        tool_call_mapping: dict = OrderedDict()

        async for event in response:
            delta_res = ChatResponse(content=[], is_last=False, id=response_id)

            if event.type == "message_start":
                message = event.message

                # Update the response ID if exists
                response_id = getattr(message, "id", None) or response_id
                delta_res.id = response_id

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
                    tool_block = event.content_block
                    # Record the id and name
                    tool_call_mapping[event.index] = (
                        tool_block.id,
                        tool_block.name,
                    )
                    # New tool call block with empty input
                    delta_res.append_tool_call(
                        block_id=tool_block.id,
                        name=tool_block.name,
                        input="",
                    )

                elif event.content_block.type == "redacted_thinking":
                    delta_res.append_thinking(
                        "",
                        block_id=_generate_id(),
                        redacted_thinking_data=getattr(
                            event.content_block,
                            "data",
                            "",
                        ),
                    )

            elif event.type == "content_block_delta":
                block_index = event.index
                delta = event.delta

                # Text block
                if delta.type == "text_delta":
                    delta_res.append_text(delta.text, block_id=text_id)

                # Thinking block
                elif delta.type == "thinking_delta":
                    delta_res.append_thinking(
                        delta.thinking,
                        block_id=thinking_id,
                    )

                # Special handling for Anthropic API that requires signature
                elif delta.type == "signature_delta":
                    delta_res.append_thinking(
                        "",
                        block_id=thinking_id,
                        signature=delta.signature,
                    )

                # Tool call block
                elif (
                    delta.type == "input_json_delta"
                    and block_index in tool_call_mapping
                ):
                    block_id, name = tool_call_mapping[block_index]
                    delta_res.append_tool_call(
                        block_id=block_id,
                        name=name,
                        input=delta.partial_json or "",
                    )

            elif event.type == "message_delta":
                if event.usage and usage:
                    usage.output_tokens = event.usage.output_tokens

            if delta_res.content:
                delta_res.usage = usage
                yield delta_res

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, dict | None]:
        """Validate and format tools and tool_choice for Anthropic.

        Converts tool schemas to Anthropic's flat format and maps
        tool_choice modes to Anthropic's type-based format. When
        ``tool_choice.tools`` is specified the schemas list is filtered
        to only those tools. When ``tool_choice.mode`` is a specific tool
        name (str) the model is forced to call exactly that tool without
        needing to filter the list, preserving prompt-cache efficiency.

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
                    f"Invalid schema: {schema}, "
                    "expect key 'name' in 'function' field."
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
        """Anthropic-specific override for structured output.

        Anthropic's extended thinking mode only supports
        ``tool_choice={"type": "auto"}`` or ``{"type": "none"}``; any
        forcing form (``"any"`` or a specific tool) raises an API error.
        When ``thinking_enable`` is on we default ``tool_choice`` to
        ``"auto"`` and rely on the base class's injected system-reminder
        prompt to guide the model. When thinking is disabled, this falls
        through to the base implementation (force the structured-output
        tool).

        See:
         https://platform.claude.com/docs/en/build-with-claude/extended-thinking#extended-thinking-with-tool-use

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
