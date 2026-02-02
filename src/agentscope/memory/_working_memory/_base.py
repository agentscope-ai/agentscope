# -*- coding: utf-8 -*-
"""The memory base class."""

from abc import abstractmethod
from typing import Any

from ...message import Msg
from ...module import StateModule


class MemoryBase(StateModule):
    """The base class for memory in agentscope."""

    def __init__(self) -> None:
        """Initialize the memory base."""
        super().__init__()

        self._compressed_summary: str = ""

        self.register_state("_compressed_summary")

    async def update_compressed_summary(self, summary: str) -> None:
        """Update the compressed summary of the memory.

        Args:
            summary (`str`):
                The new compressed summary.
        """
        self._compressed_summary = summary

    @abstractmethod
    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
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
        """

    @abstractmethod
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

    async def delete_by_mark(
        self,
        mark: str | list[str],
        *args: Any,
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
        raise NotImplementedError(
            "The delete_by_mark method is not implemented in "
            f"{self.__class__.__name__} class.",
        )

    @abstractmethod
    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Clear the memory content."""

    @abstractmethod
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

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """

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
        raise NotImplementedError(
            "The update_messages_mark method is not implemented in "
            f"{self.__class__.__name__} class.",
        )
