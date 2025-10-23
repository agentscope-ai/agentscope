# -*- coding: utf-8 -*-
"""The embedding module in agentscope."""

from ._embedding_base import EmbeddingModelBase
from ._embedding_usage import EmbeddingUsage
from ._embedding_response import EmbeddingResponse
from ._dashscope_embedding import DashScopeTextEmbedding
from ._dashscope_multimodal_embedding import DashScopeMultiModalEmbedding
from ._openai_embedding import OpenAITextEmbedding
from ._gemini_embedding import GeminiTextEmbedding
from ._ollama_embedding import OllamaTextEmbedding
from ._cache_base import EmbeddingCacheBase
from ._file_cache import FileEmbeddingCache


__all__ = [
    "EmbeddingModelBase",
    "EmbeddingUsage",
    "EmbeddingResponse",
    "DashScopeTextEmbedding",
    "DashScopeMultiModalEmbedding",
    "OpenAITextEmbedding",
    "GeminiTextEmbedding",
    "OllamaTextEmbedding",
    "EmbeddingCacheBase",
    "FileEmbeddingCache",
]
