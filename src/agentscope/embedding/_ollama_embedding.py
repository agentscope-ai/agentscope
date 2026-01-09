# -*- coding: utf-8 -*-
"""The ollama text embedding model class."""
from datetime import datetime
from typing import List, Any

from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._cache_base import EmbeddingCacheBase
from ..embedding import EmbeddingModelBase
from ..message import TextBlock


class OllamaTextEmbedding(EmbeddingModelBase):
    """The Ollama embedding model."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(
        self,
        model_name: str,
        dimensions: int,
        host: str | None = None,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Ollama text embedding model class.

        Args:
            model_name (`str`):
                The name of the embedding model.
            dimensions (`int`):
                The dimension of the embedding vector, the parameter should be
                provided according to the model used.
            host (`str | None`, defaults to `None`):
                The host URL for the Ollama API.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        import ollama

        super().__init__(model_name, dimensions)

        self.client = ollama.AsyncClient(host=host, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str | TextBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the Ollama embedding API.

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
        response = await self.client.embed(**kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=response.embeddings,
            )

        return EmbeddingResponse(
            embeddings=response.embeddings,
            usage=EmbeddingUsage(
                time=time,
            ),
        )
