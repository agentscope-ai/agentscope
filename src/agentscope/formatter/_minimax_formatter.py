# -*- coding: utf-8 -*-
"""The MiniMax formatter for agentscope.

MiniMax officially recommends using its Anthropic-compatible API
(``https://api.minimax.io/anthropic``) for chat completions, so the
formatters here are thin wrappers around
:class:`AnthropicChatFormatter` / :class:`AnthropicMultiAgentFormatter`.
The only structural difference is that the MiniMax-Speech models accept
audio input on the Anthropic-compatible endpoint while Anthropic itself
does not; the default ``input_types`` here matches Anthropic (text +
image), which covers the MiniMax M-series chat models like
``MiniMax-M3``.
"""

from pydantic import Field

from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)


class MiniMaxChatFormatter(AnthropicChatFormatter):
    """The MiniMax formatter for chatbot scenario.

    MiniMax's M-series chat models (e.g. ``MiniMax-M3``) are exposed
    through an Anthropic-compatible API. The serialised request format is
    identical to Anthropic's so this class inherits the entire formatter,
    including the
    `documented thinking-block round-trip behaviour
    <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_
    that preserves reasoning continuity across turns.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*"]``. '
            "MiniMax's M-series chat endpoint does not accept audio "
            "input."
        ),
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
        default_factory=lambda: ["text/plain", "image/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*"]``. '
            "MiniMax's M-series chat endpoint does not accept audio "
            "input."
        ),
    )
