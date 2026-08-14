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
from ._elasticsearch import ElasticsearchStore

__all__ = [
    "DocumentSummary",
    "ElasticsearchStore",
    "MilvusLiteStore",
    "VectorStoreBase",
    "VectorRecord",
    "VectorSearchResult",
    "QdrantStore",
    "MongoDBStore",
]
