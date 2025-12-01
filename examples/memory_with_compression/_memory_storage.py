# -*- coding: utf-8 -*-
"""The memory storage base class."""
from abc import abstractmethod
from typing import Any, Iterable, Optional, Union, cast
from types import TracebackType

from agentscope.message import Msg


class MessageStorageBase:
    """The message storage base class."""

    @abstractmethod
    async def start(self, **kwargs: Any) -> None:
        """Establish connection to the storage backend.

        This method should be called after initialization to perform
        async setup operations that cannot be done in __init__.
        """

    @abstractmethod
    async def stop(self, **kwargs: Any) -> None:
        """Close the connection and clean up resources.

        This method should be called when the storage is no longer needed
        to properly release resources.
        """

    @abstractmethod
    async def health(self, **kwargs: Any) -> bool:
        """Check the health status of the storage backend.

        Returns:
            bool: True if the storage is healthy and accessible,
                False otherwise.
        """

    @abstractmethod
    async def add(self, messages: list[Msg], **kwargs: Any) -> None:
        """Record the messages into the message storage.

        Args:
            messages (`list[Msg]`):
                The messages to be added to storage
        """

    @abstractmethod
    async def delete(
        self,
        indices: Union[Iterable[int], int, None] = None,
        **kwargs: Any,
    ) -> None:
        """Delete the messages from the message storage.

        Args:
            indices (`Iterable[int] | int | None`, optional):
                The indices of messages to delete. If None, no messages
                are deleted.
            **kwargs (`Any`):
                Additional keyword arguments for storage-specific options.
        """

    @abstractmethod
    async def clear(self, **kwargs: Any) -> None:
        """Clear the messages from the message storage."""

    @abstractmethod
    async def get(
        self,
        recent_n: Optional[int] = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory storage.

        Args:
            recent_n (`Optional[int]`, optional):
                The number of recent messages to retrieve

        Returns:
            `list[Msg]`:
                The list of messages from storage
        """

    @abstractmethod
    async def replace(self, messages: list[Msg], **kwargs: Any) -> None:
        """Update the messages in the message storage.

        Args:
            messages (`list[Msg]`):
                The new messages to replace existing storage content
        """

    @abstractmethod
    async def __aenter__(self) -> "MessageStorageBase":
        """Async context manager entry.

        Returns:
            `MessageStorageBase`:
                The storage instance
        """

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """Async context manager exit.

        Args:
            exc_type (`Optional[type[BaseException]]`):
                The exception type if an exception occurred
            exc_val (`Optional[BaseException]`):
                The exception value if an exception occurred
            exc_tb (`Optional[TracebackType]`):
                The exception traceback if an exception occurred

        Returns:
            `bool`:
                False to propagate exceptions
        """


class InMemoryMessageStorage(MessageStorageBase):
    """The in-memory message storage."""

    def __init__(self) -> None:
        """Initialize the in-memory message storage.

        Note: For async setup operations, call start() after initialization.
        """
        self._storage_client: list[Msg] = []
        self._connected: bool = False

    async def start(self, **kwargs: Any) -> None:
        """Establish connection to the storage backend.

        For in-memory storage, this initializes the storage list and
        sets the connection flag. Kept for interface consistency with
        other storage backends that require async connection setup.
        """

        if hasattr(self, "_connected") and self._connected:
            return
        self._storage_client = []
        self._connected = True

    async def stop(self, **kwargs: Any) -> None:
        """Close the connection and clean up resources.

        For in-memory storage, this clears the storage and resets
        the connection flag.
        """
        if hasattr(self, "_connected") and not self._connected:
            return
        self._storage_client = []
        self._connected = False

    async def health(self, **kwargs: Any) -> bool:
        """Check the health status of the storage backend.

        Returns:
            bool: True if the storage is healthy and accessible,
                False otherwise.
        """
        return hasattr(self, "_connected") and self._connected is True

    async def add(self, messages: list[Msg], **kwargs: Any) -> None:
        """Record the messages into the message storage.

        Args:
            messages (`list[Msg]`):
                The messages to be added to storage
        """
        self._storage_client.extend(messages)

    async def delete(
        self,
        indices: Union[Iterable[int], int, None] = None,
        **kwargs: Any,
    ) -> None:
        """Delete the messages from the message storage.

        Args:
            indices (`Iterable[int] | int | None`, optional):
                The indices of messages to delete. If None, no messages
                are deleted.
            **kwargs (`Any`):
                Additional keyword arguments for storage-specific options.
        """
        if indices is None:
            return
        if isinstance(indices, int):
            indices = [indices]
        indices_set = set(indices)
        self._storage_client = [
            msg
            for idx, msg in enumerate(self._storage_client)
            if idx not in indices_set
        ]

    async def clear(self, **kwargs: Any) -> None:
        """Clear the messages from the message storage."""
        self._storage_client = []

    async def get(
        self,
        recent_n: Optional[int] = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory storage.

        Args:
            recent_n (`Optional[int]`, optional):
                The number of recent messages to retrieve. If None,
                returns all messages.

        Returns:
            `list[Msg]`:
                The list of messages from storage
        """
        if recent_n is None:
            return self._storage_client
        # Type narrowing: recent_n is guaranteed to be int at this point
        n = cast(int, recent_n)
        if n > len(self._storage_client):
            return self._storage_client
        # Use positive index to avoid unary minus operator
        start_idx = len(self._storage_client) - n
        return self._storage_client[start_idx:]

    async def replace(self, messages: list[Msg], **kwargs: Any) -> None:
        """Replace the messages in the message storage.

        Args:
            messages (`list[Msg]`):
                The new messages to replace the existing storage content
        """
        self._storage_client = messages

    async def __aenter__(self) -> "InMemoryMessageStorage":
        """Async context manager entry.

        Returns:
            `InMemoryMessageStorage`:
                The storage instance
        """
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """Async context manager exit.

        Args:
            exc_type (`Optional[type[BaseException]]`):
                The exception type if an exception occurred
            exc_val (`Optional[BaseException]`):
                The exception value if an exception occurred
            exc_tb (`Optional[TracebackType]`):
                The exception traceback if an exception occurred

        Returns:
            `bool`:
                False to propagate exceptions
        """
        await self.stop()
        return False
