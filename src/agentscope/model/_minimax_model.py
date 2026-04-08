# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""MiniMax Chat model class."""
import copy
import json
import re
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
from .._utils._common import _json_loads_with_repair
from ..message import (
    ToolUseBlock,
    TextBlock,
    ThinkingBlock,
)
from ..tracing import trace_llm
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = "openai.types.chat.ChatCompletion"
    AsyncStream = "openai.types.chat.AsyncStream"

_THINK_PATTERN = re.compile(
    r"<think>(.*?)</think>",
    re.DOTALL,
)

_DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"


def _parse_think_tags(content: str) -> tuple[str, str]:
    thinking = ""
    text = content
    matches = _THINK_PATTERN.findall(content)
    if matches:
        thinking = "\n".join(m.strip() for m in matches)
        text = _THINK_PATTERN.sub("", content).strip()
    return thinking, text


class MiniMaxChatModel(ChatModelBase):
    """The MiniMax chat model class, compatible with MiniMax-M2.5 series."""

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
        if kwargs:
            logger.warning(
                "Unknown keyword arguments: %s. These will be ignored.",
                list(kwargs.keys()),
            )

        super().__init__(model_name, stream)

        import openai

        client_kwargs = client_kwargs or {}
        if "base_url" not in client_kwargs:
            client_kwargs["base_url"] = _DEFAULT_MINIMAX_BASE_URL

        self.client = openai.AsyncClient(
            api_key=api_key,
            **(client_kwargs),
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
        if not isinstance(messages, list):
            raise ValueError(
                "MiniMax `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for MiniMax API.",
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

        if self.stream:
            kwargs["stream_options"] = {"include_usage": True}

        start_datetime = datetime.now()

        if structured_model:
            if tools or tool_choice:
                logger.warning(
                    "structured_model is provided. Both 'tools' and "
                    "'tool_choice' parameters will be overridden and "
                    "ignored. The model will only perform structured output "
                    "generation without calling any other tools.",
                )
            kwargs.pop("stream", None)
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
            kwargs["response_format"] = structured_model
            if not self.stream:
                response = await self.client.chat.completions.parse(**kwargs)
            else:
                response = self.client.chat.completions.stream(**kwargs)
                return self._parse_stream_response(
                    start_datetime,
                    response,
                    structured_model,
                )
        else:
            response = await self.client.chat.completions.create(**kwargs)

        if self.stream:
            return self._parse_stream_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = self._parse_completion_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    # pylint: disable=too-many-statements
    async def _parse_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncStream,
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        usage, res = None, None
        raw_text = ""
        tool_calls = OrderedDict()
        last_input_objs = {}
        metadata: dict | None = None
        contents: List[TextBlock | ToolUseBlock | ThinkingBlock] = []
        last_contents = None

        async with response as stream:
            async for item in stream:
                if structured_model:
                    if item.type != "chunk":
                        continue
                    chunk = item.chunk
                else:
                    chunk = item

                if chunk.usage:
                    usage = ChatUsage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        time=(datetime.now() - start_datetime).total_seconds(),
                        metadata=chunk.usage,
                    )

                if not chunk.choices:
                    if usage and contents:
                        res = ChatResponse(
                            content=contents,
                            usage=usage,
                            metadata=metadata,
                        )
                        yield res
                    continue

                choice = chunk.choices[0]

                raw_text += getattr(choice.delta, "content", None) or ""

                for tool_call in (
                    getattr(choice.delta, "tool_calls", None) or []
                ):
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
                thinking, text = _parse_think_tags(raw_text)

                if thinking:
                    contents.append(
                        ThinkingBlock(
                            type="thinking",
                            thinking=thinking,
                        ),
                    )

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
                        repaired_input = _json_loads_with_repair(
                            input_str or "{}",
                        )
                        last_input = last_input_objs.get(tool_id, {})
                        if len(json.dumps(last_input)) > len(
                            json.dumps(repaired_input),
                        ):
                            repaired_input = last_input
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
                    res = ChatResponse(
                        content=contents,
                        usage=usage,
                        metadata=metadata,
                    )
                    yield res
                    last_contents = copy.deepcopy(contents)

        if not self.stream_tool_parsing and tool_calls and last_contents:
            metadata = None
            for block in last_contents:
                if block.get("type") == "tool_use":
                    block["input"] = input_obj = _json_loads_with_repair(
                        str(block.get("raw_input") or "{}"),
                    )
                    if structured_model:
                        metadata = input_obj

            yield ChatResponse(
                content=last_contents,
                usage=usage,
                metadata=metadata,
            )

    def _parse_completion_response(
        self,
        start_datetime: datetime,
        response: ChatCompletion,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        content_blocks: List[TextBlock | ToolUseBlock | ThinkingBlock] = []
        metadata: dict | None = None

        if response.choices:
            choice = response.choices[0]

            raw_content = choice.message.content or ""
            thinking, text = _parse_think_tags(raw_content)

            if thinking:
                content_blocks.append(
                    ThinkingBlock(
                        type="thinking",
                        thinking=thinking,
                    ),
                )

            if text:
                content_blocks.append(
                    TextBlock(
                        type="text",
                        text=text,
                    ),
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

            if structured_model:
                metadata = choice.message.parsed.model_dump()

        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=response.usage,
            )

        return ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=metadata,
        )

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return schemas

    def _format_tool_choice(
        self,
        tool_choice: Literal["auto", "none", "required"] | str | None,
    ) -> str | dict | None:
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
