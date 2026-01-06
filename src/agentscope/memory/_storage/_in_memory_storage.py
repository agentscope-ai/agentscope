# -*- coding: utf-8 -*-
"""The in-memory storage module for memory storage."""
from ._base import MemoryStorageBase
from ...message import Msg


class InMemoryMemoryStorage(MemoryStorageBase):
    """The in-memory implementation of MemoryStorage."""

    def __init__(self) -> None:
        """Initialize the in-memory storage."""
        self._content: list[tuple[Msg, list[str]]] = []

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

        return [msg for msg, marks in self._content if mark in marks]

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
            if mark:
                self._content.append((m, [mark]))
            else:
                self._content.append((m, []))

    async def remove_messages(self, msg_ids: list[str]) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        initial_size = len(self._content)
        self._content = [
            (msg, marks) for msg, marks in self._content if msg.id not in msg_ids
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
        for m in mark:
            self._content = [
                (msg, marks) for msg, marks in self._content if m not in marks
            ]

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
        updated_count = 0

        for idx, (msg, marks) in enumerate(self._content):
            # If msg_ids is provided, skip messages not in the list
            if msg_ids is not None and msg.id not in msg_ids:
                continue

            # If old_mark is provided, skip messages that do not have the old
            # mark
            if old_mark is not None and old_mark not in marks:
                continue

            # If new_mark is None, remove the old_mark
            if new_mark is None:
                if old_mark in marks:
                    marks.remove(old_mark)
                    updated_count += 1

            else:
                # If new_mark is provided, add or replace the old_mark
                if old_mark is not None and old_mark in marks:
                    marks.remove(old_mark)
                if new_mark not in marks:
                    marks.append(new_mark)
                    updated_count += 1

            self._content[idx] = (msg, marks)

        return updated_count
