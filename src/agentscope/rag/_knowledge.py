# -*- coding: utf-8 -*-
"""Runtime handle for a single knowledge base.

A :class:`KnowledgeBase` instance is the **single algorithmic source of
truth** for talking to one knowledge base: it pairs an embedding model
with a vector-store collection (optionally scoped by a payload
``metadata_filter``) and exposes the four operations a caller ever
needs — :meth:`search`, :meth:`insert_document`,
:meth:`delete_document`, :meth:`list_documents`.

The handle is *narrow on purpose* — it carries the resolved runtime
state (embedding model + vector store + scope) and delegates every
operation to the bound :class:`VectorStoreBase`.  Document parsing,
chunking, credential resolution, dimension policy validation, and
persistence of knowledge-base records all belong one layer up
(service-side :class:`KnowledgeBaseManagerBase` for hosted
deployments; the caller directly otherwise).

The backing collection is created on first use — each operation
transparently calls :meth:`ensure_collection`, which is itself
idempotent and memoised after the first success, so the only cost is
one extra round-trip on the very first call against a fresh
deployment.

``metadata_filter`` is the defense-in-depth scoping mechanism for
co-locating multiple logical knowledge bases inside the same physical
collection — typically multi-tenant deployments where every record
carries a ``{"tenant_id": "..."}`` payload.  It is set once at
construction time and **always** applied: search/list never escape
it, and insert forces it onto every chunk's metadata so a malicious or
buggy parser cannot rebind a record into another scope.
"""

import asyncio

from ._document import Chunk
from ._vdb import VectorRecord, VectorSearchResult, VectorStoreBase
from .._utils._common import _generate_id
from ..embedding import EmbeddingModelBase
from ..message import DataBlock, TextBlock
from ._vdb import DocumentSummary


class KnowledgeBase:
    """Runtime handle for one knowledge base.

    Binds an embedding model and a vector-store collection together so
    callers can retrieve / insert / delete / list documents without
    repeating the wiring.  Cheap to construct (no I/O); the collection
    itself is created lazily on the first operation, so a fresh
    deployment "just works" without an explicit setup step.

    .. code-block:: python

        kb = KnowledgeBase(
            name="company-handbook",
            description="Internal HR and onboarding documents.",
            embedding_model=embedding_model,
            vector_store=vector_store,
            collection="handbook",
        )
        await kb.insert_document(chunks)
        results = await kb.search(["What is the PTO policy?"])
    """

    name: str
    """Agent-oriented knowledge base name — used by tool descriptions
    and frontend rendering."""

    description: str
    """Agent-oriented knowledge base description — what this knowledge
    base contains and when to retrieve from it."""

    def __init__(
        self,
        name: str,
        description: str,
        embedding_model: EmbeddingModelBase,
        vector_store: VectorStoreBase,
        collection: str,
        metadata_filter: dict | None = None,
    ) -> None:
        """Initialize the runtime handle.

        Args:
            name (`str`):
                Agent-oriented knowledge base name.  Surfaced to the
                LLM (via tool descriptions) and to the front-end.
            description (`str`):
                Agent-oriented description.  Should answer "what is in
                this knowledge base and when should I search it?" — the
                LLM uses it to decide whether to call the search tool
                in agentic mode.
            embedding_model (`EmbeddingModelBase`):
                The embedding model used to embed both queries and
                inserted chunks.  Must be the same model used at
                indexing time and at retrieval time, otherwise vectors
                will not be comparable.
            vector_store (`VectorStoreBase`):
                The shared vector-store connection.  The store must
                already be entered (its own ``__aenter__`` already
                called) before any operation on this handle runs.
            collection (`str`):
                The physical collection backing this knowledge base.
                Created lazily on the first operation; see
                :meth:`ensure_collection`.
            metadata_filter (`dict | None`, optional):
                Defense-in-depth payload filter.  When set:

                - :meth:`search` and :meth:`list_documents` restrict
                  results to records whose payload matches every
                  ``key == value`` pair;
                - :meth:`insert_document` forces these keys onto every
                  inserted chunk's metadata, overriding caller-supplied
                  values, so records cannot leak into another scope.

                ``None`` disables filtering — the default for
                deployments where every knowledge base owns its
                collection outright.
        """
        self.name = name
        self.description = description
        self._embedding_model = embedding_model
        self._vector_store = vector_store
        self._collection = collection
        self._metadata_filter = metadata_filter
        # Memoise the "collection exists" check after the first
        # successful ensure_collection so subsequent operations avoid
        # the extra round-trip.
        self._collection_ready = False

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def embedding_model(self) -> EmbeddingModelBase:
        """The bound embedding model."""
        return self._embedding_model

    @property
    def vector_store(self) -> VectorStoreBase:
        """The bound vector store."""
        return self._vector_store

    @property
    def collection(self) -> str:
        """The physical collection backing this knowledge base."""
        return self._collection

    @property
    def metadata_filter(self) -> dict | None:
        """The defense-in-depth payload filter, or ``None``."""
        return self._metadata_filter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_collection(self) -> None:
        """Idempotently create the backing collection if missing.

        Called transparently at the top of every public operation —
        callers should not need to invoke it themselves.  Memoised on
        the instance after the first success, so subsequent calls are
        a single ``if`` check.

        Looks up the collection via
        :meth:`VectorStoreBase.has_collection` and creates it with the
        embedding model's :attr:`~EmbeddingModelBase.dimensions` when
        absent.

        Raises whatever the backend raises if the collection exists at
        an incompatible dimension (the backend is the authority on
        that; we do not double-check here).
        """
        if self._collection_ready:
            return
        if not await self._vector_store.has_collection(self._collection):
            await self._vector_store.create_collection(
                self._collection,
                dimensions=self._embedding_model.dimensions,
            )
        self._collection_ready = True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        queries: list[str | TextBlock | DataBlock],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        """Search the knowledge base with one or more queries.

        All queries are embedded in a single batch, then searched
        concurrently against the bound collection (with
        :attr:`metadata_filter` applied).  Hits are deduplicated by
        ``(document_id, chunk_index)`` keeping the best score,
        optionally filtered by ``score_threshold``, sorted by
        descending score, and truncated to ``top_k``.

        Args:
            queries (`list[str | TextBlock | DataBlock]`):
                Query inputs.  Text may be either bare ``str`` or
                :class:`TextBlock`; :class:`DataBlock` items are
                **silently dropped** when the bound embedding model
                does not declare ``supports_multimodal`` — text-only
                models would otherwise reject them.  Callers can
                therefore pass a mixed list without per-KB filtering.
            top_k (`int`, defaults to ``5``):
                Maximum number of results returned across all queries
                (after dedup).
            score_threshold (`float | None`, optional):
                Minimum similarity score for a hit to be retained.
                Only meaningful for similarity metrics where higher is
                better (cosine / dot-product).  ``None`` disables
                filtering.

        Returns:
            `list[VectorSearchResult]`:
                At most ``top_k`` deduplicated hits ordered by
                descending similarity score.  Empty when there are no
                queries the bound embedding model can consume.
        """
        if not queries:
            return []

        if not self._embedding_model.supports_multimodal:
            queries = [q for q in queries if not isinstance(q, DataBlock)]
            if not queries:
                return []

        await self.ensure_collection()
        response = await self._embedding_model(queries)

        results_per_query = await asyncio.gather(
            *(
                self._vector_store.search(
                    collection=self._collection,
                    query_vector=vector,
                    top_k=top_k,
                    metadata_filter=self._metadata_filter,
                )
                for vector in response.embeddings
            ),
        )

        best: dict[tuple[str, int], VectorSearchResult] = {}
        for results in results_per_query:
            for result in results:
                if (
                    score_threshold is not None
                    and result.score < score_threshold
                ):
                    continue
                # ``(document_id, chunk_index)`` is the stable identity
                # of a chunk: it survives reindex (block UUIDs do not)
                # and uniquely names "this slice of that document"
                # regardless of which query surfaced it.
                key = (result.document_id, result.chunk.chunk_index)
                if key not in best or result.score > best[key].score:
                    best[key] = result

        merged = sorted(
            best.values(),
            key=lambda result: result.score,
            reverse=True,
        )
        return merged[:top_k]

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def insert_document(
        self,
        chunks: list[Chunk],
        document_id: str | None = None,
        document_metadata: dict | None = None,
    ) -> str:
        """Embed and insert a list of chunks as a single source document.

        All chunks share the resolved ``document_id``;
        :meth:`delete_document` later removes them as a unit.  Each
        chunk's metadata is merged in this precedence (highest wins):

        1. :attr:`metadata_filter` keys — defense-in-depth scoping, so
           a chunk can never be inserted with a payload that escapes
           the filter (any escape would silently disappear at retrieve
           time anyway, but failing closed at insert is clearer).
        2. The chunk's pre-existing ``metadata`` — parser-supplied.
        3. ``document_metadata`` — document-level fields propagated
           down (filename, media type, upload time, ...).

        Args:
            chunks (`list[Chunk]`):
                The pre-chunked document content (already produced by
                a parser + chunker pipeline).  An empty list is a
                no-op.
            document_id (`str | None`, optional):
                The document identifier.  When ``None`` a fresh UUID
                hex is generated and returned so the caller can record
                it for future :meth:`delete_document` calls.
            document_metadata (`dict | None`, optional):
                Document-level metadata (filename, media type, size,
                upload time, ...).  Merged into each chunk's
                ``metadata``.

        Returns:
            `str`:
                The (possibly generated) document id.

        Raises:
            `RuntimeError`:
                If the embedding model returns a number of vectors
                that does not match the number of chunks.
        """
        if not chunks:
            return document_id or _generate_id()
        document_id = document_id or _generate_id()

        await self.ensure_collection()

        # Precedence: metadata_filter wins (security boundary), then
        # chunk metadata, then document_metadata.  See docstring.
        for chunk in chunks:
            chunk.metadata = {
                **(document_metadata or {}),
                **chunk.metadata,
                **(self._metadata_filter or {}),
            }

        response = await self._embedding_model(
            [chunk.content for chunk in chunks],
        )

        if len(response.embeddings) != len(chunks):
            raise RuntimeError(
                f"Embedding model returned {len(response.embeddings)} "
                f"vectors for {len(chunks)} chunks.",
            )

        records = [
            VectorRecord(
                vector=vector,
                document_id=document_id,
                chunk=chunk,
            )
            for vector, chunk in zip(response.embeddings, chunks)
        ]
        await self._vector_store.insert(self._collection, records)
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Remove every record for one source document.

        Args:
            document_id (`str`):
                The source document id whose records should be removed.
        """
        await self.ensure_collection()
        await self._vector_store.delete(
            self._collection,
            document_id,
        )

    async def list_documents(self) -> list["DocumentSummary"]:
        """List all distinct source documents in this knowledge base.

        Filtered by :attr:`metadata_filter` when set, so callers only
        ever see documents within their own scope.

        Returns:
            `list[DocumentSummary]`:
                One summary per indexed document, in unspecified order.
        """
        await self.ensure_collection()
        return await self._vector_store.list_documents(
            self._collection,
            metadata_filter=self._metadata_filter,
        )
