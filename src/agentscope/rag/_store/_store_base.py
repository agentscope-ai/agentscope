# -*- coding: utf-8 -*-
"""The embedding store base class."""
from abc import abstractmethod
from typing import Any
from xml.dom.minidom import Document


class VDBStoreBase:
    """The vector database store base class, serving as a middle layer between
    the knowledge base and the actual vector database implementation."""

    @abstractmethod
    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Record the documents into the vector database."""

    @abstractmethod
    async def delete(self, *args, **kwargs) -> None:
        """Delete texts from the embedding store."""

    @abstractmethod
    async def retrieve(
        self,
        queries: list[str],
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant texts for the given queries.

        Args:
            queries (`list[str]`):
                The list of queries to be queried.
            **kwargs (`Any`):
                Other keyword arguments for the vector database search API.
        """
