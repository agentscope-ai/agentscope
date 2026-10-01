# -*- coding: utf-8 -*-
"""The MiniMax formatters, i.e. the Anthropic formatters plus video input."""
import fnmatch
from typing import Any

from pydantic import Field

from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from ..message import DataBlock


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
        media_type = block.source.media_type
        if media_type.startswith("video/") and any(
            fnmatch.fnmatch(media_type, _)
            for _ in self.supported_input_media_types
        ):
            return self._format_source(block.source, "video")
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

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format video blocks natively and delegate other media."""
        media_type = block.source.media_type
        if media_type.startswith("video/") and any(
            fnmatch.fnmatch(media_type, _)
            for _ in self.supported_input_media_types
        ):
            return self._format_source(block.source, "video")
        return super()._format_anthropic_data_block(block)
