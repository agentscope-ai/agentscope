# -*- coding: utf-8 -*-
"""The formatter module in agentscope."""
from typing import Type, Any

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

# Built-in formatter classes (internal use only)
_BUILTIN_FORMATTERS: list[Type[FormatterBase]] = [
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
]


def _deserialize_formatter(
    data: dict[str, Any],
    custom_classes: list[Type[FormatterBase]] | None = None,
    context: dict[str, Any] | None = None,
) -> FormatterBase:
    """Deserialize a formatter from a dictionary.

    Args:
        data (`dict[str, Any]`):
            Dictionary containing serialized formatter data with 'type' field.
        custom_classes (`list[Type[FormatterBase]] | None`, optional):
            Optional list of custom formatter classes to support.
        context (`dict[str, Any] | None`, optional):
            Optional context dict to pass to nested validators.

    Returns:
        Deserialized formatter instance.

    Raises:
        ValueError: If 'type' field is missing or unknown.
    """
    if "type" not in data:
        raise ValueError("Formatter data must contain 'type' field")

    type_value = data["type"]

    all_classes = _BUILTIN_FORMATTERS + (custom_classes or [])
    registry = {
        cls.model_fields["type"].default: cls
        for cls in all_classes
        if "type" in cls.model_fields
        and cls.model_fields["type"].default is not None
    }

    if type_value not in registry:
        raise ValueError(
            f"Unknown formatter type '{type_value}'. "
            f"Available types: {list(registry.keys())}",
        )

    return registry[type_value].model_validate(data, context=context)


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
]
