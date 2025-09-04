# -*- coding: utf-8 -*-
"""The OpenAI text embedding model class."""
from datetime import datetime
from typing import Any, List

from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._cache_base import EmbeddingCacheBase
from ._embedding_base import EmbeddingModelBase


class OpenAITextEmbedding(EmbeddingModelBase):
    """OpenAI text embedding model class."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the OpenAI text embedding model class.

        Args:
            api_key (`str`):
                The OpenAI API key.
            model_name (`str`):
                The name of the embedding model.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        import openai

        super().__init__(model_name)

        self.client = openai.AsyncClient(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the OpenAI embedding API.

        Args:
            text (`List[str]`):
                The input text to be embedded. It can be a list of strings.
        """

        kwargs = {
            "input": text,
            "model": self.model_name,
            "encoding_format": "float",
            **kwargs,
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
        response = await self.client.embeddings.create(**kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=[_.embedding for _ in response.data],
            )

        return EmbeddingResponse(
            embeddings=[_.embedding for _ in response.data],
            usage=EmbeddingUsage(
                tokens=response.usage.total_tokens,
                time=time,
            ),
        )
