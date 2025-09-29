# -*- coding: utf-8 -*-
"""
Base class for memory

TODO: a abstract class for a piece of memory
TODO: data structure to organize multiple memory pieces in memory class
"""

from abc import ABC, abstractmethod
from typing import Iterable, Sequence, Any
from typing import Optional
from typing import Union
from typing import Callable
from ..module import StateModule

from ..message import Msg


class MemoryBase(StateModule):
    """Base class for memory."""

    _version: int = 1

    @abstractmethod
    def load(
        self,
        memories: Union[str, list[Msg], Msg],
        overwrite: bool = False,
    ) -> None:
        """
        Load memory, depending on how the memory are passed, design to load
        from both file or dict
        Args:
            memories (Union[str, list[Msg], Msg]):
                memories to be loaded.
                If it is in str type, it will be first checked if it is a
                file; otherwise it will be deserialized as messages.
                Otherwise, memories must be either in message type or list
                 of messages.
            overwrite (bool):
                if True, clear the current memory before loading the new ones;
                if False, memories will be appended to the old one at the end.
        """

    @abstractmethod
    def export(
        self,
        file_path: Optional[str] = None,
        to_mem: bool = False,
    ) -> Optional[list]:
        """
        Export memory, depending on how the memory are stored
        Args:
            file_path (Optional[str]):
                file path to save the memory to.
            to_mem (Optional[str]):
                if True, just return the list of messages in memory
        Notice: this method prevents file_path is None when to_mem
        is False.
        """

    @abstractmethod
    def size(self) -> int:
        """Returns the number of memory segments in memory."""
        raise NotImplementedError

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