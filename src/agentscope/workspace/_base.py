# -*- coding: utf-8 -*-
"""The workspace base class."""
from abc import abstractmethod, ABC
from typing import Any

from ..skill import Skill
from ..message import Msg, ToolResultBlock
from ..tool import ToolBase


class WorkspaceBase(ABC):
    """Abstract base class representing the execution environment of an agent.

    A workspace defines where and how an agent operates, and is
    responsible for:

    - Providing tools scoped to this environment (e.g. Bash, file I/O tools)
    - Offloading compressed context to support agentic retrieval
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the workspace, e.g. initialize the MCPs, copy the skills
        into the workspace and so on."""

    @abstractmethod
    async def close(self) -> None:
        """Close the workspace and clean the resource."""

    @abstractmethod
    async def get_instructions(self) -> str:
        """Retrieve the workspace instructions. The instructions will be
        appended to the system prompt to guide the agent on how to
        interact with this workspace.

        Returns:
            `str`:
                The workspace instructions.
        """

    @abstractmethod
    async def list_tools(self) -> list[ToolBase]:
        """List all tools available in the workspace.

        Returns:
            `list[ToolBase]`:
                The list of tools available in the workspace.
        """

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """List all skills available in the workspace.

        Returns:
            `list[Skill]`:
                The list of skills available in the workspace.
        """

    @abstractmethod
    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        """Offload the compressed context into agent accessible content."""

    @abstractmethod
    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        """Offload the tool results into agent accessible content."""
