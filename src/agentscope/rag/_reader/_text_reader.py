# -*- coding: utf-8 -*-
"""The text reader that reads text into vector records."""
import hashlib

from ._reader_base import ReaderBase, Document
from ...message import TextBlock


class TextReader(ReaderBase):
    """The text reader that splits text into chunks by a fixed chunk size
    and chunk overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 0,
    ) -> None:
        """Initialize the text reader.

        Args:
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
            chunk_overlap (`int`, default to 0):
                The number of overlapping characters between chunks.
        """
        if chunk_size <= 0:
            raise ValueError(
                f"The chunk_size must be positive, got {chunk_size}",
            )

        if chunk_overlap < 0:
            raise ValueError(
                f"The chunk_overlap must be non-negative, got {chunk_overlap}",
            )

        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"The chunk_overlap must be smaller than chunk_size, got "
                f"chunk_overlap={chunk_overlap}, chunk_size={chunk_size}",
            )

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def __call__(
        self,
        text: str,
    ) -> list[Document]:
        """Read a text string, split it into chunks, and return a list of
        Document objects.

        Args:
            text (`str`):
                The input text string.
        """
        splits = []
        for i in range(0, len(text), self.chunk_size):
            start = max(0, i - self.chunk_overlap)
            end = min(i + self.chunk_size + self.chunk_overlap, len(text))
            splits.append(text[start:end])

        doc_id = hashlib.sha256(text.encode("utf-8")).hexdigest()

        return [
            Document(
                content=TextBlock(type="text", text=_),
                doc_id=doc_id,
                chunk_id=idx,
                total_chunks=len(splits),
            )
            for idx, _ in enumerate(splits)
        ]
