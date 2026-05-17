# -*- coding: utf-8 -*-
"""The workspace base class."""
from abc import abstractmethod
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ..mcp import MCPClient
from ..skill import Skill
from ..message import Msg, ToolResultBlock
from ..tool import ToolBase


class WorkspaceBase(BaseModel):
    """Abstract base class representing the execution environment of an agent.

    Inherits from ``BaseModel`` so that each workspace instance is directly
    serialisable via ``model_dump()`` / ``model_validate()``.  The ``type``
    discriminator field enables the manager to reconstruct the correct
    subclass from ``WorkspaceRecord.data`` without a separate factory.

    Subclasses must declare ``type: Literal["<name>"] = "<name>"`` and
    implement all abstract methods.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    """Unique workspace identifier, assigned at creation."""

    type: str
    """Discriminator field — subclasses fix this to a Literal value."""

    model_config = {"arbitrary_types_allowed": True}

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the workspace (connect MCPs, copy skills, etc.)."""

    @abstractmethod
    async def close(self) -> None:
        """Close the workspace and release all resources."""

    @abstractmethod
    async def get_instructions(self) -> str:
        """Return workspace instructions appended to the agent
        system prompt."""

    @abstractmethod
    async def list_tools(self) -> list[ToolBase]:
        """Return all tools available in this workspace."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """Return all skills available in this workspace."""

    @abstractmethod
    async def list_mcps(self) -> list[MCPClient]:
        """Return all MCP clients attached to this workspace."""

    @abstractmethod
    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        """Offload compressed context to workspace-accessible storage."""

    @abstractmethod
    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        """Offload a tool result to workspace-accessible storage."""

    @abstractmethod
    async def add_mcp(self, mcp: MCPClient) -> None:
        """Add an MCP client and connect it if stateful."""

    @abstractmethod
    async def remove_mcp(self, mcp_name: str) -> None:
        """Remove an MCP client by name, disconnecting it if stateful."""

    @abstractmethod
    async def add_skill(self, skill_path: str) -> None:
        """Add a skill to the workspace by copying from the given path."""

    @abstractmethod
    async def remove_skill(self, skill_name: str) -> None:
        """Remove a skill from the workspace by copying from the given path."""
