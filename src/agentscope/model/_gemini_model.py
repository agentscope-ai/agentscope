# -*- coding: utf-8 -*-
# mypy: disable-error-code="dict-item"
"""The Google Gemini model in agentscope."""
from datetime import datetime
from typing import (
    AsyncGenerator,
    Any,
    TYPE_CHECKING,
    AsyncIterator,
    Literal,
    Type,
    List,
)

from pydantic import BaseModel

from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..message import ToolUseBlock, TextBlock, ThinkingBlock
from ._model_usage import ChatUsage
from ._model_base import ChatModelBase
from ._model_response import ChatResponse
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse
else:
    GenerateContentResponse = "google.genai.types.GenerateContentResponse"


class GeminiChatModel(ChatModelBase):
    """The Google Gemini chat model class in agentscope."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        stream: bool = True,
        thinking_config: dict | None = None,
        client_args: dict = None,
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
            thinking_config (`dict | None`, optional):
                Thinking config, supported models are 2.5 Pro, 2.5 Flash, etc.
                Refer to https://ai.google.dev/gemini-api/docs/thinking for
                more details.

                .. code-block:: python
                    :caption: Example of thinking_config

                    {
                        "include_thoughts": True, # enable thoughts or not
                        "thinking_budget": 1024   # Max tokens for reasoning
                    }

            client_args (`dict`, default `None`):
                The extra keyword arguments to initialize the OpenAI client.
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

        super().__init__(model_name, stream)

        self.client = genai.Client(
            api_key=api_key,
            **(client_args or {}),
        )
        self.thinking_config = thinking_config
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "any", "required"]
        | str
        | None = None,
        structured_model: Type[BaseModel] | None = None,
        **config_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the Gemini model with the provided arguments.

        Args:
            messages (`list[dict[str, Any]]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict] | None`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "any", "required"] | str \
            | None`, default `None`):
                Controls which (if any) tool is called by the model.
                 Can be "auto", "none", "any", "required", or specific tool
                 name. For more details, please refer to
                 https://ai.google.dev/gemini-api/docs/function-calling?hl=en&example=meeting#function_calling_modes
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

                .. note:: When `structured_model` is specified,
                    both `tools` and `tool_choice` parameters are ignored,
                    and the model will only perform structured output
                    generation without calling any other tools.

                For more details, please refer to
                    https://ai.google.dev/gemini-api/docs/structured-output

            **config_kwargs (`Any`):
                The keyword arguments for Gemini chat completions API.
        """

        config: dict = {
            "thinking_config": self.thinking_config,
            **self.generate_kwargs,
            **config_kwargs,
        }

        if tools:
            config["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            self._validate_tool_choice(tool_choice, tools)
            config["tool_config"] = self._format_tool_choice(tool_choice)

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            config.pop("tools", None)
            config.pop("tool_config", None)
            config["response_mime_type"] = "application/json"
            config["response_schema"] = structured_model

        # Prepare the arguments for the Gemini API call
        kwargs: dict[str, JSONSerializableObject] = {
            "model": self.model_name,
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
                structured_model,
            )

        # non-streaming
        response = await self.client.aio.models.generate_content(
            **kwargs,
        )

        parsed_response = self._parse_gemini_generation_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    async def _parse_gemini_stream_generation_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator[GenerateContentResponse],
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given a Gemini streaming generation response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncIterator[GenerateContentResponse]`):
                Gemini GenerateContentResponse async iterator to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            `AsyncGenerator[ChatResponse, None]`:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.
        """

        text = ""
        thinking = ""
        metadata: dict | None = None
        async for chunk in response:
            content_block: list = []

            # Thinking parts
            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    if part.thought and part.text:
                        thinking += part.text

            # Text parts
            if chunk.text:
                text += chunk.text
                if structured_model:
                    metadata = _json_loads_with_repair(text)

            # Function calls
            tool_calls = []
            if chunk.function_calls:
                for function_call in chunk.function_calls:
                    tool_calls.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=function_call.id,
                            name=function_call.name,
                            input=function_call.args or {},
                        ),
                    )

            usage = None
            if chunk.usage_metadata:
                usage = ChatUsage(
                    input_tokens=chunk.usage_metadata.prompt_token_count,
                    output_tokens=chunk.usage_metadata.total_token_count
                    - chunk.usage_metadata.prompt_token_count,
                    time=(datetime.now() - start_datetime).total_seconds(),
                )

            if thinking:
                content_block.append(
                    ThinkingBlock(
                        type="thinking",
                        thinking=thinking,
                    ),
                )

            if text:
                content_block.append(
                    TextBlock(
                        type="text",
                        text=text,
                    ),
                )

            content_block.extend(
                [
                    *tool_calls,
                ],
            )

            parsed_chunk = ChatResponse(
                content=content_block,
                usage=usage,
                metadata=metadata,
            )
            yield parsed_chunk

    def _parse_gemini_generation_response(
        self,
        start_datetime: datetime,
        response: GenerateContentResponse,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given a Gemini chat completion response object, extract the content
           blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                The OpenAI chat completion response object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            ChatResponse (`ChatResponse`):
                A ChatResponse object containing the content blocks and usage.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.
        """
        content_blocks: List[TextBlock | ToolUseBlock | ThinkingBlock] = []
        metadata: dict | None = None

        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if part.thought and part.text:
                    content_blocks.append(
                        ThinkingBlock(
                            type="thinking",
                            thinking=part.text,
                        ),
                    )

        if response.text:
            content_blocks.append(
                TextBlock(
                    type="text",
                    text=response.text,
                ),
            )
            if structured_model:
                metadata = _json_loads_with_repair(response.text)

        if response.function_calls:
            for tool_call in response.function_calls:
                content_blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=tool_call.id,
                        name=tool_call.name,
                        input=tool_call.args or {},
                    ),
                )

        if response.usage_metadata:
            usage = ChatUsage(
                input_tokens=response.usage_metadata.prompt_token_count,
                output_tokens=response.usage_metadata.total_token_count
                - response.usage_metadata.prompt_token_count,
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        else:
            usage = None

        return ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=metadata,
        )

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schema into required format for Gemini API.

        Args:
            schemas (`dict[str, Any]`):
                The tools JSON schemas.

        Returns:
            List[Dict[str, Any]]:
                A list containing a dictionary with the
                "function_declarations" key, which maps to a list of
                function definitions.

        Example:
            .. code-block:: python
                :caption: Example tool schemas of Gemini API

                # Input JSON schema
                schemas = [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'execute_shell_command',
                            'description': 'xxx',
                            'parameters': {
                                'type': 'object',
                                'properties': {
                                    'command': {
                                        'type': 'string',
                                        'description': 'xxx.'
                                    },
                                    'timeout': {
                                        'type': 'integer',
                                        'default': 300
                                    }
                                },
                                'required': ['command']
                            }
                        }
                    }
                ]

                # Output format (Gemini API expected):
                [
                    {
                        'function_declarations': [
                            {
                                'name': 'execute_shell_command',
                                'description': 'xxx.',
                                'parameters': {
                                    'type': 'object',
                                    'properties': {
                                        'command': {
                                            'type': 'string',
                                            'description': 'xxx.'
                                        },
                                        'timeout': {
                                            'type': 'integer',
                                            'default': 300
                                        }
                                    },
                                    'required': ['command']
                                }
                            }
                        ]
                    }
                ]
        """
        return [
            {
                "function_declarations": [
                    _["function"] for _ in schemas if "function" in _
                ],
            },
        ]

    def _format_tool_choice(
        self,
        tool_choice: Literal["auto", "none", "any", "required"] | str | None,
    ) -> dict | None:
        """Format tool_choice parameter for API compatibility.

        Args:
            tool_choice (`Literal["auto", "none"] | str | None`, default \
            `None`):
                Controls which (if any) tool is called by the model.
                 Can be "auto", "none", "any", "required", or specific tool
                 name.
                 For more details, please refer to
                 https://ai.google.dev/gemini-api/docs/function-calling?hl=en&example=meeting#function_calling_modes
        Returns:
            `dict | None`:
                The formatted tool choice configuration dict, or None if
                    tool_choice is None.
        """
        if tool_choice is None:
            return None

        mode_mapping = {
            "auto": "AUTO",
            "none": "NONE",
            "any": "ANY",
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
