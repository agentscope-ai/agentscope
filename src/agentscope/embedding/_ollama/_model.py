# -*- coding: utf-8 -*-
"""The Ollama embedding model."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .._embedding_response import EmbeddingResponse
from .._embedding_usage import EmbeddingUsage
from .._cache_base import EmbeddingCacheBase
from .._embedding_base import EmbeddingModelBase
from ...credential import CredentialBase


class OllamaEmbeddingModel(EmbeddingModelBase[str]):
    """Ollama text embedding model.

    Wraps locally-hosted embedding models served by Ollama (e.g.
    ``nomic-embed-text``, ``mxbai-embed-large``).  Inherits batching
    and retry logic from :class:`EmbeddingModelBase`.
    """

    _TEXT_BATCH_SIZE: int = 512

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: "OllamaEmbeddingModel.Parameters | None" = None,
        embedding_cache: EmbeddingCacheBase | None = None,
        context_size: int = 8192,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize the Ollama embedding model.

        Args:
            credential (`CredentialBase`):
                An :class:`~agentscope.credential.OllamaCredential`
                instance providing the host URL.
            model (`str`):
                The embedding model name (e.g.
                ``"nomic-embed-text"``).
            parameters (`OllamaEmbeddingModel.Parameters | None`, \
            defaults to ``None``):
                User-configurable parameters (currently only
                ``dimensions``).
            embedding_cache (`EmbeddingCacheBase | None`, defaults to \
            ``None``):
                Optional embedding cache.
            context_size (`int`, defaults to ``8192``):
                Maximum input tokens per text.
            max_retries (`int`, defaults to ``3``):
                Number of retries on transient failures.
            retry_delay (`float`, defaults to ``1.0``):
                Seconds between retry attempts.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            context_size=context_size,
            batch_size=self._TEXT_BATCH_SIZE,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self.host: str | None = getattr(credential, "host", None)
        self.embedding_cache: EmbeddingCacheBase | None = embedding_cache

    async def _call_api(
        self,
        inputs: list[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the Ollama embedding API for a single batch.

        Args:
            inputs (`list[str]`):
                A batch of texts to embed.
            **kwargs:
                Extra keyword arguments forwarded to the Ollama API.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        api_kwargs: dict[str, Any] = {
            "input": inputs,
            "model": self.model,
            "dimensions": self.dimensions,
            **kwargs,
        }

        if self.embedding_cache:
            cached = await self.embedding_cache.retrieve(
                identifier=api_kwargs,
            )
            if cached:
                return EmbeddingResponse(
                    embeddings=cached,
                    usage=EmbeddingUsage(tokens=0, time=0),
                    source="cache",
                )

        import ollama

        client = ollama.AsyncClient(host=self.host)

        start_time = datetime.now()
        response = await client.embed(**api_kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=api_kwargs,
                embeddings=response.embeddings,
            )

        return EmbeddingResponse(
            embeddings=response.embeddings,
            usage=EmbeddingUsage(time=time),
        )
