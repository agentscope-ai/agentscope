# -*- coding: utf-8 -*-
"""The Anthropic API model classes."""
import json
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    TYPE_CHECKING,
    List,
    Literal,
)
from collections import OrderedDict

from pydantic import BaseModel

from ._model_base import ChatModelBase
from ._model_response import ChatResponse
from ._model_usage import ChatUsage
from ._utils import ThinkingConfig
from ..formatter import FormatterBase, AnthropicChatFormatter
from ..message import TextBlock, ToolCallBlock, ThinkingBlock, Msg
from ..tracing import trace_llm
from ..types import JSONSerializableObject, ToolChoice

if TYPE_CHECKING:
    from anthropic.types.message import Message
    from anthropic import AsyncStream
else:
    Message = "anthropic.types.message.Message"
    AsyncStream = "anthropic.AsyncStream"


class AnthropicChatModel(ChatModelBase):
    """The Anthropic model wrapper for AgentScope."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        max_tokens: int = 2048,
        stream: bool = True,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        thinking_config: ThinkingConfig | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the Anthropic chat model.

        Args:
            model_name (`str`):
                The model names.
            api_key (`str`):
                The anthropic API key.
            max_tokens (`int`):
                Limit the maximum token count the model can generate.
            stream (`bool`):
                The streaming output or not
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
            thinking_config (`ThinkingConfig | None`, default `None`):
                Configuration for Claude's internal reasoning process.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the Anthropic client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in Anthropic API generation,
                e.g. `temperature`, `seed`.
        """

        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "Please install the `anthropic` package by running "
                "`pip install anthropic`.",
            ) from e

        super().__init__(
            model_name=model_name,
            stream=stream,
            max_retries=max_retries,
            fallback_model_name=fallback_model_name,
            formatter=formatter or AnthropicChatFormatter(),
        )

        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            **(client_kwargs or {}),
        )
        self.max_tokens = max_tokens
        self.thinking_config = thinking_config
        self.stream_tool_parsing = stream_tool_parsing
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from Anthropic chat completions API by the given
        arguments.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of formatted messages to send to the model.
            tools (`list[dict] | None`, optional):
                The tools JSON schema.
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            **kwargs (`Any`):
                The keyword arguments for Anthropic chat completions API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Anthropic chat completions API."""

        kwargs: dict[str, Any] = {
            "model": model_name,
            "max_tokens": self.max_tokens,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }
        if self.thinking_config and "thinking" not in kwargs:
            kwargs["thinking"] = {
                "type": "enabled"
                if self.thinking_config.enable
                else "disabled",
                "budget_tokens": self.thinking_config.budget,
            }

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            kwargs["tool_choice"] = self._format_tool_choice(
                tool_choice,
                tools,
            )

        # Extract the system message
        if messages[0]["role"] == "system":
            kwargs["system"] = messages[0]["content"]
            messages = messages[1:]

        kwargs["messages"] = messages

        start_datetime = datetime.now()

        response = await self.client.messages.create(**kwargs)

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
            ChatResponse (`ChatResponse`):
                A ChatResponse object containing the content blocks and usage.
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
                    )
                    thinking_block["signature"] = content_block.signature
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
                    content_blocks.append(
                        ToolCallBlock(
                            id=content_block.id,
                            name=content_block.name,
                            input=json.dumps(content_block.input, ensure_ascii=False),
                        ),
                    )

        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        return ChatResponse(
            content=content_blocks,
            is_last=True,
            usage=usage,
        )

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

        Returns:
            `AsyncGenerator[ChatResponse, None]`:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in
                the streaming response.
        """

        usage = None
        # Accumulated state
        acc_text = ""
        acc_thinking = ""
        thinking_signature = ""
        # index -> {id, name, input}
        acc_tool_calls: OrderedDict = OrderedDict()

        async for event in response:
            delta_content: list = []

            match event.type:
                case "message_start":
                    message = event.message
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
                case "content_block_start":
                    if event.content_block.type == "tool_use":
                        block_index = event.index
                        tool_block = event.content_block
                        acc_tool_calls[block_index] = {
                            "id": tool_block.id,
                            "name": tool_block.name,
                            "input": "",
                        }

                case "content_block_delta":
                    block_index = event.index
                    delta = event.delta

                    match delta.type:
                        case "text_delta":
                            acc_text += delta.text
                            delta_content.append(TextBlock(text=delta.text))

                        case "thinking_delta":
                            acc_thinking += delta.thinking
                            delta_content.append(
                                ThinkingBlock(thinking=delta.thinking)
                            )

                        case "signature_delta":
                            thinking_signature = delta.signature

                        case "input_json_delta":
                            if block_index in acc_tool_calls:
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

                case "message_delta":
                    if event.usage and usage:
                        usage.output_tokens = event.usage.output_tokens

            if delta_content:
                yield ChatResponse(
                    content=delta_content,
                    is_last=False,
                    usage=usage,
                )

        # Build final accumulated content
        final_content: list = []
        if acc_thinking:
            thinking_block = ThinkingBlock(thinking=acc_thinking)
            thinking_block["signature"] = thinking_signature
            final_content.append(thinking_block)
        if acc_text:
            final_content.append(TextBlock(text=acc_text))
        for tc in acc_tool_calls.values():
            input_str = tc["input"]
            final_content.append(
                ToolCallBlock(
                    id=tc["id"],
                    name=tc["name"],
                    input=input_str,
                ),
            )

        yield ChatResponse(
            content=final_content,
            is_last=True,
            usage=usage,
        )

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
