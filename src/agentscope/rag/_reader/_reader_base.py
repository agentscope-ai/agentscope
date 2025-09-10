# -*- coding: utf-8 -*-
"""The reader base class for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from agentscope.message import AudioBlock, TextBlock, ImageBlock, VideoBlock

@dataclass
class Document:
    """The data chunk."""

    content: TextBlock | ImageBlock | AudioBlock | VideoBlock
    """The data content, e.g., text, image, audio, video."""

    doc_id: str
    """The document ID."""

    chunk_id: int
    """The chunk ID."""

    total_chunks: int
    """The total number of chunks."""


class ReaderBase:
    """The reader base class, which is responsible for reading the original
    data, splitting it into chunks, and converting each chunk into a `Document`
    object."""

    @abstractmethod
    def __call__(self, *args: Any, **kwargs: Any) -> list[Document]:
        """The function that takes the input files and returns the
        vector records"""

