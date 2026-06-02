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
        pass_dimensions: bool = True,
        embedding_cache: EmbeddingCacheBase | None = None,
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
            pass_dimensions (`bool`, defaults to `True`):
                Whether to pass the ``dimensions`` parameter to the API.
                Some OpenAI-compatible providers do not support it.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.

        # TODO: handle batch size limit and token limit
        """
        import openai

        super().__init__(model_name, dimensions)

        self.pass_dimensions = pass_dimensions
        self.client = openai.AsyncClient(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

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
            "encoding_format": "float",
            **kwargs,
        }
        if self.pass_dimensions:
            kwargs["dimensions"] = self.dimensions

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

        # Map results back by index; fall back to dense_embedding
        embeddings: list = [None] * len(gather_text)
        for emb in response.data:
            if 0 <= emb.index < len(gather_text):
                embeddings[emb.index] = emb.embedding or getattr(
                    emb,
                    "dense_embedding",
                )

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=embeddings,
            )

        return EmbeddingResponse(
            embeddings=embeddings,
            usage=EmbeddingUsage(
                tokens=response.usage.total_tokens,
                time=time,
            ),
        )
