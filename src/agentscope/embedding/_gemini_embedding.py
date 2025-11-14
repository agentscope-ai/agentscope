# -*- coding: utf-8 -*-
"""The gemini text embedding model class."""
from datetime import datetime
from typing import Any, List

from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._cache_base import EmbeddingCacheBase
from ._embedding_base import EmbeddingModelBase
from ..message import TextBlock


class GeminiTextEmbedding(EmbeddingModelBase):
    """The Gemini text embedding model."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        dimensions: int = 3072,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Gemini text embedding model class.

        Args:
            api_key (`str`):
                The Gemini API key.
            model_name (`str`):
                The name of the embedding model.
            dimensions (`int`, defaults to 3072):
                The dimension of the embedding vector, refer to the
                `official documentation
                <https://ai.google.dev/gemini-api/docs/embeddings?hl=zh-cn#control-embedding-size>`_
                for more details.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        from google import genai

        super().__init__(model_name, dimensions)

        self.client = genai.Client(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str | TextBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """The Gemini embedding API call.

        Args:
            text (`List[str | TextBlock]`):
                The input text to be embedded. It can be a list of strings.
        """
        gather_text = []
        for _ in text:
            if isinstance(_, dict) and "text" in _:
                gather_text.append(_["text"])
            elif isinstance(_, str):
                gather_text.append(_)
            else:
                raise ValueError(
                    "Input text must be a list of strings or TextBlock dicts.",
                )

        # Gemini API has a limit of 100 requests per batch
        batch_size = 100
        all_embeddings = []
        total_time = 0

        # Process in batches
        for i in range(0, len(gather_text), batch_size):
            batch_text = gather_text[i:i + batch_size]

            batch_kwargs = {
                "model": self.model_name,
                "contents": batch_text,
                "config": kwargs,
            }

            if self.embedding_cache:
                cached_embeddings = await self.embedding_cache.retrieve(
                    identifier=batch_kwargs,
                )
                if cached_embeddings:
                    all_embeddings.extend(cached_embeddings)
                    continue

            start_time = datetime.now()
            response = self.client.models.embed_content(**batch_kwargs)
            batch_time = (datetime.now() - start_time).total_seconds()
            total_time += batch_time

            batch_embeddings = [_.values for _ in response.embeddings]
            all_embeddings.extend(batch_embeddings)

            if self.embedding_cache:
                await self.embedding_cache.store(
                    identifier=batch_kwargs,
                    embeddings=batch_embeddings,
                )

        return EmbeddingResponse(
            embeddings=all_embeddings,
            usage=EmbeddingUsage(
                time=total_time,
            ),
        )
