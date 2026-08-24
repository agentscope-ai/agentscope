# -*- coding: utf-8 -*-
"""A chunker that splits text by an approximate token count.

The token count is approximated as ``len(text.encode("utf-8")) // 4``,
which avoids a hard dependency on any tokenizer library while staying
within the right order of magnitude for most LLM tokenizers.
"""
from bisect import bisect_right
from itertools import accumulate
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
    chunk.

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
                contents = [
                    TextBlock(text=piece)
                    for piece in self._split_text(section.content.text)
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
        if self._approx_count_tokens(text) <= self.chunk_size:
            return [text]

        # Cumulative UTF-8 byte length after each character, so that
        # the byte length of text[i:j] == byte_offsets[j] - byte_offsets[i]
        byte_offsets = [0, *accumulate(len(c.encode("utf-8")) for c in text)]

        chunk_bytes = self.chunk_size * 4
        overlap_bytes = self.overlap * 4

        pieces: list[str] = []
        start = 0
        while start < len(text):
            # The largest end such that the slice fits the byte budget
            end = (
                bisect_right(
                    byte_offsets,
                    byte_offsets[start] + chunk_bytes,
                )
                - 1
            )
            # Always make progress, even for characters whose UTF-8
            # encoding exceeds the budget on their own
            end = max(end, start + 1)
            pieces.append(text[start:end])

            if end >= len(text):
                break

            # Step back by the overlap budget, ensuring forward progress
            next_start = (
                bisect_right(
                    byte_offsets,
                    byte_offsets[end] - overlap_bytes,
                )
                - 1
            )
            start = max(next_start, start + 1)

        return pieces

    @staticmethod
    def _approx_count_tokens(text: str) -> int:
        """The approx count of tokens.

        Args:
            text (`str`):
                The text to be counted.

        Returns:
            `int`:
                The approx count of tokens.
        """
        return len(text.encode("utf-8")) // 4
