# -*- coding: utf-8 -*-
"""The document data structure used in RAG as the data chunk and
retrieval result."""
from dataclasses import dataclass, field
from typing import TypedDict

from ..message import (
    TextBlock,
    ImageBlock,
    VideoBlock,
)
from ..types import Embedding, JSONSerializableObject


class DocMetadata(dict):
    """The metadata of the document."""

    def __init__(
        self,
        doc_id: str,
        chunk_id: int,
        total_chunks: int,
        **kwargs: JSONSerializableObject
    ) -> None:
        """Initialize the metadata of the document.

        Args:
            doc_id (`str`):
                The document ID.
            chunk_id (`int`):
                The chunk ID.
            total_chunks (`int`):
                The total number of chunks.
            **kwargs (`JSONSerializableObject`):
                Other metadata fields.
        """
        super().__init__(**kwargs)
        self["doc_id"] = doc_id
        self["chunk_id"] = chunk_id
        self["total_chunks"] = total_chunks



@dataclass
class Document:
    """The data chunk."""

    content: TextBlock | ImageBlock | VideoBlock
    """The data content, e.g., text, image, video."""

    doc_id: str
    """The document ID."""

    chunk_id: int
    """The chunk ID."""

    total_chunks: int
    """The total number of chunks."""

    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None
    )
    """The metadata of the data chunk."""

    # The fields that will be filled when the document is added to or
    # retrieved from the knowledge base.

    embedding: Embedding | None = field(default_factory=lambda: None)
    """The embedding of the data chunk."""

    score: float | None = None
    """The relevance score of the data chunk."""
