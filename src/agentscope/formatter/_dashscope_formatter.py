# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The dashscope formatter module."""

import json
import os.path
from typing import Any

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from .._utils._common import _is_accessible_local_file
from ..message import (
    Msg,
    TextBlock,
    ImageBlock,
    AudioBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from ..token import TokenCounterBase


def _reformat_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reformat the content to be compatible with HuggingFaceTokenCounter.

     This function processes a list of messages and converts multi-part
     text content into single string content when all parts are plain text.
     This is necessary for compatibility with HuggingFaceTokenCounter which
     expects simple string content rather than structured content with
     multiple parts.

    Args:
        messages (list[dict[str, Any]]):
            A list of message dictionaries where each message may contain a
            "content" field. The content can be either:
            - A string (unchanged)
            - A list of content items, where each item is a dict that may
                contain "text", "type", and other fields

    Returns:
        list[dict[str, Any]]:
            A list of reformatted messages. For messages where all content
            items are plain text (have "text" field and either no "type"
            field or "type" == "text"), the content list is converted to a
            single newline-joined string. Other messages remain unchanged.

    Example:
        .. code-block:: python

            # Case 1: All text content - will be converted
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": "Hello", "type": "text"},
                        {"text": "World", "type": "text"}
                    ]
                }
            ]
            result = _reformat_messages(messages)
            print(result[0]["content"])
            # Output: "Hello\nWorld"

            # Case 2: Mixed content - will remain unchanged
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": "Hello", "type": "text"},
                        {"image_url": "...", "type": "image"}
                    ]
                }
            ]

            result = _reformat_messages(messages)  # remain unchanged
            print(type(result[0]["content"]))
            # Output: <class 'list'>

    """
    for message in messages:
        content = message.get("content", [])

        is_all_text = True
        texts = []
        for item in content:
            if not isinstance(item, dict) or "text" not in item:
                is_all_text = False
                break
            if "type" in item and item["type"] != "text":
                is_all_text = False
                break
            if item["text"]:
                texts.append(item["text"])

        if is_all_text and texts:
            message["content"] = "\n".join(texts)

    return messages


class DashScopeChatFormatter(TruncatedFormatterBase):
    """Formatter for DashScope messages."""

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = False
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        ImageBlock,
        AudioBlock,
        ToolUseBlock,
        ToolResultBlock,
    ]

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into DashScope API format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        formatted_msgs: list[dict] = []
        for msg in msgs:
            content_blocks = []
            tool_calls = []
            for block in msg.get_content_blocks():
                typ = block.get("type")

                if typ == "text":
                    content_blocks.append(
                        {
                            "text": block.get("text"),
                        },
                    )

                elif typ in ["image", "audio"]:
                    source = block["source"]
                    if source["type"] == "url":
                        url = source["url"]
                        if _is_accessible_local_file(url):
                            content_blocks.append(
                                {typ: "file://" + os.path.abspath(url)},
                            )
                        else:
                            # treat as web url
                            content_blocks.append({typ: url})

                    elif source["type"] == "base64":
                        media_type = source["media_type"]
                        base64_data = source["data"]
                        content_blocks.append(
                            {typ: f"data:{media_type};base64,{base64_data}"},
                        )

                    else:
                        raise NotImplementedError(
                            f"Unsupported source type '{source.get('type')}' "
                            f"for {typ} block.",
                        )

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
                    formatted_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": self.convert_tool_result_to_string(
                                block.get("output"),  # type: ignore[arg-type]
                            ),
                            "name": block.get("name"),
                        },
                    )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        typ,
                    )

            msg_dashscope = {
                "role": msg.role,
                "content": content_blocks or [{"text": None}],
            }

            if tool_calls:
                msg_dashscope["tool_calls"] = tool_calls

            if msg_dashscope["content"] != [
                {"text": None},
            ] or msg_dashscope.get(
                "tool_calls",
            ):
                formatted_msgs.append(msg_dashscope)

        return _reformat_messages(formatted_msgs)


class DashScopeMultiAgentFormatter(TruncatedFormatterBase):
    """DashScope formatter for multi-agent conversations, where more than
    a user and an agent are involved.

    .. note:: This formatter will combine previous messages (except tool
     calls/results) into a history section in the first system message with
     the conversation history prompt.

    .. note:: For tool calls/results, they will be presented as separate
     messages as required by the DashScope API. Therefore, the tool calls/
     results messages are expected to be placed at the end of the input
     messages.

    .. tip:: Telling the assistant's name in the system prompt is very
     important in multi-agent conversations. So that LLM can know who it
     is playing as.

    """

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = True
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        # Multimodal
        ImageBlock,
        AudioBlock,
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

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
        """Initialize the DashScope multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
            token_counter (`TokenCounterBase | None`, optional):
                The token counter used for truncation.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If `None`, no truncation will be applied.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the DashScope API.

        Args:
            msgs (`list[Msg]`):
                The list of messages containing tool calls/results to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the DashScope API.
        """
        return await DashScopeChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into a user message with conversation history tags. For the
        first agent message, it will include the conversation history prompt.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be formatted.
            is_first (`bool`, defaults to `True`):
                Whether this is the first agent message in the conversation.
                If `True`, the conversation history prompt will be included.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the DashScope API.
        """

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into required DashScope format
        formatted_msgs: list[dict] = []

        # Collect the multimodal files
        conversation_blocks = []
        accumulated_text = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] in ["image", "audio"]:
                    # Handle the accumulated text as a single block
                    if accumulated_text:
                        conversation_blocks.append(
                            {"text": "\n".join(accumulated_text)},
                        )
                        accumulated_text.clear()

                    if block["source"]["type"] == "url":
                        url = block["source"]["url"]
                        if _is_accessible_local_file(url):
                            conversation_blocks.append(
                                {
                                    block["type"]: "file://"
                                    + os.path.abspath(url),
                                },
                            )
                        else:
                            conversation_blocks.append({block["type"]: url})

                    elif block["source"]["type"] == "base64":
                        media_type = block["source"]["media_type"]
                        base64_data = block["source"]["data"]
                        conversation_blocks.append(
                            {
                                block[
                                    "type"
                                ]: f"data:{media_type};base64,{base64_data}",
                            },
                        )

                    else:
                        logger.warning(
                            "Unsupported block type %s in the message, "
                            "skipped.",
                            block["type"],
                        )

        if accumulated_text:
            conversation_blocks.append({"text": "\n".join(accumulated_text)})

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

            formatted_msgs.append(
                {
                    "role": "user",
                    "content": conversation_blocks,
                },
            )

        return _reformat_messages(formatted_msgs)

    async def _format_system_message(
        self,
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for DashScope API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
