# -*- coding: utf-8 -*-
"""WorkspaceBase — abstract interface for agent workspaces.

A workspace provides:

- **Resources** — skills available to the agent.
- **Tools** — MCPs and built-in tools for operating on resources.
- **Offload** — persistence of compressed context and tool results
  for agentic retrieval.

Three concrete implementations:

- :class:`~.local_workspace.LocalWorkspace` — local filesystem.
- :class:`~.docker_workspace.DockerWorkspace` — Docker container.
- :class:`~.e2b_workspace.E2BWorkspace` — E2B cloud sandbox.

Consumers:

- **Agent** — calls ``list_mcps``, ``list_skills``, ``list_tools``,
  ``offload_context``, ``offload_tool_result``.
- **User** — dynamically adds/removes MCPs and skills via
  ``add_mcp``, ``remove_mcp``, ``add_skill``, ``remove_skill``.
- **Developer** — manages lifecycle via ``initialize`` / ``close``.
"""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..mcp import MCPClient
    from ..message import Msg, ToolResultBlock
    from ..skill import Skill
    from ..tool import ToolBase
    from .config import MCPServerConfig


class WorkspaceBase(ABC):
    """Abstract base class for all workspace implementations."""

    # ── identity ──────────────────────────────────────────────────

    @property
    @abstractmethod
    def workspace_id(self) -> str:
        """Unique identifier for this workspace instance."""

    # ── lifecycle (developer) ──────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Provision resources, connect MCP servers, copy skills."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources and connections."""

    async def is_alive(self) -> bool:
        """Check if the workspace is still operational.

        Override in container/sandbox backends to perform a real
        liveness check. Defaults to ``True`` for local workspaces.
        """
        return True

    async def __aenter__(self) -> "WorkspaceBase":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── instructions ───────────────────────────────────────────────

    @abstractmethod
    async def get_instructions(self) -> str:
        """Workspace-specific system prompt fragment."""

    # ── for Agent: tool & resource discovery ───────────────────────

    @abstractmethod
    async def list_tools(self) -> list["ToolBase"]:
        """Built-in tools scoped to this workspace."""

    @abstractmethod
    async def list_mcps(self) -> list["MCPClient"]:
        """Active MCP clients (each provides its own tools)."""

    @abstractmethod
    async def list_skills(self) -> list["Skill"]:
        """Skills available in this workspace."""

    # ── for Agent: offload ─────────────────────────────────────────

    @abstractmethod
    async def offload_context(
        self,
        session_id: str,
        msgs: list["Msg"],
        **kwargs: Any,
    ) -> str:
        """Persist compressed context for agentic retrieval.

        Returns:
            Path or identifier for the offloaded data.
        """

    @abstractmethod
    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: "ToolResultBlock",
        **kwargs: Any,
    ) -> str:
        """Persist a tool result for agentic retrieval.

        Returns:
            Path or identifier for the offloaded data.
        """

    # ── for User: dynamic MCP management ───────────────────────────

    @abstractmethod
    async def add_mcp(self, config: "MCPServerConfig") -> None:
        """Dynamically register a new MCP server."""

    @abstractmethod
    async def remove_mcp(self, name: str) -> None:
        """Dynamically remove an MCP server by name."""

    # ── for User: dynamic skill management ─────────────────────────

    @abstractmethod
    async def add_skill(self, skill_path: str) -> None:
        """Add a skill from a local directory path.

        The directory must contain a ``SKILL.md`` with ``name`` and
        ``description`` in its YAML front matter.
        """

    @abstractmethod
    async def remove_skill(self, name: str) -> None:
        """Remove a skill by its agent-facing name."""
