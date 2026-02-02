# -*- coding: utf-8 -*-
"""The retrieval-augmented generation (RAG) module in AgentScope."""

from ._document import (
    DocMetadata,
    Document,
)
from ._reader import (
    ReaderBase,
    TextReader,
    PDFReader,
    ImageReader,
    WordReader,
    ExcelReader,
    PowerPointReader,
)
from ._store import (
    VDBStoreBase,
    QdrantStore,
    MilvusLiteStore,
    MongoDBStore,
    AlibabaCloudMySQLStore,
)
from ._knowledge_base import KnowledgeBase
from ._simple_knowledge import SimpleKnowledge


__all__ = [
    "ReaderBase",
    "TextReader",
    "PDFReader",
    "ImageReader",
    "WordReader",
    "ExcelReader",
    "PowerPointReader",
    "DocMetadata",
    "Document",
    "VDBStoreBase",
    "QdrantStore",
    "MilvusLiteStore",
    "MongoDBStore",
    "AlibabaCloudMySQLStore",
    "KnowledgeBase",
    "SimpleKnowledge",
]
