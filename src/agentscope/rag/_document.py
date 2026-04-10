# -*- coding: utf-8 -*-
"""The document data structure used in RAG as the data chunk and
retrieval result."""
from dataclasses import dataclass, field
from typing import Any, ClassVar

import shortuuid
from dashscope.api_entities.dashscope_response import DictMixin

from ..message import (
    TextBlock,
    ImageBlock,
    VideoBlock,
)
from ..types import Embedding


@dataclass
class DocMetadata(DictMixin):
    """The metadata of the document."""

    CORE_FIELDS: ClassVar[tuple[str, ...]] = (
        "content",
        "doc_id",
        "chunk_id",
        "total_chunks",
    )

    content: TextBlock | ImageBlock | VideoBlock
    """The data content, e.g., text, image, video."""

    doc_id: str
    """The document ID."""

    chunk_id: int
    """The chunk ID."""

    total_chunks: int
    """The total number of chunks."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocMetadata":
        """Build metadata from a payload dict while preserving extra fields."""
        payload = dict(data)
        metadata = cls(
            content=payload.pop("content"),
            doc_id=payload.pop("doc_id"),
            chunk_id=payload.pop("chunk_id"),
            total_chunks=payload.pop("total_chunks"),
        )
        for key, value in payload.items():
            setattr(metadata, key, value)
        return metadata

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata, including dynamic fields added at runtime."""
        return dict(self)


@dataclass
class Document:
    """The data chunk."""

    metadata: DocMetadata
    """The metadata of the data chunk."""

    id: str = field(default_factory=shortuuid.uuid)
    """The unique ID of the data chunk."""

    # The fields that will be filled when the document is added to or
    # retrieved from the knowledge base.

    embedding: Embedding | None = field(default_factory=lambda: None)
    """The embedding of the data chunk."""

    score: float | None = None
    """The relevance score of the data chunk."""
