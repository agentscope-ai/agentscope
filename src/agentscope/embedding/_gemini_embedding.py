# -*- coding: utf-8 -*-
"""The gemini text embedding model class."""
from datetime import datetime
from typing import Any, List

from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._cache_base import EmbeddingCacheBase
from ._embedding_base import EmbeddingModelBase


class GeminiTextEmbedding(EmbeddingModelBase):
    """The Gemini text embedding model."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Gemini text embedding model class.

        Args:
            api_key (`str`):
                The Gemini API key.
            model_name (`str`):
                The name of the embedding model.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        from google import genai

        super().__init__(model_name)

        self.client = genai.Client(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """The Gemini embedding API call.

        Args:
            text (`List[str]`):
                The input text to be embedded. It can be a list of strings.
        """
        kwargs = {
            "model": self.model_name,
            "contents": text,
            "config": kwargs,
        }

        if self.embedding_cache:
            cached_embeddings = await self.embedding_cache.retrieve(
                identifier=kwargs,
            )
            if cached_embeddings:
                return EmbeddingResponse(
                    embeddings=cached_embeddings,
                    usage=EmbeddingUsage(
                        tokens=0,
                        time=0,
                    ),
                    source="cache",
                )

        start_time = datetime.now()
        response = self.client.models.embed_content(**kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=[_.values for _ in response.embeddings],
            )

        return EmbeddingResponse(
            embeddings=[_.values for _ in response.embeddings],
            usage=EmbeddingUsage(
                time=time,
            ),
        )
