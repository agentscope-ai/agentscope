# -*- coding: utf-8 -*-
"""The formatter module in agentscope."""

from ._anthropic_formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from ._dashscope_formatter import (
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
)
from ._deepseek_formatter import (
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
)
from ._formatter_base import FormatterBase
from ._gemini_formatter import (
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
)
from ._ollama_formatter import (
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
)
from ._openai_formatter import (
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from ._truncated_formatter_base import TruncatedFormatterBase
from ._zhipu_formatter import (
    ZhipuChatFormatter,
    ZhipuMultiAgentFormatter,
)

__all__ = [
    "FormatterBase",
    "TruncatedFormatterBase",
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
    "ZhipuChatFormatter",
    "ZhipuMultiAgentFormatter",
]
