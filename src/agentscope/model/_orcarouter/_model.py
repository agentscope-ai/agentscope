# -*- coding: utf-8 -*-
"""The OrcaRouter chat model implementation.

OrcaRouter (https://www.orcarouter.ai) is an OpenAI-compatible meta-router
that exposes 150+ models from upstream providers (OpenAI, Anthropic, Google,
DeepSeek, Qwen, xAI, MiniMax, etc.) behind a single API key and endpoint,
plus an adaptive routing model id ``orcarouter/auto`` that selects the best
upstream per request.
"""
from collections import OrderedDict
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OrcaRouterCredential
from ...formatter import FormatterBase, OpenAIChatFormatter
from ...message import Msg, ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any

_ORCAROUTER_ATTRIBUTION_REFERER = "https://www.agentscope.io/"
_ORCAROUTER_ATTRIBUTION_TITLE = "AgentScope"


class OrcaRouterChatModel(ChatModelBase):
    """The OrcaRouter chat model.

    Model ids follow the ``<vendor>/<model>`` namespacing convention used by
    OrcaRouter (e.g. ``openai/gpt-5``, ``anthropic/claude-opus-4.7``,
    ``deepseek/deepseek-v4-pro``). The special id ``orcarouter/auto`` enables
    OrcaRouter's adaptive router (contextual bandit) which selects the best
    upstream per request. See https://www.orcarouter.ai/models for the full
    catalog.

    .. note::

        Reasoning models such as ``anthropic/claude-opus-4.7``,
        ``openai/gpt-5`` and ``deepseek/deepseek-reasoner`` reject the
        ``temperature`` parameter at the upstream layer; do not pass
        ``temperature`` for those models.
    """

    class Parameters(BaseModel):
        """The parameters for the OrcaRouter chat model."""

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
                "Whether to enable reasoning for reasoning models routed "
                "by OrcaRouter (e.g. gpt-5, o3, deepseek-reasoner). Use "
                "reasoning_effort to control the depth of reasoning."
            ),
        )

        reasoning_effort: (
            Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
        ) = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "Controls the depth of reasoning for reasoning models. "
                "OrcaRouter passes this through to the upstream provider; "
                "upstreams that do not understand it silently ignore it."
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

    type: Literal["orcarouter_chat"] = "orcarouter_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OrcaRouterCredential,
        model: str,
        parameters: "OrcaRouterChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        context_size: int = 128000,
        formatter: FormatterBase | None = None,
    ) -> None:
        """Initialize the OrcaRouter chat model.

        Args:
            credential (`OrcaRouterCredential`):
                The OrcaRouter credential used to authenticate API calls.
            model (`str`):
                The OrcaRouter model id, e.g. ``orcarouter/auto``,
                ``openai/gpt-5``, ``anthropic/claude-opus-4.7``,
                ``deepseek/deepseek-v4-pro``. See
                https://www.orcarouter.ai/models for the full catalog.
            parameters (`OrcaRouterChatModel.Parameters | None`, defaults to \
            `None`):
                The chat parameters. When ``None``, defaults are used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the OrcaRouter API.
            context_size (`int`, defaults to `128000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by OrcaRouter. When ``None``, an
                ``OpenAIChatFormatter`` instance is used (OrcaRouter is
                OpenAI-compatible).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.formatter = formatter or OpenAIChatFormatter()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the OrcaRouter chat completions API."""
        import openai

        client = openai.AsyncClient(
            api_key=self.credential.api_key.get_secret_value(),
            base_url=self.credential.base_url,
            default_headers={
                "HTTP-Referer": _ORCAROUTER_ATTRIBUTION_REFERER,
                "X-Title": _ORCAROUTER_ATTRIBUTION_TITLE,
            },
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

        if (
            self.parameters.thinking_enable
            and self.parameters.reasoning_effort
        ):
            kwargs["reasoning_effort"] = self.parameters.reasoning_effort

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
        """Parse the OrcaRouter streaming response."""
        usage = None
        response_id: str | None = None
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

                delta_tool_call_blocks: List[ToolCallBlock] = []
                for tool_call in getattr(delta, "tool_calls", None) or []:
                    idx = tool_call.index
                    args = tool_call.function.arguments or ""
                    if idx in acc_tool_calls:
                        acc_tool_calls[idx]["input"] += args
                    else:
                        acc_tool_calls[idx] = {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
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
        """Parse the OrcaRouter non-streaming response."""
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
        """Validate, filter, and format tools and tool_choice for the
        OpenAI-compatible OrcaRouter API."""
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
