# -*- coding: utf-8 -*-
"""The MiniMax formatter for agentscope.

MiniMax officially recommends using its Anthropic-compatible API
(``https://api.minimax.io/anthropic``) for chat completions. These
formatters extend the Anthropic formatters with MiniMax-M3 native video
input while reusing their text, image, tool, and thinking behavior.
"""

import base64
import fnmatch
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from pydantic import Field

from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from .._logging import logger
from ..message import Base64Source, DataBlock, URLSource


def _file_url_to_path(url: str) -> Path:
    """Convert a local file URL to a platform-compatible path."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    return Path(url2pathname(path))


def _format_minimax_data_block(
    block: DataBlock,
    supported_media_types: list[str],
    fallback: Callable[[DataBlock], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Format MiniMax video blocks and delegate all other media."""
    source = block.source
    media_type = source.media_type

    if not media_type.startswith("video/"):
        return fallback(block)

    if not any(
        fnmatch.fnmatch(media_type, pattern)
        for pattern in supported_media_types
    ):
        logger.warning(
            "Media type %s is not supported, skipped.",
            media_type,
        )
        return None

    if isinstance(source, Base64Source):
        formatted_source = {
            "type": "base64",
            "media_type": media_type,
            "data": source.data,
        }
    elif isinstance(source, URLSource):
        url = str(source.url)
        if url.startswith("file://"):
            data = base64.b64encode(
                _file_url_to_path(url).read_bytes(),
            ).decode("utf-8")
            formatted_source = {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            }
        else:
            formatted_source = {
                "type": "url",
                "url": url,
            }
    else:
        raise ValueError(f"Unsupported source type: {type(source)}")

    return {
        "type": "video",
        "source": formatted_source,
    }


class MiniMaxChatFormatter(AnthropicChatFormatter):
    """The MiniMax formatter for chatbot scenario.

    MiniMax's M-series chat models (e.g. ``MiniMax-M3``) are exposed
    through an Anthropic-compatible API. This class inherits the Anthropic
    formatter and adds MiniMax-M3's native video content block. It also
    retains the
    `documented thinking-block round-trip behaviour
    <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_
    that preserves reasoning continuity across turns.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "video/*"]``. '
            "MiniMax's M-series chat endpoint does not accept audio "
            "input."
        ),
    )

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format MiniMax video input or delegate Anthropic media."""
        return _format_minimax_data_block(
            block,
            self.supported_input_media_types,
            super()._format_anthropic_data_block,
        )


class MiniMaxMultiAgentFormatter(AnthropicMultiAgentFormatter):
    """The MiniMax formatter for multi-agent conversations.

    MiniMax's M-series chat models follow Anthropic's API conventions, so
    the multi-agent history-collapsing logic is reused verbatim from
    :class:`AnthropicMultiAgentFormatter`.
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
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "video/*"]``. '
            "MiniMax's M-series chat endpoint does not accept audio "
            "input."
        ),
    )

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format MiniMax video input or delegate Anthropic media."""
        return _format_minimax_data_block(
            block,
            self.supported_input_media_types,
            super()._format_anthropic_data_block,
        )
