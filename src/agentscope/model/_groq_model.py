# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""Groq Chat model class."""
import copy
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    Literal,
    Type,
)
from collections import OrderedDict

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from .._logging import logger
from .._utils._common import (
    _json_loads_with_repair,
    _parse_streaming_json_dict,
)
from ..message import (
    ToolUseBlock,
    TextBlock,
)
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from groq.types.chat import ChatCompletion
    from groq import AsyncStream
else:
    ChatCompletion = "groq.types.chat.ChatCompletion"
    AsyncStream = "groq.types.chat.AsyncStream"


class GroqChatModel(ChatModelBase):
    """The Groq chat model class."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = True,
        stream_tool_parsing: bool = True,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Groq client.

        Args:
            model_name (`str`):
                The name of the model to use in Groq API, e.g.
                ``"llama-3.3-70b-versatile"``,
                ``"mixtral-8x7b-32768"``, etc.
            api_key (`str`, default `None`):
                The API key for Groq API. If not specified, it will
                be read from the environment variable ``GROQ_API_KEY``.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            stream_tool_parsing (`bool`, default to `True`):
                Whether to parse incomplete tool use JSON during streaming
                with auto-repair. If True, partial JSON is repaired to
                valid dicts in real-time for immediate tool function input.
                Otherwise, the input field remains {} until the final
                chunk arrives.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the Groq client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in Groq API generation,
                e.g. ``temperature``, ``max_tokens``, ``top_p``.
            **kwargs (`Any`):
                Additional keyword arguments.
        """

        if kwargs:
            logger.warning(
                "Unknown keyword arguments: %s. These will be ignored.",
                list(kwargs.keys()),
            )

        try:
            from groq import AsyncGroq
        except ImportError as e:
            raise ImportError(
                "The package groq is not found. Please install it by "
                'running command `pip install "groq"`',
            ) from e

        super().__init__(model_name, stream)

        self.client = AsyncGroq(
            api_key=api_key,
            **(client_kwargs or {}),
        )
        self.stream_tool_parsing = stream_tool_parsing
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from Groq chat completions API.

        Args:
            messages (`list[dict]`):
                A list of dictionaries, where ``role`` and ``content``
                fields are required, and ``name`` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "required"] | str \
            | None`, default `None`):
                Controls which (if any) tool is called by the model.
                Can be ``"auto"``, ``"none"``, ``"required"``, or a
                specific tool name.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected
                structure for the model's output. When provided, the
                model will be forced to return JSON conforming to the
                schema via ``response_format``.

                .. note:: When ``structured_model`` is specified,
                    both ``tools`` and ``tool_choice`` parameters are
                    ignored.

            **kwargs (`Any`):
                The keyword arguments for Groq chat completions API,
                e.g. ``temperature``, ``max_tokens``, ``top_p``, etc.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Groq chat completions API.
        """

        if not isinstance(messages, list):
            raise ValueError(
                "Groq `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for Groq API.",
            )

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            self._validate_tool_choice(tool_choice, tools)
            kwargs["tool_choice"] = self._format_tool_choice(tool_choice)

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
            kwargs["response_format"] = {
                "type": "json_object",
                "schema": structured_model.model_json_schema(),
            }

        start_datetime = datetime.now()
        response = await self.client.chat.completions.create(**kwargs)

        if self.stream:
            return self._parse_groq_stream_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = self._parse_groq_completion_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    # pylint: disable=too-many-statements
    async def _parse_groq_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given a Groq streaming completion response, extract the content
        blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                Groq AsyncStream object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected
                structure for the model's output.

        Returns:
            `AsyncGenerator[ChatResponse, None]`:
                An async generator that yields ChatResponse objects.
        """
        usage, res = None, None
        response_id: str | None = None
        text = ""
        tool_calls = OrderedDict()
        last_input_objs = {}
        metadata: dict | None = None
        contents: List[TextBlock | ToolUseBlock] = []
        last_contents = None

        async for chunk in response:
            if response_id is None:
                response_id = getattr(chunk, "id", None)

            if chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    time=(datetime.now() - start_datetime).total_seconds(),
                    metadata=chunk.usage,
                )

            if not chunk.choices:
                if usage and contents:
                    _kwargs: dict[str, Any] = {
                        "content": contents,
                        "usage": usage,
                        "metadata": metadata,
                    }
                    if response_id:
                        _kwargs["id"] = response_id
                    res = ChatResponse(**_kwargs)
                    yield res
                continue

            choice = chunk.choices[0]
            text += getattr(choice.delta, "content", None) or ""

            for tool_call in getattr(choice.delta, "tool_calls", None) or []:
                if tool_call.index in tool_calls:
                    if tool_call.function.arguments is not None:
                        tool_calls[tool_call.index][
                            "input"
                        ] += tool_call.function.arguments
                else:
                    tool_calls[tool_call.index] = {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": tool_call.function.arguments or "",
                    }

            contents = []

            if text:
                contents.append(
                    TextBlock(
                        type="text",
                        text=text,
                    ),
                )

                if structured_model:
                    metadata = _json_loads_with_repair(text)

            for tool_call in tool_calls.values():
                input_str = tool_call["input"]
                tool_id = tool_call["id"]

                if self.stream_tool_parsing:
                    repaired_input = _parse_streaming_json_dict(
                        input_str,
                        last_input_objs.get(tool_id),
                    )
                    last_input_objs[tool_id] = repaired_input
                else:
                    repaired_input = {}

                contents.append(
                    ToolUseBlock(
                        type=tool_call["type"],
                        id=tool_id,
                        name=tool_call["name"],
                        input=repaired_input,
                        raw_input=input_str,
                    ),
                )

            if contents:
                _kwargs = {
                    "content": contents,
                    "usage": usage,
                    "metadata": metadata,
                }
                if response_id:
                    _kwargs["id"] = response_id
                res = ChatResponse(**_kwargs)
                yield res
                last_contents = copy.deepcopy(contents)

        # If stream_tool_parsing is False, yield last contents
        if not self.stream_tool_parsing and tool_calls and last_contents:
            metadata = None
            for block in last_contents:
                if block.get("type") == "tool_use":
                    block["input"] = input_obj = _json_loads_with_repair(
                        str(block.get("raw_input") or "{}"),
                    )

                    if structured_model:
                        metadata = input_obj

            _kwargs = {
                "content": last_contents,
                "usage": usage,
                "metadata": metadata,
            }
            if response_id:
                _kwargs["id"] = response_id
            yield ChatResponse(**_kwargs)

    def _parse_groq_completion_response(
        self,
        start_datetime: datetime,
        response: ChatCompletion,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given a Groq chat completion response object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                Groq ChatCompletion object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected
                structure for the model's output.

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks
                and usage.
        """
        content_blocks: List[TextBlock | ToolUseBlock] = []
        metadata: dict | None = None

        if response.choices:
            choice = response.choices[0]

            if choice.message.content:
                content_blocks.append(
                    TextBlock(
                        type="text",
                        text=choice.message.content,
                    ),
                )

                if structured_model:
                    metadata = _json_loads_with_repair(
                        choice.message.content,
                    )

            for tool_call in choice.message.tool_calls or []:
                content_blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=tool_call.id,
                        name=tool_call.function.name,
                        input=_json_loads_with_repair(
                            tool_call.function.arguments,
                        ),
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
            "usage": usage,
            "metadata": metadata,
        }
        response_id = getattr(response, "id", None)
        if response_id:
            resp_kwargs["id"] = response_id

        return ChatResponse(**resp_kwargs)

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schemas to the Groq format.

        Groq uses the same OpenAI-compatible tool format.
        """
        return schemas

    def _format_tool_choice(
        self,
        tool_choice: Literal["auto", "none", "required"] | str | None,
    ) -> str | dict | None:
        """Format tool_choice parameter for API compatibility.

        Args:
            tool_choice (`Literal["auto", "none", "required"] | str \
            | None`, default `None`):
                Controls which (if any) tool is called by the model.

        Returns:
            `str | dict | None`:
                The formatted tool choice configuration.
        """
        if tool_choice is None:
            return None

        mode_mapping = {
            "auto": "auto",
            "none": "none",
            "required": "required",
        }
        if tool_choice in mode_mapping:
            return mode_mapping[tool_choice]
        return {"type": "function", "function": {"name": tool_choice}}
