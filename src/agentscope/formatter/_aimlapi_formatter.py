# -*- coding: utf-8 -*-
"""The aimlapi.com formatter classes.

aimlapi.com speaks the OpenAI Chat Completions wire format verbatim, so both
formatters reuse the OpenAI implementations and only narrow the advertised
input types: the aggregator's chat surface takes text and images, while audio
and PDF support is per-model rather than provider-wide. Per-model input types
come from the model cards in ``agentscope/model/_aimlapi/_models``.
"""
from pydantic import Field

from ._openai_formatter import OpenAIChatFormatter, OpenAIMultiAgentFormatter

_AIMLAPI_INPUT_TYPES = ["text/plain", "image/*"]


class AIMLAPIChatFormatter(OpenAIChatFormatter):
    """The aimlapi.com formatter for the chatbot scenario, where only a user
    and an agent are involved."""

    input_types: list[str] = Field(
        default_factory=lambda: list(_AIMLAPI_INPUT_TYPES),
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*"]``.'
        ),
    )


class AIMLAPIMultiAgentFormatter(OpenAIMultiAgentFormatter):
    """The aimlapi.com formatter for multi-agent conversations, where more
    than a user and an agent are involved."""

    input_types: list[str] = Field(
        default_factory=lambda: list(_AIMLAPI_INPUT_TYPES),
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*"]``.'
        ),
    )
