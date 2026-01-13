# -*- coding: utf-8 -*-
"""The in-memory storage module for memory storage."""
from copy import deepcopy
from typing import Any

from ...message import Msg
from ._base import MemoryBase


class InMemoryMemory(MemoryBase):
    """The in-memory implementation of memory storage."""

    def __init__(self) -> None:
        """Initialize the in-memory storage."""
        super().__init__()
        # Use a list of tuples to store messages along with their marks
        self.content: list[tuple[Msg, list[str]]] = []

        # Register the state for serialization
        self.register_state("content")

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory by mark (if provided). Otherwise,
        get all messages.

        .. note:: If `mark` and `exclude_mark` are both provided, the messages
         will be filtered by both arguments.

        .. note:: `mark` and `exclude_mark` should not overlap.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages. If provided, messages with
                this mark will be excluded from the results.
            prepend_summary (`bool`, defaults to True):
                Whether to prepend the compressed summary as a message

        Raises:
            `TypeError`:
                If the provided mark is not a string or None.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        # Type checks
        if not (mark is None or isinstance(mark, str)):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        if not (exclude_mark is None or isinstance(exclude_mark, str)):
            raise TypeError(
                f"The exclude_mark should be a string or None, but got "
                f"{type(exclude_mark)}.",
            )

        # Filter messages based on mark
        filtered_content = [
            (msg, marks)
            for msg, marks in self.content
            if mark is None or mark in marks
        ]

        # Further filter messages based on exclude_mark
        if exclude_mark is not None:
            filtered_content = [
                (msg, marks)
                for msg, marks in filtered_content
                if exclude_mark not in marks
            ]

        if prepend_summary and self._compressed_summary:
            return [
                Msg(
                    "user",
                    self._compressed_summary,
                    "user",
                ),
                *[msg for msg, _ in filtered_content],
            ]

        return [msg for msg, _ in filtered_content]

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        allow_duplicates: bool = False,
        **kwargs: Any,
    ) -> None:
        """Add message(s) into the memory storage with the given mark
        (if provided).

        Args:
            memories (`Msg | list[Msg] | None`):
                The message(s) to be added.
            marks (`str | list[str] | None`, optional):
                The mark(s) to associate with the message(s). If `None`, no
                mark is associated.
            allow_duplicates (`bool`, defaults to `False`):
                Whether to allow duplicate messages in the storage.
        """
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        if marks is None:
            marks = []
        elif isinstance(marks, str):
            marks = [marks]
        elif not isinstance(marks, list) or not all(
            isinstance(m, str) for m in marks
        ):
            raise TypeError(
                f"The mark should be a string, a list of strings, or None, "
                f"but got {type(marks)}.",
            )

        if not allow_duplicates:
            existing_ids = {msg.id for msg, _ in self.content}
            memories = [msg for msg in memories if msg.id not in existing_ids]

        for msg in memories:
            self.content.append((deepcopy(msg), deepcopy(marks)))

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        initial_size = len(self.content)
        self.content = [
            (msg, marks)
            for msg, marks in self.content
            if msg.id not in msg_ids
        ]
        return initial_size - len(self.content)

    async def delete_by_mark(
        self,
        mark: str | list[str],
        **kwargs: Any,
    ) -> int:
        """Remove messages from the memory by their marks.

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

        initial_size = len(self.content)
        for m in mark:
            self.content = [
                (msg, marks) for msg, marks in self.content if m not in marks
            ]

        return initial_size - len(self.content)

    async def clear(self) -> None:
        """Clear all messages from the storage."""
        self.content.clear()

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        return len(self.content)

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

        for idx, (msg, marks) in enumerate(self.content):
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

            self.content[idx] = (msg, marks)

        return updated_count

    def state_dict(self) -> dict:
        """Get the state dictionary for serialization."""
        return {
            "content": [[msg.to_dict(), marks] for msg, marks in self.content],
        }

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary for deserialization."""
        if strict and "content" not in state_dict:
            raise KeyError(
                "The state_dict does not contain 'content' key required for "
                "InMemoryMemory.",
            )

        self.content = []
        for item in state_dict.get("content", []):
            if isinstance(item, (tuple, list)) and len(item) == 2:
                msg_dict, marks = item
                msg = Msg.from_dict(msg_dict)
                self.content.append((msg, marks))

            elif isinstance(item, dict):
                # For compatibility with older versions
                msg = Msg.from_dict(item)
                self.content.append((msg, []))

            else:
                raise ValueError(
                    "Invalid item format in state_dict for InMemoryMemory.",
                )
