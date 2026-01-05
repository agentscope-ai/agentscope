# -*- coding: utf-8 -*-
"""The in-memory storage module for memory storage."""
from ._base import MemoryStorageBase
from ...message import Msg


class InMemoryMemoryStorageBase(MemoryStorageBase):
    """The in-memory implementation of MemoryStorage."""

    def __init__(self) -> None:
        """Initialize the in-memory storage."""
        self._content: list[tuple[Msg, str | None]] = []

    async def get_messages(self, mark: str | None = None) -> list[Msg]:
        """Get the messages from the storage by mark (if provided). Otherwise,
        get all messages.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, retrieves all
                messages.

        Raises:
            `TypeError`:
                If the provided mark is not a string or None.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        if mark is None:
            return [msg for msg, _ in self._content]

        if not isinstance(mark, str):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        return [msg for msg, m in self._content if m == mark]

    async def add_message(
        self,
        msg: Msg | list[Msg],
        mark: str | None = None,
    ) -> None:
        """Add message(s) into the storage with the given mark (if provided).

        Args:
            msg (`Msg | list[Msg]`):
                The message(s) to be added.
            mark (`str | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """
        if isinstance(msg, Msg):
            msg = [msg]

        for m in msg:
            self._content.append((m, mark))

    async def remove_messages(self, msg_ids: list[int]) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[int]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        initial_size = len(self._content)
        self._content = [
            (msg, mark) for msg, mark in self._content if msg.id not in msg_ids
        ]
        return initial_size - len(self._content)

    async def remove_messages_by_mark(self, mark: str | list[str]) -> int:
        """Remove messages from the storage by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Raises:
            `TypeError`:
                If the provided mark is not a string or a list of strings.

        Returns:
            `int`:
                The number of messages removed.
        """
        if isinstance(mark, str):
            mark = [mark]

        if isinstance(mark, list) and not all(
            isinstance(m, str) for m in mark
        ):
            raise TypeError(
                f"The mark should be a string or a list of strings, "
                f"but got {type(mark)} with elements of types "
                f"{[type(m) for m in mark]}.",
            )

        initial_size = len(self._content)
        self._content = [(msg, m) for msg, m in self._content if m not in mark]
        return initial_size - len(self._content)

    async def clear(self) -> None:
        """Clear all messages from the storage."""
        self._content.clear()

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        return len(self._content)
