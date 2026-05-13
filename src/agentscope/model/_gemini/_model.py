# -*- coding: utf-8 -*-
"""The Google Gemini chat model implementation."""
import base64
import copy
import json
import uuid
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, Field

from .._base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ...credential import GeminiCredential
from ...formatter import FormatterBase, GeminiChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...tool import ToolChoice
from ...tracing import trace_llm
from ..._logging import logger

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse
else:
    GenerateContentResponse = Any


def _flatten_json_schema(schema: dict) -> dict:
    """Flatten a JSON schema by resolving all $ref references.

    Gemini API does not support ``$defs`` and ``$ref`` in JSON schemas.

    Args:
        schema (`dict`):
            The JSON schema that may contain ``$defs`` and ``$ref`` references.

    Returns:
        `dict`:
            A flattened JSON schema with all references resolved inline.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def _resolve_ref(obj: Any, visited: set | None = None) -> Any:
        if visited is None:
            visited = set()
        if not isinstance(obj, dict):
            if isinstance(obj, list):
                return [_resolve_ref(item, visited.copy()) for item in obj]
            return obj
        if "$ref" in obj:
            ref_path = obj["$ref"]
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path[len("#/$defs/") :]
                if def_name in visited:
                    logger.warning(
                        "Circular reference detected for '%s' in tool schema",
                        def_name,
                    )
                    return {
                        "type": "object",
                        "description": f"(circular: {def_name})",
                    }
                visited.add(def_name)
                if def_name in defs:
                    resolved = _resolve_ref(defs[def_name], visited.copy())
                    for key, value in obj.items():
                        if key != "$ref":
                            resolved[key] = _resolve_ref(value, visited.copy())
                    return resolved
            return obj
        result = {}
        for key, value in obj.items():
            result[key] = _resolve_ref(value, visited.copy())
        return result

    return _resolve_ref(schema)


class GeminiChatModel(ChatModelBase):
    """The Google Gemini chat model."""

    class Parameters(BaseModel):
        """The parameters for the Gemini chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description="Whether to enable thinking output.",
        )

        thinking_budget: int | None = Field(
            default=None,
            title="Thinking Budget",
            description="The thinking budget in tokens.",
            gt=0,
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

    type: Literal["gemini_chat"] = "gemini_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: GeminiCredential,
        model: str,
        parameters: "GeminiChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        context_size: int = 1048576,
        formatter: FormatterBase | None = None,
    ) -> None:
        """Initialize the Gemini chat model.

        Args:
            credential (`GeminiCredential`):
                The Google Gemini credential used to authenticate API calls.
            model (`str`):
                The Gemini model name, e.g. ``gemini-2.0-flash-exp``.
            parameters (`GeminiChatModel.Parameters | None`, defaults to \
            `None`):
                The Gemini API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the Gemini API.
            context_size (`int`, defaults to `1048576`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the Gemini API. When ``None``, a
                ``GeminiChatFormatter`` instance will be used.
        """
        super().__init__(
            model=model,
            stream=stream,
            max_retries=max_retries,
            context_size=context_size,
        )
        self.credential = credential
        self.parameters = parameters or self.Parameters()
        self.formatter = formatter or GeminiChatFormatter()

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **config_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the Gemini chat API.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list`):
                A list of message objects for Gemini API.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas.
            tool_choice (`ToolChoice | None`, optional):
                Controls which (if any) tool is called by the model.
            **config_kwargs (`Any`):
                Extra keyword arguments for the Gemini config.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                A ``ChatResponse`` when streaming is disabled, or an async
                generator of ``ChatResponse`` objects when streaming is
                enabled.
        """
        from google import genai

        client = genai.Client(
            api_key=self.credential.api_key.get_secret_value(),
        )

        formatted_messages = await self.formatter.format(messages)

        config: dict[str, Any] = {**config_kwargs}

        if self.parameters.max_tokens is not None:
            config["max_output_tokens"] = self.parameters.max_tokens

        if self.parameters.temperature is not None:
            config["temperature"] = self.parameters.temperature

        if self.parameters.top_p is not None:
            config["top_p"] = self.parameters.top_p

        if self.parameters.thinking_enable:
            config["thinking_config"] = {
                "include_thoughts": True,
                "thinking_budget": self.parameters.thinking_budget or 1024,
            }

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)

        if fmt_tools is not None:
            config["tools"] = fmt_tools

        if fmt_tool_choice is not None:
            config["tool_config"] = fmt_tool_choice

        kwargs: dict[str, Any] = {
            "model": model_name,
            "contents": formatted_messages,
            "config": config,
        }

        start_datetime = datetime.now()

        if self.stream:
            response = await client.aio.models.generate_content_stream(
                **kwargs,
            )
            # Pass client to the generator so the aiohttp session it owns
            # stays alive until the stream is fully consumed.
            return self._parse_stream_response(
                start_datetime,
                response,
                client,
            )

        response = await client.aio.models.generate_content(**kwargs)
        return self._parse_completion_response(start_datetime, response)

    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: Any,
        _client: Any = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the Gemini streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`Any`):
                The Gemini async stream object from
                ``client.aio.models.generate_content_stream``.
            _client (`Any`, optional):
                The ``genai.Client`` that produced the stream. Held here so
                its aiohttp session is not garbage-collected before the
                stream is fully consumed.

        Yields:
            `ChatResponse`:
                Incremental ``ChatResponse`` objects with ``is_last=False``
                followed by a final one with ``is_last=True``.
        """
        # All delta should have the same block identifier
        # Use the API's response_id when available (it arrives at the first
        # chunk); otherwise generate a UUID to ensure all chunks share a
        # stable id.
        response_id: str | None = None
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        acc_tool_calls: dict = {}
        usage = None

        async for chunk in response:
            # Capture response_id from the first chunk that carries it
            if response_id is None:
                response_id = (
                    getattr(chunk, "response_id", None) or uuid.uuid4().hex
                )

            delta_content: list = []

            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    if part.text:
                        if part.thought:
                            acc_thinking.thinking += part.text
                            delta_content.append(
                                ThinkingBlock(
                                    id=acc_thinking.id,
                                    thinking=part.text,
                                ),
                            )
                        else:
                            acc_text.text += part.text
                            delta_content.append(
                                TextBlock(id=acc_text.id, text=part.text),
                            )

                    if part.function_call:
                        keyword_args = part.function_call.args or {}
                        if part.thought_signature:
                            call_id = base64.b64encode(
                                part.thought_signature,
                            ).decode("utf-8")
                        else:
                            call_id = part.function_call.id
                        input_str = json.dumps(
                            keyword_args,
                            ensure_ascii=False,
                        )
                        acc_tool_calls[call_id] = {
                            "name": part.function_call.name,
                            "input": input_str,
                        }
                        delta_content.append(
                            ToolCallBlock(
                                id=call_id,
                                name=part.function_call.name,
                                input=input_str,
                            ),
                        )

            usage = self._extract_usage(chunk.usage_metadata, start_datetime)

            if delta_content:
                yield ChatResponse(
                    id=response_id,
                    content=delta_content,
                    is_last=False,
                    usage=usage,
                )

        final_content: list = []
        if acc_thinking.thinking:
            final_content.append(acc_thinking)
        if acc_text.text:
            final_content.append(acc_text)
        for call_id, tc in acc_tool_calls.items():
            final_content.append(
                ToolCallBlock(id=call_id, name=tc["name"], input=tc["input"]),
            )

        yield ChatResponse(
            id=response_id or uuid.uuid4().hex,
            content=final_content,
            is_last=True,
            usage=usage,
        )

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: GenerateContentResponse,
    ) -> ChatResponse:
        """Parse the Gemini non-streaming response.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`GenerateContentResponse`):
                The Gemini generate content response object.

        Returns:
            `ChatResponse`:
                A single ``ChatResponse`` with ``is_last=True``.
        """
        content_blocks: List[TextBlock | ToolCallBlock | ThinkingBlock] = []

        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if part.text:
                    if part.thought:
                        content_blocks.append(
                            ThinkingBlock(thinking=part.text),
                        )
                    else:
                        content_blocks.append(TextBlock(text=part.text))

                if part.function_call:
                    keyword_args = part.function_call.args or {}
                    if part.thought_signature:
                        call_id = base64.b64encode(
                            part.thought_signature,
                        ).decode("utf-8")
                    else:
                        call_id = part.function_call.id
                    content_blocks.append(
                        ToolCallBlock(
                            id=call_id,
                            name=part.function_call.name,
                            input=json.dumps(keyword_args, ensure_ascii=False),
                        ),
                    )

        usage = self._extract_usage(response.usage_metadata, start_datetime)

        return ChatResponse(
            id=getattr(response, "response_id", None) or uuid.uuid4().hex,
            content=content_blocks,
            is_last=True,
            usage=usage,
        )

    def _extract_usage(
        self,
        usage_metadata: Any,
        start_datetime: datetime,
    ) -> ChatUsage | None:
        """Extract ChatUsage from usage_metadata.

        Args:
            usage_metadata (`Any`):
                The usage metadata object from a Gemini response.
            start_datetime (`datetime`):
                The start datetime of the response generation.

        Returns:
            `ChatUsage | None`:
                A ``ChatUsage`` object if usage data is available, otherwise
                ``None``.
        """
        if not usage_metadata:
            return None
        prompt_tokens = usage_metadata.prompt_token_count
        total_tokens = usage_metadata.total_token_count
        if prompt_tokens is not None and total_tokens is not None:
            return ChatUsage(
                input_tokens=prompt_tokens,
                output_tokens=total_tokens - prompt_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=usage_metadata,
            )
        return None

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, dict | None]:
        """Validate and format tools and tool_choice for Gemini.

        Converts tool schemas to Gemini's ``function_declarations``
        format (resolving ``$ref`` references) and maps tool_choice
        modes to Gemini's ``function_calling_config``. When
        ``tool_choice.tools`` is specified the schemas list is filtered
        to only those tools. When ``tool_choice.mode`` is a specific
        tool name (str) the model is restricted via
        ``allowed_function_names`` without needing to filter the list,
        preserving prompt-cache efficiency.

        Args:
            tools (`list[dict] | None`):
                The raw tool schemas.
            tool_choice (`ToolChoice | None`):
                The tool choice configuration.

        Returns:
            `tuple[list[dict] | None, dict | None]`:
                A tuple of (formatted_tools, formatted_tool_config).
        """
        if tool_choice and tools:
            self._validate_tool_choice(tool_choice, tools)
            if tool_choice.get("tools"):
                allowed = set(tool_choice["tools"])
                tools = [t for t in tools if t["function"]["name"] in allowed]

        fmt_tools = None
        if tools:
            function_declarations = []
            for schema in tools:
                if "function" not in schema:
                    continue
                func = schema["function"].copy()
                if "parameters" in func:
                    func["parameters"] = _flatten_json_schema(
                        func["parameters"],
                    )
                function_declarations.append(func)
            fmt_tools = [{"function_declarations": function_declarations}]

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice["mode"]

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            # mode is a specific tool name — restrict to that single tool
            fmt_choice: dict = {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [mode],
                },
            }
            return fmt_tools, fmt_choice

        mode_mapping = {
            "auto": "AUTO",
            "none": "NONE",
            "required": "ANY",
        }
        fmt_choice = {
            "function_calling_config": {"mode": mode_mapping[mode]},
        }
        return fmt_tools, fmt_choice
