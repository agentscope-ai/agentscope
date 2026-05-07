# -*- coding: utf-8 -*-
"""Formatter for the OpenAI Responses API."""
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
    UserMsg,
)


class OpenAIResponseFormatter(_OpenAIFormatterBase):
    """Formatter for the OpenAI Responses API.

    Produces input items compatible with ``client.responses.create(
    input=...)``.
    Compared with the Chat Completions format, the key differences are:

    * Text content blocks use ``input_text`` instead of ``text``.
    * Image content blocks use ``input_image`` instead of ``image_url``.
    * Assistant tool-call messages become top-level ``function_call`` items.
    * Tool result messages become ``function_call_output`` items.
    """

    supported_input_media_types: list[str] = Field(
        default_factory=lambda: ["image/*", "audio/*"],
        description=(
            "The supported input media types, using glob-style patterns "
            '(e.g. ``"image/*"``, ``"audio/mp3"``). '
            'Defaults to ``["image/*", "audio/*"]``.'
        ),
    )

    def _format_response_data_block(
        self,
        block: DataBlock,
        role: str = "user",
    ) -> dict[str, Any] | None:
        """Format a DataBlock into the Response API format.

        Images are converted to ``input_image`` format. Audio blocks reuse the
        base class ``input_audio`` format (already compatible).
        """
        base_result = self._format_openai_data_block(block, role)
        if base_result is None:
            return None

        if base_result.get("type") == "image_url":
            return {
                "type": "input_image",
                "image_url": base_result["image_url"]["url"],
            }

        return base_result

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
                    content_parts.append(
                        {"type": "input_text", "text": block.text},
                    )

                elif isinstance(block, DataBlock):
                    formatted = self._format_response_data_block(
                        block,
                        role=msg.role,
                    )
                    if formatted is not None:
                        content_parts.append(formatted)

                elif isinstance(block, HintBlock):
                    if content_parts:
                        items.append(
                            {
                                "role": msg.role,
                                "content": content_parts,
                            },
                        )
                        content_parts = []

                elif isinstance(block, ThinkingBlock):
                    # OpenAI Responses API does not accept reasoning/thinking
                    # content in conversation history — skip silently.
                    pass

                elif isinstance(block, ToolCallBlock):
                    function_calls.append(
                        {
                            "type": "function_call",
                            "id": block.id,
                            "call_id": block.id,
                            "name": block.name,
                            "arguments": block.input,
                        },
                    )

                elif isinstance(block, ToolResultBlock):
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
                        msgs.insert(
                            i + 1,
                            UserMsg(
                                name="system-reminder",
                                content=multimodal_data,
                            ),
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
