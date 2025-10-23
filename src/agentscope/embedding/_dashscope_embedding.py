# -*- coding: utf-8 -*-
"""The dashscope embedding module in agentscope."""
from datetime import datetime
from typing import Any, List, Literal

from ._cache_base import EmbeddingCacheBase
from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._embedding_base import EmbeddingModelBase
from .._logging import logger
from ..message import TextBlock


class DashScopeTextEmbedding(EmbeddingModelBase):
    """DashScope text embedding API class.

    .. note:: From the `official documentation
    <https://bailian.console.aliyun.com/?tab=api#/api/?type=model&url=2712515>`_:

     - The max batch size that DashScope text embedding API
     supports is 10 for `text-embedding-v4` and `text-embedding-v3` models, and
     25 for `text-embedding-v2` and `text-embedding-v1` models.
     - The max token limit for a single input is 8192 tokens for `v4` and `v3`
     models, and 2048 tokens for `v2` and `v1` models.

    """

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        dimensions: int = 1024,
        embedding_cache: EmbeddingCacheBase | None = None,
    ) -> None:
        """Initialize the DashScope text embedding model class.

        Args:
            api_key (`str`):
                The dashscope API key.
            model_name (`str`):
                The name of the embedding model.
            dimensions (`int`, defaults to 1024):
                The dimension of the embedding vector, refer to the
                `official documentation
                <https://bailian.console.aliyun.com/?tab=api#/api/?type=model&url=2712515>`_
                for more details.
            embedding_cache (`EmbeddingCacheBase`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        super().__init__(model_name, dimensions)

        self.api_key = api_key
        self.embedding_cache = embedding_cache
        self.batch_size_limit = 10

    async def _call_api(self, kwargs: dict[str, Any]) -> EmbeddingResponse:
        """Call the DashScope embedding API by the given keyword arguments."""

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

        return EmbeddingResponse(
            embeddings=[_["embedding"] for _ in response.output["embeddings"]],
            usage=EmbeddingUsage(
                tokens=response.usage["total_tokens"],
                time=time,
            ),
        )

    async def __call__(
        self,
        text: List[str | TextBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope embedding API.

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

        if len(gather_text) > self.batch_size_limit:
            logger.info(
                "The input texts (%d) will be embedded with %d API calls due "
                f"to the batch size limit of {self.batch_size_limit} for "
                f"DashScope embedding API.",
                len(gather_text),
                (len(gather_text) + self.batch_size_limit - 1)
                // self.batch_size_limit,
            )

        # Handle the batch size limit for DashScope embedding API
        collected_embeddings = []
        collected_time = 0.0
        collected_tokens = 0
        collected_source: Literal["cache", "api"] = "cache"
        for _ in range(0, len(gather_text), self.batch_size_limit):
            batch_texts = gather_text[_ : _ + self.batch_size_limit]
            batch_kwargs = {
                "input": batch_texts,
                "model": self.model_name,
                "dimension": self.dimensions,
                **kwargs,
            }

            res = await self._call_api(batch_kwargs)

            collected_embeddings.extend(res.embeddings)
            collected_time += res.usage.time
            if res.usage.tokens:
                collected_tokens += res.usage.tokens
            if res.source == "api":
                collected_source = "api"

        return EmbeddingResponse(
            embeddings=collected_embeddings,
            usage=EmbeddingUsage(
                tokens=collected_tokens,
                time=collected_time,
            ),
            source=collected_source,
        )
