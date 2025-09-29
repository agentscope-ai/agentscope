# -*- coding: utf-8 -*-
"""The embedding cache base class."""
from abc import abstractmethod
from typing import List, Any

from ..types import (
    JSONSerializableObject,
    Embedding,
)


class EmbeddingCacheBase:
    """Base class for embedding caches, which is responsible for storing and
    retrieving embeddings."""

    @abstractmethod
    async def store(
        self,
        embeddings: List[Embedding],
        identifier: JSONSerializableObject,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> None:
        """Store the embeddings with the given identifier.

        Args:
            embeddings (`List[Embedding]`):
                The embeddings to store.
            identifier (`JSONSerializableObject`):
                The identifier to distinguish the embeddings.
            overwrite (`bool`, defaults to `False`):
                Whether to overwrite existing embeddings with the same
                identifier. If `True`, existing embeddings will be replaced.
        """

    @abstractmethod
    async def retrieve(
        self,
        identifier: JSONSerializableObject,
    ) -> List[Embedding] | None:
        """Retrieve the embeddings with the given identifier. If not
        found, return `None`.

        Args:
            identifier (`JSONSerializableObject`):
                The identifier to retrieve the embeddings.
        """

    @abstractmethod
    async def remove(
        self,
        identifier: JSONSerializableObject,
    ) -> None:
        """Remove the embeddings with the given identifier.

        Args:
            identifier (`JSONSerializableObject`):
                The identifier to remove the embeddings.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Clear all cached embeddings."""
