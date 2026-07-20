# -*- coding: utf-8 -*-
"""A chunker that splits text by an approximate token count.

The token count is approximated as ``len(text.encode("utf-8")) // 4``,
which avoids a hard dependency on any tokenizer library while staying
within the right order of magnitude for most LLM tokenizers.
"""
from bisect import bisect_right
from itertools import accumulate

from ._base import ChunkerBase
from .._document import Chunk, Section
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

    def __init__(self, chunk_size: int = 512, overlap: int = 50) -> None:
        """Initialize the approx token chunker.

        Args:
            chunk_size (`int`, defaults to `512`):
                The maximum number of approximate tokens per chunk.
                Must be a positive integer.
            overlap (`int`, defaults to `50`):
                The number of approximate tokens shared between two
                consecutive chunks.  Must be non-negative and smaller
                than ``chunk_size``.

        Raises:
            `ValueError`:
                If ``chunk_size`` is not positive, or ``overlap`` is
                negative or not smaller than ``chunk_size``.
        """
        if chunk_size <= 0:
            raise ValueError(
                f"chunk_size must be positive, got {chunk_size}.",
            )
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError(
                "overlap must satisfy 0 <= overlap < chunk_size, "
                f"got overlap={overlap}, chunk_size={chunk_size}.",
            )

        self.chunk_size = chunk_size
        self.overlap = overlap

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
