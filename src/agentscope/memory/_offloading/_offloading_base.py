# -*- coding: utf-8 -*-
"""The base class for memory offloading backends.

This module provides a unified abstraction for offloading compressed
memory to storage. When an agent compresses its working memory, the
original messages and their summary can be stored in an offloading
backend for later retrieval.

The offloading system is designed to be:
- **Unified**: A single abstraction reusable across different modules
- **Extensible**: New backends (e.g., vector databases, file-based
  storage) can be implemented by subclassing `MemoryOffloadingBase`
- **Tool-driven**: Each backend decides which tools to expose via
  `list_tools`
- **Async-first**: All operations are asynchronous

Example:
    .. code-block:: python

        from agentscope.memory import InMemoryMemoryOffloading

        offloading = InMemoryMemoryOffloading()
        await offloading.store(messages, summary="User discussed travel")
        tools = offloading.list_tools()  # returns tool callables
"""
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

from ...message import Msg
from ...module import StateModule
from ...tool import ToolResponse


class MemoryOffloadingBase(StateModule, ABC):
    """Base class for memory offloading backends.

    Subclasses must implement `store`, `clear`, and `list_tools` methods.
    The `list_tools` method allows each backend to expose its own set of
    tool functions (e.g., search, browse) to the agent, rather than
    fixing a specific retrieval interface.
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
    async def clear(self) -> None:
        """Clear all offloaded data from the backend."""

    @abstractmethod
    def list_tools(
        self,
    ) -> list[Callable[..., Coroutine[Any, Any, ToolResponse]]]:
        """List all tool functions provided to the agent.

        Each backend decides which tools to expose. For example, an
        in-memory backend may expose a search tool, while a file-based
        backend may rely on existing file tools and return an empty list.

        Returns:
            `list[Callable[..., Coroutine[Any, Any, ToolResponse]]]`:
                A list of async tool functions that return `ToolResponse`.
        """

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
