# -*- coding: utf-8 -*-
"""The base class for short-term memory storage in AgentScope."""
from abc import abstractmethod

from ...message import Msg


class MemoryStorageBase:
    """The base class for memory database storage in AgentScope."""

    @abstractmethod
    async def update_messages_mark(
            self,
            new_mark: str | None,
            old_mark: str | None = None,
            msg_ids: list[str] | None = None,
    ) -> int:
        """A unified method to update marks of messages in the storage (add,
        remove, or change marks).

        - If `msg_ids` is provided, the update will be applied to the messages
         with the specified IDs.
        - If `old_mark` is provided, the update will be applied to the
         messages with the specified old mark. Otherwise, the `new_mark` will
         be added to all messages (or those filtered by `msg_ids`).
        - If `new_mark` is `None`, the mark will be removed from the messages.

        Args:
            new_mark (`str | None`, optional):
                The new mark to set for the messages. If `None`, the mark
                will be removed.
            old_mark (`str | None`, optional):
                The old mark to filter messages. If `None`, this constraint
                is ignored.
            msg_ids (`list[str] | None`, optional):
                The list of message IDs to be updated. If `None`, this
                constraint is ignored.

        Returns:
            `int`:
                The number of messages updated.
        """

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
        self,
        msg: Msg | list[Msg],
        mark: str | None = None,
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
    async def size(self, mark: str | None) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
