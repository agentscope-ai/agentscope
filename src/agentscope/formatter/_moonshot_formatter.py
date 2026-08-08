# -*- coding: utf-8 -*-
"""The Moonshot AI formatter for agentscope."""
import base64
from typing import Any

import requests
from pydantic import Field

from ._openai_formatter import _OpenAIFormatterBase
from .._logging import logger
from ..message import (
    Msg,
    URLSource,
    Base64Source,
    TextBlock,
    DataBlock,
    ThinkingBlock,
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _moonshot_format_image_source(
    source: URLSource | Base64Source,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert an image source to Moonshot ``image_url`` format.

    Moonshot's vision API only accepts base64 data URIs or file IDs — raw
    remote URLs are rejected. This helper downloads remote ``http(s)://``
    URLs and converts them to base64 data URIs, while ``file://`` URLs and
    ``Base64Source`` go through the same conversion as the OpenAI base. Extra
    fields are merged into the nested ``image_url`` object, matching OpenAI
    Chat Completions.

    Args:
        source (`URLSource | Base64Source`):
            The image source to convert.
        extra (`dict[str, Any] | None`, optional):
            Provider-specific image parameters to merge into the nested
            ``image_url`` object.

    Returns:
        `dict[str, Any]`:
            A Moonshot image content item.
    """
    if isinstance(source, Base64Source):
        url = f"data:{source.media_type};base64,{source.data}"

    elif isinstance(source, URLSource):
        url_str = str(source.url)
        if url_str.startswith("file://"):
            local_path = url_str.removeprefix("file://")
            with open(local_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            url = f"data:{source.media_type};base64,{encoded}"
        else:
            response = requests.get(url_str, timeout=30)
            response.raise_for_status()
            encoded = base64.b64encode(response.content).decode("utf-8")
            url = f"data:{source.media_type};base64,{encoded}"

    else:
        raise ValueError(f"Unsupported image source type: {type(source)}")

    image_url_payload: dict[str, Any] = {"url": url}
    if extra:
        image_url_payload.update(extra)
    return {
        "type": "image_url",
        "image_url": image_url_payload,
    }


class MoonshotChatFormatter(_OpenAIFormatterBase):
    """The Moonshot AI formatter for chatbot scenario.

    Moonshot's API is OpenAI-compatible, but thinking models (``kimi-k2.6``,
    ``kimi-k2-thinking``) return a ``reasoning_content`` field alongside
    ``content`` in assistant messages.  This formatter preserves that field
    when re-sending assistant messages back to the API so that the
    *Preserved Thinking* feature works correctly in multi-turn conversations.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "audio/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "audio/*"]``.'
        ),
    )

    def _format_image_source(
        self,
        source: URLSource | Base64Source,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert an image source to Moonshot ``image_url`` format.

        Args:
            source (`URLSource | Base64Source`):
                The image source to convert.
            extra (`dict[str, Any] | None`, optional):
                Provider-specific image parameters to merge into the nested
                ``image_url`` object.

        Returns:
            `dict[str, Any]`:
                A Moonshot image content item.
        """
        return _moonshot_format_image_source(source, extra)

    # pylint: disable=too-many-branches
    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format messages into the Moonshot / OpenAI-compatible API format.

        Behaves identically to :class:`OpenAIChatFormatter` except that
        :class:`ThinkingBlock` content is placed into the ``reasoning_content``
        field of the assistant message dict (required for Preserved Thinking).

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            content_blocks: list[dict] = []
            reasoning_parts: list[str] = []
            tool_calls: list[dict] = []

            for block in msg.get_content_blocks():
                if isinstance(block, ThinkingBlock):
                    # Preserve reasoning_content for multi-turn
                    # Preserved Thinking (kimi-k2.6 / kimi-k2-thinking)
                    reasoning_parts.append(block.thinking)

                elif isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock):
                    formatted = self._format_openai_data_block(block)
                    if formatted is not None:
                        content_blocks.append(formatted)

                elif isinstance(block, HintBlock):
                    if content_blocks or tool_calls or reasoning_parts:
                        msg_moonshot = {
                            "role": msg.role,
                            "name": msg.name,
                            "content": content_blocks or None,
                        }
                        if msg.role == "assistant":
                            msg_moonshot["reasoning_content"] = (
                                "\n".join(reasoning_parts)
                                if reasoning_parts
                                else ""
                            )
                        if tool_calls:
                            msg_moonshot["tool_calls"] = tool_calls
                        messages.append(msg_moonshot)
                        content_blocks = []
                        reasoning_parts = []
                        tool_calls = []

                    if isinstance(block.hint, str):
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": block.hint},
                                ],
                            },
                        )
                    else:
                        hint_parts: list[dict] = []
                        for sub in block.hint:
                            if isinstance(sub, TextBlock):
                                hint_parts.append(
                                    {"type": "text", "text": sub.text},
                                )
                            elif isinstance(sub, DataBlock):
                                formatted_sub = self._format_openai_data_block(
                                    sub,
                                )
                                if formatted_sub is not None:
                                    hint_parts.append(formatted_sub)
                        if hint_parts:
                            messages.append(
                                {"role": "user", "content": hint_parts},
                            )

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
                    if content_blocks or tool_calls or reasoning_parts:
                        msg_flush = {
                            "role": msg.role,
                            "name": msg.name,
                            "content": content_blocks or None,
                        }
                        if msg.role == "assistant":
                            msg_flush["reasoning_content"] = (
                                "\n".join(reasoning_parts)
                                if reasoning_parts
                                else ""
                            )
                        if tool_calls:
                            msg_flush["tool_calls"] = tool_calls
                        messages.append(msg_flush)
                        content_blocks = []
                        reasoning_parts = []
                        tool_calls = []

                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": textual_output,
                            "name": block.name,
                        },
                    )

                    if multimodal_data:
                        promo_content = []
                        for item in multimodal_data:
                            if isinstance(item, TextBlock):
                                promo_content.append(
                                    {"type": "text", "text": item.text},
                                )
                            elif isinstance(item, DataBlock):
                                fmt_item = self._format_openai_data_block(
                                    item,
                                )
                                if fmt_item is not None:
                                    promo_content.append(fmt_item)
                        if promo_content:
                            messages.append(
                                {
                                    "role": "user",
                                    "name": "system-reminder",
                                    "content": promo_content,
                                },
                            )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            msg_moonshot = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            # Moonshot's Preserved Thinking requires `reasoning_content` on ALL
            # assistant messages in multi-turn conversations (None when no
            # thinking took place), so that the model can continue its chain
            # of thought correctly.
            if msg.role == "assistant":
                msg_moonshot["reasoning_content"] = (
                    "\n".join(reasoning_parts) if reasoning_parts else ""
                )

            if tool_calls:
                msg_moonshot["tool_calls"] = tool_calls

            if (
                msg_moonshot["content"]
                or msg_moonshot.get("tool_calls")
                or reasoning_parts
            ):
                messages.append(msg_moonshot)

            i += 1

        return messages


class MoonshotMultiAgentFormatter(_OpenAIFormatterBase):
    """Formatter for the Moonshot AI API in multi-agent conversations.

    Moonshot's API is OpenAI-compatible, so the multi-agent history collapsing
    strategy is the same as :class:`OpenAIMultiAgentFormatter`.  Tool
    sequences are delegated to :class:`MoonshotChatFormatter` so that
    ``reasoning_content`` is preserved correctly for multi-turn
    *Preserved Thinking* conversations.

    .. note:: Telling the assistant's name in the system prompt is important
        in multi-agent conversations so that the model knows which role it
        is playing.
    """

    conversation_history_prompt: str = Field(
        default=(
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        description="The prompt to use for the conversation history section.",
    )

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "audio/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "audio/*"]``.'
        ),
    )

    def _format_image_source(
        self,
        source: URLSource | Base64Source,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert an image source to Moonshot ``image_url`` format.

        Args:
            source (`URLSource | Base64Source`):
                The image source to convert.
            extra (`dict[str, Any] | None`, optional):
                Provider-specific image parameters to merge into the nested
                ``image_url`` object.

        Returns:
            `dict[str, Any]`:
                A Moonshot image content item.
        """
        return _moonshot_format_image_source(source, extra)

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the Moonshot AI API format for
        multi-agent conversations.

        Non-tool messages from all agents are collapsed into a single user
        message with ``<history></history>`` tags.  Tool call / result
        sequences are delegated to :class:`MoonshotChatFormatter`.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        formatted_msgs: list[dict] = []
        start_index = 0
        if msgs and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            if typ == "tool_sequence":
                formatted_msgs.extend(
                    await MoonshotChatFormatter(
                        input_types=self.input_types,
                    ).format(group),
                )
            elif typ == "agent_message":
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
        """Format a sequence of tool-related messages using
        MoonshotChatFormatter."""
        return await MoonshotChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Collapse agent messages into a ``<history>`` user message."""
        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        accumulated_text: list[str] = []
        media_blocks: list[dict] = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")
                elif isinstance(block, DataBlock):
                    formatted = self._format_openai_data_block(block)
                    if formatted is not None:
                        media_blocks.append(formatted)

        if not accumulated_text and not media_blocks:
            return []

        history_text = "\n".join(accumulated_text)
        if history_text:
            history_text = (
                conversation_history_prompt
                + "<history>\n"
                + history_text
                + "\n</history>"
            )

        content_list: list[dict[str, Any]] = []
        if history_text:
            content_list.append({"type": "text", "text": history_text})
        content_list.extend(media_blocks)

        return [{"role": "user", "content": content_list}]

    @staticmethod
    async def _format_system_message(msg: Msg) -> dict[str, Any]:
        """Format a system message for the Moonshot AI API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
