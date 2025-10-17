# -*- coding: utf-8 -*-
"""The document data structure used in RAG as the data chunk and
retrieval result."""
from dataclasses import dataclass, field

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

    content: TextBlock | ImageBlock | VideoBlock
    """The data content, e.g., text, image, video."""

    doc_id: str
    """The document ID."""

    chunk_id: int
    """The chunk ID."""

    total_chunks: int
    """The total number of chunks."""


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
