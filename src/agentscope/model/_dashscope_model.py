# -*- coding: utf-8 -*-
"""The dashscope API model classes."""
import copy
import collections
import json
import os
import warnings
from datetime import datetime
from http import HTTPStatus
from typing import (
    Any,
    AsyncGenerator,
    Generator,
    Union,
    TYPE_CHECKING,
    List,
    Literal,
    Type,
)
from pydantic import BaseModel
from aioitertools import iter as giter

from ._model_base import ChatModelBase
from ._model_response import ChatResponse
from ._model_usage import ChatUsage
from .._utils._common import (
    _json_loads_with_repair,
    _create_tool_from_base_model,
)
from ..message import TextBlock, ToolUseBlock, ThinkingBlock
from ..tracing import trace_llm
from ..types import JSONSerializableObject
from .._logging import logger

if TYPE_CHECKING:
    from dashscope.api_entities.dashscope_response import GenerationResponse
    from dashscope.api_entities.dashscope_response import (
        MultiModalConversationResponse,
    )
else:
    GenerationResponse = (
        "dashscope.api_entities.dashscope_response.GenerationResponse"
    )
    MultiModalConversationResponse = (
        "dashscope.api_entities.dashscope_response."
        "MultiModalConversationResponse"
    )


class DashScopeChatModel(ChatModelBase):
    """The DashScope chat model class, which unifies the Generation and
    MultimodalConversation APIs into one method."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        stream: bool = True,
        enable_thinking: bool | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        base_http_api_url: str | None = None,
        stream_tool_parsing: bool = True,
        **_kwargs: Any,
    ) -> None:
        """Initialize the DashScope chat model.

        Args:
            model_name (`str`):
                The model names.
            api_key (`str`):
                The dashscope API key.
            stream (`bool`):
                The streaming output or not
            enable_thinking (`bool | None`, optional):
                Enable thinking or not, only support Qwen3, QwQ, DeepSeek-R1.
                Refer to `DashScope documentation
                <https://help.aliyun.com/zh/model-studio/deep-thinking>`_
                for more details.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
            optional):
               The extra keyword arguments used in DashScope API generation,
               e.g. `temperature`, `seed`.
            base_http_api_url (`str | None`, optional):
                The base URL for DashScope API requests. If not provided,
                the default base URL from the DashScope SDK will be used.
            stream_tool_parsing (`bool`, default to `True`):
                Whether to parse incomplete tool use JSON in streaming mode
                with auto-repair. If True, partial JSON (e.g., `'{"a": "x'`)
                is repaired to valid dicts (`{"a": "x"}`) in real-time for
                immediate tool function input. Otherwise, the input field
                remains {} until the final chunk arrives.
            **_kwargs (`Any`):
                Additional keyword arguments.
        """
        if enable_thinking and not stream:
            logger.info(
                "In DashScope API, `stream` must be True when "
                "`enable_thinking` is True. ",
            )
            stream = True

        super().__init__(model_name, stream)

        self.api_key = api_key
        self.enable_thinking = enable_thinking
        self.generate_kwargs = generate_kwargs or {}
        self.stream_tool_parsing = stream_tool_parsing

        if base_http_api_url is not None:
            import dashscope

            dashscope.base_http_api_url = base_http_api_url

        # Load headers from environment variable if exists
        headers = os.getenv("DASHSCOPE_API_HEADERS")
        if headers:
            try:
                headers = json.loads(str(headers))
                if not isinstance(headers, dict):
                    raise json.JSONDecodeError("", "", 0)

                if self.generate_kwargs.get("headers"):
                    headers.update(self.generate_kwargs["headers"])

                self.generate_kwargs["headers"] = headers

            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse DASHSCOPE_API_HEADERS environment "
                    "variable as JSON. It should be a JSON object.",
                )

    @trace_llm
    async def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from the dashscope
        Generation/MultimodalConversation API by the given arguments.

        .. note:: We unify the dashscope generation and multimodal conversation
         APIs into one method, since they support similar arguments and share
         the same functionality.

        Args:
            messages (`list[dict[str, Any]]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict] | None`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "required"] | str \
             |  None`,  default `None`):
                Controls which (if any) tool is called by the model.
                 Can be "auto", "none", "required", or specific tool name.
                 Note: DashScope API only supports "auto" and "none", so
                 "required" will be converted to "auto".
                 For more details, please refer to
                 https://help.aliyun.com/zh/model-studio/qwen-function-calling
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output. When provided, the model will be forced
                to return data that conforms to this schema by automatically
                converting the BaseModel to a tool function and setting
                `tool_choice` to enforce its usage. This enables structured
                output generation.

                .. note:: When `structured_model` is specified,
                    both `tools` and `tool_choice` parameters are ignored,
                    and the model will only perform structured output
                    generation without calling any other tools.

            **kwargs (`Any`):
                The keyword arguments for DashScope chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please
                refer to `DashScope documentation
                <https://help.aliyun.com/zh/dashscope/developer-reference/api-details>`_
                for more detailed arguments.
        """
        import dashscope

        kwargs = {
            "messages": messages,
            "model": self.model_name,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
            "result_format": "message",
            # In agentscope, the `incremental_output` must be `True` when
            # `self.stream` is True
            "incremental_output": self.stream,
        }

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            # Handle deprecated "any" option with warning
            if tool_choice in ["any", "required"]:
                warnings.warn(
                    f"'{tool_choice}' is not supported by DashScope API. "
                    "It will be converted to 'auto'.",
                    DeprecationWarning,
                )
                tool_choice = "auto"

            self._validate_tool_choice(tool_choice, tools)
            kwargs["tool_choice"] = self._format_tool_choice(tool_choice)

        if (
            self.enable_thinking is not None
            and "enable_thinking" not in kwargs
        ):
            kwargs["enable_thinking"] = self.enable_thinking

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            format_tool = _create_tool_from_base_model(structured_model)
            kwargs["tools"] = self._format_tools_json_schemas(
                [format_tool],
            )
            kwargs["tool_choice"] = self._format_tool_choice(
                format_tool["function"]["name"],
            )

        start_datetime = datetime.now()
        if self.model_name.startswith("qvq") or "-vl" in self.model_name:
            response = dashscope.MultiModalConversation.call(
                api_key=self.api_key,
                **kwargs,
            )

        else:
            response = await dashscope.aigc.generation.AioGeneration.call(
                api_key=self.api_key,
                **kwargs,
            )

        if self.stream:
            return self._parse_dashscope_stream_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = await self._parse_dashscope_generation_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    # pylint: disable=too-many-branches, too-many-statements
    async def _parse_dashscope_stream_response(
        self,
        start_datetime: datetime,
        response: Union[
            AsyncGenerator[GenerationResponse, None],
            Generator[MultiModalConversationResponse, None, None],
        ],
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, Any]:
        """Given a DashScope streaming response generator, extract the content
            blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (
                `Union[AsyncGenerator[GenerationResponse, None], Generator[ \
                MultiModalConversationResponse, None, None]]`
            ):
                DashScope streaming response generator (GenerationResponse or
                MultiModalConversationResponse) to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            AsyncGenerator[ChatResponse, Any]:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.
        """
        acc_content, acc_thinking_content = "", ""
        acc_tool_calls = collections.defaultdict(dict)
        last_input_objs = {}  # Store last input_obj for each tool_call
        metadata = None
        last_content = None
        usage = None

        async for chunk in giter(response):
            if chunk.status_code != HTTPStatus.OK:
                raise RuntimeError(
                    f"Failed to get response from _ API: {chunk}",
                )

            message = chunk.output.choices[0].message

            # Update reasoning content
            if isinstance(message.get("reasoning_content"), str):
                acc_thinking_content += message["reasoning_content"]

            # Update text content
            if isinstance(message.content, str):
                acc_content += message.content
            elif isinstance(message.content, list):
                for item in message.content:
                    if isinstance(item, dict) and "text" in item:
                        acc_content += item["text"]

            # Update tool calls
            for tool_call in message.get("tool_calls", []):
                index = tool_call.get("index", 0)

                if "id" in tool_call and tool_call["id"] != acc_tool_calls[
                    index
                ].get("id"):
                    acc_tool_calls[index]["id"] = (
                        acc_tool_calls[index].get("id", "") + tool_call["id"]
                    )

                if "function" in tool_call:
                    func = tool_call["function"]
                    if "name" in func:
                        acc_tool_calls[index]["name"] = (
                            acc_tool_calls[index].get("name", "")
                            + func["name"]
                        )

                    if "arguments" in func:
                        acc_tool_calls[index]["arguments"] = (
                            acc_tool_calls[index].get("arguments", "")
                            + func["arguments"]
                        )

            # Build content blocks (always include thinking and text)
            content_blocks: list[TextBlock | ToolUseBlock | ThinkingBlock] = []

            if acc_thinking_content:
                content_blocks.append(
                    ThinkingBlock(
                        type="thinking",
                        thinking=acc_thinking_content,
                    ),
                )

            if acc_content:
                content_blocks.append(
                    TextBlock(
                        type="text",
                        text=acc_content,
                    ),
                )

            for tool_call in acc_tool_calls.values():
                # Only add intermediate tool use blocks if
                # stream_tool_parsing is True
                tool_id = tool_call.get("id", "")
                input_str = tool_call.get("arguments")

                # If parsing the tool input in streaming mode
                if self.stream_tool_parsing:
                    repaired_input = _json_loads_with_repair(
                        input_str or "{}",
                    )
                    # If the new repaired input is shorter than one in the last
                    # chunk, use the last one to avoid regression
                    last_input = last_input_objs.get(tool_id, {})
                    if len(json.dumps(last_input)) > len(
                        json.dumps(repaired_input),
                    ):
                        repaired_input = last_input
                    last_input_objs[tool_id] = repaired_input

                else:
                    # Otherwise, keep input as empty dict until the final chunk
                    repaired_input = {}

                content_blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=tool_id,
                        name=tool_call.get("name", ""),
                        input=repaired_input,
                        raw_input=input_str,
                    ),
                )

                if structured_model:
                    metadata = repaired_input

            if chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.input_tokens,
                    output_tokens=chunk.usage.output_tokens,
                    time=(datetime.now() - start_datetime).total_seconds(),
                    metadata=chunk.usage,
                )

            if content_blocks:
                parsed_chunk = ChatResponse(
                    content=content_blocks,
                    usage=usage,
                    metadata=metadata,
                )
                yield parsed_chunk
                last_content = copy.deepcopy(content_blocks)

        # If stream_tool_parsing is False, we need to parse the final tool
        # use inputs here
        if not self.stream_tool_parsing and last_content and acc_tool_calls:
            metadata = None
            # Update tool use blocks in last_contents inplace
            for block in last_content:
                if block.get("type") == "tool_use":
                    block["input"] = input_obj = _json_loads_with_repair(
                        str(block.get("raw_input") or "{}"),
                    )

                    if structured_model:
                        metadata = input_obj

            yield ChatResponse(
                content=last_content,
                usage=usage,
                metadata=metadata,
            )

    async def _parse_dashscope_generation_response(
        self,
        start_datetime: datetime,
        response: Union[
            GenerationResponse,
            MultiModalConversationResponse,
        ],
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given a DashScope GenerationResponse object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (
                `Union[GenerationResponse, MultiModalConversationResponse]`
            ):
                Dashscope GenerationResponse | MultiModalConversationResponse
                object to parse.
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
        # Collect the content blocks from the response.
        if response.status_code != 200:
            raise RuntimeError(response)

        content_blocks: List[TextBlock | ToolUseBlock] = []
        metadata: dict | None = None

        message = response.output.choices[0].message
        content = message.get("content")

        if response.output.choices[0].message.get("content") not in [
            None,
            "",
            [],
        ]:
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        content_blocks.append(
                            TextBlock(
                                type="text",
                                text=item["text"],
                            ),
                        )
            else:
                content_blocks.append(
                    TextBlock(
                        type="text",
                        text=content,
                    ),
                )

        if message.get("tool_calls"):
            for tool_call in message["tool_calls"]:
                input_ = _json_loads_with_repair(
                    tool_call["function"].get(
                        "arguments",
                        "{}",
                    )
                    or "{}",
                )
                content_blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        name=tool_call["function"]["name"],
                        input=input_,
                        id=tool_call["id"],
                    ),
                )

                if structured_model:
                    metadata = input_

        # Usage information
        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=response.usage,
            )

        parsed_response = ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=metadata,
        )

        return parsed_response

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schema into required format for DashScope API.

        Args:
            schemas (`dict[str, dict[str, Any]]`):
                The tools JSON schemas.
        """
        # Check schemas format
        for value in schemas:
            if (
                not isinstance(value, dict)
                or "type" not in value
                or value["type"] != "function"
                or "function" not in value
            ):
                raise ValueError(
                    f"Each schema must be a dict with 'type' as 'function' "
                    f"and 'function' key, got {value}",
                )

        return schemas

    def _format_tool_choice(
        self,
        tool_choice: Literal["auto", "none", "required"] | str | None,
    ) -> str | dict | None:
        """Format tool_choice parameter for API compatibility.

        Args:
            tool_choice (`Literal["auto", "none", "required"] | str \
            | None`, default  `None`):
                Controls which (if any) tool is called by the model. For more
                details, please refer to
                https://help.aliyun.com/zh/model-studio/qwen-function-calling

        Returns:
            `dict | None`:
                The formatted tool choice configuration dict, or None if
                    tool_choice is None.
        """
        if tool_choice is None:
            return None
        if tool_choice in ["auto", "none"]:
            return tool_choice
        if tool_choice == "required":
            return "auto"
        return {"type": "function", "function": {"name": tool_choice}}
