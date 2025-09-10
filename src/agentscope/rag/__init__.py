# -*- coding: utf-8 -*-
"""The retrieval-augmented generation (RAG) module in AgentScope."""

from ._knowledge_base import KnowledgeBase
from ._store import (
    VectorRecord,
    EmbeddingStoreBase,
    QdrantStore,
)

__all__ = [
    "VectorRecord",
    "EmbeddingStoreBase",
    "KnowledgeBase",
    "QdrantStore",
]