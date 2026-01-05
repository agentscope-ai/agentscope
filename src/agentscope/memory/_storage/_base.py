# -*- coding: utf-8 -*-
"""The base class for short-term memory storage in AgentScope."""
from abc import abstractmethod

from ...message import Msg


class MemoryStorageBase:
    """The base class for memory database storage in AgentScope."""

    @abstractmethod
    async def get_messages(self, mark: str | None = None) -> list[Msg]:
        """Get uncompressed messages from the storage.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, retrieves all
                messages.
        """

    @abstractmethod
    async def add_message(
        self, msg: Msg | list[Msg], mark: str | None = None
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            msg (`Msg | list[Msg]`):
                The message(s) to be added.
            mark (`str | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """

    @abstractmethod
    async def remove_messages(
        self,
        msg_ids: list[int],
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[int]`):
                The list of message IDs to be removed.
        """

    @abstractmethod
    async def remove_messages_by_mark(
        self,
        mark: str | list[str],
    ) -> int:
        """Remove messages from the storage by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Clear all messages from the storage."""

    @abstractmethod
    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
