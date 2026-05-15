# -*- coding: utf-8 -*-
"""The OpenAI text embedding model class."""
from datetime import datetime
from typing import Any, List

from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._cache_base import EmbeddingCacheBase
from ._embedding_base import EmbeddingModelBase
from ..message import TextBlock


class OpenAITextEmbedding(EmbeddingModelBase):
    """OpenAI text embedding model class."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        dimensions: int = 1024,
        embedding_cache: EmbeddingCacheBase | None = None,
        batch_size: int = 2048,
        **kwargs: Any,
    ) -> None:
        """Initialize the OpenAI text embedding model class.

        Args:
            api_key (`str`):
                The OpenAI API key.
            model_name (`str`):
                The name of the embedding model.
            dimensions (`int`, defaults to 1024):
                The dimension of the embedding vector.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
            batch_size (`int`, defaults to 2048):
                The maximum number of texts to embed in a single API call.
        """
        import openai

        super().__init__(model_name, dimensions)

        self.client = openai.AsyncClient(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache
        self.batch_size = batch_size

    async def __call__(
        self,
        text: List[str | TextBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the OpenAI embedding API.

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

        kwargs = {
            "input": gather_text,
            "model": self.model_name,
            "dimensions": self.dimensions,
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
        all_embeddings = []
        total_tokens = 0

        for i in range(0, len(gather_text), self.batch_size):
            chunk = gather_text[i : i + self.batch_size]
            chunk_kwargs = kwargs.copy()
            chunk_kwargs["input"] = chunk
            response = await self.client.embeddings.create(**chunk_kwargs)
            all_embeddings.extend([_.embedding for _ in response.data])
            total_tokens += response.usage.total_tokens

        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=all_embeddings,
            )

        return EmbeddingResponse(
            embeddings=all_embeddings,
            usage=EmbeddingUsage(
                tokens=total_tokens,
                time=time,
            ),
        )
