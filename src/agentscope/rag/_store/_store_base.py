# -*- coding: utf-8 -*-
"""The embedding store base class."""
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..._utils._common import _get_timestamp
from ..._utils._mixin import DictMixin
from ...message import TextBlock, ImageBlock, VideoBlock, AudioBlock


class VectorSource(DictMixin):

    index: dict[str, Any]

    content: TextBlock | ImageBlock | AudioBlock | VideoBlock


@dataclass
class VectorRecord:
    """The embedding record."""

    embedding: list[float]
    """The embedding vector."""

    content: TextBlock | ImageBlock | AudioBlock | VideoBlock
    """The embedding source."""

    metadata: dict | None = field(default_factory=lambda: None)
    """The metadata of the embedding record."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The unique identifier of the embedding record."""


class EmbeddingStoreBase:
    """The embedding store abstraction for retrieval-augmented generation
    (RAG)."""

    @abstractmethod
    async def add(self, embeddings: list[VectorRecord], **kwargs: Any) -> None:
        """Add texts to the embedding store."""

    @abstractmethod
    async def delete(self, *args, **kwargs) -> None:
        """Delete texts from the embedding store."""

    @abstractmethod
    async def retrieve(
        self,
        queries: list[VectorRecord],
        **kwargs: Any
    ) -> list[VectorRecord]:
        """Retrieve relevant texts for the given queries.

        Args:
            queries (`list[VectorRecord]`):
                The list of embedding records to be queried.

        Returns:
            `list[VectorRecord]`:
                The list of relevant embedding records retrieved.
        """