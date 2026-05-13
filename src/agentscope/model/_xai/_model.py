# -*- coding: utf-8 -*-
"""The xAI chat model implementation using the official xai_sdk."""
from datetime import datetime
from typing import Any, AsyncGenerator, List, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import XAICredential
from ...formatter import XAIChatFormatter
from ...message import (
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from ...tool import ToolChoice
from ...tracing import trace_llm

if TYPE_CHECKING:
    from xai_sdk import AsyncClient
    from xai_sdk.chat import Response
else:
    AsyncClient = Any
    Response = Any


class XAIChatModel(ChatModelBase):
    """The xAI chat model using the official ``xai_sdk`` gRPC client.

    This model provides native access to xAI-specific features such as
    server-side agentic tools (web search, X search, code execution) and
    reasoning effort control, which are not available through the
    OpenAI-compatible REST endpoint.
    """

    class Parameters(BaseModel):
        """The parameters for the xAI chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )
        """The maximum number of tokens to generate."""

        reasoning_effort: Literal["low", "medium", "high"] | None = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "Controls the depth of reasoning for models that support "
                "extended thinking (e.g. ``grok-3-mini``). Set to "
                "``'low'``, ``'medium'``, or ``'high'`` to enable reasoning "
                "with the corresponding effort level. ``None`` disables "
                "reasoning."
            ),
        )
        """Reasoning effort level for xAI reasoning models."""

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            le=2,
        )
        """The sampling temperature."""

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top-p nucleus sampling value.",
            gt=0,
            le=1,
        )
        """The top-p sampling parameter."""

    type: Literal["xai_chat"] = "xai_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: XAICredential,
        model: str,
        parameters: "XAIChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        context_size: int = 131072,
        formatter: XAIChatFormatter | None = None,
    ) -> None:
        """Initialize the xAI chat model.

        Args:
            credential (`XAICredential`):
                The xAI credential used to authenticate API calls.
            model (`str`):
                The xAI model name, e.g. ``grok-3`` or ``grok-3-mini``.
            parameters (`XAIChatModel.Parameters | None`, defaults to \
            `None`):
                The xAI API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the xAI API.
            context_size (`int`, defaults to `131072`):
                The model context size used for context compression.
            formatter (`XAIChatFormatter | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to xai_sdk
                proto messages. When ``None``, an ``XAIChatFormatter``
                instance will be used.
        """
        super().__init__(
            model=model,
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.credential = credential
        self.parameters = parameters or self.Parameters()
        self.formatter = formatter or XAIChatFormatter()

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the xAI API using the official ``xai_sdk`` gRPC client.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                A list of ``Msg`` objects representing the conversation.
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
        from xai_sdk import AsyncClient

        client = AsyncClient(
            api_key=self.credential.api_key.get_secret_value(),
        )

        xai_messages = await self.formatter.format(messages)

        xai_tools, xai_tool_choice = self._format_tools(tools, tool_choice)

        create_kwargs: dict[str, Any] = {"model": model_name}
        if self.parameters.max_tokens is not None:
            create_kwargs["max_tokens"] = self.parameters.max_tokens
        if self.parameters.temperature is not None:
            create_kwargs["temperature"] = self.parameters.temperature
        if self.parameters.top_p is not None:
            create_kwargs["top_p"] = self.parameters.top_p
        if self.parameters.reasoning_effort is not None:
            create_kwargs[
                "reasoning_effort"
            ] = self.parameters.reasoning_effort
        if xai_tools:
            create_kwargs["tools"] = xai_tools
        if xai_tool_choice is not None:
            create_kwargs["tool_choice"] = xai_tool_choice

        create_kwargs.update(generate_kwargs)

        chat = client.chat.create(**create_kwargs)
        for xai_msg in xai_messages:
            chat.append(xai_msg)

        start_datetime = datetime.now()

        if self.stream:
            return self._parse_stream_response(start_datetime, chat, client)

        try:
            response = await chat.sample()
        finally:
            await client.close()

        return self._parse_completion_response(start_datetime, response)

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list | None, Any]:
        """Validate, filter, and format tools and tool_choice for the xAI API.

        When ``tool_choice.tools`` is specified the schemas list is filtered
        to only those tools. When ``tool_choice.mode`` is a specific tool name
        (str) the model is forced to call exactly that tool without needing to
        filter the list, preserving prompt-cache efficiency.

        Args:
            tools (`list[dict] | None`):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`):
                The tool choice configuration.

        Returns:
            `tuple[list | None, Any]`:
                A tuple of (xai_tools, xai_tool_choice) ready for the
                ``xai_sdk`` client.
        """
        from xai_sdk.chat import required_tool, tool

        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.get("tools"):
                allowed = set(tool_choice["tools"])
                tools = [t for t in tools if t["function"]["name"] in allowed]

        xai_tools = None
        if tools:
            xai_tools = []
            for t in tools:
                if t.get("type") == "function" and "function" in t:
                    fn = t["function"]
                    xai_tools.append(
                        tool(
                            name=fn["name"],
                            description=fn.get("description", ""),
                            parameters=fn.get("parameters", {}),
                        ),
                    )

        if not tool_choice:
            return xai_tools, None

        mode = tool_choice["mode"]

        if mode in _TOOL_CHOICE_LITERAL_MODES:
            return xai_tools, mode

        return xai_tools, required_tool(mode)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        chat: Any,
        client: AsyncClient,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the xAI streaming response from ``xai_sdk``.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            chat (`Any`):
                The ``xai_sdk`` chat session object.
            client (`Any`):
                The ``xai_sdk.AsyncClient`` instance; closed when the
                generator is exhausted or abandoned.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        last_response = None
        response_id: str | None = None

        try:
            async for response, chunk in chat.stream():
                if response_id is None:
                    response_id = getattr(response, "id", None) or None

                delta_text: str = chunk.content or ""
                delta_thinking: str = chunk.reasoning_content or ""

                delta_contents: List[TextBlock | ThinkingBlock] = []

                if delta_thinking:
                    acc_thinking.thinking += delta_thinking
                    delta_contents.append(
                        ThinkingBlock(
                            id=acc_thinking.id,
                            thinking=delta_thinking,
                        ),
                    )
                if delta_text:
                    acc_text.text += delta_text
                    delta_contents.append(
                        TextBlock(id=acc_text.id, text=delta_text),
                    )

                if delta_contents:
                    _kwargs: dict[str, Any] = {
                        "content": delta_contents,
                        "is_last": False,
                    }
                    if response_id:
                        _kwargs["id"] = response_id
                    yield ChatResponse(**_kwargs)

                last_response = response

        finally:
            await client.close()

        final_contents: List[TextBlock | ToolCallBlock | ThinkingBlock] = []
        if acc_thinking.thinking:
            final_contents.append(acc_thinking)
        if acc_text.text:
            final_contents.append(acc_text)

        if last_response is not None:
            for tc in last_response.tool_calls or []:
                final_contents.append(
                    ToolCallBlock(
                        id=tc.id,
                        name=tc.function.name,
                        input=tc.function.arguments,
                    ),
                )

        usage = None
        if last_response is not None and last_response.usage is not None:
            u = last_response.usage
            usage = ChatUsage(
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=u,
            )

        final_kwargs: dict[str, Any] = {
            "content": final_contents,
            "usage": usage,
            "is_last": True,
        }
        if response_id:
            final_kwargs["id"] = response_id
        yield ChatResponse(**final_kwargs)

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: Response,
    ) -> ChatResponse:
        """Parse the xAI non-streaming response from ``xai_sdk``.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Any`):
                The ``xai_sdk`` ``Response`` object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if response.reasoning_content:
            content_blocks.append(
                ThinkingBlock(thinking=response.reasoning_content),
            )
        if response.content:
            content_blocks.append(TextBlock(text=response.content))

        for tc in response.tool_calls or []:
            content_blocks.append(
                ToolCallBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=tc.function.arguments,
                ),
            )

        usage = None
        if response.usage is not None:
            u = response.usage
            usage = ChatUsage(
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=u,
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
