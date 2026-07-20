# -*- coding: utf-8 -*-
"""The vector store classes in AgentScope."""

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from ._qdrant import QdrantStore
from ._mongodb import MongoDBStore
from ._milvus_lite import MilvusLiteStore
from ._ragflow import RAGFlowStore

__all__ = [
    "DocumentSummary",
    "MilvusLiteStore",
    "RAGFlowStore",
    "VectorStoreBase",
    "VectorRecord",
    "VectorSearchResult",
    "QdrantStore",
    "MongoDBStore",
]
