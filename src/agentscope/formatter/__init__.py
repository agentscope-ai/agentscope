# -*- coding: utf-8 -*-
"""The formatter module in agentscope."""

from ._formatter_base import FormatterBase
from ._dashscope_formatter import (
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
)
from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from ._openai_formatter import (
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from ._gemini_formatter import (
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
)
from ._ollama_formatter import (
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
)
from ._deepseek_formatter import (
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
)
from ._openai_response_formatter import OpenAIResponseFormatter
from ._openai_response_formatter import OpenAIResponseMultiAgentFormatter
from ._kimi_formatter import KimiChatFormatter, KimiMultiAgentFormatter
from ._xai_formatter import (
    XAIChatFormatter,
    XAIMultiAgentFormatter,
)

__all__ = [
    "FormatterBase",
    "DashScopeChatFormatter",
    "DashScopeMultiAgentFormatter",
    "OpenAIChatFormatter",
    "OpenAIMultiAgentFormatter",
    "AnthropicChatFormatter",
    "AnthropicMultiAgentFormatter",
    "GeminiChatFormatter",
    "GeminiMultiAgentFormatter",
    "OllamaChatFormatter",
    "OllamaMultiAgentFormatter",
    "DeepSeekChatFormatter",
    "DeepSeekMultiAgentFormatter",
    "OpenAIResponseFormatter",
    "OpenAIResponseMultiAgentFormatter",
    "KimiChatFormatter",
    "KimiMultiAgentFormatter",
    "XAIChatFormatter",
    "XAIMultiAgentFormatter",
]
