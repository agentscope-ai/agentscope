# -*- coding: utf-8 -*-
"""RAGFlow-backed knowledge base handle.

RAGFlow is a managed, end-to-end RAG pipeline: it owns document
parsing, chunking, indexing, and retrieval all on the server side.
That makes it fundamentally different from a vector database, where
AgentScope is responsible for the whole parse -> chunk -> embed
pipeline.  Forcing RAGFlow underneath :class:`VectorStoreBase` would
require bypassing its core strengths (its built-in document processing
and indexing pipeline), so it is exposed instead as a knowledge-layer
handle that sits *alongside* :class:`~agentscope.rag.KnowledgeBase`
and implements the same application-level interface.

:class:`RAGFlowKnowledge` therefore mirrors the public surface of
:class:`~agentscope.rag.KnowledgeBase` — :meth:`search`,
:meth:`insert_document`, :meth:`delete_document`, :meth:`list_documents`,
:meth:`list_chunks` — so code written against one knowledge backend
keeps working with the other, while delegating the heavy lifting to the
RAGFlow service.

The one deliberate divergence from :class:`~agentscope.rag.KnowledgeBase`
is how a document is added.  ``KnowledgeBase.insert_document`` takes
pre-embedded ``Chunk`` objects because AgentScope runs the parsing and
chunking locally.  RAGFlow runs those steps on the server, so
:meth:`RAGFlowKnowledge.insert_document` accepts raw document bytes plus a
filename and lets RAGFlow parse, chunk, and index them.

.. note:: The ``ragflow-sdk`` package is required.  Install it with
    ``pip install "agentscope[vdb-ragflow]"``.  It is imported lazily so
    importing :mod:`agentscope` stays lightweight.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .._utils._common import _generate_id
from ._document import Chunk
from ._vdb import DocumentSummary, VectorSearchResult
from ..message import TextBlock

if TYPE_CHECKING:
    from ragflow_sdk import RAGFlow, Dataset, Document


class RAGFlowConfig(BaseModel):
    """Connection and retrieval tuning for a RAGFlow knowledge base.

    Carries everything needed to talk to one RAGFlow dataset (RAGFlow's
    name for a knowledge base) and to tune how :meth:`RAGFlowKnowledge.search`
    retrieves chunks from it.
    """

    api_key: str
    """The RAGFlow API key."""

    base_url: str
    """The RAGFlow service base URL, e.g. ``"http://localhost:9380"``."""

    dataset_id: str
    """The RAGFlow dataset (knowledge base) id to bind against."""

    top_k: int = 10
    """Server-side top-k: the number of candidates RAGFlow considers for
    vector cosine computation before reranking/filtering."""

    similarity_threshold: float = 0.2
    """Server-side minimum similarity score a chunk must reach to be
    returned."""

    vector_similarity_weight: float = 0.3
    """Relative weight of vector cosine similarity versus term (keyword)
    similarity when combining the two.  ``x`` is the weight of the vector
    score, ``1 - x`` the weight of the term score."""

    enable_rerank: bool = False
    """Whether to rerank the candidates with a RAGFlow rerank model.  When
    ``True``, :attr:`rerank_id` must point at a configured rerank model."""

    rerank_id: str | None = None
    """The id of the rerank model to use when :attr:`enable_rerank` is
    ``True``."""

    keyword: bool = False
    """Whether to additionally match chunks by keyword (in addition to the
    vector similarity search)."""


class RAGFlowKnowledge:
    """Runtime handle for one RAGFlow knowledge base.

    Binds a RAGFlow dataset together with retrieval tuning so callers
    can retrieve / add / delete / list documents without repeating the
    wiring.  Cheap to construct (no I/O); the RAGFlow client is created
    lazily on the first network call.

    .. code-block:: python

        kb = RAGFlowKnowledge(
            name="company-handbook",
            description="Internal HR and onboarding documents.",
            config=RAGFlowConfig(
                api_key="ragflow-xxxxx",
                base_url="http://localhost:9380",
                dataset_id="kb-xxxxx",
            ),
        )
        await kb.insert_document(
            b"...raw pdf bytes...",
            filename="handbook.pdf",
        )
        results = await kb.search(["What is the PTO policy?"])
    """

    name: str
    """Agent-oriented knowledge base name — used by tool descriptions
    and frontend rendering, mirroring
    :class:`~agentscope.rag.KnowledgeBase`."""

    description: str
    """Agent-oriented knowledge base description — what this knowledge
    base contains and when to retrieve from it."""

    def __init__(
        self,
        name: str,
        description: str,
        config: RAGFlowConfig,
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
            config (`RAGFlowConfig`):
                Connection details (API key, base URL, dataset id) and
                retrieval tuning.
        """
        self.name = name
        self.description = description
        self._config = config
        self._client: "RAGFlow | None" = None

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def config(self) -> RAGFlowConfig:
        """The bound :class:`RAGFlowConfig`."""
        return self._config

    @property
    def api_key(self) -> str:
        """The RAGFlow API key."""
        return self._config.api_key

    @property
    def base_url(self) -> str:
        """The RAGFlow service base URL."""
        return self._config.base_url

    @property
    def dataset_id(self) -> str:
        """The bound RAGFlow dataset (knowledge base) id."""
        return self._config.dataset_id

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def get_client(self) -> "RAGFlow":
        """Lazily create and cache the RAGFlow client.

        The ``ragflow-sdk`` package is imported here (not at module top
        level) so ``import agentscope`` stays lightweight.

        Returns:
            `ragflow_sdk.RAGFlow`:
                The shared synchronous RAGFlow client.
        """
        if self._client is None:
            from ragflow_sdk import RAGFlow

            self._client = RAGFlow(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
            )
        return self._client

    async def _get_dataset(self) -> "Dataset":
        """Resolve the bound RAGFlow dataset.

        Returns:
            `ragflow_sdk.Dataset`:
                The dataset object matching :attr:`dataset_id`.
        """
        client = self.get_client()
        datasets = await asyncio.to_thread(
            client.list_datasets,
            id=self._config.dataset_id,
        )
        if not datasets:
            raise RuntimeError(
                f"RAGFlow dataset {self._config.dataset_id!r} not found "
                f"at {self._config.base_url!r}.",
            )
        return datasets[0]

    async def _get_document(self, document_id: str) -> "Document":
        """Resolve a single document inside the bound dataset.

        Args:
            document_id (`str`):
                The RAGFlow document id.

        Returns:
            `ragflow_sdk.Document`:
                The document object.

        Raises:
            `RuntimeError`:
                If the document does not exist in the dataset.
        """
        dataset = await self._get_dataset()
        documents = await asyncio.to_thread(
            dataset.list_documents,
            id=document_id,
        )
        if not documents:
            raise RuntimeError(
                f"RAGFlow document {document_id!r} not found in dataset "
                f"{self._config.dataset_id!r}.",
            )
        return documents[0]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        queries: list[str | TextBlock],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        """Search the knowledge base with one or more queries.

        Each query is sent to RAGFlow's ``retrieve`` endpoint, which
        combines vector similarity and (optionally) keyword matching and
        returns chunks already ranked by the server.  Hits across all
        queries are deduplicated by ``(document_id, chunk_index)`` keeping
        the best similarity score, optionally filtered by
        ``score_threshold``, and truncated to ``top_k`` — the same
        normalisation applied by :class:`~agentscope.rag.KnowledgeBase`.

        Unlike :class:`~agentscope.rag.KnowledgeBase`, no embedding model
        is needed: RAGFlow embeds the query server-side with the model
        configured on the dataset.

        Args:
            queries (`list[str | TextBlock]`):
                Query inputs, each a plain ``str`` or a :class:`TextBlock`.
            top_k (`int`, defaults to ``5``):
                Maximum number of results returned across all queries
                (after dedup).  This is the *client-side* cap and is
                independent of :attr:`RAGFlowConfig.top_k`, which controls
                how many candidates RAGFlow considers server-side.
            score_threshold (`float | None`, optional):
                Minimum similarity score for a hit to be retained.
                ``None`` falls back to the server-side
                :attr:`RAGFlowConfig.similarity_threshold`.

        Returns:
            `list[VectorSearchResult]`:
                At most ``top_k`` deduplicated hits ordered by descending
                similarity score.  Empty when there are no queries.
        """
        if not queries:
            return []

        query_texts = [
            query.text if isinstance(query, TextBlock) else query
            for query in queries
        ]

        client = self.get_client()
        page_size = max(top_k, 1)

        results_per_query = await asyncio.gather(
            *(
                asyncio.to_thread(
                    client.retrieve,
                    question=text,
                    dataset_ids=[self._config.dataset_id],
                    similarity_threshold=self._config.similarity_threshold,
                    vector_similarity_weight=(
                        self._config.vector_similarity_weight
                    ),
                    top_k=self._config.top_k,
                    rerank_id=(
                        self._config.rerank_id
                        if self._config.enable_rerank
                        else None
                    ),
                    keyword=self._config.keyword,
                    page_size=page_size,
                )
                for text in query_texts
            ),
        )

        best: dict[tuple[str, int], VectorSearchResult] = {}
        for query_results in results_per_query:
            for index, chunk in enumerate(query_results):
                score = self._extract_score(chunk)
                threshold = (
                    self._config.similarity_threshold
                    if score_threshold is None
                    else score_threshold
                )
                if threshold is not None and score < threshold:
                    continue
                document_id = (
                    getattr(chunk, "dataset_id", None)
                    or self._config.dataset_id
                )
                agent_chunk = self._to_agentscope_chunk(chunk, index)
                key = (document_id, agent_chunk.chunk_index)
                if key not in best or score > best[key].score:
                    best[key] = VectorSearchResult(
                        score=score,
                        document_id=document_id,
                        chunk=agent_chunk,
                    )

        merged = sorted(
            best.values(),
            key=lambda result: result.score,
            reverse=True,
        )
        return merged[:top_k] if top_k > 0 else merged

    @staticmethod
    def _extract_score(chunk: Any) -> float:
        """Extract the similarity score from an SDK ``Chunk``.

        RAGFlow exposes the score under a ``similarity`` attribute on the
        returned chunk objects; the exact attribute may vary across SDK
        versions, so a small set of candidates is tried and ``0.0`` is
        returned when none is present.

        Args:
            chunk (`Any`):
                A ``ragflow_sdk.Chunk`` returned by ``retrieve``.

        Returns:
            `float`: The similarity score, or ``0.0`` if unavailable.
        """
        for attr in ("similarity", "score"):
            value = getattr(chunk, attr, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    break
        return 0.0

    @staticmethod
    def _to_agentscope_chunk(chunk: Any, index: int) -> Chunk:
        """Wrap an SDK ``Chunk`` into an :class:`~agentscope.rag.Chunk`.

        RAGFlow's chunks have no dense ``chunk_index`` / ``total_chunks``
        sequence of their own, so a stable index (the retrieval ordering
        position) is used for the chunk index.  Document and source names
        are taken from the SDK chunk fields when available.

        Args:
            chunk (`Any`):
                A ``ragflow_sdk.Chunk`` returned by ``retrieve``.
            index (`int`):
                The 0-based position of the chunk within this query's
                results — used as the chunk index for dedup.

        Returns:
            `Chunk`: The AgentScope chunk.
        """
        content = getattr(chunk, "content", None) or ""
        source = getattr(chunk, "document_name", None) or ""
        metadata: dict[str, Any] = {}
        chunk_id = getattr(chunk, "id", None)
        if chunk_id:
            metadata["ragflow_chunk_id"] = chunk_id
        return Chunk(
            content=TextBlock(text=str(content)),
            source=source,
            chunk_index=index,
            total_chunks=0,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def insert_document(
        self,
        blob: bytes,
        filename: str,
        document_id: str | None = None,
    ) -> str:
        """Upload a raw document to the bound RAGFlow dataset.

        RAGFlow parses, chunks, and indexes the document on the server
        side, so — unlike
        :meth:`~agentscope.rag.KnowledgeBase.insert_document` — this takes
        raw file bytes rather than pre-embedded ``Chunk`` objects.

        Args:
            blob (`bytes`):
                The raw bytes of the document (PDF, DOCX, TXT, ...).
            filename (`str`):
                The display filename RAGFlow stores the document under.
                RAGFlow infers the parser from its extension.
            document_id (`str | None`, optional):
                An optional caller-supplied RAGFlow document id.  RAGFlow
                assigns its own id on upload, so when ``None``, the id of
                the newly created document is resolved and returned.

        Returns:
            `str`:
                The RAGFlow document id of the uploaded document.
        """
        dataset = await self._get_dataset()
        documents = await asyncio.to_thread(
            dataset.upload_documents,
            [{"display_name": filename, "blob": blob}],
        )
        if not documents:
            raise RuntimeError(
                f"RAGFlow did not return a document after uploading "
                f"{filename!r} to dataset {self._config.dataset_id!r}.",
            )
        document = documents[0]
        return getattr(document, "id", None) or document_id or _generate_id()

    async def delete_document(self, document_id: str) -> None:
        """Remove one document from the bound RAGFlow dataset.

        Args:
            document_id (`str`):
                The RAGFlow document id to delete.
        """
        dataset = await self._get_dataset()
        await asyncio.to_thread(
            dataset.delete_documents,
            ids=[document_id],
        )

    async def list_documents(self) -> list[DocumentSummary]:
        """List all documents in the bound RAGFlow dataset.

        Returns:
            `list[DocumentSummary]`:
                One summary per document in the dataset, in server-defined
                order.
        """
        dataset = await self._get_dataset()
        documents = await asyncio.to_thread(
            dataset.list_documents,
            id=None,
            page=1,
            page_size=30,
        )
        summaries: list[DocumentSummary] = []
        for document in documents:
            document_id = getattr(document, "id", None) or ""
            if not document_id:
                continue
            summaries.append(
                DocumentSummary(
                    document_id=document_id,
                    source=getattr(document, "name", "") or "",
                    chunk_count=int(getattr(document, "chunk_count", 0) or 0),
                    metadata={
                        "parse_progress": getattr(
                            document,
                            "progress",
                            None,
                        ),
                        "run": getattr(document, "run", None),
                        "size": getattr(document, "size", None),
                    },
                ),
            )
        return summaries

    async def list_chunks(
        self,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
    ) -> list[Chunk]:
        """List one document's chunks as indexed by RAGFlow.

        RAGFlow numbers chunks per document from 1; AgentScope numbers
        them from 0, so the returned :attr:`Chunk.chunk_index` is rebased
        accordingly to stay consistent with the rest of the RAG module.

        Args:
            document_id (`str`):
                The RAGFlow document id whose chunks should be listed.
            offset (`int`, defaults to ``0``):
                Number of leading chunks to skip.
            limit (`int`, defaults to ``30``):
                Maximum number of chunks to return.

        Returns:
            `list[Chunk]`:
                At most ``limit`` chunks.
        """
        document = await self._get_document(document_id)
        # RAGFlow pages from 1 and numbers chunks from 1.
        page = offset // limit + 1 if limit > 0 else 1
        chunk_data = await asyncio.to_thread(
            document.list_chunks,
            page=page,
            page_size=limit,
        )
        chunks: list[Chunk] = []
        base_index = (page - 1) * limit
        for index, chunk in enumerate(chunk_data):
            content = getattr(chunk, "content", None) or ""
            chunk_id = getattr(chunk, "id", None)
            chunks.append(
                Chunk(
                    content=TextBlock(text=str(content)),
                    source=getattr(chunk, "document_name", None) or "",
                    chunk_index=base_index + index,
                    total_chunks=0,
                    metadata=(
                        {"ragflow_chunk_id": chunk_id} if chunk_id else {}
                    ),
                ),
            )
        return chunks
