# -*- coding: utf-8 -*-
"""The MiniMax formatters, i.e. the Anthropic formatters plus video input."""
from typing import Any

from pydantic import Field

from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from ..message import DataBlock, Msg


class MiniMaxChatFormatter(AnthropicChatFormatter):
    """The MiniMax formatter for chatbot scenario."""

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "video/*"]``.'
        ),
    )

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format video blocks natively and delegate other media."""
        supported = self._get_supported_tool_result_block(block)
        if supported is not None and supported.source.media_type.startswith(
            "video/",
        ):
            return self._format_source(
                supported.source,
                "video",
                supported.model_extra,
            )
        return super()._format_anthropic_data_block(block)


class MiniMaxMultiAgentFormatter(AnthropicMultiAgentFormatter):
    """The MiniMax formatter for multi-agent conversations."""

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "video/*"]``.'
        ),
    )

    async def _format_hint_message(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format hint messages with the MiniMax chat formatter.

        Args:
            msgs (`list[Msg]`):
                One message containing a single ``HintBlock``.

        Returns:
            `list[dict[str, Any]]`:
                The MiniMax user-message representation.
        """
        return await MiniMaxChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format video blocks natively and delegate other media."""
        supported = self._get_supported_tool_result_block(block)
        if supported is not None and supported.source.media_type.startswith(
            "video/",
        ):
            return self._format_source(
                supported.source,
                "video",
                supported.model_extra,
            )
        return super()._format_anthropic_data_block(block)
