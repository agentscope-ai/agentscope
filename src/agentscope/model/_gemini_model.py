# -*- coding: utf-8 -*-
"""The Google Gemini model in agentscope."""
import base64
import copy
from datetime import datetime
import json
from typing import (
    AsyncGenerator,
    Any,
    TYPE_CHECKING,
    AsyncIterator,
    List,
)

from pydantic import BaseModel

from .._logging import logger
from ..formatter import FormatterBase
from ..message import ToolCallBlock, TextBlock, ThinkingBlock
from ._model_usage import ChatUsage
from ._model_base import ChatModelBase
from ._model_response import ChatResponse
from ..tracing import trace_llm
from ..types import JSONSerializableObject
from ..tool import ToolChoice

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse
else:
    GenerateContentResponse = "google.genai.types.GenerateContentResponse"


def _flatten_json_schema(schema: dict) -> dict:
    """Flatten a JSON schema by resolving all $ref references.

    .. note::
        Gemini API does not support `$defs` and `$ref` in JSON schemas.
        This function resolves all `$ref` references by inlining the
        referenced definitions, producing a self-contained schema without
        any references.

    Args:
        schema (`dict`):
            The JSON schema that may contain `$defs` and `$ref` references.

    Returns:
        `dict`:
            A flattened JSON schema with all references resolved inline.
    """
    # Deep copy to avoid modifying the original schema
    schema = copy.deepcopy(schema)

    # Extract $defs if present
    defs = schema.pop("$defs", {})

    def _resolve_ref(obj: Any, visited: set | None = None) -> Any:
        """Recursively resolve $ref references in the schema."""
        if visited is None:
            visited = set()

        if not isinstance(obj, dict):
            if isinstance(obj, list):
                return [_resolve_ref(item, visited.copy()) for item in obj]
            return obj

        # Handle $ref
        if "$ref" in obj:
            ref_path = obj["$ref"]
            # Extract definition name from "#/$defs/DefinitionName"
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path[len("#/$defs/") :]

                # Prevent infinite recursion for circular references
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
                    # Recursively resolve any nested refs in the definition
                    resolved = _resolve_ref(
                        defs[def_name],
                        visited.copy(),
                    )
                    # Merge any additional properties from the original object
                    # (excluding $ref itself)
                    for key, value in obj.items():
                        if key != "$ref":
                            resolved[key] = _resolve_ref(value, visited.copy())
                    return resolved

            # If we can't resolve the ref, return as-is (shouldn't happen)
            return obj

        # Recursively process all nested objects
        result = {}
        for key, value in obj.items():
            result[key] = _resolve_ref(value, visited.copy())

        return result

    return _resolve_ref(schema)


class GeminiChatModel(ChatModelBase):
    """The Google Gemini chat model class in agentscope."""

    class ThinkingConfig(BaseModel):
        """Configuration for enabling thinking blocks in Gemini responses."""

        enable_thinking: bool
        thinking_budget: int = 1024

    def __init__(
        self,
        model_name: str,
        api_key: str,
        stream: bool = True,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        thinking_config: ThinkingConfig | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the Gemini chat model.

        Args:
            model_name (`str`):
                The name of the Gemini model to use, e.g. "gemini-2.5-flash".
            api_key (`str`):
                The API key for Google Gemini.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
            thinking_config (`ThinkingConfig | None`, optional):
                Thinking config, supported models are 2.5 Pro, 2.5 Flash, etc.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the Gemini client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in Gemini API generation,
               e.g. `temperature`, `seed`.
        """

        try:
            from google import genai
        except ImportError as e:
            raise ImportError(
                "Please install gemini Python sdk with "
                "`pip install -q -U google-genai`",
            ) from e

        super().__init__(
            model_name=model_name,
            stream=stream,
            max_retries=max_retries,
            fallback_model_name=fallback_model_name,
            formatter=formatter,
        )

        self.client = genai.Client(
            api_key=api_key,
            **(client_kwargs or {}),
        )
        self.thinking_config = thinking_config
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **config_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the Gemini model with the provided arguments.

        Args:
            messages (`list[dict[str, Any]]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict] | None`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`ToolChoice | None`, default `None`):
                Controls which (if any) tool is called by the model.
                 For more details, please refer to
                 https://ai.google.dev/gemini-api/docs/function-calling?hl=en&example=meeting#function_calling_modes
            **config_kwargs (`Any`):
                The keyword arguments for Gemini chat completions API.
        """

        config: dict = {
            **self.generate_kwargs,
            **config_kwargs,
        }
        if self.thinking_config:
            config["thinking_config"] = {
                "include_thoughts": self.thinking_config.enable_thinking,
                "thinking_budget": self.thinking_config.thinking_budget,
            }

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            config["tools"] = fmt_tools
        if fmt_tool_choice is not None:
            config["tool_config"] = fmt_tool_choice

        # Prepare the arguments for the Gemini API call
        kwargs: dict = {
            "model": model_name,
            "contents": messages,
            "config": config,
        }

        start_datetime = datetime.now()
        if self.stream:
            response = await self.client.aio.models.generate_content_stream(
                **kwargs,
            )

            return self._parse_gemini_stream_generation_response(
                start_datetime,
                response,
            )

        # non-streaming
        response = await self.client.aio.models.generate_content(
            **kwargs,
        )

        parsed_response = self._parse_gemini_generation_response(
            start_datetime,
            response,
        )

        return parsed_response

    def _extract_usage(
        self,
        usage_metadata: Any,
        start_datetime: datetime,
    ) -> ChatUsage | None:
        """Extract ChatUsage from usage_metadata safely, returning None if
        unavailable or if token counts are None.

        Args:
            usage_metadata:
                The usage metadata object from the Gemini response.
            start_datetime (`datetime`):
                The start datetime of the generation.

        Returns:
            `ChatUsage | None`:
                A ChatUsage object, or None if data is unavailable.
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
            )
        return None

    # pylint: disable=too-many-branches
    async def _parse_gemini_stream_generation_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator[GenerateContentResponse],
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given a Gemini streaming generation response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncIterator[GenerateContentResponse]`):
                Gemini GenerateContentResponse async iterator to parse.

        Returns:
            `AsyncGenerator[ChatResponse, None]`:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.
        """

        # Accumulated state
        acc_text = ""
        acc_thinking = ""
        acc_tool_calls: dict = {}  # call_id -> {name, input}
        usage = None

        async for chunk in response:
            delta_content: list = []

            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    if part.text:
                        if part.thought:
                            acc_thinking += part.text
                            delta_content.append(
                                ThinkingBlock(thinking=part.text),
                            )
                        else:
                            acc_text += part.text
                            delta_content.append(TextBlock(text=part.text))

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
                    content=delta_content,
                    is_last=False,
                    usage=usage,
                )

        # Build final accumulated content
        final_content: list = []
        if acc_thinking:
            final_content.append(ThinkingBlock(thinking=acc_thinking))
        if acc_text:
            final_content.append(TextBlock(text=acc_text))
        for call_id, tc in acc_tool_calls.items():
            final_content.append(
                ToolCallBlock(
                    id=call_id,
                    name=tc["name"],
                    input=tc["input"],
                ),
            )

        yield ChatResponse(
            content=final_content,
            is_last=True,
            usage=usage,
        )

    def _parse_gemini_generation_response(
        self,
        start_datetime: datetime,
        response: GenerateContentResponse,
    ) -> ChatResponse:
        """Given a Gemini chat completion response object, extract the content
           blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`GenerateContentResponse`):
                The Gemini generation response object to parse.

        Returns:
            ChatResponse (`ChatResponse`):
                A ChatResponse object containing the content blocks and usage.
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
                        content_blocks.append(
                            TextBlock(text=part.text),
                        )

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
            content=content_blocks,
            is_last=True,
            usage=usage,
        )

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, dict | None]:
        """Validate, filter, and format tools and tool_choice for Gemini.

        Converts tool schemas to Gemini's ``function_declarations``
        format (resolving ``$ref`` references) and maps tool_choice
        modes to Gemini's ``function_calling_config``. When mode is
        "required" (``ANY``) and ``tool_choice.tools`` is specified,
        ``allowed_function_names`` is included.

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
        mode_mapping = {
            "auto": "AUTO",
            "none": "NONE",
            "required": "ANY",
        }
        gemini_mode = mode_mapping[mode]

        if gemini_mode == "ANY" and tool_choice.get("tools") and tools:
            fmt_choice: dict = {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [
                        t["function"]["name"] for t in tools
                    ],
                },
            }
        else:
            fmt_choice = {"function_calling_config": {"mode": gemini_mode}}

        return fmt_tools, fmt_choice
