# -*- coding: utf-8 -*-
"""The model module."""
from typing import Any, Type

from ._model_base import ChatModelBase
from ._model_response import ChatResponse, StructuredResponse
from ._dashscope_model import DashScopeChatModel
from ._openai_model import OpenAIChatModel
from ._anthropic_model import AnthropicChatModel
from ._ollama_model import OllamaChatModel
from ._gemini_model import GeminiChatModel
from ._model_usage import ChatUsage

# Built-in model classes (internal use only)
_BUILTIN_MODELS: list[Type[ChatModelBase]] = [
    DashScopeChatModel,
    OpenAIChatModel,
    AnthropicChatModel,
    OllamaChatModel,
    GeminiChatModel,
]


def _deserialize_model(
    data: dict[str, Any],
    custom_classes: list[Type[ChatModelBase]] | None = None,
    context: dict[str, Any] | None = None,
) -> ChatModelBase:
    """Deserialize a chat model from a dictionary.

    Args:
        data: Dictionary containing serialized model data with 'type' field.
        custom_classes: Optional list of custom model classes to support.
        context: Optional context dict to pass to nested validators.

    Returns:
        Deserialized model instance.

    Raises:
        ValueError: If 'type' field is missing or unknown.
    """
    if "type" not in data:
        raise ValueError("Model data must contain 'type' field")

    type_value = data["type"]

    all_classes = _BUILTIN_MODELS + (custom_classes or [])
    registry = {
        cls.model_fields["type"].default: cls
        for cls in all_classes
        if "type" in cls.model_fields
        and cls.model_fields["type"].default is not None
    }

    if type_value not in registry:
        raise ValueError(
            f"Unknown model type '{type_value}'. "
            f"Available types: {list(registry.keys())}",
        )

    return registry[type_value].model_validate(data, context=context)


__all__ = [
    "ChatUsage",
    "ChatModelBase",
    "ChatResponse",
    "StructuredResponse",
    "DashScopeChatModel",
    "OpenAIChatModel",
    "AnthropicChatModel",
    "OllamaChatModel",
    "GeminiChatModel",
]
