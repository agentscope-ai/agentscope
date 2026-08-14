# -*- coding: utf-8 -*-
"""The DashScope embedding model.

Handles both text-only and multimodal models under a single class.
Text models (``text-embedding-v3``, ``text-embedding-v4``) accept
``list[str | TextBlock]``.  Multimodal models (``qwen*-vl-embedding``,
``multimodal-embedding-*``, ``tongyi-embedding-vision-*``)
additionally accept :class:`~agentscope.message.DataBlock`.
The model name determines which DashScope API endpoint is used.

Text payloads may be passed either as bare ``str`` or as
:class:`~agentscope.message.TextBlock` — the latter is unpacked to its
``.text`` field on entry so the rest of the pipeline only deals with
``str`` and ``DataBlock``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from dataclasses import dataclass
from typing import Any

from .._cache_base import EmbeddingCacheBase
from .._embedding_response import EmbeddingResponse
from .._embedding_usage import EmbeddingUsage
from .._embedding_base import EmbeddingModelBase
from ..._logging import logger
from ...credential import CredentialBase
from ...message import DataBlock, Base64Source, TextBlock, URLSource

#: Model name prefixes that route to the multimodal API.
_MULTIMODAL_PREFIXES = (
    "multimodal-embedding-",
    "tongyi-embedding-vision-",
    "qwen3-vl-embedding",
    "qwen2.5-vl-embedding",
)


@dataclass
class _MultimodalLimits:
    """Per-request constraints for a multimodal embedding model."""

    max_elements: int = 20
    """Maximum total content elements per API call."""

    max_images: int = 5
    """Maximum image elements per API call."""

    max_videos: int = 1
    """Maximum video elements per API call."""


#: Known per-model multimodal constraints (from DashScope docs).
_MODEL_LIMITS: dict[str, _MultimodalLimits] = {
    "qwen3-vl-embedding": _MultimodalLimits(
        max_elements=20,
        max_images=5,
        max_videos=1,
    ),
    "qwen2.5-vl-embedding": _MultimodalLimits(
        max_elements=20,
        max_images=5,
        max_videos=1,
    ),
    "tongyi-embedding-vision-plus": _MultimodalLimits(
        max_elements=20,
        max_images=64,
        max_videos=8,
    ),
    "tongyi-embedding-vision-flash": _MultimodalLimits(
        max_elements=20,
        max_images=64,
        max_videos=8,
    ),
    "multimodal-embedding-v1": _MultimodalLimits(
        max_elements=20,
        max_images=1,
        max_videos=1,
    ),
}

#: Fallback for unknown multimodal models — safest constraints.
_DEFAULT_LIMITS = _MultimodalLimits(
    max_elements=20,
    max_images=1,
    max_videos=1,
)


class DashScopeEmbeddingModel(EmbeddingModelBase[str | TextBlock | DataBlock]):
    """Unified DashScope embedding model.

    Routes to the text or multimodal DashScope API based on the model
    name.

    - **Text mode** (``text-embedding-*``): uses the base class's
      simple batch splitting + concurrent retry.
    - **Multimodal mode** (``qwen*-vl-*``, ``multimodal-*``,
      ``tongyi-embedding-vision-*``): overrides ``__call__`` to
      perform content-aware batching that respects per-model limits
      on total elements, images, and videos per request.
    """

    #: Text-mode batch size (from DashScope docs: 10 for v3/v4, 25 for
    #: v1/v2).  Multimodal models use :data:`_MODEL_LIMITS` instead.
    _TEXT_BATCH_SIZE: int = 10

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        dimensions: int | None,
        parameters: "DashScopeEmbeddingModel.Parameters | None" = None,
        embedding_cache: EmbeddingCacheBase | None = None,
        context_size: int = 8192,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize the DashScope embedding model.

        Args:
            credential (`CredentialBase`):
                A :class:`~agentscope.credential.DashScopeCredential`
                instance providing the API key.
            model (`str`):
                The embedding model name (e.g.
                ``"text-embedding-v4"`` or
                ``"qwen3-vl-embedding"``).
            dimensions (`int | None`):
                The output embedding vector dimensions.  Required at
                the contract level — see :class:`EmbeddingModelBase`
                for the rationale.  ``None`` is accepted only for
                backward compatibility with legacy configs that
                persisted ``dimensions`` inside ``parameters``.
            parameters (`DashScopeEmbeddingModel.Parameters | None`, \
            defaults to ``None``):
                Provider-specific non-dimensional parameters.  Currently
                empty for DashScope.
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
        self._is_multimodal: bool = model.startswith(_MULTIMODAL_PREFIXES)

        super().__init__(
            credential=credential,
            model=model,
            dimensions=dimensions,
            parameters=parameters,
            context_size=context_size,
            batch_size=self._TEXT_BATCH_SIZE,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self.api_key: str = credential.api_key.get_secret_value()
        self.embedding_cache: EmbeddingCacheBase | None = embedding_cache

        # Resolve multimodal constraints.
        if self._is_multimodal:
            self._limits = _MODEL_LIMITS.get(model, _DEFAULT_LIMITS)

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        """Return retryable exception types.

        DashScope SDK does not expose typed exception classes.  We
        retry on ``RuntimeError``, which is raised when the API
        returns a non-200 status code.
        """
        return (RuntimeError,)

    # ------------------------------------------------------------------
    # __call__ — override for multimodal content-aware batching
    # ------------------------------------------------------------------

    async def __call__(
        self,
        inputs: list[str | TextBlock | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Embed inputs with batching and retry.

        For text models, delegates to the base class (simple
        ``batch_size`` splitting).  For multimodal models, performs
        content-aware batching that respects per-model limits on
        total elements, images, and videos per request.

        Args:
            inputs (`list[str | TextBlock | DataBlock]`):
                The input data to embed.  ``TextBlock`` items are
                unpacked to their ``.text`` field on entry, so the
                remainder of the pipeline only sees ``str`` and
                ``DataBlock``.
            **kwargs:
                Forwarded to the DashScope API.

        Returns:
            `EmbeddingResponse`: Merged response for all inputs.
        """
        normalized: list[str | DataBlock] = [
            item.text if isinstance(item, TextBlock) else item
            for item in inputs
        ]

        if not self._is_multimodal:
            # Text mode — use base class batching.
            return await super().__call__(normalized, **kwargs)

        # Multimodal mode — content-aware batching.
        batches = self._split_multimodal_batches(normalized)

        if len(batches) > 1:
            logger.info(
                "Embedding %d multimodal inputs in %d batches for "
                "model %s (limits: elements=%d, images=%d, videos=%d).",
                len(normalized),
                len(batches),
                self.model,
                self._limits.max_elements,
                self._limits.max_images,
                self._limits.max_videos,
            )

        results: list[EmbeddingResponse] = await asyncio.gather(
            *(self._call_with_retry(batch, **kwargs) for batch in batches),
        )

        return self._merge_responses(results)

    def _split_multimodal_batches(
        self,
        inputs: list[str | DataBlock],
    ) -> list[list[str | DataBlock]]:
        """Split inputs into batches that satisfy multimodal limits.

        Greedy algorithm: keep adding items to the current batch
        until adding the next item would violate any constraint,
        then start a new batch.

        Args:
            inputs (`list[str | DataBlock]`):
                All inputs to split.

        Returns:
            `list[list[str | DataBlock]]`: List of batches.
        """
        limits = self._limits
        batches: list[list[str | DataBlock]] = []
        current_batch: list[str | DataBlock] = []
        n_elements = 0
        n_images = 0
        n_videos = 0

        for item in inputs:
            # Determine what this item contributes.
            is_image = False
            is_video = False
            if isinstance(item, DataBlock):
                media_type = item.source.media_type
                is_image = media_type.startswith("image/")
                is_video = media_type.startswith("video/")

            # Check if adding this item would exceed any limit.
            would_exceed = (
                n_elements + 1 > limits.max_elements
                or (is_image and n_images + 1 > limits.max_images)
                or (is_video and n_videos + 1 > limits.max_videos)
            )

            if would_exceed and current_batch:
                batches.append(current_batch)
                current_batch = []
                n_elements = 0
                n_images = 0
                n_videos = 0

            current_batch.append(item)
            n_elements += 1
            if is_image:
                n_images += 1
            if is_video:
                n_videos += 1

        if current_batch:
            batches.append(current_batch)

        return batches

    # ------------------------------------------------------------------
    # _call_api — single batch dispatch
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Route to the text or multimodal DashScope API.

        Args:
            inputs (`list[str | DataBlock]`):
                A single batch.  For text models every element must be
                ``str``; for multimodal models elements may also be
                :class:`~agentscope.message.DataBlock`.
            **kwargs:
                Forwarded to the DashScope API.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        if self._is_multimodal:
            return await self._call_multimodal(inputs, **kwargs)
        return await self._call_text(inputs, **kwargs)

    # ------------------------------------------------------------------
    # Text API
    # ------------------------------------------------------------------

    async def _call_text(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope text embedding API for a single batch.

        Args:
            inputs (`list[str | DataBlock]`):
                Must all be ``str``; raises ``ValueError`` otherwise.
            **kwargs:
                Forwarded to the API.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        texts: list[str] = []
        for item in inputs:
            if not isinstance(item, str):
                raise ValueError(
                    f"Text embedding model {self.model!r} only accepts "
                    f"str inputs, got {type(item).__name__}.",
                )
            texts.append(item)

        api_kwargs: dict[str, Any] = {
            "input": texts,
            "model": self.model,
            "dimension": self.dimensions,
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

        import dashscope

        start_time = datetime.now()
        response = dashscope.embeddings.TextEmbedding.call(
            api_key=self.api_key,
            **api_kwargs,
        )
        time = (datetime.now() - start_time).total_seconds()

        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope text embedding API error: {response}",
            )

        embeddings = [
            entry["embedding"] for entry in response.output["embeddings"]
        ]
        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=api_kwargs,
                embeddings=embeddings,
            )
        return EmbeddingResponse(
            embeddings=embeddings,
            usage=EmbeddingUsage(
                tokens=response.usage["total_tokens"],
                time=time,
            ),
        )

    # ------------------------------------------------------------------
    # Multimodal API
    # ------------------------------------------------------------------

    async def _call_multimodal(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope multimodal embedding API for a single batch.

        Args:
            inputs (`list[str | DataBlock]`):
                ``str`` for text, ``DataBlock`` for images / videos.
            **kwargs:
                Forwarded to the API.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        formatted: list[dict[str, str]] = []
        for item in inputs:
            if isinstance(item, str):
                formatted.append({"text": item})
            elif isinstance(item, DataBlock):
                formatted.append(self._format_data_block(item))
            else:
                raise ValueError(
                    f"Invalid input: {item!r}. Expected str or DataBlock.",
                )

        api_kwargs: dict[str, Any] = {
            "input": formatted,
            "model": self.model,
            "api_key": self.api_key,
            **kwargs,
        }

        # Exclude api_key from cache identifier to avoid persisting secrets
        # and to keep cache valid across key rotations.
        cache_identifier = {
            k: v for k, v in api_kwargs.items() if k != "api_key"
        }

        if self.embedding_cache:
            cached = await self.embedding_cache.retrieve(
                identifier=cache_identifier,
            )
            if cached:
                return EmbeddingResponse(
                    embeddings=cached,
                    usage=EmbeddingUsage(tokens=0, time=0),
                    source="cache",
                )

        import dashscope

        start_time = datetime.now()
        res = dashscope.MultiModalEmbedding.call(**api_kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if res.status_code != 200:
            raise RuntimeError(
                f"DashScope multimodal embedding API error: {res}",
            )

        embeddings = [entry["embedding"] for entry in res.output["embeddings"]]
        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=cache_identifier,
                embeddings=embeddings,
            )
        return EmbeddingResponse(
            embeddings=embeddings,
            usage=EmbeddingUsage(
                tokens=res.usage.get("image_tokens", 0)
                + res.usage.get("input_tokens", 0),
                time=time,
            ),
            source="api",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_data_block(block: DataBlock) -> dict[str, str]:
        """Convert a :class:`~agentscope.message.DataBlock` to the dict
        format expected by the DashScope multimodal embedding API.

        The ``DataBlock.source.media_type`` determines whether the
        block is treated as an image or video.

        Args:
            block (`DataBlock`):
                A data block with a ``Base64Source`` or ``URLSource``.

        Returns:
            `dict[str, str]`: E.g.
            ``{"image": "data:image/png;base64,..."}`` or
            ``{"video": "https://..."}``.

        Raises:
            `ValueError`: If the media type is unsupported or a video
                block uses a non-URL source.
        """

        source = block.source
        media_type = source.media_type

        if media_type.startswith("video/"):
            if not isinstance(source, URLSource):
                raise ValueError(
                    "Multimodal embedding API only supports URL input "
                    f"for video data, got {type(source).__name__}.",
                )
            return {"video": str(source.url)}

        if media_type.startswith("image/"):
            if isinstance(source, Base64Source):
                return {
                    "image": f"data:{media_type};" f"base64,{source.data}",
                }
            if isinstance(source, URLSource):
                return {"image": str(source.url)}

        raise ValueError(
            f"Unsupported media type {media_type!r} in DataBlock. "
            f"Expected image/* or video/*.",
        )
