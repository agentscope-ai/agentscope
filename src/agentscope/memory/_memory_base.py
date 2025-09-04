# -*- coding: utf-8 -*-
"""The memory base class."""

from abc import abstractmethod
from typing import Any

from ..message import Msg
from ..module import StateModule


class MemoryBase(StateModule):
    """The base class for memory in agentscope."""

    @abstractmethod
    async def add(self, *args: Any, **kwargs: Any) -> None:
        """Add items to the memory."""

    @abstractmethod
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete items from the memory."""

    @abstractmethod
    async def retrieve(self, *args: Any, **kwargs: Any) -> None:
        """Retrieve items from the memory."""

    @abstractmethod
    async def size(self) -> int:
        """Get the size of the memory."""

    @abstractmethod
    async def clear(self) -> None:
        """Clear the memory content."""

    @abstractmethod
    async def get_memory(self, *args: Any, **kwargs: Any) -> list[Msg]:
        """Get the memory content."""

    @abstractmethod
    def state_dict(self) -> dict:
        """Get the state dictionary of the memory."""

    @abstractmethod
    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary of the memory."""
