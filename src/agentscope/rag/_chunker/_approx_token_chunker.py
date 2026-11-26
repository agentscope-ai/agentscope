# -*- coding: utf-8 -*-
"""A chunker that splits text by an approximate token count.

The token count is approximated as ``len(text.encode("utf-8")) // 4``,
which avoids a hard dependency on any tokenizer library while staying
within the right order of magnitude for most LLM tokenizers.
"""
from typing import Any

from pydantic import Field, model_validator

from ._base import ChunkerBase
from .._document import Chunk, Section
from ..._logging import logger
from ...message import TextBlock, DataBlock


class ApproxTokenChunker(ChunkerBase):
    """A chunker based on an approximate token counting strategy.

    Text sections are sliced into pieces of at most ``chunk_size``
    approximate tokens, with ``overlap`` approximate tokens shared
    between two consecutive pieces.  The token count of a string is
    approximated as ``len(text.encode("utf-8")) // 4``, so no
    tokenizer dependency is required.

    Sections carrying a :class:`~agentscope.message.DataBlock`
    (images, video, etc.) are passed through unchanged as a single
    chunk. Empty or whitespace-only text produces no chunk.

    .. note:: Chunks never span across two input Sections, as
        required by :class:`ChunkerBase`.
    """

    chunker_type = "approx_token"

    class Parameters(ChunkerBase.Parameters):
        """The tunable parameters of the approximate-token chunker."""

        chunk_size: int = Field(
            default=512,
            ge=1,
            title="Chunk Size",
            description="Maximum number of approximate tokens per chunk.",
        )
        overlap: int = Field(
            default=50,
            ge=0,
            title="Overlap",
            description=(
                "Number of approximate tokens shared between "
                "consecutive chunks."
            ),
        )

        @model_validator(mode="after")
        def _overlap_less_than_chunk_size(
            self,
        ) -> "ApproxTokenChunker.Parameters":
            if self.overlap >= self.chunk_size:
                raise ValueError(
                    "overlap must be less than chunk_size, got "
                    f"overlap={self.overlap}, chunk_size={self.chunk_size}.",
                )
            return self

    def __init__(
        self,
        parameters: "ApproxTokenChunker.Parameters | None" = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the approx token chunker.

        Args:
            parameters (`ApproxTokenChunker.Parameters | None`, optional):
                The chunker parameters (``chunk_size`` and ``overlap``).
                Defaults to ``Parameters()`` when not provided.
            **kwargs (`Any`):
                Deprecated. ``chunk_size`` and ``overlap`` are still
                accepted for backward compatibility and override the
                corresponding fields in ``parameters``; other keys are
                ignored.

        Raises:
            `TypeError`:
                If ``parameters`` is not a ``Parameters`` instance.
            `ValueError`:
                If ``chunk_size`` is not positive, or ``overlap`` is
                negative or not smaller than ``chunk_size``.
        """
        if parameters is not None and not isinstance(
            parameters,
            self.Parameters,
        ):
            raise TypeError(
                "The first argument of ApproxTokenChunker must be an "
                "ApproxTokenChunker.Parameters instance, got "
                f"{type(parameters).__name__}. Use keyword arguments "
                "chunk_size=/overlap= or parameters=Parameters(...).",
            )
        legacy = {
            key: kwargs[key]
            for key in ("chunk_size", "overlap")
            if key in kwargs
        }
        if legacy:
            logger.warning(
                "Passing %s to ApproxTokenChunker directly is deprecated, "
                "use ApproxTokenChunker.Parameters instead.",
                ", ".join(f"``{k}``" for k in legacy),
            )
            base = parameters.model_dump() if parameters is not None else {}
            parameters = self.Parameters(**{**base, **legacy})

        super().__init__(parameters)

    @property
    def chunk_size(self) -> int:
        """The maximum number of approximate tokens per chunk."""
        return self.parameters.chunk_size

    @property
    def overlap(self) -> int:
        """The number of approximate tokens shared between chunks."""
        return self.parameters.overlap

    async def chunk(self, sections: list[Section]) -> list[Chunk]:
        """Chunk the input sections into smaller chunks based on an approx
        token counting strategy.

        Args:
            sections (`list[Section]`):
                A list of sections to chunk.

        Returns:
            `list[Chunk]`:
                A list of chunks, with ``chunk_index`` numbered
                ``0..N-1`` and ``total_chunks`` set to ``N`` on every
                chunk.
        """
        chunks: list[Chunk] = []
        for section in sections:
            contents: list[TextBlock | DataBlock]
            if isinstance(section.content, TextBlock):
                # Blank pieces (e.g. empty scanned pages) are dropped
                contents = [
                    TextBlock(text=piece)
                    for piece in self._split_text(section.content.text)
                    if piece.strip()
                ]
            else:
                # DataBlock pass-through: never slice multimodal data
                contents = [section.content]

            chunks.extend(
                Chunk(
                    content=content,
                    source=section.source,
                    chunk_index=0,  # renumbered below
                    total_chunks=0,  # renumbered below
                    metadata=dict(section.metadata),
                )
                for content in contents
            )

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
            chunk.total_chunks = len(chunks)

        return chunks

    def _split_text(self, text: str) -> list[str]:
        """Split text into pieces of at most ``chunk_size`` approx tokens.

        Consecutive pieces share approximately ``overlap`` tokens.

        Args:
            text (`str`):
                The text to split.

        Returns:
            `list[str]`:
                The text pieces, in document order.
        """
        encoded = text.encode("utf-8")
        if len(encoded) // 4 <= self.chunk_size:
            return [text]

        chunk_bytes = self.chunk_size * 4
        overlap_bytes = self.overlap * 4

        pieces: list[str] = []
        start = 0
        while start < len(encoded):
            end = min(start + chunk_bytes, len(encoded))
            if end == len(encoded):
                pieces.append(encoded[start:end].decode("utf-8"))
                break

            # A continuation byte (10xxxxxx) is inside a code point.
            # A window of at least four bytes always fits one whole point.
            while encoded[end] & 0xC0 == 0x80:
                end -= 1
            pieces.append(encoded[start:end].decode("utf-8"))

            next_start = max(start, end - overlap_bytes)
            while encoded[next_start] & 0xC0 == 0x80:
                next_start -= 1
            if next_start == start:
                # Rounding the overlap back can leave us at the same point.
                next_start += 1
                while encoded[next_start] & 0xC0 == 0x80:
                    next_start += 1
            start = next_start

        return pieces
