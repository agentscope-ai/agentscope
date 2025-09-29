# -*- coding: utf-8 -*-
"""The formatter for OpenAI model."""
import base64
import json
import os
from typing import Union, Any
from urllib.parse import urlparse

import requests
from loguru import logger

from ._truncated_formatter_base import TruncatedFormatterBase
from ..message import Msg, URLSource, Base64Source, ToolUseBlock, TextBlock, ImageBlock, AudioBlock, ToolResultBlock
from ..token import TokenCounterBase
from ..utils.common import _to_openai_image_url


def _to_openai_audio_data(source: URLSource | Base64Source) -> dict:
    """Covert an audio source to OpenAI format."""
    if source["type"] == "url":
        extension = source["url"].split(".")[-1].lower()
        if extension not in ["wav", "mp3"]:
            raise TypeError(
                f"Unsupported audio file extension: {extension}, "
                "wav and mp3 are supported.",
            )

        parsed_url = urlparse(source["url"])

        if os.path.exists(source["url"]):
            with open(source["url"], "rb") as audio_file:
                data = base64.b64encode(audio_file.read()).decode("utf-8")

        # web url
        elif parsed_url.scheme != "":
            response = requests.get(source["url"])
            response.raise_for_status()
            data = base64.b64encode(response.content).decode("utf-8")

        else:
            raise ValueError(
                f"Unsupported audio source: {source['url']}, "
                "it should be a local file or a web URL.",
            )

        return {
            "data": data,
            "format": extension,
        }

    if source["type"] == "base64":
        data = source["data"]
        media_type = source["media_type"]

        if media_type not in ["audio/wav", "audio/mp3"]:
            raise TypeError(
                f"Unsupported audio media type: {media_type}, "
                "only audio/wav and audio/mp3 are supported.",
            )

        return {
            "data": data,
            "format": media_type.split("/")[-1],
        }

    raise TypeError(f"Unsupported audio source: {source['type']}.")


class OpenAIChatFormatter(TruncatedFormatterBase):
    # OpenAIFormatter
    """The formatter for OpenAI model, which is responsible for formatting
    messages, JSON schemas description of the tool functions."""

    supported_model_regexes: list[str] = [
        "gpt-.*",
        "o1",
        "o1-mini",
        "o3-mini",
    ]

    async def format_chat(
            self,
            *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in chat scenario, where only one user and one
        assistant are involved.

        For OpenAI model, the `name` field can be used to distinguish
        different agents (even with the same role as `"assistant"`). So we
        simply reuse the `format_multi_agent` here.
        """
        return await self._format(*msgs)

    async def _format(
            self,
            msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into OpenAI API required format.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries, where each dictionary has "name",
                "role", and "content" keys.
        """

        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for msg in msgs:
            content_blocks = []
            tool_calls = []
            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    content_blocks.append({**block})

                elif typ == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    )

                elif typ == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": self.convert_tool_result_to_string(
                                block.get("output"),  # type: ignore[arg-type]
                            ),
                            "name": block.get("name"),
                        },
                    )

                elif typ == "image":
                    source_type = block["source"]["type"]
                    if source_type == "url":
                        url = _to_openai_image_url(block["source"]["url"])

                    elif source_type == "base64":
                        data = block["source"]["data"]
                        media_type = block["source"]["media_type"]
                        url = f"data:{media_type};base64,{data}"

                    else:
                        raise ValueError(
                            f"Unsupported image source type: {source_type}",
                        )
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": url,
                            },
                        },
                    )

                elif typ == "audio":
                    input_audio = _to_openai_audio_data(block["source"])
                    content_blocks.append(
                        {
                            "type": "input_audio",
                            "input_audio": input_audio,
                        },
                    )

                else:
                    logger.warning(
                        f"Unsupported block type {typ} in the message, "
                        f"skipped.",
                    )

            msg_openai = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            if tool_calls:
                msg_openai["tool_calls"] = tool_calls

            # When both content and tool_calls are None, skipped
            if msg_openai["content"] or msg_openai.get("tool_calls"):
                messages.append(msg_openai)

        return messages

    @classmethod
    def format_tools_json_schemas(cls, schemas: dict[str, dict]) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        OpenAI API expects. This function will take the parsed JSON schema
        from `agentscope.service.ServiceToolkit` as input and return
        the formatted JSON schema.

        Note:
            An example of the input tool JSON schema

            ..code-block:: json

                {
                    "bing_search": {
                        "type": "function",
                        "function": {
                            "name": "bing_search",
                            "description": "Search the web using Bing.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query.",
                                    }
                                },
                                "required": ["query"],
                            }
                        }
                    }
                }

        Args:
            schemas (`dict[str, dict]`):
                The JSON schema of the tool functions.

        Returns:
            `list[dict]`:
                The formatted JSON schema.
        """

        # The schemas from ServiceToolkit is exactly the same with the format
        # that OpenAI API expects, so we just return the input schemas.

        assert isinstance(
            schemas,
            dict,
        ), f"Expect dict of schemas, got {type(schemas)}."

        for schema in schemas.values():
            assert isinstance(
                schema,
                dict,
            ), f"Expect dict schema, got {type(schema)}."

            assert (
                    "type" in schema and "function" in schema
            ), f"Invalid schema: {schema}, expect keys 'type' and 'function'."

            assert (
                    schema["type"] == "function"
            ), f"Invalid schema type: {schema['type']}, expect 'function'."

            assert "name" in schema["function"], (
                f"Invalid schema: {schema}, "
                f"expect key 'name' in 'function' field."
            )

        return list(schemas.values())


class OpenAIMultiAgentFormatter(TruncatedFormatterBase):
    """
    OpenAI formatter for multi-agent conversations, where more than
    a user and an agent are involved.
    .. tip:: This formatter is compatible with OpenAI API and
    OpenAI-compatible services like vLLM, Azure OpenAI, and others.
    """

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = True
    """Whether support multi-agent conversation"""

    support_vision: bool = True
    """Whether support vision models"""

    supported_blocks: list[type] = [
        TextBlock,
        ImageBlock,
        AudioBlock,
        ToolUseBlock,
        ToolResultBlock,
    ]
    """Supported message blocks for OpenAI API"""

    def __init__(
        self,
        conversation_history_prompt: str = (
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the OpenAI multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the OpenAI API."""
        return await OpenAIChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the OpenAI API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into required OpenAI format
        formatted_msgs: list[dict] = []

        conversation_blocks: list = []
        accumulated_text = []
        images = []
        audios = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] == "image":
                    source_type = block["source"]["type"]
                    if source_type == "url":
                        url = _to_openai_image_url(block["source"]["url"])

                    elif source_type == "base64":
                        data = block["source"]["data"]
                        media_type = block["source"]["media_type"]
                        url = f"data:{media_type};base64,{data}"

                    else:
                        raise ValueError(
                            f"Unsupported image source type: {source_type}",
                        )
                    images.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": url,
                            },
                        },
                    )
                elif block["type"] == "audio":
                    input_audio = _to_openai_audio_data(block["source"])
                    audios.append(
                        {
                            "type": "input_audio",
                            "input_audio": input_audio,
                        },
                    )

        if accumulated_text:
            conversation_blocks.append(
                {"text": "\n".join(accumulated_text)},
            )

        if conversation_blocks:
            if conversation_blocks[0].get("text"):
                conversation_blocks[0]["text"] = (
                    conversation_history_prompt
                    + "<history>\n"
                    + conversation_blocks[0]["text"]
                )

            else:
                conversation_blocks.insert(
                    0,
                    {
                        "text": conversation_history_prompt + "<history>\n",
                    },
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"

            else:
                conversation_blocks.append({"text": "</history>"})

        conversation_blocks_text = "\n".join(
            conversation_block.get("text", "")
            for conversation_block in conversation_blocks
        )

        content_list: list[dict[str, Any]] = []
        if conversation_blocks_text:
            content_list.append(
                {
                    "type": "text",
                    "text": conversation_blocks_text,
                },
            )
        if images:
            content_list.extend(images)
        if audios:
            content_list.extend(audios)

        user_message = {
            "role": "user",
            "content": content_list,
        }

        if content_list:
            formatted_msgs.append(user_message)

        return formatted_msgs
