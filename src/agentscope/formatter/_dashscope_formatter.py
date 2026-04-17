# -*- coding: utf-8 -*-
"""The dashscope formatter module."""

from typing import Any, Literal
from fnmatch import fnmatch
from abc import ABC

from . import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
    DataBlock,
    ToolCallBlock,
    Base64Source,
    UserMsg,
    HintBlock,
)


class _DashScopeFormatterBase(FormatterBase, ABC):
    """Base class for DashScope formatters, providing shared data block
    formatting logic."""

    def _format_dashscope_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a DataBlock into the required format for DashScope API.

        Args:
            block (`DataBlock`):
                The DataBlock to format.

        Returns:
            `dict[str, Any] | None`:
                A dictionary representing the formatted DataBlock for
                DashScope API.
        """
        if not any(
            fnmatch(block.source.media_type, pattern)
            for pattern in self.supported_input_media_types
        ):
            logger.warning(
                "Unsupported media type %s for DashScope API. Supported "
                "types: %s. This block will be skipped.",
                block.source.media_type,
                ", ".join(self.supported_input_media_types),
            )
            return None

        main_type = block.source.media_type.split("/")[0]

        if isinstance(block.source, URLSource):
            return {main_type: block.source.url}

        if isinstance(block.source, Base64Source):
            return {
                main_type: f"data:{block.source.media_type};"
                f"base64,{block.source.data}",
            }

        return None


class DashScopeChatFormatter(_DashScopeFormatterBase):
    """The DashScope formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.

    .. warning::
        Known Issues with DashScope API:

        1. **Missing content field**: When messages lack the 'content' field,
           qwen-vl-max models will raise ``KeyError: 'content'``.

        2. **None content value**: When content is ``None``, qwen-vl-max models
           will raise ``TypeError: 'NoneType' object is not iterable``.

        3. **Empty text in content**: When content contains
           ``[{"text": None}]``, qwen3-max may repeatedly invoke tools
           multiple times. Note that when qwen3-max initiates tool calls,
           the returned message contains ``"content": ""``.

        To avoid these issues, this formatter assigns content as an empty
        list ``[]`` for messages without valid content blocks.
    """

    type: Literal["dashscope_chat_formatter"] = "dashscope_chat_formatter"
    supported_input_media_types: list[str] = ["image/*", "audio/*", "video/*"]

    async def format(
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
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            content_blocks: list[dict] = []
            tool_calls = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append({"text": block.text})

                elif isinstance(block, DataBlock):
                    formatted_block = self._format_dashscope_data_block(block)
                    if formatted_block:
                        content_blocks.append(formatted_block)

                elif isinstance(block, HintBlock):
                    # Insert a new user message with the hint content right
                    # after the current message, and go on processing the
                    # rest of the blocks in the current message
                    if content_blocks or tool_calls:
                        formatted_msgs.append(
                            {
                                "role": "user",
                                "content": content_blocks,
                                "tool_calls": tool_calls
                                if tool_calls
                                else None,
                            },
                        )
                        # Refresh content_blocks and tool_calls for the last
                        # blocks
                        content_blocks = []
                        tool_calls = []

                elif isinstance(block, ToolCallBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": block.input,
                            },
                        },
                    )

                elif isinstance(block, ToolResultBlock):
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    # First add the tool result message in DashScope API format
                    formatted_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": textual_output,
                            "name": block.name,
                        },
                    )

                    # If we have multimodal data that needs to be prompted to
                    # the out-tool-result message
                    if multimodal_data:
                        msgs.insert(
                            i + 1,
                            UserMsg(
                                name="system-reminder",
                                content=multimodal_data,
                            ),
                        )

            msg_dashscope = {
                "role": msg.role,
                "content": content_blocks,
            }

            if tool_calls:
                msg_dashscope["tool_calls"] = tool_calls

            if msg_dashscope["content"] or msg_dashscope.get("tool_calls"):
                formatted_msgs.append(msg_dashscope)

            # Move to next message
            i += 1

        # Merge adjacent text block into one block to avoid API issues
        cleaned_msgs: list = []
        for msg in formatted_msgs:
            new_content = []
            for block in msg["content"]:
                if (
                    block.get("text")
                    and new_content
                    and new_content[-1].get("text")
                ):
                    new_content[-1]["text"] += "\n" + block["text"]
                else:
                    new_content.append(block)
            msg["content"] = new_content
            cleaned_msgs.append(msg)

        return cleaned_msgs


class DashScopeMultiAgentFormatter(_DashScopeFormatterBase):
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

    type: Literal[
        "dashscope_multiagent_formatter"
    ] = "dashscope_multiagent_formatter"

    def __init__(
        self,
        conversation_history_prompt: str = (
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        supported_input_media_types: list[str] | None = None,
    ) -> None:
        """Initialize the DashScope multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
            supported_input_media_types (`list[str] | None`, optional):
                The list of supported input media types. Defaults to
                ["image/*", "audio/*", "video/*"].
        """
        super().__init__(
            supported_input_media_types=supported_input_media_types
            or ["image/*", "audio/*", "video/*"],
        )
        self.conversation_history_prompt = conversation_history_prompt

    async def format(self, msgs: list[Msg]) -> list[dict]:
        """Format input messages into the structure required by the DashScope
        API.

        To support multi-agent conversations, this formatter processes messages
        as follows:

        - Prepends an instruction before the first conversation history
         section.
        - Combines conversation turns into a history section, where each entry
         is formatted as `{name}: {content}`.
        - Wraps the conversation history with `<history>` and `</history>`
         tags.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the DashScope API.
        """

        formatted_msgs = []
        start_index = 0
        if len(msgs) > 0 and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            match typ:
                case "tool_sequence":
                    formatted_msgs.extend(
                        await self._format_tool_sequence(group),
                    )
                case "agent_message":
                    formatted_msgs.extend(
                        await self._format_agent_message(
                            group,
                            is_first_agent_message,
                        ),
                    )
                    is_first_agent_message = False

        return formatted_msgs

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
        return await DashScopeChatFormatter(
            supported_input_media_type=self.supported_input_media_types,
        ).format(msgs)

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
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif isinstance(block, DataBlock):
                    # Handle the accumulated text as a single block
                    if accumulated_text:
                        conversation_blocks.append(
                            {"text": "\n".join(accumulated_text)},
                        )
                        accumulated_text.clear()

                    formatted_block = self._format_dashscope_data_block(block)
                    if formatted_block is not None:
                        conversation_blocks.append(formatted_block)

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

            conversation_blocks.append({"text": "</history>"})

            # Merge the adjacent text blocks into one text blocks to avoid API
            # issues
            new_content = []
            for block in conversation_blocks:
                if (
                    block.get("text")
                    and new_content
                    and new_content[-1].get("text")
                ):
                    new_content[-1]["text"] += "\n" + block["text"]
                else:
                    new_content.append(block)

            formatted_msgs.append(
                {
                    "role": "user",
                    "content": new_content,
                },
            )

        return formatted_msgs

    @staticmethod
    async def _format_system_message(
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for DashScope API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
