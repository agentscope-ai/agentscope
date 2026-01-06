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
)
from ._store import (
    StoreBase,
    VDBStoreBase,
    GraphStoreBase,
    QdrantStore,
    MilvusLiteStore,
)
from ._knowledge_base import KnowledgeBase
from ._simple_knowledge import SimpleKnowledge

# Optional imports for graph features (requires neo4j package)
try:
    from ._store import Neo4jGraphStore
    from ._graph import (
        GraphKnowledgeBase,
        Entity,
        Relationship,
        Community,
        EntityDict,
        RelationshipDict,
        CommunityDict,
        SearchMode,
        CommunityAlgorithm,
    )

    _HAS_GRAPH_FEATURES = True
except ImportError:
    _HAS_GRAPH_FEATURES = False
    Neo4jGraphStore = None  # type: ignore
    GraphKnowledgeBase = None  # type: ignore
    Entity = None  # type: ignore
    Relationship = None  # type: ignore
    Community = None  # type: ignore
    EntityDict = None  # type: ignore
    RelationshipDict = None  # type: ignore
    CommunityDict = None  # type: ignore
    SearchMode = None  # type: ignore
    CommunityAlgorithm = None  # type: ignore


__all__ = [
    # Readers
    "ReaderBase",
    "TextReader",
    "PDFReader",
    "ImageReader",
    # Documents
    "WordReader",
    "DocMetadata",
    "Document",
    # Stores
    "StoreBase",
    "VDBStoreBase",
    "GraphStoreBase",
    "QdrantStore",
    "MilvusLiteStore",
    # Knowledge bases
    "KnowledgeBase",
    "SimpleKnowledge",
]

# Add graph features to __all__ if available
if _HAS_GRAPH_FEATURES:
    __all__.extend(
        [
            "Neo4jGraphStore",
            "GraphKnowledgeBase",
            "Entity",
            "Relationship",
            "Community",
            "EntityDict",
            "RelationshipDict",
            "CommunityDict",
            "SearchMode",
            "CommunityAlgorithm",
        ],
    )
