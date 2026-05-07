# -*- coding: utf-8 -*-
"""The Anthropic chat model implementation."""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, SecretStr, Field

from .. import ChatUsage, ChatModelBase
from ...formatter import FormatterBase, AnthropicChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...model import ChatResponse
from ...tool import ToolChoice
from ...tracing import trace_llm

if TYPE_CHECKING:
    from anthropic.types.message import Message
    from anthropic import AsyncStream
else:
    Message = Any
    AsyncStream = Any


class AnthropicCredential(BaseModel):
    """The Anthropic credential model."""

    type: Literal["anthropic_credential"] = "anthropic_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Anthropic API key",
    )
    """The API key."""

    base_url: str | None = Field(
        description="The base URL for the Anthropic API.",
        default=None,
    )
    """The base URL for the Anthropic API."""


class AnthropicChatModel(ChatModelBase):
    """The Anthropic chat model."""

    class Parameters(BaseModel):
        """The parameters for the Anthropic chat model."""

        max_tokens: int | None = Field(
            default=None,
            description=(
                "The maximum number of tokens to generate in the chat "
                "completion.",
            ),
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

    type: Literal["anthropic_chat"] = "anthropic_chat"
    """The type of the chat model."""

    credential: AnthropicCredential
    """The Anthropic credential model."""

    model: str = Field(
        title="Model",
        description="The LLM of Anthropic API.",
    )
    """The Anthropic API model."""

    stream: bool = Field(
        default=True,
        title="Enable Streaming Output.",
        description="The enable stream output for the LLM output.",
    )

    max_retries: int = Field(
        default=0,
        title="Max Retries",
        description="The maximum retries for the Anthropic API.",
        ge=0,
    )
    """The maximum retries for the Anthropic API."""

    parameters: Parameters = Field(
        default_factory=Parameters,
        title="Anthropic API parameters",
        description="The Anthropic API parameters",
    )
    """The Anthropic API parameters."""

    formatter: FormatterBase = Field(
        default_factory=AnthropicChatFormatter,
    )
    """The Anthropic API formatter, responsible for converting Msg objects into
    the format required by the Anthropic API."""

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
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
            api_key=self.credential.api_key.get_secret_value(),
            base_url=self.credential.base_url,
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

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            kwargs["tool_choice"] = self._format_tool_choice(
                tool_choice,
                tools,
            )

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
                        signature=getattr(content_block, "signature", "")
                        or "",
                    )
                    content_blocks.append(thinking_block)

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
            usage = ChatUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
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
        response_id: str | None = None
        # All delta should have the same block identifier
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        thinking_signature = ""
        # index -> {id, name, input}
        acc_tool_calls: OrderedDict = OrderedDict()

        async for event in response:
            delta_content: list = []

            if event.type == "message_start":
                message = event.message
                if response_id is None:
                    response_id = getattr(message, "id", None)
                if message.usage:
                    usage = ChatUsage(
                        input_tokens=message.usage.input_tokens,
                        output_tokens=getattr(
                            message.usage,
                            "output_tokens",
                            0,
                        ),
                        time=(datetime.now() - start_datetime).total_seconds(),
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
            input_str = tc["input"]
            final_content.append(
                ToolCallBlock(
                    id=tc["id"],
                    name=tc["name"],
                    input=input_str,
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

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the JSON schemas of the tool functions to the format that
        Anthropic API expects."""
        formatted_schemas = []
        for schema in schemas:
            assert (
                "function" in schema
            ), f"Invalid schema: {schema}, expect key 'function'."

            assert "name" in schema["function"], (
                f"Invalid schema: {schema}, "
                "expect key 'name' in 'function' field."
            )

            formatted_schemas.append(
                {
                    "name": schema["function"]["name"],
                    "description": schema["function"].get("description", ""),
                    "input_schema": schema["function"].get("parameters", {}),
                },
            )

        return formatted_schemas

    def _format_tool_choice(
        self,
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> dict | None:
        """Format tool_choice parameter for API compatibility.

        Args:
            tool_choice (`ToolChoice | None`):
                The unified tool choice parameter which can be a mode ("auto",
                "none", "required") or a specific function name.
            tools (`list[dict] | None`):
                The list of available tools, used for validation if
                tool_choice is a specific function name.

        Returns:
            `dict | None`:
                The formatted tool choice configuration dict, or None if
                tool_choice is None.
        """
        self._validate_tool_choice(tool_choice, tools)

        if tool_choice is None:
            return None

        type_mapping = {
            "auto": {"type": "auto"},
            "none": {"type": "none"},
            "required": {"type": "any"},
        }
        if tool_choice in type_mapping:
            return type_mapping[tool_choice]

        return {"type": "tool", "name": tool_choice}
