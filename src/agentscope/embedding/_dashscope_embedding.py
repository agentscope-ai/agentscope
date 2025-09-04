# -*- coding: utf-8 -*-
"""The dashscope embedding module in agentscope."""
from datetime import datetime
from typing import Any, List

from ._cache_base import EmbeddingCacheBase
from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._embedding_base import EmbeddingModelBase


class DashScopeTextEmbedding(EmbeddingModelBase):
    """DashScope text embedding model class"""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        embedding_cache: EmbeddingCacheBase | None = None,
    ) -> None:
        """Initialize the DashScope text embedding model class.

        Args:
            api_key (`str`):
                The dashscope API key.
            model_name (`str`):
                The name of the embedding model.
            embedding_cache (`EmbeddingCacheBase`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        super().__init__(model_name)

        self.api_key = api_key
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope embedding API.

        Args:
            text (`List[str]`):
                The input text to be embedded. It can be a list of strings.
        """

        kwargs = {
            "input": text,
            "model": self.model_name,
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

        import dashscope

        start_time = datetime.now()
        response = dashscope.embeddings.TextEmbedding.call(
            api_key=self.api_key,
            **kwargs,
        )
        time = (datetime.now() - start_time).total_seconds()

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to get embedding from DashScope API: {response}",
            )

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=[
                    _["embedding"] for _ in response.output["embeddings"]
                ],
            )

        embedding_response = EmbeddingResponse(
            embeddings=[_["embedding"] for _ in response.output["embeddings"]],
            usage=EmbeddingUsage(
                tokens=response.usage["total_tokens"],
                time=time,
            ),
        )

        return embedding_response
