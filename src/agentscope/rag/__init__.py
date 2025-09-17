# -*- coding: utf-8 -*-
"""The retrieval-augmented generation (RAG) module in AgentScope."""

from ._document import Document
from ._knowledge_base import KnowledgeBase
from ._knowlege import Knowledge
from ._store import (
    VDBStoreBase,
    QdrantLocalStore,
)

__all__ = [
    "Document",
    "VDBStoreBase",
    "QdrantLocalStore",
    "KnowledgeBase",
    "Knowledge"
]
