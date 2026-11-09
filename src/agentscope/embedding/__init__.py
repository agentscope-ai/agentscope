# -*- coding: utf-8 -*-
"""The embedding module in agentscope."""

from ._embedding_base import EmbeddingModelBase
from ._embedding_model_card import EmbeddingModelCard
from ._embedding_usage import EmbeddingUsage
from ._embedding_response import EmbeddingResponse
from ._dashscope import DashScopeEmbeddingModel
from ._openai import OpenAIEmbeddingModel
from ._gemini import GeminiEmbeddingModel
from ._ollama import OllamaEmbeddingModel
from ._cache_base import EmbeddingCacheBase
from ._file_cache import FileEmbeddingCache


__all__ = [
    "EmbeddingModelBase",
    "EmbeddingModelCard",
    "EmbeddingUsage",
    "EmbeddingResponse",
    "DashScopeEmbeddingModel",
    "OpenAIEmbeddingModel",
    "GeminiEmbeddingModel",
    "OllamaEmbeddingModel",
    "EmbeddingCacheBase",
    "FileEmbeddingCache",
]
