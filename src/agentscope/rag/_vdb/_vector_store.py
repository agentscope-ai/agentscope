# -*- coding: utf-8 -*-
"""Abstract base class for vector store backends.

A :class:`VectorStoreBase` instance is the single connection point to
one vector database deployment.  It is created once at application
startup, passed into ``create_app(vector_store=...)``, and shared
across all requests for the lifetime of the process — similar to
:class:`~agentscope.app.storage.StorageBase` and
:class:`~agentscope.app.message_bus.MessageBus`.

Each **knowledge base** maps to one **collection** inside the vector
store.  Collections are isolated: different knowledge bases never
share a collection, so retrieval is always scoped to a single
collection without cross-collection filtering.

Lifecycle is managed via the async context manager protocol
(``__aenter__`` / ``__aexit__``), which the app lifespan calls
automatically.
"""
from abc import ABC, abstractmethod
from typing import Any, Self

from pydantic import BaseModel, Field

from .._document import Chunk


class VectorRecord(BaseModel):
    """A single record to insert into a vector store collection.

    Pairs a :class:`Chunk` (the business payload — content, source,
    structural metadata) with its dense embedding vector.  ``Chunk``
    is intentionally not extended with an ``embedding`` field so its
    semantics stay stable across the indexing pipeline; instead the
    vector lives in this wrapper whose only purpose is "I am about
    to be inserted into a vector database."
    """

    vector: list[float]
    """The dense embedding vector for :attr:`chunk`."""

    document_id: str
    """The ID of the source document this record belongs to.
    Assigned by the knowledge base layer when the document is
    registered.  Backends must persist it at insertion time so that
    :meth:`VectorStoreBase.delete` can remove all records of one
    document as a unit."""

    chunk: Chunk
    """The business payload — content, source, structural metadata."""


class VectorSearchResult(BaseModel):
    """A single result returned by a similarity search.

    Pairs the matched :class:`Chunk` with its similarity score.
    ``Chunk`` is intentionally not extended with a ``score`` field so
    its semantics stay stable; instead the score lives in this
    wrapper whose only purpose is "I am a query hit."
    """

    score: float
    """Similarity score.  Higher = more similar for cosine /
    dot-product; lower = more similar for L2 distance."""

    document_id: str
    """The ID of the source document the matched chunk belongs to —
    the same value carried by :attr:`VectorRecord.document_id` at
    insertion time.  Lets callers cite, group, or delete the source
    document of a hit."""

    chunk: Chunk
    """The matched business payload."""


class DocumentSummary(BaseModel):
    """A lightweight description of one source document inside a collection.

    Aggregated by :meth:`VectorStoreBase.list_documents` from the
    records of each ``document_id`` — the vector store is the single
    source of truth for "what documents exist in a knowledge base".
    """

    document_id: str
    """The source document identifier — the same value carried by
    :attr:`VectorRecord.document_id` at insertion time."""

    source: str
    """The original filename, taken from the first chunk encountered.
    All chunks of the same document share the same filename so any
    chunk yields the same value."""

    chunk_count: int
    """The total number of chunks indexed for this document."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Document-level metadata propagated from the parser / uploader
    (media type, size, upload time, ...).  Taken from the first chunk
    encountered."""


class VectorStoreBase(ABC):
    """Abstract base class for vector store backends.

    Subclasses implement the concrete connection and query logic for a
    specific vector database (Chroma, Milvus, Qdrant, FAISS, etc.).

    A single instance is shared across the entire application.  The
    underlying client SDK is expected to handle connection pooling and
    thread safety internally.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context — open connections if needed.

        The default implementation is a no-op.  Subclasses that need
        explicit connection setup should override this.

        Returns:
            `VectorStoreBase`: ``self``.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — close connections if needed.

        The default implementation is a no-op.
        """

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new collection (vector index).

        If the collection already exists, implementations should raise
        or silently no-op depending on the backend's semantics.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
                All vectors inserted later must have this many elements.
        """

    @abstractmethod
    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data.

        Args:
            name (`str`):
                The collection name to delete.
        """

    @abstractmethod
    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the collection exists.
        """

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """

    @abstractmethod
    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Identifies records by the :attr:`VectorRecord.document_id`
        field that backends persist at insertion time.  This matches
        the typical RAG workflow where a user uploads or removes a
        file as a unit.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document ID whose records should be
                removed.
        """

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Find the most similar records to a query vector.

        Args:
            collection (`str`):
                The collection to search.
            query_vector (`list[float]`):
                The query embedding vector.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict the search to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Backends translate this into a native
                payload filter.  Used for defense-in-depth
                cross-tenant scoping when an isolation strategy
                co-locates multiple knowledge bases inside the same
                collection.

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score.
        """

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection.

        Aggregates records by :attr:`VectorRecord.document_id` and
        returns one :class:`DocumentSummary` per document.  Backends
        are free to use whatever scrolling / aggregation primitive
        they expose; this method is expected to be O(documents) not
        O(chunks) on backends that support payload-only scans.

        Args:
            collection (`str`):
                The target collection name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Used together with the search-time
                filter when an isolation strategy co-locates multiple
                knowledge bases inside the same collection.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``, in
                unspecified order.
        """
