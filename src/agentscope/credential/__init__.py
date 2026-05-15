# -*- coding: utf-8 -*-
"""The credential module."""

from ._anthropic import AnthropicCredential
from ._dashscope import DashScopeCredential
from ._deepseek import DeepSeekCredential
from ._gemini import GeminiCredential
from ._moonshot import MoonshotCredential
from ._ollama import OllamaCredential
from ._openai import OpenAICredential
from ._xai import XAICredential

__all__ = [
    "AnthropicCredential",
    "DashScopeCredential",
    "DeepSeekCredential",
    "GeminiCredential",
    "MoonshotCredential",
    "OllamaCredential",
    "OpenAICredential",
    "XAICredential",
]


def list_credential() -> list[dict]:
    """List the available credential and their schemas, used for frontend to
    render the credential form."""

    credentials = [
        AnthropicCredential,
        DashScopeCredential,
        DeepSeekCredential,
        GeminiCredential,
        MoonshotCredential,
        OllamaCredential,
        OpenAICredential,
        XAICredential,
    ]

    return [_.model_json_schema() for _ in credentials]
