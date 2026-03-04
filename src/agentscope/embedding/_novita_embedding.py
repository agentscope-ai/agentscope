# -*- coding: utf-8 -*-
"""The Novita text embedding model class."""
from typing import (
    Any,
    List,
)

from ._openai_embedding import OpenAITextEmbedding
from ._cache_base import EmbeddingCacheBase


class NovitaTextEmbedding(OpenAITextEmbedding):
    """Novita text embedding model class."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        dimensions: int = 1024,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Novita text embedding model class.

        Args:
            model_name (`str`):
                The name of the embedding model.
            api_key (`str`, optional):
                The Novita API key. If not specified, it will
                be read from the environment variable `NOVITA_API_KEY` or
                `OPENAI_API_KEY`.
            dimensions (`int`, defaults to 1024):
                The dimension of the embedding vector.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to `None`):
                The embedding cache class instance.
            **kwargs (`Any`):
                Additional keyword arguments.
        """
        import os

        if api_key is None:
            api_key = os.environ.get("NOVITA_API_KEY") or os.environ.get(
                "OPENAI_API_KEY",
            )

        if "base_url" not in kwargs:
            kwargs["base_url"] = "https://api.novita.ai/openai"

        super().__init__(
            api_key=api_key,
            model_name=model_name,
            dimensions=dimensions,
            embedding_cache=embedding_cache,
            **kwargs,
        )
