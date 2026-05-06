# -*- coding: utf-8 -*-
"""The Kimi (Moonshot AI) chat model implementation."""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, SecretStr, Field

from .. import ChatUsage, ChatModelBase
from ...formatter import FormatterBase, KimiChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...model import ChatResponse
from ...tool import ToolChoice
from ...tracing import trace_llm

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any

_KIMI_BASE_URL = "https://api.moonshot.cn/v1"


class KimiCredential(BaseModel):
    """The Kimi (Moonshot AI) credential model."""

    type: Literal["kimi_credential"] = "kimi_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Kimi (Moonshot AI) API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_KIMI_BASE_URL,
        description="The base URL for the Kimi API.",
    )
    """The base URL for the Kimi API."""


class KimiChatModel(ChatModelBase):
    """The Kimi (Moonshot AI) chat model."""

    class Parameters(BaseModel):
        """The parameters for the Kimi chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            le=1,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

    type: Literal["kimi_chat"] = "kimi_chat"
    """The type of the chat model."""

    credential: KimiCredential
    """The Kimi credential."""

    model: str = Field(
        title="Model",
        description="The Kimi model name.",
    )

    stream: bool = Field(
        default=True,
        title="Enable Streaming Output",
        description="Whether to enable streaming output.",
    )

    max_retries: int = Field(
        default=0,
        title="Max Retries",
        description="The maximum retries for the Kimi API.",
        ge=0,
    )

    parameters: Parameters = Field(
        default_factory=Parameters,
        title="Kimi API parameters",
        description="The Kimi API parameters.",
    )

    formatter: FormatterBase = Field(
        default_factory=KimiChatFormatter,
    )
    """The formatter for Kimi API."""

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the Kimi chat API (OpenAI-compatible).

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
            api_key=self.credential.api_key.get_secret_value(),
            base_url=self.credential.base_url,
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

        kwargs.update(generate_kwargs)

        if tools:
            kwargs["tools"] = tools

        if tool_choice:
            kwargs["tool_choice"] = self._format_tool_choice(
                tool_choice,
                tools,
            )

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
        """Parse the Kimi streaming response.

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
        response_id: str | None = None
        # All delta should have the same block identifier
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        acc_tool_calls: OrderedDict = OrderedDict()

        async with response as stream:
            async for chunk in stream:
                if chunk.usage:
                    usage = ChatUsage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        metadata=chunk.usage,
                    )

                # Capture response_id from the first chunk that carries it
                response_id = response_id or getattr(chunk, "id", None)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Kimi thinking models (kimi-k2.6, kimi-k2-thinking) return
                # reasoning_content before content in the stream.
                delta_thinking = (
                    getattr(delta, "reasoning_content", None) or ""
                )
                if delta_thinking:
                    acc_thinking.thinking += delta_thinking
                    _thinking_kwargs: dict[str, Any] = {
                        "content": [
                            ThinkingBlock(
                                id=acc_thinking.id,
                                thinking=delta_thinking,
                            ),
                        ],
                        "usage": usage,
                        "is_last": False,
                    }
                    if response_id:
                        _thinking_kwargs["id"] = response_id
                    yield ChatResponse(**_thinking_kwargs)
                    continue

                delta_text = getattr(delta, "content", None) or ""
                acc_text.text += delta_text

                for tool_call in getattr(delta, "tool_calls", None) or []:
                    idx = tool_call.index
                    if idx in acc_tool_calls:
                        if tool_call.function.arguments is not None:
                            acc_tool_calls[idx][
                                "input"
                            ] += tool_call.function.arguments
                    else:
                        acc_tool_calls[idx] = {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": tool_call.function.arguments or "",
                        }

                if delta_text:
                    _text_kwargs: dict[str, Any] = {
                        "content": [
                            TextBlock(id=acc_text.id, text=delta_text),
                        ],
                        "usage": usage,
                        "is_last": False,
                    }
                    if response_id:
                        _text_kwargs["id"] = response_id
                    yield ChatResponse(**_text_kwargs)

        final_contents: List[ThinkingBlock | TextBlock | ToolCallBlock] = []
        if acc_thinking.thinking:
            final_contents.append(acc_thinking)
        if acc_text.text:
            final_contents.append(acc_text)
        for tc in acc_tool_calls.values():
            final_contents.append(
                ToolCallBlock(id=tc["id"], name=tc["name"], input=tc["input"]),
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
        """Parse the Kimi non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                The OpenAI-compatible chat completion object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[ThinkingBlock | TextBlock | ToolCallBlock] = []

        if response.choices:
            choice = response.choices[0]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if reasoning:
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
            usage = ChatUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=response.usage,
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

    def _format_tool_choice(
        self,
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> str | dict | None:
        """Format tool_choice parameter for the Kimi API.

        Args:
            tool_choice (`ToolChoice | None`):
                The unified tool choice parameter which can be ``"auto"``,
                ``"none"``, ``"required"``, or a specific function name.
            tools (`list[dict] | None`):
                The list of available tools, used for validation when
                ``tool_choice`` is a specific function name.

        Returns:
            `str | dict | None`:
                The formatted tool choice string or configuration dict for
                the Kimi API, or ``None`` if ``tool_choice`` is ``None``.
        """
        self._validate_tool_choice(tool_choice, tools)

        if tool_choice is None:
            return None

        if tool_choice in ["auto", "none", "required"]:
            return tool_choice

        return {"type": "function", "function": {"name": tool_choice}}
