# -*- coding: utf-8 -*-
"""The OpenAI Chat Completions model implementation."""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OpenAICredential
from ...formatter import FormatterBase, OpenAIChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice
from ...tracing import trace_llm

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
                "Whether to enable reasoning output. Note: for the OpenAI "
                "Chat Completions API, reasoning is controlled by model "
                "selection (e.g. o3, o4-mini) rather than an API parameter."
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

    type: Literal["openai_chat"] = "openai_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OpenAICredential,
        model: str,
        parameters: "OpenAIChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        context_size: int = 128000,
        formatter: FormatterBase | None = None,
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
            context_size (`int`, defaults to `128000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the OpenAI API. When ``None``, an
                ``OpenAIChatFormatter`` instance will be used.
        """
        super().__init__(
            model=model,
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.credential = credential
        self.parameters = parameters or self.Parameters()
        self.formatter = formatter or OpenAIChatFormatter()

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
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
            api_key=self.credential.api_key.get_secret_value(),
            organization=self.credential.organization,
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

        if self.stream:
            return self._parse_stream_response(start_datetime, response)

        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the OpenAI Chat streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                The OpenAI async stream object.

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

                # Capture response_id from the first chunk that carries it
                response_id = response_id or getattr(chunk, "id", None)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                delta_thinking = getattr(delta, "reasoning_content", None)
                if not isinstance(delta_thinking, str):
                    delta_thinking = getattr(delta, "reasoning", None)
                if not isinstance(delta_thinking, str):
                    delta_thinking = ""

                delta_text = getattr(delta, "content", None) or ""

                acc_thinking.thinking += delta_thinking
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

                if delta_contents:
                    _kwargs: dict[str, Any] = {
                        "content": delta_contents,
                        "usage": usage,
                        "is_last": False,
                    }
                    if response_id:
                        _kwargs["id"] = response_id
                    yield ChatResponse(**_kwargs)

        final_contents: List[TextBlock | ToolCallBlock | ThinkingBlock] = []
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
        """Parse the OpenAI Chat non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                The OpenAI chat completion object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if response.choices:
            choice = response.choices[0]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if not isinstance(reasoning, str):
                reasoning = getattr(choice.message, "reasoning", None)
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
