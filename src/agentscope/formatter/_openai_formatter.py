# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The OpenAI formatter for agentscope."""
import base64
import json
import os
from typing import Any
from urllib.parse import urlparse

import requests

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from ..message import (
    Msg,
    URLSource,
    TextBlock,
    ImageBlock,
    AudioBlock,
    Base64Source,
    ToolUseBlock,
    ToolResultBlock,
)
from ..token import TokenCounterBase


def _to_openai_image_url(url: str) -> str:
    """Convert an image url to openai format. If the given url is a local
    file, it will be converted to base64 format. Otherwise, it will be
    returned directly.

    Args:
        url (`str`):
            The local or public url of the image.
    """
    # See https://platform.openai.com/docs/guides/vision for details of
    # support image extensions.
    support_image_extensions = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    )

    parsed_url = urlparse(url)

    lower_url = url.lower()

    # Web url
    if not os.path.exists(url) and parsed_url.scheme != "":
        if any(lower_url.endswith(_) for _ in support_image_extensions):
            return url

    # Check if it is a local file
    elif os.path.exists(url) and os.path.isfile(url):
        if any(lower_url.endswith(_) for _ in support_image_extensions):
            with open(url, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode(
                    "utf-8",
                )
            extension = parsed_url.path.lower().split(".")[-1]
            mime_type = f"image/{extension}"
            return f"data:{mime_type};base64,{base64_image}"

    raise TypeError(f'"{url}" should end with {support_image_extensions}.')


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
    """The class used to format message objects into the OpenAI API required
    format."""

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
                        "Unsupported block type %s in the message, skipped.",
                        typ,
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
