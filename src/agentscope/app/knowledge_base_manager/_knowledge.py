# -*- coding: utf-8 -*-
"""Runtime handle for a single knowledge base.

A :class:`Knowledge` instance is the **single algorithmic source of
truth** for talking to one knowledge base: HTTP handlers (via
:class:`~agentscope.app._service.KnowledgeBaseService`) and agent
runtime alike obtain a ``Knowledge`` from
:class:`KnowledgeBaseManagerBase.get_knowledge` and use the same
``search`` / ``insert_document`` / ``delete_document`` /
``list_documents`` methods.  This guarantees that retrieval behaviour
matches between front-end testing and live agent execution.

The handle is *narrow on purpose* — it only carries the resolved
runtime state (record + embedding model + scope) and delegates every
operation to the bound :class:`~agentscope.rag.VectorStoreBase`.
Authorisation, credential lookup, and dimension validation happen one
layer up in the manager before this object is constructed.
"""
import asyncio
import uuid
from typing import TYPE_CHECKING

from ...message import DataBlock

if TYPE_CHECKING:
    from ...embedding import EmbeddingModelBase
    from ...rag import (
        DocumentSummary,
        VectorSearchResult,
        VectorStoreBase,
    )
    from ..storage import KnowledgeBaseRecord


class Knowledge:
    """Runtime handle for one knowledge base.

    The handle is bound to a specific
    :class:`~agentscope.app.storage.KnowledgeBaseRecord`, embedding
    model, and vector store.  Its scope (collection name + optional
    metadata filter) is resolved by the
    :class:`KnowledgeBaseManagerBase` according to its isolation
    strategy.

    Instances are cheap to construct (no I/O) and not cached by the
    manager — callers may freely instantiate one per request without
    worrying about lifetime.
    """

    def __init__(
        self,
        record: "KnowledgeBaseRecord",
        embedding_model: "EmbeddingModelBase",
        vector_store: "VectorStoreBase",
        collection_name: str,
        metadata_filter: dict | None = None,
    ) -> None:
        """Initialize the runtime handle.

        Args:
            record (`KnowledgeBaseRecord`):
                The persisted knowledge base metadata.  Used by
                callers that need access to ``name`` / ``description``
                / ``embedding_model_config`` for display.
            embedding_model (`EmbeddingModelBase`):
                The embedding model resolved from the record's
                credential.  Must produce vectors of the same
                dimension as the underlying collection.
            vector_store (`VectorStoreBase`):
                The application-wide vector store instance.
            collection_name (`str`):
                The physical collection backing this knowledge base —
                resolved by the manager from the isolation strategy.
            metadata_filter (`dict | None`, optional):
                The defense-in-depth payload filter applied on every
                search / list operation.  Required by manager
                strategies that co-locate multiple knowledge bases
                inside one collection; ``None`` for the
                collection-per-KB strategy.
        """
        self._record = record
        self._embedding_model = embedding_model
        self._vector_store = vector_store
        self._collection_name = collection_name
        self._metadata_filter = metadata_filter

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def record(self) -> "KnowledgeBaseRecord":
        """The bound knowledge base record."""
        return self._record

    @property
    def embedding_model(self) -> "EmbeddingModelBase":
        """The bound embedding model."""
        return self._embedding_model

    @property
    def collection_name(self) -> str:
        """The physical collection backing this knowledge base."""
        return self._collection_name

    @property
    def metadata_filter(self) -> dict | None:
        """The defense-in-depth payload filter, or ``None``."""
        return self._metadata_filter

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        queries: list[str | DataBlock],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list["VectorSearchResult"]:
        """Search the knowledge base with one or more queries.

        Each query is embedded with the bound embedding model and the
        resulting vectors are searched concurrently against the
        collection (with the manager's metadata filter applied).
        Hits are deduplicated by chunk content id (best score
        wins), filtered by ``score_threshold`` (when set), sorted
        by descending score, and truncated to ``top_k``.

        Args:
            queries (`list[str | DataBlock]`):
                The query inputs (text and/or multimodal blocks).
            top_k (`int`, defaults to ``5``):
                Maximum number of results returned across all queries.
            score_threshold (`float | None`, optional):
                Minimum similarity score for a hit to be retained.
                ``None`` disables filtering.

        Returns:
            `list[VectorSearchResult]`:
                At most ``top_k`` deduplicated hits ordered by
                descending similarity score.
        """
        if not queries:
            return []

        response = await self._embedding_model(queries)

        results_per_query = await asyncio.gather(
            *(
                self._vector_store.search(
                    collection=self._collection_name,
                    query_vector=vector,
                    top_k=top_k,
                    metadata_filter=self._metadata_filter,
                )
                for vector in response.embeddings
            ),
        )

        best: dict[str, "VectorSearchResult"] = {}
        for results in results_per_query:
            for result in results:
                if (
                    score_threshold is not None
                    and result.score < score_threshold
                ):
                    continue
                key = result.chunk.content.id
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
        chunks: list,
        document_id: str | None = None,
        document_metadata: dict | None = None,
    ) -> str:
        """Embed and insert a list of chunks as a single source document.

        All chunks of the same document share the same
        ``document_id``; ``delete_document`` later removes them as a
        unit.  The manager's metadata filter (when present) is merged
        into each chunk's metadata so cross-tenant scoping works.

        Args:
            chunks (`list[Chunk]`):
                The pre-chunked document content (already produced by
                a parser + chunker pipeline).
            document_id (`str | None`, optional):
                The document identifier.  When ``None`` a fresh UUID
                hex is generated.
            document_metadata (`dict | None`, optional):
                Document-level metadata (filename, media type, size,
                upload time, ...).  Merged into each chunk's
                ``metadata`` dict so :meth:`list_documents` can
                surface it.  ``source`` is **not** overwritten — it
                stays as the parser-provided filename.

        Returns:
            `str`:
                The (possibly generated) document id.
        """
        from ...rag import VectorRecord

        if not chunks:
            return document_id or uuid.uuid4().hex
        document_id = document_id or uuid.uuid4().hex

        extra_metadata: dict = {}
        if self._metadata_filter:
            extra_metadata.update(self._metadata_filter)
        if document_metadata:
            extra_metadata.update(document_metadata)
        if extra_metadata:
            for chunk in chunks:
                chunk.metadata = {**extra_metadata, **chunk.metadata}

        contents = [chunk.content for chunk in chunks]
        response = await self._embedding_model(contents)

        if len(response.embeddings) != len(chunks):
            raise RuntimeError(
                f"Embedding model returned {len(response.embeddings)} "
                f"vectors for {len(chunks)} chunks.",
            )

        records: list["VectorRecord"] = [
            VectorRecord(
                vector=vector,
                document_id=document_id,
                chunk=chunk,
            )
            for vector, chunk in zip(response.embeddings, chunks)
        ]
        await self._vector_store.insert(self._collection_name, records)
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Remove every record for one source document.

        Args:
            document_id (`str`):
                The source document id whose records should be
                removed.
        """
        await self._vector_store.delete(
            self._collection_name,
            document_id,
        )

    async def list_documents(self) -> list["DocumentSummary"]:
        """List all distinct source documents in this knowledge base.

        Returns:
            `list[DocumentSummary]`:
                One summary per indexed document, in unspecified
                order.
        """
        return await self._vector_store.list_documents(
            self._collection_name,
            metadata_filter=self._metadata_filter,
        )
