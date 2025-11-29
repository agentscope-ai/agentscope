# -*- coding: utf-8 -*-
"""The storage abstraction in AgentScope RAG module.

This module provides storage abstractions for both vector databases
and graph databases.
"""

from ._store_base import (
    StoreBase,
    VDBStoreBase,
    GraphStoreBase,
)
from ._qdrant_store import QdrantStore
from ._milvuslite_store import MilvusLiteStore

# Neo4jGraphStore requires neo4j package (optional dependency)
try:
    from ._neo4j_graph_store import Neo4jGraphStore

    _HAS_NEO4J = True
except ImportError:
    _HAS_NEO4J = False
    Neo4jGraphStore = None  # type: ignore

__all__ = [
    "StoreBase",
    "VDBStoreBase",
    "GraphStoreBase",
    "QdrantStore",
    "MilvusLiteStore",
]

if _HAS_NEO4J:
    __all__.append("Neo4jGraphStore")
