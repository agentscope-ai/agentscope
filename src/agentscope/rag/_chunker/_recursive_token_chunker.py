# -*- coding: utf-8 -*-
"""A token-aware chunker that preserves natural text boundaries."""
from bisect import bisect_left, bisect_right
from itertools import accumulate

from pydantic import ConfigDict, Field, model_validator

from ._base import ChunkerBase
from .._document import Chunk, Section
from ...message import DataBlock, TextBlock


_DEFAULT_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ". ",
    "! ",
    "? ",
    "；",
    "; ",
    "，",
    ", ",
    " ",
    "",
]


class RecursiveTokenChunker(ChunkerBase):
    """Recursively split text while enforcing an approximate token limit.

    Text that already fits within ``chunk_size`` is returned unchanged.
    Oversized text is split using the configured separators from coarse to
    fine, and only pieces that remain oversized recurse to the next finer
    separator. The resulting short pieces are merged back together up to the
    approximate token budget. If no separator can reduce an oversized piece,
    it is split with character-safe UTF-8 byte windows. ``overlap`` is applied
    when consecutive chunks are formed without exceeding the budget.

    The token estimate matches :class:`ApproxTokenChunker`: four UTF-8 bytes
    are treated as one token. Chunks never cross input Section boundaries,
    and DataBlock content passes through unchanged.
    """

    chunker_type = "recursive_token"

    class Parameters(ChunkerBase.Parameters):
        """The tunable parameters of recursive token chunking."""

        model_config = ConfigDict(extra="forbid")

        chunk_size: int = Field(
            default=512,
            ge=1,
            title="Chunk Size",
            description=("Maximum number of approximate tokens per chunk."),
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
        separators: list[str] = Field(
            default_factory=lambda: list(_DEFAULT_SEPARATORS),
            min_length=1,
            title="Separators",
            description=(
                "Separator hierarchy used from coarse to fine. An empty "
                "string enables the final character-level fallback."
            ),
            json_schema_extra={"default": list(_DEFAULT_SEPARATORS)},
        )

        @model_validator(mode="after")
        def _overlap_less_than_chunk_size(
            self,
        ) -> "RecursiveTokenChunker.Parameters":
            if self.overlap >= self.chunk_size:
                raise ValueError(
                    "overlap must be less than chunk_size, got "
                    f"overlap={self.overlap}, "
                    f"chunk_size={self.chunk_size}.",
                )
            return self

    def __init__(
        self,
        parameters: "RecursiveTokenChunker.Parameters | None" = None,
    ) -> None:
        """Initialize the recursive token chunker.

        Args:
            parameters (`RecursiveTokenChunker.Parameters | None`, optional):
                The chunker parameters. Defaults to ``Parameters()``.
        """
        super().__init__(parameters)

    @property
    def chunk_size(self) -> int:
        """The maximum approximate token count of each chunk."""
        return self.parameters.chunk_size

    @property
    def overlap(self) -> int:
        """The approximate token overlap between consecutive chunks."""
        return self.parameters.overlap

    @property
    def separators(self) -> list[str]:
        """The configured separator hierarchy."""
        return self.parameters.separators

    async def chunk(self, sections: list[Section]) -> list[Chunk]:
        """Split Sections into recursively bounded Chunks.

        Args:
            sections (`list[Section]`):
                The Sections produced by a parser.

        Returns:
            `list[Chunk]`:
                Chunks in document order with continuous indices.
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
                contents = [section.content]

            chunks.extend(
                Chunk(
                    content=content,
                    source=section.source,
                    chunk_index=0,
                    total_chunks=0,
                    metadata=dict(section.metadata),
                )
                for content in contents
            )

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
            chunk.total_chunks = len(chunks)

        return chunks

    def _split_text(self, text: str) -> list[str]:
        """Split text with natural boundaries and a byte-based budget."""
        if self._fits_budget(text):
            return [text]

        splits = self._split_recursively(text, self.separators)
        return self._merge_splits(splits)

    def _split_recursively(
        self,
        text: str,
        separators: list[str],
    ) -> list[str]:
        """Recursively reduce oversized text with finer separators."""
        if self._fits_budget(text) or not separators:
            return [text]

        separator = separators[0]
        remaining = separators[1:]
        if separator == "":
            return [text]

        results: list[str] = []
        for split in self._split_by_separator(text, separator):
            if self._fits_budget(split):
                results.append(split)
            else:
                results.extend(
                    self._split_recursively(split, remaining),
                )
        return results

    @staticmethod
    def _split_by_separator(text: str, separator: str) -> list[str]:
        """Split text while retaining each separator in the left piece."""
        pieces = text.split(separator)
        return [
            f"{piece}{separator}" if index < len(pieces) - 1 else piece
            for index, piece in enumerate(pieces)
            if piece or index < len(pieces) - 1
        ]

    def _merge_splits(self, splits: list[str]) -> list[str]:
        """Merge short splits and apply overlap at chunk boundaries."""
        chunks: list[str] = []
        current = ""

        for split in splits:
            if not split:
                continue
            if not self._fits_budget(split):
                if current:
                    chunks.append(current)
                forced = self._force_split(
                    self._with_overlap(current, split),
                )
                chunks.extend(forced[:-1])
                current = forced[-1]
                continue

            if self._fits_budget(f"{current}{split}"):
                current = f"{current}{split}"
                continue

            if current:
                chunks.append(current)
                current = self._with_overlap(current, split)
            else:
                current = split

        if current:
            chunks.append(current)
        return chunks

    def _with_overlap(self, previous: str, next_split: str) -> str:
        """Prefix a split with the largest overlap that still fits."""
        if not previous or self.overlap == 0:
            return next_split

        available = self._budget_bytes - self._byte_size(next_split)
        if available <= 0:
            return next_split

        overlap_bytes = min(self.overlap * 4, available)
        return f"{self._suffix_by_bytes(previous, overlap_bytes)}{next_split}"

    def _force_split(self, text: str) -> list[str]:
        """Split oversized text into UTF-8-safe overlapping windows."""
        byte_offsets = [0, *accumulate(len(c.encode("utf-8")) for c in text)]
        overlap_bytes = self.overlap * 4
        pieces: list[str] = []
        start = 0

        while start < len(text):
            end = (
                bisect_right(
                    byte_offsets,
                    byte_offsets[start] + self._budget_bytes,
                )
                - 1
            )
            end = max(end, start + 1)
            pieces.append(text[start:end])
            if end >= len(text):
                break

            target = byte_offsets[end] - overlap_bytes
            next_start = bisect_left(byte_offsets, target)
            start = max(next_start, start + 1)

        return pieces

    def _fits_budget(self, text: str) -> bool:
        """Return whether text fits the approximate token budget."""
        return self._byte_size(text) <= self._budget_bytes

    @property
    def _budget_bytes(self) -> int:
        """The approximate token budget expressed as UTF-8 bytes."""
        return self.chunk_size * 4

    @staticmethod
    def _byte_size(text: str) -> int:
        """Return the UTF-8 byte size used by the token approximation."""
        return len(text.encode("utf-8"))

    @staticmethod
    def _suffix_by_bytes(text: str, budget: int) -> str:
        """Return the longest character-safe suffix within a byte budget."""
        suffix: list[str] = []
        used = 0
        for character in reversed(text):
            size = len(character.encode("utf-8"))
            if used + size > budget:
                break
            suffix.append(character)
            used += size
        return "".join(reversed(suffix))
