# -*- coding: utf-8 -*-
"""The base class for memory offloading storage backends.

This module provides a unified abstraction for offloading compressed
memory to searchable storage. When an agent compresses its working
memory, the original messages and their summary can be stored in an
offloading backend for later retrieval via search.

The offloading system is designed to be:
- **Unified**: A single abstraction reusable across different modules
- **Extensible**: New backends (e.g., vector databases, embedding-based
  search) can be implemented by subclassing `MemoryOffloadingBase`
- **Async-first**: All operations are asynchronous

Example:
    .. code-block:: python

        from agentscope.memory import InMemorySearchableStorage

        storage = InMemorySearchableStorage()
        await storage.store(messages, summary="User discussed travel plans")
        results = await storage.search("travel", limit=3)
"""
from abc import ABC, abstractmethod
from typing import Any

from ...message import Msg


class MemoryOffloadingBase(ABC):
    """Base class for memory offloading storage backends.

    Subclasses must implement `store`, `search`, and `clear` methods
    to provide different storage and retrieval strategies.
    """

    @abstractmethod
    async def store(
        self,
        msgs: list[Msg],
        summary: str,
        **kwargs: Any,
    ) -> None:
        """Store compressed messages and their summary in the backend.

        Args:
            msgs (`list[Msg]`):
                The original messages that were compressed.
            summary (`str`):
                The LLM-generated summary of the compressed messages.
            **kwargs (`Any`):
                Additional keyword arguments for the storage operation.
        """

    @abstractmethod
    async def search(
        self,
        query: str,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[dict]:
        """Search the offloaded memories by query.

        Args:
            query (`str`):
                The search query string.
            limit (`int`, optional):
                The maximum number of results to return. Defaults to 5.
            **kwargs (`Any`):
                Additional keyword arguments for the search operation.

        Returns:
            `list[dict]`:
                A list of result dictionaries, each containing:
                - ``summary`` (`str`): The summary of the compressed chunk
                - ``timestamp`` (`str`): When the chunk was offloaded
                - ``num_messages`` (`int`): Number of original messages
        """

    @abstractmethod
    async def clear(self) -> None:
        """Clear all offloaded data from the backend."""

    async def size(self) -> int:
        """Return the number of offloaded chunks.

        Returns:
            `int`:
                The number of offloaded chunks in the backend.
        """
        raise NotImplementedError(
            f"The `size` method is not implemented in "
            f"{self.__class__.__name__}.",
        )
