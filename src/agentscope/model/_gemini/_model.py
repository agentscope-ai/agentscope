# -*- coding: utf-8 -*-
"""The Google Gemini chat model implementation."""
import base64
import copy
import json
import uuid
from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, List

from pydantic import BaseModel, SecretStr, Field

from .. import ChatUsage, ChatModelBase
from ...formatter import FormatterBase, GeminiChatFormatter
from ...message import ThinkingBlock, ToolCallBlock, TextBlock
from ...model import ChatResponse
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


class GeminiCredential(BaseModel):
    """The Google Gemini credential model."""

    type: Literal["gemini_credential"] = "gemini_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Google Gemini API key.",
    )
    """The API key."""


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

    credential: GeminiCredential
    """The Gemini credential."""

    model: str = Field(
        title="Model",
        description="The Gemini model name.",
    )

    stream: bool = Field(
        default=True,
        title="Enable Streaming Output",
        description="Whether to enable streaming output.",
    )

    max_retries: int = Field(
        default=0,
        title="Max Retries",
        description="The maximum retries for the Gemini API.",
        ge=0,
    )

    parameters: Parameters = Field(
        default_factory=Parameters,
        title="Gemini API parameters",
        description="The Gemini API parameters.",
    )

    formatter: FormatterBase = Field(
        default_factory=GeminiChatFormatter,
    )
    """The formatter for Gemini API."""

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

        if tools:
            config["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            config["tool_config"] = self._format_tool_choice(
                tool_choice,
                tools,
            )

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

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format tool schemas for the Gemini API.

        Args:
            schemas (`list[dict[str, Any]]`):
                The raw tool JSON schemas in the standard ``{"function": ...}``
                format.

        Returns:
            `list[dict[str, Any]]`:
                A list containing a single dict with
                ``"function_declarations"`` key, as required by the Gemini
                API.
        """
        function_declarations = []
        for schema in schemas:
            if "function" not in schema:
                continue
            func = schema["function"].copy()
            if "parameters" in func:
                func["parameters"] = _flatten_json_schema(func["parameters"])
            function_declarations.append(func)
        return [{"function_declarations": function_declarations}]

    def _format_tool_choice(
        self,
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> dict | None:
        """Format tool_choice parameter for the Gemini API.

        Args:
            tool_choice (`ToolChoice | None`):
                The unified tool choice parameter which can be ``"auto"``,
                ``"none"``, ``"required"``, or a specific function name.
            tools (`list[dict] | None`):
                The list of available tools, used for validation when
                ``tool_choice`` is a specific function name.

        Returns:
            `dict | None`:
                The formatted ``tool_config`` dict for the Gemini API, or
                ``None`` if ``tool_choice`` is ``None``.
        """
        self._validate_tool_choice(tool_choice, tools)

        if tool_choice is None:
            return None

        mode_mapping = {
            "auto": "AUTO",
            "none": "NONE",
            "required": "ANY",
        }
        mode = mode_mapping.get(tool_choice)
        if mode:
            return {"function_calling_config": {"mode": mode}}
        return {
            "function_calling_config": {
                "mode": "ANY",
                "allowed_function_names": [tool_choice],
            },
        }
