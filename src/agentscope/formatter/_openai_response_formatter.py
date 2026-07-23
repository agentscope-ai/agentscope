# -*- coding: utf-8 -*-
"""Formatters for the OpenAI Responses API."""
from abc import ABC
from typing import Any

from pydantic import Field

from ._openai_formatter import _OpenAIFormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    HintBlock,
    ThinkingBlock,
)


class _OpenAIResponseFormatterBase(_OpenAIFormatterBase, ABC):
    """Base class for OpenAI Responses API formatters.

    Provides the shared ``_format_response_data_block`` helper used by both
    :class:`OpenAIResponseFormatter` (chat) and
    :class:`OpenAIResponseMultiAgentFormatter` (multi-agent).
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*"]``. '
            "Audio is not supported by the Responses API."
        ),
    )

    def _format_response_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a DataBlock into the Response API format.

        The Responses API uses different content types from the Chat
        Completions API:

        * ``image_url`` → ``input_image``
        * ``input_audio`` → skipped (the Responses API does not support
          audio input yet; use Chat Completions API instead). See
          https://developers.openai.com/api/docs/guides/audio

        For image blocks, ``DataBlock`` extra fields are merged into the
        ``input_image`` item itself (for example ``detail``), because the
        Responses API uses a flat ``image_url`` string instead of the nested
        Chat Completions ``image_url`` object.

        Args:
            block (`DataBlock`):
                The DataBlock to format.

        Returns:
            `dict[str, Any] | None`:
                A dictionary in the Responses API format, or ``None`` when the
                block type is unsupported.
        """
        # Intercept audio blocks before the generic formatter rejects them
        # with a less helpful "Unsupported media type" warning. The Responses
        # API does not support audio input yet; use Chat Completions API with
        # an audio-capable model instead.
        # https://developers.openai.com/api/docs/guides/audio
        media_type = getattr(block.source, "media_type", "") or ""
        if media_type.split("/", 1)[0] == "audio":
            logger.warning(
                "Audio input is not supported by the OpenAI Responses API. "
                "Use OpenAIChatModel with an audio-capable model instead. "
                "This audio block will be skipped.",
            )
            return None

        base_result = self._format_openai_data_block(block)
        if base_result is None:
            return None

        if base_result.get("type") == "image_url":
            result = {
                "type": "input_image",
                "image_url": base_result["image_url"]["url"],
            }
            extra = block.model_extra or {}
            if extra:
                result.update(extra)
            return result

        return base_result


class OpenAIResponseFormatter(_OpenAIResponseFormatterBase):
    """Formatter for the OpenAI Responses API in chat (single-agent) mode.

    Produces input items compatible with ``client.responses.create(
    input=...)``.
    Compared with the Chat Completions format, the key differences are:

    * Text content blocks use ``input_text`` instead of ``text``.
    * Image content blocks use ``input_image`` instead of ``image_url``.
    * Assistant tool-call messages become top-level ``function_call`` items.
    * Tool result messages become ``function_call_output`` items.
    * Reasoning items (``ThinkingBlock`` with ``reasoning_item_id``) are
      echoed back verbatim as required by reasoning models (e.g. ``o1``).
    """

    # pylint: disable=too-many-branches
    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into OpenAI Response API input items.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of input items for ``client.responses.create``.
        """
        self.assert_list_of_msgs(msgs)

        items: list[dict] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            content_parts: list[dict] = []
            function_calls: list[dict] = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    text_type = (
                        "output_text"
                        if msg.role == "assistant"
                        else "input_text"
                    )
                    content_parts.append(
                        {"type": text_type, "text": block.text},
                    )

                elif isinstance(block, DataBlock):
                    formatted = self._format_response_data_block(block)
                    if formatted is not None:
                        content_parts.append(formatted)

                elif isinstance(block, HintBlock):
                    if function_calls:
                        if content_parts:
                            items.append(
                                {
                                    "role": msg.role,
                                    "content": content_parts,
                                },
                            )
                            content_parts = []
                        items.extend(function_calls)
                        function_calls = []
                    elif content_parts:
                        items.append(
                            {
                                "role": msg.role,
                                "content": content_parts,
                            },
                        )
                        content_parts = []

                    if isinstance(block.hint, str):
                        items.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": block.hint,
                                    },
                                ],
                            },
                        )
                    else:
                        hint_parts: list[dict] = []
                        for sub in block.hint:
                            if isinstance(sub, TextBlock):
                                hint_parts.append(
                                    {
                                        "type": "input_text",
                                        "text": sub.text,
                                    },
                                )
                            elif isinstance(sub, DataBlock):
                                formatted_sub = (
                                    self._format_response_data_block(
                                        sub,
                                    )
                                )
                                if formatted_sub is not None:
                                    hint_parts.append(formatted_sub)
                        if hint_parts:
                            items.append(
                                {"role": "user", "content": hint_parts},
                            )

                elif isinstance(block, ThinkingBlock):
                    # When reasoning_item_id is present the block originated
                    # from a Responses API "reasoning" output item.  The API
                    # requires that such items are echoed back verbatim in
                    # multi-turn history (especially when they precede a
                    # function_call).  Without the ID we skip silently.
                    reasoning_item_id = getattr(
                        block,
                        "reasoning_item_id",
                        None,
                    )
                    if reasoning_item_id:
                        if content_parts and block.thinking:
                            items.append(
                                {
                                    "role": msg.role,
                                    "content": content_parts,
                                },
                            )
                            content_parts = []
                        # Empty reasoning blocks can arrive after text deltas
                        # only to carry reasoning_item_id; emit them before
                        # pending assistant text for replay. Non-empty
                        # reasoning starts a new output segment, so flush text
                        # first.
                        summary = (
                            [{"type": "summary_text", "text": block.thinking}]
                            if block.thinking
                            else []
                        )
                        items.append(
                            {
                                "type": "reasoning",
                                "id": reasoning_item_id,
                                "summary": summary,
                                "content": [],
                            },
                        )

                elif isinstance(block, ToolCallBlock):
                    function_calls.append(
                        {
                            "type": "function_call",
                            "call_id": block.id,
                            "name": block.name,
                            "arguments": block.input,
                        },
                    )

                elif isinstance(block, ToolResultBlock):
                    if function_calls:
                        if content_parts:
                            items.append(
                                {
                                    "role": msg.role,
                                    "content": content_parts,
                                },
                            )
                            content_parts = []
                        items.extend(function_calls)
                        function_calls = []
                    elif content_parts:
                        items.append(
                            {
                                "role": msg.role,
                                "content": content_parts,
                            },
                        )
                        content_parts = []

                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": block.id,
                            "output": textual_output,
                        },
                    )

                    if multimodal_data:
                        promo_content = []
                        for item in multimodal_data:
                            if isinstance(item, TextBlock):
                                promo_content.append(
                                    {
                                        "type": "input_text",
                                        "text": item.text,
                                    },
                                )
                            elif isinstance(item, DataBlock):
                                fmt_item = self._format_response_data_block(
                                    item,
                                )
                                if fmt_item is not None:
                                    promo_content.append(fmt_item)
                        if promo_content:
                            items.append(
                                {
                                    "role": "user",
                                    "content": promo_content,
                                },
                            )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            if function_calls:
                if content_parts:
                    items.append(
                        {
                            "role": msg.role,
                            "content": content_parts,
                        },
                    )
                items.extend(function_calls)
            elif content_parts:
                items.append(
                    {
                        "role": msg.role,
                        "content": content_parts,
                    },
                )

            i += 1

        return items


class OpenAIResponseMultiAgentFormatter(_OpenAIResponseFormatterBase):
    """Formatter for the OpenAI Responses API in multi-agent mode.

    Handles conversations where more than a user and a single agent are
    involved.  Tool call/result sequences are formatted with the Responses API
    ``function_call`` / ``function_call_output`` items (delegated to
    :class:`OpenAIResponseFormatter`).  Agent conversation history messages
    are wrapped inside ``<history></history>`` tags and presented as a single
    ``user`` input item.
    """

    conversation_history_prompt: str = Field(
        default=(
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        description="The prompt to use for the conversation history section.",
    )

    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format messages for multi-agent Responses API conversations.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of input items for ``client.responses.create``.
        """
        self.assert_list_of_msgs(msgs)

        formatted_msgs: list[dict] = []
        start_index = 0
        if len(msgs) > 0 and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            if typ == "tool_sequence":
                formatted_msgs.extend(
                    await self._format_tool_sequence(group),
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
        """Format a sequence of tool call/result messages using the Responses
        API format.

        Args:
            msgs (`list[Msg]`):
                The tool call/result messages to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of Responses API input items.
        """
        return await OpenAIResponseFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Format a sequence of agent messages as a history-wrapped user item.

        Args:
            msgs (`list[Msg]`):
                The agent messages to format.
            is_first (`bool`, defaults to ``True``):
                Whether this is the first agent message group, which triggers
                the conversation history prompt.

        Returns:
            `list[dict[str, Any]]`:
                A list containing at most one user input item.
        """
        conversation_history_prompt = (
            self.conversation_history_prompt if is_first else ""
        )

        accumulated_text: list[str] = []
        media_blocks: list[dict] = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")
                elif isinstance(block, DataBlock):
                    formatted = self._format_response_data_block(block)
                    if formatted is not None:
                        media_blocks.append(formatted)

        if not accumulated_text and not media_blocks:
            return []

        history_text = "\n".join(accumulated_text)
        wrapped = (
            conversation_history_prompt
            + "<history>\n"
            + history_text
            + "\n</history>"
        )

        content_list: list[dict[str, Any]] = [
            {"type": "input_text", "text": wrapped},
        ]
        content_list.extend(media_blocks)

        return [{"role": "user", "content": content_list}]

    @staticmethod
    async def _format_system_message(
        msg: Msg,
    ) -> dict[str, Any]:
        """Format a system message for the Responses API.

        Args:
            msg (`Msg`):
                The system message to format.

        Returns:
            `dict[str, Any]`:
                A dictionary with ``role`` and ``content`` keys.
        """
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
