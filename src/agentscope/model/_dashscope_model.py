# -*- coding: utf-8 -*-
"""The dashscope API model classes."""
import uuid
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
)

from pydantic import BaseModel
from aioitertools import iter as giter

from ._model_base import ChatModelBase
from ._model_response import ChatResponse
from ._model_usage import ChatUsage
from ..formatter import FormatterBase, DashScopeChatFormatter
from ..message import TextBlock, ToolCallBlock, ThinkingBlock, Msg
from ..tool import ToolChoice
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
    MultimodalConversation APIs into one method.

    This class provides a unified interface for DashScope API by automatically
    selecting between text-only (Generation API) and multimodal
    (MultiModalConversation API) endpoints. The `multimodality` parameter
    allows explicit control over API selection:

    - When `multimodality=True`: Forces use of MultiModalConversation API
      for handling images, videos, and other multimodal inputs
    - When `multimodality=False`: Forces use of Generation API for
      text-only processing
    - When `multimodality=None` (default): Automatically selects the API
      based on model name (e.g., models with "-vl" suffix or starting
      with "qvq" will use MultiModalConversation API)

    This design enables seamless switching between text and multimodal
    models without changing code structure, making it easier to work with
    DashScope's diverse model offerings.
    """

    class ThinkingConfig(BaseModel):
        """The configuration for the thinking process in DashScope API."""

        enable_thinking: bool
        thinking_budget: int = 2000
        preserve_thinking: bool = False

    def __init__(
        self,
        model_name: str,
        api_key: str,
        stream: bool = True,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        thinking_config: ThinkingConfig | None = None,
        multimodality: bool = False,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        base_http_api_url: str | None = None,
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
            multimodality (`bool | None`, optional):
                Whether to use multimodal conversation API. If `True`,
                it will use `dashscope.AioMultiModalConversation.call`
                to process multimodal inputs such as images and text. If
                `False`, it will use
                `dashscope.aigc.generation.AioGeneration.call` to process
                text inputs. If `None` (default), the choice is based on
                the model name.
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

        self.thinking_config = (
            thinking_config
            or DashScopeChatModel.ThinkingConfig(
                enable_thinking=False,
            )
        )

        if self.thinking_config.enable_thinking and not stream:
            logger.info(
                "In DashScope API, `stream` must be True when "
                "`enable_thinking` is True. ",
            )
            stream = True

        super().__init__(
            model_name,
            stream,
            max_retries,
            fallback_model_name,
            formatter or DashScopeChatFormatter(),
        )

        self.api_key = api_key
        self.multimodality = multimodality
        self.generate_kwargs = generate_kwargs or {}

        if base_http_api_url is not None:
            import dashscope

            dashscope.base_http_api_url = base_http_api_url

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from the dashscope
        Generation/MultimodalConversation API by the given arguments.

        .. note:: We unify the DashScope generation and multimodal conversation
         APIs into one method, since they support similar arguments and share
         the same functionality.

        Args:
            messages (`list[Msg]`):
                The Msg object that will be formatted and sent to the LLM API
                as the conversation messages.
            tools (`list[dict] | None`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`ToolChoice | None`, default `None`):
                Controls which (if any) tool is called by the model.
                 Note: DashScope API only supports "auto" and "none", so
                 "required" will be converted to "auto".
                 For more details, please refer to
                 https://help.aliyun.com/zh/model-studio/qwen-function-calling
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
            "model": model_name,
            "stream": self.stream,
            "result_format": "message",
            # In agentscope, the `incremental_output` must be `True` when
            # `self.stream` is True
            "incremental_output": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            kwargs["tools"] = fmt_tools
        if fmt_tool_choice is not None:
            kwargs["tool_choice"] = fmt_tool_choice

        # thinking related options
        kwargs = {**kwargs, **self.thinking_config.model_dump()}

        # 3. Call the API and parse the response
        start_datetime = datetime.now()
        # Use MultiModalConversation API if multimodality is True, or if the
        # model is multimodal based on the model name.
        if self.multimodality or (
            self.multimodality is None
            and ("qvq" in self.model_name or "-vl" in self.model_name)
        ):
            response = await dashscope.AioMultiModalConversation.call(
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
            )

        parsed_response = await self._parse_dashscope_generation_response(
            start_datetime,
            response,
        )

        return parsed_response

    async def _parse_dashscope_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncGenerator[GenerationResponse, None]
        | AsyncGenerator[MultiModalConversationResponse, None]
        | Generator[MultiModalConversationResponse, None, None],
    ) -> AsyncGenerator[ChatResponse, Any]:
        """Given a DashScope streaming response generator, extract the content
            blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (
                `AsyncGenerator[GenerationResponse, None] | \
                AsyncGenerator[MultiModalConversationResponse, None] | \
                Generator[MultiModalConversationResponse, None, None]`
            ):
                DashScope streaming response (async) generator
                (GenerationResponse or MultiModalConversationResponse).

        Returns:
            `AsyncGenerator[ChatResponse, Any]`:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.
        """
        # All delta should have the same block identifier
        acc_text = TextBlock(text="")
        acc_thinking = ThinkingBlock(thinking="")
        acc_tool_calls = {}
        usage = None
        response_id: str | None = None

        async for chunk in giter(response):
            # The yield delta content blocks in the current chunk
            delta_content: list = []

            if chunk.status_code != HTTPStatus.OK:
                raise RuntimeError(
                    f"Failed to get response from DashScope API: {chunk}",
                )

            # Capture response_id from the first chunk if not already set
            response_id = response_id or chunk.request_id

            # The message field in the chunk
            message = chunk.output.choices[0].message

            # Update reasoning content
            if isinstance(message.get("reasoning_content"), str):
                acc_thinking.thinking += message["reasoning_content"]
                delta_content.append(
                    ThinkingBlock(
                        id=acc_thinking.id,
                        thinking=message["reasoning_content"],
                    ),
                )

            # Update text content
            if isinstance(message.content, str):
                acc_text.text += message.content
                delta_content.append(
                    TextBlock(
                        id=acc_text.id,
                        text=message.content,
                    ),
                )
            elif isinstance(message.content, list):
                delta_text = "".join(
                    [
                        _["text"]
                        for _ in message.content
                        if isinstance(_, dict)
                        and isinstance(_.get("text"), str)
                    ],
                )
                acc_text.text += delta_text
                delta_content.append(
                    ThinkingBlock(id=acc_text.id, thinking=delta_text),
                )

            # Update tool calls
            for tool_call in message.get("tool_calls") or []:
                # Must be a dictionary
                if not isinstance(tool_call, dict):
                    continue

                # Use the index to identify different tool calls
                index = tool_call.get("index", 0)

                # Avoid appending duplicate id
                if "id" in tool_call and tool_call["id"] != acc_tool_calls[
                    index
                ].get("id"):
                    acc_tool_calls[index]["id"] = tool_call["id"]

                # Arguments
                if isinstance(tool_call.get("function"), dict):
                    func = tool_call["function"]

                    # Function name
                    if "name" in func:
                        acc_tool_calls[index]["name"] = func["name"]

                    # Function input arguments
                    if isinstance(func.get("arguments"), str):
                        acc_tool_calls[index]["arguments"] = (
                            acc_tool_calls[index].get("arguments", "")
                            + func["arguments"]
                        )

                    # Append the tool call block for this chunk
                    delta_content.append(
                        ToolCallBlock(
                            id=acc_tool_calls[index].get(
                                "id",
                                uuid.uuid4().hex,
                            ),
                            name=func.get("name", ""),
                            input=func.get("arguments", ""),
                        ),
                    )

            # The chunk usage
            if chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.input_tokens,
                    output_tokens=chunk.usage.output_tokens,
                    time=(datetime.now() - start_datetime).total_seconds(),
                    metadata=chunk.usage,
                )

            # Yield the chat response
            yield ChatResponse(
                id=response_id,
                content=delta_content,
                is_last=False,
                usage=usage,
            )

        # Yield a final complete response with the accumulated content and
        # final usage
        content = []
        if acc_text.text:
            content.append(acc_text)

        if acc_thinking.thinking:
            content.append(acc_thinking)

        if acc_tool_calls:
            content.extend(acc_tool_calls.values())

        yield ChatResponse(
            id=response_id or uuid.uuid4().hex,
            content=content,
            is_last=True,
            usage=usage,
        )

    async def _parse_dashscope_generation_response(
        self,
        start_datetime: datetime,
        response: Union[
            GenerationResponse,
            MultiModalConversationResponse,
        ],
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

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks and usage.
        """
        # Collect the content blocks from the response.
        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"Failed to get response from DashScope API: {response}",
            )

        content_blocks: List[TextBlock | ToolCallBlock] = []

        message = response.output.choices[0].message

        # The content and tool calls in the message
        content, tool_calls = message.get("content"), message.get("tool_calls")

        if isinstance(content, str):
            content_blocks.append(TextBlock(text=content))

        elif isinstance(content, list):
            content_blocks.append(
                TextBlock(
                    text="".join(
                        [
                            _["text"]
                            for _ in content
                            if isinstance(_, dict)
                            and isinstance(_.get("text"), str)
                        ],
                    ),
                ),
            )

        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                name = function.get("name") or ""
                input_ = function.get("arguments") or "{}"

                content_blocks.append(
                    ToolCallBlock(
                        id=tool_call.get("id", uuid.uuid4().hex),
                        name=name,
                        input=input_,
                    ),
                )

        # Usage information
        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=response.usage,
            )

        return ChatResponse(
            id=getattr(response, "request_id", uuid.uuid4().hex),
            content=content_blocks,
            is_last=True,
            usage=usage,
        )

    def _format_tools(
        self,
        tools: list[dict] | None,
        tool_choice: ToolChoice | None,
    ) -> tuple[list[dict] | None, str | dict | None]:
        """Validate, filter, and format tools and tool_choice for DashScope.

        DashScope only supports "auto" and "none" modes; "required" is
        converted to "auto" with a warning. When ``tool_choice.tools``
        is specified, filters tools accordingly. When mode is "required"
        and only one tool remains, it is formatted as a forced function
        call.

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
            if tool_choice.get("tools"):
                allowed = set(tool_choice["tools"])
                tools = [t for t in tools if t["function"]["name"] in allowed]

        fmt_tools = None
        if tools:
            for value in tools:
                if (
                    not isinstance(value, dict)
                    or "type" not in value
                    or value["type"] != "function"
                    or "function" not in value
                ):
                    raise ValueError(
                        f"Each schema must be a dict with 'type' as "
                        f"'function' and 'function' key, got {value}",
                    )
            fmt_tools = tools

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice["mode"]

        if mode == "required" and tools and len(tools) == 1:
            return fmt_tools, {
                "type": "function",
                "function": {"name": tools[0]["function"]["name"]},
            }

        if mode == "required":
            warnings.warn(
                f"'{mode}' is not supported by DashScope API. "
                "It will be converted to 'auto'.",
                DeprecationWarning,
            )
            return fmt_tools, "auto"

        return fmt_tools, mode
