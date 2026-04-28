# -*- coding: utf-8 -*-
"""The base class for offloading."""
from abc import abstractmethod, ABC
from typing import Any

from ..message import Msg, ToolResultBlock, DataBlock, TextBlock


class OffloadBase(ABC):
    """The offload base class for agentic retrieval, e.g. offload the
    compressed context into accessible MarkDown files so that the agent
    can read them by file reading tools.
    """

    @abstractmethod
    async def offload_context(self, msgs: list[Msg], **kwargs: Any) -> None:
        """Offload the compressed context into agent accessible content."""

    @abstractmethod
    async def offload_tool_result(
        self,
        blocks: list[ToolResultBlock],
        **kwargs: Any,
    ) -> None:
        """Offload the tool results into agent accessible content."""

    @abstractmethod
    async def offload_plan(self, plan: str, **kwargs: Any) -> None:
        """Offload the finished or deprecated plan."""

    @abstractmethod
    async def offload_summary(
        self,
        summary: list[TextBlock | DataBlock],
        **kwargs: Any,
    ) -> None:
        """Offload the outdated context summary."""
