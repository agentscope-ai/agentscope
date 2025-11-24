# -*- coding: utf-8 -*-
"""The memory storage base class."""
from abc import abstractmethod
from typing import Any, Iterable, Union

from agentscope.message import Msg


class MessageStorageBase:
    """The message storage base class."""

    @abstractmethod
    async def get_storage_client(self) -> Any:
        """Get the storage client."""

    @abstractmethod
    async def add(self, messages: list[Msg], **kwargs: Any) -> None:
        """Record the messages into the message storage."""

    @abstractmethod
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete the messages from the message storage."""

    @abstractmethod
    async def clear(self, **kwargs: Any) -> None:
        """Clear the messages from the message storage."""

    @abstractmethod
    async def get(self, **kwargs: Any) -> list[Msg]:
        """Get the messages from the memory storage."""

    @abstractmethod
    async def update_all(self, messages: list[Msg], **kwargs: Any) -> None:
        """Update the messages in the message storage."""

    @abstractmethod
    def get_client(self) -> Any:
        """get the underlying storage client, so that developers can
        access the full functionality
        """


class InMemoryMessageStorage(MessageStorageBase):
    """The in-memory message storage."""

    def __init__(self) -> None:
        """Initialize the in-memory message storage."""
        super().__init__()
        self._storage_client = []

    async def get_storage_client(self) -> Any:
        """Get the storage client."""
        return self._storage_client

    async def add(self, messages: list[Msg], **kwargs: Any) -> None:
        """Record the messages into the message storage."""
        self._storage_client.extend(messages)

    async def delete(
        self,
        indices: Union[Iterable[int], int],
        **kwargs: Any,
    ) -> None:
        """Delete the messages from the message storage."""
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

    async def get(self, **kwargs: Any) -> list[Msg]:
        """Get the messages from the message storage."""
        return self._storage_client

    async def update_all(self, messages: list[Msg], **kwargs: Any) -> None:
        """Update the messages in the message storage."""
        self._storage_client = messages

    def get_client(self) -> list[Msg]:
        """get the underlying storage client, so that developers can
        access the full functionality
        """
        return self._storage_client
