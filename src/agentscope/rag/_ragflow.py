# -*- coding: utf-8 -*-
"""RAGFlow-backed knowledge base using its asynchronous HTTP API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field, SecretStr

from ._document import Chunk
from ._knowledge import KnowledgeBaseBase
from ._vdb import DocumentSummary, VectorSearchResult
from ..message import DataBlock, TextBlock


class RAGFlowConfig(BaseModel):
    """Connection details and retrieval defaults for one RAGFlow dataset."""

    api_key: SecretStr
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
    """Runtime handle for one managed RAGFlow dataset."""

    def __init__(
        self,
        name: str,
        description: str,
        config: RAGFlowConfig,
    ) -> None:
        """Initialize a RAGFlow knowledge base."""
        super().__init__(name, description)
        self._config = config
        self._client = httpx.AsyncClient(timeout=config.timeout)

    @property
    def config(self) -> RAGFlowConfig:
        """The bound connection and retrieval configuration."""
        return self._config

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

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
        api_key = self._config.api_key.get_secret_value()
        headers["Authorization"] = f"Bearer {api_key}"
        response = await self._client.request(
            method,
            self._url(path),
            headers=headers,
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
        if payload["code"] != 0:
            raise RuntimeError(
                payload.get("message", "RAGFlow request failed."),
            )
        return payload["data"]

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
        body["highlight"] = True
        return await self._request("POST", "retrieval", json=body)

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
        best: dict[str, VectorSearchResult] = {}
        for data in responses:
            for raw in data["chunks"]:
                document_id = str(raw["document_id"])
                chunk_id = str(raw["id"])
                score = float(raw["similarity"])
                if score_threshold is not None and score < score_threshold:
                    continue
                key = chunk_id
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
                        content=TextBlock(text=str(raw["content"])),
                        source=str(raw["document_keyword"]),
                        # RAGFlow retrieval does not expose the source
                        # position.
                        chunk_index=0,
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

    async def upload_document(
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
        data = await self._request(
            "POST",
            f"datasets/{self._config.dataset_id}/documents",
            files={"file": (filename, blob, "application/octet-stream")},
        )
        document = data[0]
        document_id = str(document["id"])
        if metadata:
            await self._request(
                "PATCH",
                (
                    f"datasets/{self._config.dataset_id}/documents/"
                    f"{document_id}"
                ),
                json={"meta_fields": metadata},
            )
        await self._request(
            "POST",
            f"datasets/{self._config.dataset_id}/chunks",
            json={"document_ids": [document_id]},
        )
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Delete one document from the RAGFlow dataset."""
        await self._request(
            "DELETE",
            f"datasets/{self._config.dataset_id}/documents",
            json={"ids": [document_id]},
        )

    async def list_documents(self) -> list[DocumentSummary]:
        """List every document and expose RAGFlow's processing status."""
        page = 1
        page_size = 100
        raw_documents: list[dict[str, Any]] = []
        while True:
            data = await self._request(
                "GET",
                f"datasets/{self._config.dataset_id}/documents",
                params={"page": page, "page_size": page_size},
            )
            batch = data["docs"]
            raw_documents.extend(batch)
            if len(batch) < page_size:
                break
            page += 1

        return [
            DocumentSummary(
                document_id=str(document["id"]),
                source=str(document["name"]),
                chunk_count=int(document["chunk_count"]),
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
        target = offset + limit
        data = await self._request(
            "GET",
            (
                f"datasets/{self._config.dataset_id}/documents/"
                f"{document_id}/chunks"
            ),
            params={"page": 1, "page_size": target},
        )
        total = int(data["total"])

        return [
            Chunk(
                content=TextBlock(text=str(raw["content"])),
                source=str(raw["docnm_kwd"]),
                chunk_index=index,
                total_chunks=total,
                metadata={
                    "ragflow_chunk_id": str(raw["id"]),
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
                data["chunks"][offset:target],
                start=offset,
            )
        ]
