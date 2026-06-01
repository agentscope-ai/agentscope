# -*- coding: utf-8 -*-
"""Base class for RAG (Retrieval-Augmented Generation) implementations."""
from abc import ABC, abstractmethod

from ..tool import ToolBase


class RAGBase(ABC):
    """The base class for RAG implementations.

    The RAG module supports two types of usages:

    - Retrieve and record manually at specifically time, e.g. before reasoning
    - Providing agent RAG related tools and allowing agent to retrieve and
     record by itself.
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolBase]:
        """List all tools provided by this RAG module.

        Returns:
            `list[ToolBase]`:
                A list of tools provided by this RAG module.
        """

    @abstractmethod
    async def retrieve(self, query: str) -> str:
        """Retrieve relevant information based on the query.

        Args:
            query (`str`):
                The query string.

        Returns:
            `str`:
                The retrieved information.
        """

    @abstractmethod
    async def record(self, query: str) -> None:
        """Record information into the RAG storage.

        Args:
            query (`str`):
                The information to be recorded.
        """

    async def get_instructions(self) -> str | None:
        """Get the instructions for the RAG module, which will be attached to
        the system prompt. Optional to implement.

        Returns:
            `str | None`:
                The instructions for the RAG module.
        """
        return None
