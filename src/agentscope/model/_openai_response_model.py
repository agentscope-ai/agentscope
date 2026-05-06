# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""OpenAI Response API Chat model class."""
from datetime import datetime
from typing import (
    Any,
    List,
    AsyncGenerator,
    Literal,
    Type,
)

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from ._model_usage import ChatUsage
from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..formatter import FormatterBase
from ..message import (
    ToolCallBlock,
    TextBlock,
    ThinkingBlock,
)
from ..tool import ToolChoice
from ..tracing import trace_llm
from ..types import JSONSerializableObject


class OpenAIResponseModel(ChatModelBase):
    """Chat model using the OpenAI Responses API
    (``client.responses.create``).

    Compared with the Chat Completions API, the Responses API provides
    first-class streaming events for reasoning / thinking, text output
    and function-call arguments, which makes it a natural fit for models
    that expose chain-of-thought reasoning (e.g. ``o3``, ``o4-mini``).

    Compatible with any OpenAI-compatible endpoint by passing a custom
    ``base_url`` via ``client_kwargs``.
    """

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = True,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        reasoning_effort: Literal["minimal", "low", "medium", "high"]
        | None = None,
        reasoning_summary: Literal[
            "auto",
            "concise",
            "detailed",
        ]
        | None = None,
        organization: str | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the OpenAI Response API client.

        Args:
            model_name (`str`):
                The name of the model to use (e.g. ``"o3"``).
            api_key (`str`, optional):
                API key. Falls back to ``OPENAI_API_KEY`` env var.
            stream (`bool`, default ``True``):
                Whether to use streaming output.
            max_retries (`int`, default ``0``):
                Maximum number of retries on failure.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
            reasoning_effort (`Literal["minimal", "low", "medium", "high"]`,
                optional):
                Reasoning effort level.
            reasoning_summary (`Literal["auto", "concise", "detailed"]`,
                optional):
                Controls how reasoning summaries are returned in streaming
                mode. Defaults to ``"auto"`` when ``reasoning_effort``
                is set.
            organization (`str`, optional):
                OpenAI organization ID.
            client_kwargs (`dict`, optional):
                Extra keyword arguments forwarded to
                ``openai.AsyncClient`` (e.g. ``base_url``).
            generate_kwargs (`dict`, optional):
                Extra keyword arguments forwarded to
                ``client.responses.create`` on every call
                (e.g. ``temperature``, ``top_p``).
            **kwargs:
                Ignored (with a warning).
        """
        if kwargs:
            logger.warning(
                "Unknown keyword arguments: %s. These will be ignored.",
                list(kwargs.keys()),
            )

        super().__init__(
            model_name,
            stream,
            max_retries=max_retries,
            fallback_model_name=fallback_model_name,
            formatter=formatter,
        )

        import openai

        self.client = openai.AsyncClient(
            api_key=api_key,
            organization=organization,
            **(client_kwargs or {}),
        )

        self.reasoning_effort = reasoning_effort
        self.reasoning_summary = reasoning_summary
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the OpenAI Responses API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of message dicts with at least ``role`` and
                ``content`` keys. Passed as the ``input`` parameter to
                the API.
            tools (`list[dict]`, optional):
                Tool JSON schemas (Chat-Completions format accepted;
                they are automatically converted to the Responses API
                format).
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            structured_model (`Type[BaseModel]`, optional):
                A Pydantic BaseModel class for structured output.
                When provided, the model is instructed to return JSON
                conforming to the schema via the ``text.format``
                parameter. ``tools`` and ``tool_choice`` are ignored.
            **kwargs:
                Forwarded to ``client.responses.create``.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`
        """
        if not isinstance(messages, list):
            raise ValueError(
                "OpenAI Response API `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )

        api_kwargs: dict[str, Any] = {
            "model": model_name,
            "input": messages,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }

        if self.reasoning_effort and "reasoning" not in api_kwargs:
            reasoning_cfg: dict[str, str | None] = {
                "effort": self.reasoning_effort,
            }
            if self.reasoning_summary:
                reasoning_cfg["summary"] = self.reasoning_summary
            api_kwargs["reasoning"] = reasoning_cfg

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            api_kwargs.pop("tools", None)
            api_kwargs.pop("tool_choice", None)
            api_kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": structured_model.__name__,
                    "schema": structured_model.model_json_schema(),
                    "strict": True,
                },
            }
        else:
            fmt_tools, fmt_tool_choice = self._format_tools(
                tools,
                tool_choice,
            )
            if fmt_tools is not None:
                api_kwargs["tools"] = fmt_tools
            if fmt_tool_choice is not None:
                api_kwargs["tool_choice"] = fmt_tool_choice

        start_datetime = datetime.now()

        response = await self.client.responses.create(**api_kwargs)

        if self.stream:
            return self._parse_stream_response(
                start_datetime,
                response,
                structured_model,
            )

        return self._parse_response(
            start_datetime,
            response,
            structured_model,
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the event stream produced by the Responses API.

        Recognised event types (``event.type``):

        * ``response.reasoning_summary_text.delta`` -- thinking delta
        * ``response.output_text.delta`` -- text delta
        * ``response.output_item.added`` -- new output item (may be a
          ``function_call``)
        * ``response.function_call_arguments.delta`` -- tool-call arg delta
        * ``response.completed`` -- final event carrying usage info
        """
        usage: ChatUsage | None = None
        response_id: str | None = None
        text = ""
        thinking = ""
        tool_calls: dict[str, dict[str, Any]] = {}
        metadata: dict | None = None

        async for event in response:
            event_type = event.type

            if response_id is None:
                resp_obj = getattr(event, "response", None)
                if resp_obj is not None:
                    response_id = getattr(resp_obj, "id", None)

            if event_type == "response.reasoning_summary_text.delta":
                thinking += event.delta

            elif event_type == "response.output_text.delta":
                text += event.delta

            elif event_type == "response.output_item.added":
                item = event.item
                if getattr(item, "type", None) == "function_call":
                    call_id = getattr(item, "call_id", None) or getattr(
                        item,
                        "id",
                        "",
                    )
                    tool_calls[item.id] = {
                        "id": call_id,
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
                    usage = ChatUsage(
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        metadata=resp.usage,
                    )

            contents = self._build_content_blocks(
                thinking,
                text,
                tool_calls,
            )

            if structured_model and text:
                metadata = _json_loads_with_repair(text)

            is_last = event_type == "response.completed"

            if contents:
                chat_resp_kwargs: dict[str, Any] = {
                    "content": contents,
                    "is_last": is_last,
                    "usage": usage,
                    "metadata": metadata,
                }
                if response_id:
                    chat_resp_kwargs["id"] = response_id
                yield ChatResponse(**chat_resp_kwargs)

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        start_datetime: datetime,
        response: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Parse a non-streaming ``Response`` object."""
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []
        metadata: dict | None = None

        for item in response.output:
            item_type = getattr(item, "type", None)

            if item_type == "reasoning":
                for summary in getattr(item, "summary", []):
                    summary_text = getattr(summary, "text", "")
                    if summary_text:
                        content_blocks.append(
                            ThinkingBlock(
                                type="thinking",
                                thinking=summary_text,
                            ),
                        )

            elif item_type == "message":
                for part in getattr(item, "content", []):
                    if getattr(part, "type", None) == "output_text":
                        content_blocks.append(
                            TextBlock(type="text", text=part.text),
                        )
                        if structured_model:
                            metadata = _json_loads_with_repair(part.text)

            elif item_type == "function_call":
                call_id = getattr(item, "call_id", None) or getattr(
                    item,
                    "id",
                    "",
                )
                content_blocks.append(
                    ToolCallBlock(
                        id=call_id,
                        name=item.name,
                        input=getattr(item, "arguments", "") or "{}",
                    ),
                )

        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=response.usage,
            )

        resp_kwargs: dict[str, Any] = {
            "content": content_blocks,
            "is_last": True,
            "usage": usage,
            "metadata": metadata,
        }
        response_id = getattr(response, "id", None)
        if response_id:
            resp_kwargs["id"] = response_id

        return ChatResponse(**resp_kwargs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_content_blocks(
        thinking: str,
        text: str,
        tool_calls: dict[str, dict[str, Any]],
    ) -> List[TextBlock | ToolCallBlock | ThinkingBlock]:
        """Assemble content blocks from accumulated state."""
        contents: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if thinking:
            contents.append(
                ThinkingBlock(type="thinking", thinking=thinking),
            )

        if text:
            contents.append(TextBlock(type="text", text=text))

        for tc in tool_calls.values():
            contents.append(
                ToolCallBlock(
                    id=tc["id"],
                    name=tc["name"],
                    input=tc["input"] or "{}",
                ),
            )

        return contents

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, str | dict | None]:
        """Validate and format tools and tool_choice for Response API.

        Converts tool schemas to the Response API's flattened format and
        maps tool_choice to the Response API format.

        Unlike other model adapters, the Response API schema list is
        **never filtered** regardless of ``tool_choice.tools``, because
        the API natively supports an ``allowed_tools`` directive that
        restricts callable tools at the API layer without changing the
        schema list — keeping prompt caches intact.

        When ``tool_choice.mode`` is a specific tool name (str), it is
        formatted as a forced single-tool call. When ``tool_choice.tools``
        is specified with a Literal mode, the ``allowed_tools`` format
        restricts callable tools at the API layer without filtering
        schemas.

        Args:
            tools (`list[dict] | None`):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, str | dict | None]`:
                A tuple of (formatted_tools, formatted_tool_choice).
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

        if tool_choice not in _TOOL_CHOICE_LITERAL_MODES:
            # tool_choice is a specific tool name — force call it
            return fmt_tools, {"type": "function", "name": tool_choice}

        return fmt_tools, tool_choice
