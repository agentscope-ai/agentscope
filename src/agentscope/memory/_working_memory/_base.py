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

    def update_compressed_summary(self, summary: str) -> None:
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
        mark: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add items to the memory."""

    @abstractmethod
    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Delete items from the memory."""

    async def delete_by_mark(
        self,
        mark: str | list[str],
        *args: Any,
        **kwargs: Any,
    ) -> int:
        """Delete items from the memory by mark."""
        raise NotImplementedError(
            "The delete_by_mark method is not implemented in "
            f"{self.__class__.__name__} class.",
        )

    @abstractmethod
    async def size(self) -> int:
        """Get the size of the memory."""

    @abstractmethod
    async def clear(self) -> None:
        """Clear the memory content."""

    @abstractmethod
    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory by mark (if provided). Otherwise,
        get all messages.

        .. note:: If provided a list of strings as `mark` or `exclude_mark`,
         these marks will be treated as an OR condition.

        .. note:: `mark` and `exclude_mark` should not overlap.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages. If provided, messages with
                this mark will be excluded from the results.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
