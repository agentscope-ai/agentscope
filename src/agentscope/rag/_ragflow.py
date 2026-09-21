# -*- coding: utf-8 -*-
"""RAGFlow-backed knowledge base using its asynchronous HTTP API.

RAGFlow owns parsing, chunking, embedding, indexing, and retrieval.  It is
therefore integrated beside :class:`KnowledgeBase`, rather than pretending to
be a :class:`VectorStoreBase`.  ``httpx`` is already a core AgentScope
dependency, which keeps this backend usable on every supported Python version
without the synchronous ``ragflow-sdk`` package.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from ._document import Chunk
from ._knowledge import KnowledgeBaseBase
from ._vdb import DocumentSummary, VectorSearchResult
from ..message import DataBlock, TextBlock


class RAGFlowError(RuntimeError):
    """Raised when RAGFlow returns an unsuccessful API response."""

    def __init__(self, message: str, *, code: int | str | None = None) -> None:
        """Initialize the error with RAGFlow's response code, if present."""
        super().__init__(message)
        self.code = code


class RAGFlowConfig(BaseModel):
    """Connection details and retrieval defaults for one RAGFlow dataset."""

    api_key: str
    """RAGFlow API key."""

    base_url: str
    """RAGFlow server URL, for example ``http://localhost:9380``."""

    dataset_id: str
    """Dataset (knowledge base) identifier."""

    similarity_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    """Default server-side minimum similarity."""

    vector_similarity_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    """Weight of vector similarity versus term similarity."""

    knn_top_k: int = Field(default=1024, ge=1)
    """Number of candidates used for vector similarity computation."""

    rerank_id: str | None = None
    """Optional RAGFlow reranker model identifier."""

    keyword: bool = False
    """Whether to enable keyword matching."""

    metadata_condition: dict[str, Any] | None = None
    """Optional native RAGFlow metadata filter."""

    timeout: float = Field(default=30.0, gt=0.0)
    """HTTP request timeout in seconds."""


class RAGFlowKnowledgeBase(KnowledgeBaseBase):
    """Runtime handle for one managed RAGFlow dataset.

    The shared :class:`KnowledgeBaseBase` contract makes this class directly
    usable by :class:`~agentscope.middleware.RAGMiddleware`.  File ingestion
    remains intentionally backend-specific because RAGFlow needs the original
    bytes in order to run its server-side parsing pipeline.
    """

    def __init__(
        self,
        name: str,
        description: str,
        config: RAGFlowConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize a RAGFlow knowledge base.

        Args:
            name (`str`): Agent-facing knowledge base name.
            description (`str`): Agent-facing retrieval description.
            config (`RAGFlowConfig`): Connection and retrieval settings.
            client (`httpx.AsyncClient | None`, optional): Existing async
                client.  Primarily useful for shared connection pools and
                offline ``MockTransport`` tests.  Caller-owned clients are not
                closed by :meth:`aclose`.
        """
        super().__init__(name, description)
        self._config = config
        self._client = client
        self._owns_client = client is None

    @property
    def config(self) -> RAGFlowConfig:
        """The bound connection and retrieval configuration."""
        return self._config

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close a client created by this handle."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally created HTTP client, if any."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Create the shared asynchronous client on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout)
        return self._client

    def _url(self, path: str) -> str:
        """Build an absolute RAGFlow API URL."""
        return f"{self._config.base_url.rstrip('/')}/api/v1/{path.lstrip('/')}"

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """Send one request and unwrap RAGFlow's ``code/data`` envelope."""
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._config.api_key}"
        response = await self._get_client().request(
            method,
            self._url(path),
            headers=headers,
            **kwargs,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RAGFlowError(
                "RAGFlow returned a non-JSON response.",
            ) from exc
        if not isinstance(payload, dict):
            raise RAGFlowError(
                "RAGFlow returned an invalid response envelope.",
            )
        code = payload.get("code")
        if code not in (0, "0"):
            message = payload.get("message") or "RAGFlow request failed."
            raise RAGFlowError(str(message), code=code)
        return payload.get("data")

    async def _retrieve(self, question: str, page_size: int) -> dict[str, Any]:
        """Retrieve one query's result envelope."""
        body: dict[str, Any] = {
            "question": question,
            "dataset_ids": [self._config.dataset_id],
            "page": 1,
            "page_size": page_size,
            "similarity_threshold": self._config.similarity_threshold,
            "vector_similarity_weight": (
                self._config.vector_similarity_weight
            ),
            "knn_top_k": self._config.knn_top_k,
            "keyword": self._config.keyword,
        }
        if self._config.rerank_id:
            body["rerank_id"] = self._config.rerank_id
        if self._config.metadata_condition:
            body["metadata_condition"] = self._config.metadata_condition
        data = await self._request("POST", "retrieval", json=body)
        if not isinstance(data, dict):
            raise RAGFlowError("RAGFlow retrieval returned invalid data.")
        return data

    async def search(
        self,
        queries: list[str | TextBlock | DataBlock],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        """Search the dataset with one or more text queries.

        ``DataBlock`` inputs are ignored because RAGFlow's retrieval endpoint
        accepts text.  Results from all queries are deduplicated by RAGFlow's
        stable chunk id, keeping the highest score.
        """
        if top_k <= 0:
            return []
        query_texts = [
            query.text if isinstance(query, TextBlock) else query
            for query in queries
            if not isinstance(query, DataBlock)
        ]
        if not query_texts:
            return []

        responses = await asyncio.gather(
            *(self._retrieve(text, top_k) for text in query_texts),
        )
        best: dict[tuple[str, str], VectorSearchResult] = {}
        chunk_indexes: dict[tuple[str, str], int] = {}
        next_index: defaultdict[str, int] = defaultdict(int)
        for data in responses:
            chunks = data.get("chunks", [])
            if not isinstance(chunks, list):
                raise RAGFlowError("RAGFlow retrieval chunks are invalid.")
            for raw in chunks:
                if not isinstance(raw, dict):
                    continue
                document_id = str(raw.get("document_id") or "")
                chunk_id = str(raw.get("id") or "")
                if not document_id or not chunk_id:
                    continue
                score = float(raw.get("similarity") or 0.0)
                if score_threshold is not None and score < score_threshold:
                    continue
                key = (document_id, chunk_id)
                if key not in chunk_indexes:
                    chunk_indexes[key] = next_index[document_id]
                    next_index[document_id] += 1
                metadata = {
                    "ragflow_chunk_id": chunk_id,
                    **{
                        field: raw[field]
                        for field in (
                            "vector_similarity",
                            "term_similarity",
                            "highlight",
                            "positions",
                            "important_keywords",
                        )
                        if field in raw
                    },
                }
                result = VectorSearchResult(
                    score=score,
                    document_id=document_id,
                    chunk=Chunk(
                        content=TextBlock(text=str(raw.get("content") or "")),
                        source=str(
                            raw.get("document_keyword")
                            or raw.get("docnm_kwd")
                            or "",
                        ),
                        chunk_index=chunk_indexes[key],
                        total_chunks=0,
                        metadata=metadata,
                    ),
                )
                if key not in best or result.score > best[key].score:
                    best[key] = result
        return sorted(
            best.values(),
            key=lambda result: result.score,
            reverse=True,
        )[:top_k]

    async def insert_document(
        self,
        blob: bytes,
        filename: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Upload a source file and start RAGFlow's asynchronous parser.

        The returned document is not necessarily searchable yet.  Poll
        :meth:`list_documents` until its ``metadata['run']`` becomes
        ``'DONE'`` before requiring read-after-write retrieval.
        """
        dataset_id = quote(self._config.dataset_id, safe="")
        data = await self._request(
            "POST",
            f"datasets/{dataset_id}/documents",
            files={"file": (filename, blob, "application/octet-stream")},
        )
        if not isinstance(data, list) or not data:
            raise RAGFlowError("RAGFlow did not return the uploaded document.")
        document = data[0]
        if not isinstance(document, dict) or not document.get("id"):
            raise RAGFlowError(
                "RAGFlow returned an invalid uploaded document.",
            )
        document_id = str(document["id"])
        escaped_document_id = quote(document_id, safe="")
        if metadata:
            await self._request(
                "PATCH",
                f"datasets/{dataset_id}/documents/{escaped_document_id}",
                json={"meta_fields": metadata},
            )
        await self._request(
            "POST",
            f"datasets/{dataset_id}/chunks",
            json={"document_ids": [document_id]},
        )
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Delete one document from the RAGFlow dataset."""
        dataset_id = quote(self._config.dataset_id, safe="")
        await self._request(
            "DELETE",
            f"datasets/{dataset_id}/documents",
            json={"ids": [document_id]},
        )

    async def list_documents(self) -> list[DocumentSummary]:
        """List every document and expose RAGFlow's processing status."""
        dataset_id = quote(self._config.dataset_id, safe="")
        page = 1
        page_size = 100
        raw_documents: list[dict[str, Any]] = []
        while True:
            data = await self._request(
                "GET",
                f"datasets/{dataset_id}/documents",
                params={"page": page, "page_size": page_size},
            )
            if not isinstance(data, dict) or not isinstance(
                data.get("docs"),
                list,
            ):
                raise RAGFlowError("RAGFlow document listing is invalid.")
            batch = [item for item in data["docs"] if isinstance(item, dict)]
            raw_documents.extend(batch)
            if len(batch) < page_size:
                break
            page += 1

        return [
            DocumentSummary(
                document_id=str(document.get("id") or ""),
                source=str(
                    document.get("name") or document.get("location") or "",
                ),
                chunk_count=int(document.get("chunk_count") or 0),
                metadata={
                    field: document[field]
                    for field in (
                        "run",
                        "progress",
                        "progress_msg",
                        "size",
                        "token_count",
                        "meta_fields",
                    )
                    if field in document
                },
            )
            for document in raw_documents
            if document.get("id")
        ]

    async def list_chunks(
        self,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
    ) -> list[Chunk]:
        """List a page window of one document's RAGFlow chunks."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit <= 0:
            return []
        dataset_id = quote(self._config.dataset_id, safe="")
        escaped_document_id = quote(document_id, safe="")
        target = offset + limit
        page = 1
        page_size = min(100, target)
        raw_chunks: list[dict[str, Any]] = []
        total = 0
        source = ""
        while len(raw_chunks) < target:
            data = await self._request(
                "GET",
                (
                    f"datasets/{dataset_id}/documents/"
                    f"{escaped_document_id}/chunks"
                ),
                params={"page": page, "page_size": page_size},
            )
            if not isinstance(data, dict) or not isinstance(
                data.get("chunks"),
                list,
            ):
                raise RAGFlowError("RAGFlow chunk listing is invalid.")
            batch = [item for item in data["chunks"] if isinstance(item, dict)]
            raw_chunks.extend(batch)
            total = int(data.get("total") or len(raw_chunks))
            document = data.get("doc")
            if isinstance(document, dict):
                source = str(
                    document.get("name") or document.get("location") or source,
                )
            if len(batch) < page_size or len(raw_chunks) >= total:
                break
            page += 1

        return [
            Chunk(
                content=TextBlock(text=str(raw.get("content") or "")),
                source=str(raw.get("docnm_kwd") or source),
                chunk_index=index,
                total_chunks=total,
                metadata={
                    "ragflow_chunk_id": str(raw.get("id") or ""),
                    **{
                        field: raw[field]
                        for field in (
                            "available",
                            "positions",
                            "important_keywords",
                        )
                        if field in raw
                    },
                },
            )
            for index, raw in enumerate(
                raw_chunks[offset:target],
                start=offset,
            )
        ]
