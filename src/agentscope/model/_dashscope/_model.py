# -*- coding: utf-8 -*-
"""The DashScope chat model class."""
import uuid
import warnings
from datetime import datetime
from http import HTTPStatus
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, Generator, List

from pydantic import BaseModel, Field, SecretStr
from aioitertools import iter as giter

from .. import ChatUsage, ChatModelBase
from ...formatter import FormatterBase, DashScopeChatFormatter
from ...message import Msg, TextBlock, ThinkingBlock, ToolCallBlock
from ...model import ChatResponse
from ...tool import ToolChoice
from ...tracing import trace_llm


if TYPE_CHECKING:
    from dashscope.api_entities.dashscope_response import GenerationResponse
    from dashscope.api_entities.dashscope_response import (
        MultiModalConversationResponse,
    )
else:
    GenerationResponse = Any
    MultiModalConversationResponse = Any


class DashScopeCredential(BaseModel):
    """The credential for DashScope API."""

    type: Literal["dashscope_credential"] = "dashscope_credential"
    """The type of the credential."""

    api_key: SecretStr = Field(
        description="The DashScope API key.",
        title="API Key",
    )

    base_http_api_url: str | None = Field(
        default=None,
        title="API Base URL",
        description="The base URL of the DashScope API.",
    )


class DashScopeChatModel(ChatModelBase):
    """The DashScope chat model."""

    class Parameters(BaseModel):
        """The parameters for DashScope LLM API."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description="The thinking enable for the LLM output.",
        )

        thinking_budget: int | None = Field(
            default=None,
            title="Thinking budget",
            description="The thinking budget for the LLM output.",
            gt=0,
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            lt=2,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

        top_k: int | None = Field(
            default=None,
            title="Top K",
            description="The top K value for the LLM output.",
            gt=0,
            le=100,
        )

        parallel_tool_calls: bool = Field(
            default=True,
            title="Parallel Tool Calls",
            description="If enable parallel tool calls for the LLM output.",
        )

    type: Literal["dashscope_chat"] = "dashscope_chat"
    """The type of the chat model."""

    credential: DashScopeCredential
    """The DashScope API credential."""

    model: str = Field(
        title="Model",
        description="The LLM of DashScope API.",
    )
    """The DashScope API model."""

    stream: bool = Field(
        default=True,
        title="Enable Streaming Output.",
        description="The enable stream output for the LLM output.",
    )

    multimodality: bool | None = Field(
        default=None,
        title="Multimodality",
        description=(
            "Whether to call the MultiModalConversation API. "
            "``True`` forces multimodal mode, ``False`` forces text-only "
            "mode, and ``None`` (default) auto-detects from the model name "
            "(e.g. 'qvq' or '-vl' suffix)."
        ),
    )

    max_retries: int = Field(
        default=0,
        title="Max Retries",
        description="The maximum retries for the DashScope API.",
        ge=0,
    )
    """The maximum retries for the DashScope API."""

    parameters: Parameters = Field(
        default_factory=Parameters,
        title="DashScope API parameters",
        description="The DashScope API parameters",
    )
    """The DashScope API parameters."""

    formatter: FormatterBase = Field(
        default_factory=DashScopeChatFormatter,
    )
    """The DashScope API formatter, responsible for converting Msg objects
    into the format required by the DashScope API."""

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
            tool_choice (`Literal["auto", "none", "required"] | str \
             |  None`,  default `None`):
                Controls which (if any) tool is called by the model.
                 Can be "auto", "none", "required", or specific tool name.
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

        # 1. Format the messages to the format required by DashScope API
        formatted_msg = await self.formatter.format(messages)

        # 2. Prepare the generation keyword arguments
        kwargs = {
            "messages": formatted_msg,
            "model": model_name,
            "stream": self.stream,
            "result_format": "message",
            # In agentscope, the `incremental_output` must be `True` when
            # `self.stream` is True
            "incremental_output": self.stream,
            **kwargs,
        }

        # tools
        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        # tool choice options
        if tool_choice:
            kwargs["tool_choice"] = self._format_tool_choice(
                tool_choice,
                tools,
            )

        # thinking related options — map to DashScope API parameter names
        kwargs["enable_thinking"] = self.parameters.thinking_enable
        if self.parameters.thinking_budget is not None:
            kwargs["thinking_budget"] = self.parameters.thinking_budget

        # 3. Call the API and parse the response
        start_datetime = datetime.now()
        # Use MultiModalConversation API if multimodality is True, or if the
        # model is multimodal based on the model name.
        api_key = self.credential.api_key.get_secret_value()
        if self.multimodality or (
            self.multimodality is None
            and (
                "qvq" in model_name
                or "-vl" in model_name
                or "-omni" in model_name
            )
        ):
            response = await dashscope.AioMultiModalConversation.call(
                api_key=api_key,
                **kwargs,
            )

        else:
            response = await dashscope.aigc.generation.AioGeneration.call(
                api_key=api_key,
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

                # Initialize accumulator for a new tool call index
                acc_tool_calls.setdefault(index, {})

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
        # final usage.  Thinking comes before text to preserve the natural
        # generation order (reasoning first, then answer).
        content = []
        if acc_thinking.thinking:
            content.append(acc_thinking)

        if acc_text.text:
            content.append(acc_text)

        if acc_tool_calls:
            content.extend(
                ToolCallBlock(
                    id=tc.get("id", uuid.uuid4().hex),
                    name=tc.get("name", ""),
                    input=tc.get("arguments", "{}"),
                )
                for tc in acc_tool_calls.values()
            )

        yield ChatResponse(
            id=response_id or uuid.uuid4().hex,
            content=content,
            is_last=True,
            usage=usage,
        )

    async def _parse_dashscope_generation_response(
        self,
        start_datetime: datetime,
        response: GenerationResponse | MultiModalConversationResponse,
    ) -> ChatResponse:
        """Given a DashScope GenerationResponse object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (
                `GenerationResponse | MultiModalConversationResponse`
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

    @staticmethod
    def _format_tools_json_schemas(
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
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> str | dict | None:
        """Format tool_choice parameter for API compatibility.

        Args:
            tool_choice (`ToolChoice | None`):
                The unified tool choice parameter which can be a mode ("auto",
                "none", "required") or a specific function name.
            tools (`list[dict] | None`):
                The list of available tools, used for validation if
                tool_choice is a specific function name.

        Returns:
            `dict | None`:
                The formatted tool choice configuration dict, or None if
                    tool_choice is None.
        """
        # Validate the tool_choice value
        self._validate_tool_choice(tool_choice, tools)

        # DashScope API specific validation and formatting
        if tool_choice in ["auto", "none"]:
            return tool_choice

        if tool_choice == "required":
            warnings.warn(
                f"'{tool_choice}' is not supported by DashScope API. "
                "It will be converted to 'auto'.",
                DeprecationWarning,
            )
            return "auto"

        return {"type": "function", "function": {"name": tool_choice}}
