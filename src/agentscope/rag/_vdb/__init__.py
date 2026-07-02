# -*- coding: utf-8 -*-
"""The vector store classes in AgentScope."""

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from ._milvus_lite import MilvusLiteStore
from ._qdrant import QdrantStore
from ._valkey import ValkeyStore

__all__ = [
    "DocumentSummary",
    "MilvusLiteStore",
    "ValkeyStore",
    "VectorStoreBase",
    "VectorRecord",
    "VectorSearchResult",
    "QdrantStore",
]
