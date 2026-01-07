# -*- coding: utf-8 -*-
"""The memory base class."""

from abc import abstractmethod
from typing import Any

from ...message import Msg
from ...module import StateModule


class MemoryBase(StateModule):
    """The base class for memory in agentscope."""

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
    async def get_memory(self, *args: Any, **kwargs: Any) -> list[Msg]:
        """Get the memory content."""
