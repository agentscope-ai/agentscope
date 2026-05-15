# -*- coding: utf-8 -*-
"""The OpenAI Responses API chat model implementation."""
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, List, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import OpenAICredential
from ...formatter import FormatterBase, OpenAIResponseFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice
from ...tracing import trace_llm

if TYPE_CHECKING:
    from openai.types.responses import Response
    from openai.types.responses import ResponseStreamEvent
    from openai import AsyncStream
else:
    Response = Any
    ResponseStreamEvent = Any
    AsyncStream = Any


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

        reasoning_effort: Literal["low", "medium", "high"] | None = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "The reasoning effort level for reasoning models "
                "(e.g. o3, o4-mini)."
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
        context_size: int = 200000,
        formatter: FormatterBase | None = None,
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
            context_size (`int`, defaults to `200000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the OpenAI Responses API. When ``None``, an
                ``OpenAIResponseFormatter`` instance will be used.
        """
        super().__init__(
            model=model,
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.credential = credential
        self.parameters = parameters or self.Parameters()
        self.formatter = formatter or OpenAIResponseFormatter()

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
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
            api_key=self.credential.api_key.get_secret_value(),
            organization=self.credential.organization,
            base_url=self.credential.base_url,
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

        if self.parameters.reasoning_effort is not None:
            api_kwargs["reasoning"] = {
                "effort": self.parameters.reasoning_effort,
            }

        api_kwargs.update(generate_kwargs)

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

        Each event yields only the delta content produced by that event so
        that callers see a true incremental stream, consistent with other
        model implementations.  The final ``response.completed`` event emits
        an ``is_last=True`` response with the full accumulated state.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream[ResponseStreamEvent]`):
                The OpenAI Responses API async stream object.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        usage: ChatUsage | None = None
        response_id: str | None = None
        # All delta should have the same block identifier
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        tool_calls: dict[str, dict[str, Any]] = {}

        async for event in response:
            event_type = event.type

            if response_id is None:
                resp_obj = getattr(event, "response", None)
                if resp_obj is not None:
                    response_id = getattr(resp_obj, "id", None)

            delta_contents: List[
                TextBlock | ToolCallBlock | ThinkingBlock
            ] = []

            if event_type == "response.reasoning_summary_text.delta":
                # Reasoning summary text is NOT emitted by all models.
                # As of 2026-05, o1 and o4-mini do not stream reasoning
                # summary deltas.  This handler exists for forward
                # compatibility with models that do expose it.
                delta = event.delta
                acc_thinking.thinking += delta
                delta_contents.append(
                    ThinkingBlock(id=acc_thinking.id, thinking=delta),
                )

            elif event_type == "response.output_text.delta":
                delta = event.delta
                acc_text.text += delta
                delta_contents.append(TextBlock(id=acc_text.id, text=delta))

            elif event_type == "response.output_item.added":
                item = event.item
                if getattr(item, "type", None) == "function_call":
                    # item.id  → fc_xxx  (item identifier, needed for
                    #             function_call.id in multi-turn history)
                    # item.call_id → call_xxx (needed for
                    #             function_call_output.call_id)
                    tool_calls[item.id] = {
                        "id": item.id,
                        "call_id": getattr(item, "call_id", None),
                        "name": getattr(item, "name", ""),
                        "input": "",
                    }

            elif event_type == "response.function_call_arguments.delta":
                item_id = event.item_id
                if item_id in tool_calls:
                    tool_calls[item_id]["input"] += event.delta

            elif event_type == "response.completed":
                resp = event.response
                if response_id is None:
                    response_id = getattr(resp, "id", None)
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
                # Attach reasoning item IDs from the completed response so the
                # formatter can echo them back in multi-turn history.
                # The Responses API requires every function_call item to be
                # accompanied by its preceding reasoning item (see the
                # function-calling guide).  The reasoning item may have an
                # empty summary when the model does not expose it (e.g.
                # o1/o4-mini as of 2026-05).
                for output_item in getattr(resp, "output", []):
                    if getattr(output_item, "type", None) == "reasoning":
                        reasoning_item_id = getattr(output_item, "id", None)
                        if reasoning_item_id:
                            acc_thinking = ThinkingBlock(
                                id=acc_thinking.id,
                                thinking=acc_thinking.thinking,
                                reasoning_item_id=reasoning_item_id,
                            )
                # Emit the full accumulated state as the final response
                final_contents: List[
                    TextBlock | ToolCallBlock | ThinkingBlock
                ] = []
                if acc_thinking.thinking or getattr(
                    acc_thinking,
                    "reasoning_item_id",
                    None,
                ):
                    final_contents.append(acc_thinking)
                if acc_text.text:
                    final_contents.append(acc_text)
                for tc in tool_calls.values():
                    final_contents.append(
                        ToolCallBlock(
                            id=tc["id"],
                            call_id=tc.get("call_id"),
                            name=tc["name"],
                            input=tc["input"] or "{}",
                        ),
                    )
                final_kwargs: dict[str, Any] = {
                    "content": final_contents,
                    "is_last": True,
                    "usage": usage,
                }
                if response_id:
                    final_kwargs["id"] = response_id
                yield ChatResponse(**final_kwargs)
                return

            # Yield incremental delta for non-terminal events
            if delta_contents:
                chat_resp_kwargs: dict[str, Any] = {
                    "content": delta_contents,
                    "is_last": False,
                    "usage": usage,
                }
                if response_id:
                    chat_resp_kwargs["id"] = response_id
                yield ChatResponse(**chat_resp_kwargs)

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
                        id=getattr(item, "id", ""),
                        call_id=getattr(item, "call_id", None),
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
        """Validate, filter, and format tools and tool_choice for the
        Responses API.

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
                A tuple of ``(formatted_tools, formatted_tool_choice)``
                ready for the Responses API.
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.tools:
                allowed = set(tool_choice.tools)
                tools = [t for t in tools if t["function"]["name"] in allowed]

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

        return fmt_tools, mode
