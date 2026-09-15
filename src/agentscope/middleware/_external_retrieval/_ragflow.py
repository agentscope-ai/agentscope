# -*- coding: utf-8 -*-
"""RAGFlow retrieval backend for ExternalRetrievalMiddleware."""

import logging
from typing import Any

import httpx

from ._backend import RetrievalResult

logger = logging.getLogger(__name__)


class RAGFlowRetrievalBackend:
    """Backend that calls RAGFlow's /api/v1/retrieval endpoint.

    RAGFlow is a full-featured RAG engine (parsing, chunking, hybrid
    retrieval, reranking).  This backend normalizes its REST API
    responses into :class:`RetrievalResult` items.

    .. code-block:: python

        backend = RAGFlowRetrievalBackend(
            base_url="http://localhost:9380",
            api_key="ragflow-xxx",
            dataset_ids=["your-dataset-id"],
        )
        results = await backend.search("How to deploy docker?", top_k=5)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        dataset_ids: list[str],
        timeout: float = 30.0,
    ) -> None:
        """Initialize the RAGFlow backend.

        Args:
            base_url (`str`):
                RAGFlow server base URL, e.g. ``http://localhost:9380``.
            api_key (`str`):
                RAGFlow API key (from "User Settings -> API Key").
            dataset_ids (`list[str]`):
                The RAGFlow dataset(s) to search across.
            timeout (`float`, defaults to ``30.0``):
                HTTP request timeout in seconds.
        """
        self._dataset_ids = dataset_ids
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        similarity_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[RetrievalResult]:
        """Search RAGFlow's retrieval API.

        Args:
            query (`str`):
                Natural-language query string.
            top_k (`int`, defaults to ``5``):
                Maximum chunks to return.
            similarity_threshold (`float | None`, optional):
                Minimum similarity score, 0.0 - 1.0.
            **kwargs (`Any`):
                Ignored; kept for protocol compatibility.

        Returns:
            `list[RetrievalResult]`:
                Normalized results, ordered by descending similarity.
                Empty list on failure or no matches.
        """
        payload = {
            "question": query,
            "dataset_ids": self._dataset_ids,
            "top_k": top_k,
        }
        if similarity_threshold is not None:
            payload["similarity_threshold"] = similarity_threshold

        try:
            resp = await self._client.post("/api/v1/retrieval", json=payload)
            resp.raise_for_status()
            body = resp.json()
        except Exception:
            logger.exception("RAGFlow retrieval request failed.")
            return []

        if body.get("code") != 0:
            logger.warning(
                "RAGFlow retrieval returned error: %s",
                body.get("message", "unknown error"),
            )
            return []

        results = []
        for chunk in body.get("data", {}).get("chunks", []):
            source = (
                chunk.get("document_keyword")
                or chunk.get("document_name")
                or chunk.get("doc_name", "")
            )
            results.append(
                RetrievalResult(
                    content=chunk.get("content", ""),
                    source=source,
                    score=round(chunk.get("similarity", 0.0), 4),
                    metadata={
                        "document_id": chunk.get("document_id", ""),
                        "chunk_id": chunk.get("id", ""),
                    },
                ),
            )

        # Sort by descending score and truncate
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
