# -*- coding: utf-8 -*-
"""The embedding store base class."""
from abc import abstractmethod
from typing import Any

from .. import Document
from ...types import Embedding


class VDBStoreBase:
    """The vector database store base class, serving as a middle layer between
    the knowledge base and the actual vector database implementation."""

    @abstractmethod
    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Record the documents into the vector database."""

    @abstractmethod
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete texts from the embedding store."""

    @abstractmethod
    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant texts for the given queries.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The threshold of the score to filter the results.
            **kwargs (`Any`):
                Other keyword arguments for the vector database search API.
        """

    def get_client(self) -> Any:
        """Get the underlying vector database client, so that developers can
        access the full functionality of the vector database."""
        raise NotImplementedError(
            "``get_client`` is not implemented for "
            f"{self.__class__.__name__}.",
        )
