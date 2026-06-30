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

__all__ = [
    "DocumentSummary",
    "MilvusLiteStore",
    "VectorStoreBase",
    "VectorRecord",
    "VectorSearchResult",
    "QdrantStore",
]
