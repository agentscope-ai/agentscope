# -*- coding: utf-8 -*-
"""The vector store classes in AgentScope."""

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from ._elasticsearch import ElasticsearchStore
from ._milvus_lite import MilvusLiteStore
from ._mongodb import MongoDBStore
from ._qdrant import QdrantStore
from ._valkey import ValkeyStore

__all__ = [
    "DocumentSummary",
    "ElasticsearchStore",
    "MilvusLiteStore",
    "MongoDBStore",
    "QdrantStore",
    "ValkeyStore",
    "VectorStoreBase",
    "VectorRecord",
    "VectorSearchResult",
]
