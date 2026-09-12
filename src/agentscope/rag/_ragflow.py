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
rather than beneath :class:`~agentscope.rag.VectorStoreBase`.

:class:`RAGFlowKnowledge` exposes the same *purpose-built operations* as
:class:`~agentscope.rag.KnowledgeBase` — :meth:`search`,
:meth:`insert_document`, :meth:`delete_document`, :meth:`list_documents`,
:meth:`list_chunks` — so callers consult and manage a knowledge base
through the same method names while the heavy lifting is delegated to the
RAGFlow service.  The method signatures are *not* fully interchangeable:
``search`` accepts only text queries (RAGFlow embeds them server-side, so
no embedding model or ``DataBlock`` inputs are involved), and
``insert_document`` takes raw document bytes rather than pre-embedded
``Chunk`` objects.

The deliberate divergence in how a document is added follows from
the same root cause.  ``KnowledgeBase.insert_document`` takes pre-embedded
``Chunk`` objects because AgentScope runs the parsing and chunking
locally.  RAGFlow runs those steps on the server, so
:meth:`RAGFlowKnowledge.insert_document` accepts raw document bytes plus a
filename and lets RAGFlow parse, chunk, and index them.

.. note:: The ``ragflow-sdk`` package is required.  Install it with
    ``pip install "agentscope[vdb-ragflow]"``.  It is imported lazily so
    importing :mod:`agentscope` stays lightweight.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel

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
        queries are deduplicated by the RAGFlow chunk id (kept in
        ``chunk.metadata["ragflow_chunk_id"]``) inside the same
        ``document_id``, keeping the best similarity score, optionally
        filtered by ``score_threshold``, and truncated to ``top_k``.

        Unlike :class:`~agentscope.rag.KnowledgeBase`, no embedding model
        is needed: RAGFlow embeds the query server-side with the model
        configured on the dataset.  RAGFlow reports neither the 0-based
        index nor the total chunk count of a chunk *within* its source
        document, so ``Chunk.chunk_index`` is a per-document ordinal of
        the retrieved hits and ``Chunk.total_chunks`` is ``0``.

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

        # The same underlying chunk can surface from more than one query
        # (and / or under repeated chunks when RAGFlow splits a document),
        # so it is deduplicated by its stable ``ragflow_chunk_id``.  The
        # best (highest-similarity) representation of each chunk is kept.
        best: dict[tuple[str, str], VectorSearchResult] = {}
        # Per-document 0-based ordinal, so ``Chunk.chunk_index`` stays a
        # meaningful index within its source document rather than a rank.
        document_offsets: dict[str, int] = defaultdict(int)
        for query_results in results_per_query:
            for chunk in query_results:
                score = chunk.similarity
                threshold = (
                    self._config.similarity_threshold
                    if score_threshold is None
                    else score_threshold
                )
                if threshold is not None and score < threshold:
                    continue
                document_id = chunk.document_id
                # RAGFlow always identifies the source document of a hit;
                # without it the chunk cannot be cited or deleted, so drop it.
                if not document_id:
                    continue
                chunk_data = chunk.id
                # ``document_offsets`` keeps a running 0, 1, 2, ... ordinal
                # per document so ``chunk_index`` is a position within its
                # document rather than a retrieval rank.
                chunk_index = document_offsets[document_id]
                document_offsets[document_id] += 1
                agent_chunk = Chunk(
                    content=TextBlock(text=str(chunk.content)),
                    source=chunk.document_name,
                    chunk_index=chunk_index,
                    total_chunks=0,  # RAGFlow does not report a total
                    metadata={"ragflow_chunk_id": chunk_data},
                )
                key = (document_id, chunk_data)
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

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def insert_document(self, blob: bytes, filename: str) -> str:
        """Upload a raw document to the bound RAGFlow dataset.

        RAGFlow parses, chunks, and indexes the document on the server
        side, so — unlike
        :meth:`~agentscope.rag.KnowledgeBase.insert_document` — this takes
        raw file bytes rather than pre-embedded ``Chunk`` objects.

        .. note:: Indexing is **asynchronous**.  This method uploads the
            document and asks RAGFlow to parse it (via
            ``async_parse_documents``), then returns immediately; the
            document becomes searchable only after RAGFlow finishes
            parsing, which may lag behind this call.  Poll
            :meth:`list_documents` (e.g. on ``parse_progress`` / ``run``)
            to wait for readiness before searching.

        Args:
            blob (`bytes`):
                The raw bytes of the document (PDF, DOCX, TXT, ...).
            filename (`str`):
                The display filename RAGFlow stores the document under.
                RAGFlow infers the parser from its extension.

        Returns:
            `str`:
                The RAGFlow document id assigned to the uploaded document.
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
        await asyncio.to_thread(
            dataset.async_parse_documents,
            [document.id],
        )
        return document.id

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
            summaries.append(
                DocumentSummary(
                    document_id=document.id,
                    source=document.name,
                    chunk_count=document.chunk_count,
                    metadata={
                        "parse_progress": document.progress,
                        "run": document.run,
                        "size": document.size,
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

        RAGFlow does not expose a dense ``chunk_index``/``total_chunks``
        sequence, so ``chunk_index`` is the 0-based ordinal of the chunk
        within the pages returned so far and ``total_chunks`` is ``0``.
        The RAGFlow chunk id is preserved in
        ``metadata["ragflow_chunk_id"]``.

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
        # RAGFlow pages from 1.
        page = offset // limit + 1 if limit > 0 else 1
        chunk_data = await asyncio.to_thread(
            document.list_chunks,
            page=page,
            page_size=limit,
        )
        chunks: list[Chunk] = []
        base_index = (page - 1) * limit
        for index, chunk in enumerate(chunk_data):
            chunks.append(
                Chunk(
                    content=TextBlock(text=str(chunk.content)),
                    source=chunk.document_name,
                    chunk_index=base_index + index,
                    total_chunks=0,
                    metadata={"ragflow_chunk_id": chunk.id},
                ),
            )
        return chunks
