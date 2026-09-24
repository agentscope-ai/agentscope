# -*- coding: utf-8 -*-
"""Protocol and data structures for external retrieval backends."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class RetrievalResult:
    """Normalized retrieval result across all external backends.

    Attributes:
        content (`str`):
            The retrieved text chunk.
        source (`str`):
            Document name or identifier for citation.
        score (`float`):
            Similarity/relevance score from the backend.
        metadata (`dict[str, Any]`):
            Backend-specific extra fields (e.g. page numbers, URLs).
    """

    content: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RetrievalBackend(Protocol):
    """Protocol for external retrieval service backends.

    Implementations connect to managed RAG services (RAGFlow, Dify,
    FastGPT, etc.) and normalize their responses into
    :class:`RetrievalResult` items.

    The backend is responsible for:
    - HTTP client lifecycle (connection pooling, auth headers)
    - Service-specific request/response formatting
    - Error handling (return empty list on failure, log warnings)
    """

    async def search(
        self,
        query: str,
        top_k: int = 5,
        **kwargs: Any,
    ) -> list[RetrievalResult]:
        """Search the external service.

        Args:
            query (`str`):
                Natural-language query string.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            **kwargs (`Any`):
                Backend-specific options (e.g. similarity_threshold).

        Returns:
            `list[RetrievalResult]`:
                Normalized results, ordered by descending score.
                Empty list on failure or no matches.
        """
        ...
