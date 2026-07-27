# -*- coding: utf-8 -*-
"""The Google Gemini embedding model.

Handles both text-only and multimodal models under a single class.
``gemini-embedding-001`` accepts ``list[str | TextBlock]``.
``gemini-embedding-2`` additionally accepts
:class:`~agentscope.message.DataBlock` (images, video, audio, PDF).
The model name determines the API call style.

Text payloads may be passed either as bare ``str`` or as
:class:`~agentscope.message.TextBlock` — the latter is unpacked to its
``.text`` field on entry so the rest of the pipeline only deals with
``str`` and ``DataBlock``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .._cache_base import EmbeddingCacheBase
from .._embedding_response import EmbeddingResponse
from .._embedding_usage import EmbeddingUsage
from .._embedding_base import EmbeddingModelBase
from ..._logging import logger
from ...credential import CredentialBase
from ...message import DataBlock, TextBlock

#: Model name prefixes that use the multimodal API path.
_MULTIMODAL_PREFIXES = ("gemini-embedding-2",)


@dataclass
class _MultimodalLimits:
    """Per-request constraints for Gemini multimodal embedding."""

    max_elements: int = 20
    """Maximum total content elements per API call."""

    max_images: int = 6
    """Maximum image elements per API call."""

    max_videos: int = 1
    """Maximum video elements per API call."""

    max_audios: int = 1
    """Maximum audio elements per API call."""

    max_pdfs: int = 1
    """Maximum PDF documents per API call."""


_MODEL_LIMITS: dict[str, _MultimodalLimits] = {
    "gemini-embedding-2": _MultimodalLimits(
        max_elements=20,
        max_images=6,
        max_videos=1,
        max_audios=1,
        max_pdfs=1,
    ),
}

_DEFAULT_LIMITS = _MultimodalLimits()


class GeminiEmbeddingModel(EmbeddingModelBase[str | TextBlock | DataBlock]):
    """Unified Google Gemini embedding model.

    Routes to the text-only or multimodal Gemini API based on the
    model name.

    - **Text mode** (``gemini-embedding-001``): uses the base class's
      simple batch splitting + concurrent retry.  The Gemini API
      accepts a list of strings and returns individual embeddings.
    - **Multimodal mode** (``gemini-embedding-2``): overrides
      ``__call__`` with content-aware batching (respecting per-model
      limits on images, videos, audios, PDFs).  Each input is wrapped
      in a ``Content`` object so the API returns separate embeddings.

    Key API differences from other providers:

    - Dimensions are controlled via ``output_dimensionality`` in the
      ``config`` parameter (not a top-level ``dimensions`` field).
    - ``gemini-embedding-001`` supports ``task_type`` in config;
      ``gemini-embedding-2`` uses prompt prefixes instead.
    """

    #: Text-mode batch size.  Gemini docs don't specify an explicit
    #: limit; we use a conservative default.
    _TEXT_BATCH_SIZE: int = 100

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        dimensions: int | None,
        parameters: "GeminiEmbeddingModel.Parameters | None" = None,
        embedding_cache: EmbeddingCacheBase | None = None,
        context_size: int = 8192,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize the Gemini embedding model.

        Args:
            credential (`CredentialBase`):
                A :class:`~agentscope.credential.GeminiCredential`
                instance providing the API key.
            model (`str`):
                The embedding model name (e.g.
                ``"gemini-embedding-001"`` or
                ``"gemini-embedding-2"``).
            dimensions (`int | None`):
                The output embedding vector dimensions.  Required at
                the contract level — see :class:`EmbeddingModelBase`
                for the rationale.  ``None`` is accepted only for
                backward compatibility with legacy configs that
                persisted ``dimensions`` inside ``parameters``.
            parameters (`GeminiEmbeddingModel.Parameters | None`, \
            defaults to ``None``):
                Provider-specific non-dimensional parameters.  Currently
                empty for Gemini.
            embedding_cache (`EmbeddingCacheBase | None`, defaults to \
            ``None``):
                Optional embedding cache.
            context_size (`int`, defaults to ``8192``):
                Maximum input tokens.  2048 for ``gemini-embedding-001``,
                8192 for ``gemini-embedding-2``.
            max_retries (`int`, defaults to ``3``):
                Number of retries on transient failures.
            retry_delay (`float`, defaults to ``1.0``):
                Seconds between retry attempts.
        """
        from google import genai

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
        self.supports_multimodal = self._is_multimodal

        self.client: genai.Client = genai.Client(
            api_key=credential.api_key.get_secret_value(),
        )
        self.embedding_cache: EmbeddingCacheBase | None = embedding_cache

        if self._is_multimodal:
            self._limits = _MODEL_LIMITS.get(model, _DEFAULT_LIMITS)

    # ------------------------------------------------------------------
    # __call__ — override for multimodal content-aware batching
    # ------------------------------------------------------------------

    async def __call__(
        self,
        inputs: list[str | TextBlock | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Embed inputs with batching and retry.

        For text models, delegates to the base class.  For multimodal
        models, performs content-aware batching that respects per-model
        limits on images, videos, audios, and PDFs.

        Args:
            inputs (`list[str | TextBlock | DataBlock]`):
                The input data to embed.  ``TextBlock`` items are
                unpacked to their ``.text`` field on entry, so the
                remainder of the pipeline only sees ``str`` and
                ``DataBlock``.
            **kwargs:
                Forwarded to the Gemini API config.

        Returns:
            `EmbeddingResponse`: Merged response for all inputs.
        """
        normalized: list[str | DataBlock] = [
            item.text if isinstance(item, TextBlock) else item
            for item in inputs
        ]

        if not self._is_multimodal:
            return await super().__call__(normalized, **kwargs)

        batches = self._split_multimodal_batches(normalized)

        if len(batches) > 1:
            logger.info(
                "Embedding %d multimodal inputs in %d batches "
                "for model %s.",
                len(normalized),
                len(batches),
                self.model,
            )

        results: list[EmbeddingResponse] = await asyncio.gather(
            *(self._call_with_retry(batch, **kwargs) for batch in batches),
        )

        return self._merge_responses(results)

    def _split_multimodal_batches(
        self,
        inputs: list[str | DataBlock],
    ) -> list[list[str | DataBlock]]:
        """Split inputs into batches respecting Gemini multimodal limits.

        Greedy: keep adding items until any constraint would be
        violated, then start a new batch.

        Args:
            inputs (`list[str | DataBlock]`):
                All inputs to split.

        Returns:
            `list[list[str | DataBlock]]`: List of batches.
        """
        limits = self._limits
        batches: list[list[str | DataBlock]] = []
        current: list[str | DataBlock] = []
        n_elem = 0
        n_img = 0
        n_vid = 0
        n_aud = 0
        n_pdf = 0

        for item in inputs:
            is_img = is_vid = is_aud = is_pdf = False
            if isinstance(item, DataBlock):
                mt = item.source.media_type
                is_img = mt.startswith("image/")
                is_vid = mt.startswith("video/")
                is_aud = mt.startswith("audio/")
                is_pdf = mt == "application/pdf"

            would_exceed = (
                n_elem + 1 > limits.max_elements
                or (is_img and n_img + 1 > limits.max_images)
                or (is_vid and n_vid + 1 > limits.max_videos)
                or (is_aud and n_aud + 1 > limits.max_audios)
                or (is_pdf and n_pdf + 1 > limits.max_pdfs)
            )

            if would_exceed and current:
                batches.append(current)
                current = []
                n_elem = n_img = n_vid = n_aud = n_pdf = 0

            current.append(item)
            n_elem += 1
            n_img += is_img
            n_vid += is_vid
            n_aud += is_aud
            n_pdf += is_pdf

        if current:
            batches.append(current)

        return batches

    # ------------------------------------------------------------------
    # _call_api — single batch
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Route to text or multimodal Gemini API for a single batch.

        Args:
            inputs (`list[str | DataBlock]`):
                A single batch of inputs.
            **kwargs:
                Extra keyword arguments merged into the Gemini
                ``EmbedContentConfig``.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        if self._is_multimodal:
            return await self._call_multimodal(inputs, **kwargs)
        return await self._call_text(inputs, **kwargs)

    # ------------------------------------------------------------------
    # Text API (gemini-embedding-001)
    # ------------------------------------------------------------------

    async def _call_text(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the Gemini text embedding API for a single batch.

        Passes the list of strings directly to ``embed_content``,
        which returns one embedding per string.

        Args:
            inputs (`list[str | DataBlock]`):
                Must all be ``str``; raises ``ValueError`` otherwise.
            **kwargs:
                Merged into ``EmbedContentConfig`` (e.g.
                ``task_type``).

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        from google.genai import types

        texts: list[str] = []
        for item in inputs:
            if not isinstance(item, str):
                raise ValueError(
                    f"Text embedding model {self.model!r} only accepts "
                    f"str inputs, got {type(item).__name__}.",
                )
            texts.append(item)

        config = types.EmbedContentConfig(
            output_dimensionality=self.dimensions,
            **kwargs,
        )

        cache_key = {
            "model": self.model,
            "contents": texts,
            "output_dimensionality": self.dimensions,
            **kwargs,
        }

        if self.embedding_cache:
            cached = await self.embedding_cache.retrieve(
                identifier=cache_key,
            )
            if cached:
                return EmbeddingResponse(
                    embeddings=cached,
                    usage=EmbeddingUsage(tokens=0, time=0),
                    source="cache",
                )

        start_time = datetime.now()
        response = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config=config,
        )
        time = (datetime.now() - start_time).total_seconds()

        embeddings = [item.values for item in response.embeddings]

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=cache_key,
                embeddings=embeddings,
            )

        return EmbeddingResponse(
            embeddings=embeddings,
            usage=EmbeddingUsage(time=time),
        )

    # ------------------------------------------------------------------
    # Multimodal API (gemini-embedding-2)
    # ------------------------------------------------------------------

    async def _call_multimodal(
        self,
        inputs: list[str | DataBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the Gemini multimodal embedding API for a single batch.

        Each input is wrapped in a separate ``Content`` object so the
        API returns one embedding per input (not one aggregated
        embedding).

        Args:
            inputs (`list[str | DataBlock]`):
                ``str`` for text, ``DataBlock`` for images / video /
                audio / PDF.
            **kwargs:
                Merged into ``EmbedContentConfig``.

        Returns:
            `EmbeddingResponse`: Embedding vectors and usage info.
        """
        from google.genai import types

        contents: list[types.Content] = []
        for item in inputs:
            if isinstance(item, str):
                contents.append(
                    types.Content(
                        parts=[types.Part.from_text(text=item)],
                    ),
                )
            elif isinstance(item, DataBlock):
                contents.append(
                    types.Content(
                        parts=[self._data_block_to_part(item)],
                    ),
                )
            else:
                raise ValueError(
                    f"Invalid input: {item!r}. Expected str or DataBlock.",
                )

        config = types.EmbedContentConfig(
            output_dimensionality=self.dimensions,
            **kwargs,
        )

        start_time = datetime.now()
        response = self.client.models.embed_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        time = (datetime.now() - start_time).total_seconds()

        embeddings = [item.values for item in response.embeddings]

        return EmbeddingResponse(
            embeddings=embeddings,
            usage=EmbeddingUsage(time=time),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _data_block_to_part(block: DataBlock) -> Any:
        """Convert a :class:`~agentscope.message.DataBlock` to a Gemini
        ``Part`` object.

        Args:
            block (`DataBlock`):
                A data block with ``Base64Source`` or ``URLSource``.

        Returns:
            A ``google.genai.types.Part`` instance.

        Raises:
            `ValueError`: If the source type is unsupported.
        """
        from google.genai import types
        from ...message import Base64Source, URLSource

        source = block.source

        if isinstance(source, Base64Source):
            import base64

            return types.Part.from_bytes(
                data=base64.b64decode(source.data),
                mime_type=source.media_type,
            )

        if isinstance(source, URLSource):
            # Gemini SDK doesn't have a direct from_url for
            # embed_content; download or use File API.
            # For now, raise — callers should use Base64Source.
            raise ValueError(
                "Gemini embedding API requires inline data "
                "(Base64Source). URLSource is not directly supported "
                f"for embedding. Got URL: {source.url}",
            )

        raise ValueError(
            f"Unsupported source type {type(source).__name__} "
            f"in DataBlock.",
        )
