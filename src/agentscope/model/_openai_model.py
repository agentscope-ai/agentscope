# -*- coding: utf-8 -*-
"""OpenAI Chat model class."""
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    Literal,
)
from collections import OrderedDict

from openai.types import ReasoningEffort
from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase, _TOOL_CHOICE_LITERAL_MODES
from ._model_usage import ChatUsage
from ..formatter import FormatterBase, OpenAIChatFormatter
from ..message import (
    TextBlock,
    ThinkingBlock,
    Base64Source,
    DataBlock,
    ToolCallBlock,
)
from ..tool import ToolChoice
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = "openai.types.chat.ChatCompletion"
    AsyncStream = "openai.types.chat.AsyncStream"


def _format_audio_data_for_qwen_omni(messages: list[dict]) -> None:
    """Qwen-omni uses OpenAI-compatible API but requires different audio
    data format than OpenAI with "data:;base64," prefix.
    Refer to `Qwen-omni documentation
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2867839>`_
    for more details.

    Args:
        messages (`list[dict]`):
            The list of message dictionaries from OpenAI formatter.
    """
    for msg in messages:
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if (
                    isinstance(block, dict)
                    and "input_audio" in block
                    and isinstance(block["input_audio"].get("data"), str)
                ):
                    if not block["input_audio"]["data"].startswith("http"):
                        block["input_audio"]["data"] = (
                            "data:;base64," + block["input_audio"]["data"]
                        )


class OpenAIChatModel(ChatModelBase):
    """The OpenAI chat model class."""

    class ThinkingConfig(BaseModel):
        """Configuration for reasoning effort levels."""

        enable_thinking: bool
        reasoning_effect: ReasoningEffort | None = None

    def __init__(
        self,
        model_name: str,
        context_length: int,
        api_key: str | None = None,
        stream: bool = True,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
        thinking_config: ThinkingConfig | None = None,
        organization: str = None,
        client_type: Literal["openai", "azure"] = "openai",
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the openai client.

        Args:
            model_name (`str`, default `None`):
                The name of the model to use in OpenAI API.
            context_length (`int`):
                The context length of the model, which will be used in
                context compression.
            api_key (`str`, default `None`):
                The API key for OpenAI API. If not specified, it will
                be read from the environment variable `OPENAI_API_KEY`.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
            thinking_config (`ThinkingConfig | None`, optional):
                Configuration for reasoning effort levels.
            organization (`str`, default `None`):
                The organization ID for OpenAI API.
            client_type (`Literal["openai", "azure"]`, default `openai`):
                Selects which OpenAI-compatible client to initialize.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the OpenAI client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in OpenAI API generation,
                e.g. `temperature`, `seed`.
        """
        self.thinking_config = (
            thinking_config
            or OpenAIChatModel.ThinkingConfig(enable_thinking=False)
        )

        super().__init__(
            model_name=model_name,
            stream=stream,
            max_retries=max_retries,
            context_length=context_length,
            fallback_model_name=fallback_model_name,
            formatter=formatter or OpenAIChatFormatter(),
        )

        import openai

        if client_type not in ("openai", "azure"):
            raise ValueError(
                "Invalid client_type. Supported values: 'openai', 'azure'.",
            )

        if client_type == "azure":
            self.client = openai.AsyncAzureOpenAI(
                api_key=api_key,
                organization=organization,
                **(client_kwargs or {}),
            )
        else:
            self.client = openai.AsyncClient(
                api_key=api_key,
                organization=organization,
                **(client_kwargs or {}),
            )

        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def _call_api(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from OpenAI chat completions API by the given
        arguments.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required, and `name` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`ToolChoice | None`, default `None`):
                Controls which (if any) tool is called by the model.
            **kwargs (`Any`):
                The keyword arguments for OpenAI chat completions API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the OpenAI chat completions API.
        """

        # checking messages
        if not isinstance(messages, list):
            raise ValueError(
                "OpenAI `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for OpenAI API.",
            )

        # Qwen-omni requires different base64 audio format from openai
        if "omni" in model_name.lower():
            _format_audio_data_for_qwen_omni(messages)

        kwargs = {
            "model": model_name,
            "messages": messages,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }
        if (
            self.thinking_config.enable_thinking
            and self.thinking_config.reasoning_effect
        ):
            kwargs["reasoning_effort"] = self.thinking_config.reasoning_effect

        fmt_tools, fmt_tool_choice = self._format_tools(tools, tool_choice)
        if fmt_tools is not None:
            kwargs["tools"] = fmt_tools
        if fmt_tool_choice is not None:
            kwargs["tool_choice"] = fmt_tool_choice

        if self.stream:
            kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()

        response = await self.client.chat.completions.create(**kwargs)

        if self.stream:
            return self._parse_openai_stream_response(
                start_datetime,
                response,
            )

        # Non-streaming response
        parsed_response = self._parse_openai_completion_response(
            start_datetime,
            response,
        )

        return parsed_response

    async def _parse_openai_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given an OpenAI streaming completion response, extract the content
         blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncStream`):
                OpenAI AsyncStream object to parse.

        Returns:
            `AsyncGenerator[ChatResponse, None]`:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in
                the streaming response.
        """
        usage = None

        # Accumulators
        acc_text = ""
        acc_thinking = ""
        acc_audio = ""
        acc_tool_calls = OrderedDict()

        async with response as stream:
            async for chunk in stream:
                if chunk.usage:
                    usage = ChatUsage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        metadata=chunk.usage,
                    )

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]

                # Extract delta content
                delta_thinking = getattr(
                    choice.delta,
                    "reasoning_content",
                    None,
                )
                if not isinstance(delta_thinking, str):
                    delta_thinking = getattr(choice.delta, "reasoning", None)
                if not isinstance(delta_thinking, str):
                    delta_thinking = ""

                delta_text = getattr(choice.delta, "content", None) or ""
                delta_audio = ""

                if isinstance(getattr(choice.delta, "audio", None), dict):
                    if "data" in choice.delta.audio:
                        delta_audio = choice.delta.audio["data"]
                    if "transcript" in choice.delta.audio:
                        delta_text += choice.delta.audio["transcript"]

                # Accumulate
                acc_thinking += delta_thinking
                acc_text += delta_text
                acc_audio += delta_audio

                # Handle tool calls
                for tool_call in (
                    getattr(choice.delta, "tool_calls", None) or []
                ):
                    if tool_call.index in acc_tool_calls:
                        if tool_call.function.arguments is not None:
                            acc_tool_calls[tool_call.index][
                                "input"
                            ] += tool_call.function.arguments
                    else:
                        acc_tool_calls[tool_call.index] = {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": tool_call.function.arguments or "",
                        }

                # Build delta content blocks
                delta_contents: List[
                    TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
                ] = []

                if delta_thinking:
                    delta_contents.append(
                        ThinkingBlock(thinking=delta_thinking),
                    )

                if delta_audio:
                    media_type = self.generate_kwargs.get("audio", {}).get(
                        "format",
                        "wav",
                    )
                    delta_contents.append(
                        DataBlock(
                            source=Base64Source(
                                data=delta_audio,
                                media_type=f"audio/{media_type}",
                            ),
                        ),
                    )

                if delta_text:
                    delta_contents.append(
                        TextBlock(text=delta_text),
                    )

                # Yield delta response
                if delta_contents:
                    yield ChatResponse(
                        content=delta_contents,
                        usage=usage,
                        is_last=False,
                    )

        # Build final accumulated content blocks
        final_contents: List[
            TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
        ] = []

        if acc_thinking:
            final_contents.append(ThinkingBlock(thinking=acc_thinking))

        if acc_audio:
            media_type = self.generate_kwargs.get("audio", {}).get(
                "format",
                "wav",
            )
            final_contents.append(
                DataBlock(
                    source=Base64Source(
                        data=acc_audio,
                        media_type=f"audio/{media_type}",
                    ),
                ),
            )

        if acc_text:
            final_contents.append(TextBlock(text=acc_text))

        for tool_call in acc_tool_calls.values():
            final_contents.append(
                ToolCallBlock(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    input=tool_call["input"],
                ),
            )

        # Yield final accumulated response
        yield ChatResponse(
            content=final_contents,
            usage=usage,
            is_last=True,
        )

    def _parse_openai_completion_response(
        self,
        start_datetime: datetime,
        response: ChatCompletion,
    ) -> ChatResponse:
        """Given an OpenAI chat completion response object, extract the content
            blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`ChatCompletion`):
                OpenAI ChatCompletion object to parse.

        Returns:
            ChatResponse (`ChatResponse`):
                A ChatResponse object containing the content blocks and usage.
        """
        content_blocks: List[
            TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
        ] = []

        if response.choices:
            choice = response.choices[0]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if not isinstance(reasoning, str):
                reasoning = getattr(choice.message, "reasoning", None)
            if not isinstance(reasoning, str):
                reasoning = None

            if reasoning is not None:
                content_blocks.append(
                    ThinkingBlock(thinking=reasoning),
                )

            if choice.message.content:
                content_blocks.append(
                    TextBlock(text=response.choices[0].message.content),
                )
            if choice.message.audio:
                media_type = self.generate_kwargs.get("audio", {}).get(
                    "format",
                    "mp3",
                )
                content_blocks.append(
                    DataBlock(
                        source=Base64Source(
                            data=choice.message.audio.data,
                            media_type=f"audio/{media_type}",
                        ),
                    ),
                )

                if choice.message.audio.transcript:
                    content_blocks.append(
                        TextBlock(text=choice.message.audio.transcript),
                    )

            for tool_call in choice.message.tool_calls or []:
                content_blocks.append(
                    ToolCallBlock(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        input=tool_call.function.arguments,
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
        """Validate and format tools and tool_choice for OpenAI API.

        When ``tool_choice.tools`` is specified the schemas list is
        filtered to only those tools (the caller deliberately limits the
        available tool set). When ``tool_choice.mode`` is a specific tool
        name (str) the model is forced to call exactly that tool; this
        avoids the need to filter down to a single tool just to trigger a
        forced call, preserving prompt-cache efficiency.

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

        fmt_tools = tools if tools else None

        if not tool_choice:
            return fmt_tools, None

        mode = tool_choice["mode"]

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            # mode is a specific tool name — force call it
            fmt_choice: str | dict = {
                "type": "function",
                "function": {"name": mode},
            }
        else:
            fmt_choice = mode

        return fmt_tools, fmt_choice
