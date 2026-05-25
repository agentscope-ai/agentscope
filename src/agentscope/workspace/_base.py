# -*- coding: utf-8 -*-
"""WorkspaceBase — abstract interface for agent workspaces.

A workspace provides:

- **Resources** — skills available to the agent.
- **Tools** — MCPs and built-in tools for operating on resources.
- **Offload** — persistence of compressed context and tool results
  for agentic retrieval.

Three concrete implementations:

- `LocalWorkspace` — local filesystem.
- `DockerWorkspace` — Docker container.
- `E2BWorkspace` — E2B cloud sandbox.

Consumers:

- **Agent** — calls ``list_mcps``, ``list_skills``, ``list_tools``,
  ``offload_context``, ``offload_tool_result``.
- **User** — dynamically adds/removes MCPs and skills via
  ``add_mcp``, ``remove_mcp``, ``add_skill``, ``remove_skill``.
- **Developer** — manages lifecycle via ``initialize`` / ``close``.
"""

import uuid
from abc import abstractmethod
from typing import Self

from ..mcp import MCPClient
from ..message import Msg, ToolResultBlock
from ..skill import Skill
from ..tool import ToolBase


class WorkspaceBase:
    """Abstract base class for all workspace implementations.

    Subclasses provide concrete behaviour for one execution backend
    (local filesystem, Docker container, E2B sandbox). The base class
    only fixes the lifecycle contract (``initialize`` / ``close`` /
    ``reset``), the ``async with`` protocol, and the discovery /
    offload / add-remove method signatures consumed by ``Agent`` and
    by the workspace manager layer.

    State held on the base class is intentionally minimal:
    ``workspace_id`` (stable identifier, generated if not given) and
    ``is_alive`` (lifecycle flag). All backend-specific state lives on
    the subclass.
    """

    workspace_id: str
    """Unique identifier for this workspace instance."""

    is_alive: bool
    """If the workspace is still operational."""

    def __init__(self, workspace_id: str | None) -> None:
        """Initialize the workspace base instance."""
        self.workspace_id = workspace_id or uuid.uuid4().hex
        self.is_alive = False

    # ── lifecycle (developer) ──────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Provision resources, connect MCP servers, copy skills."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources and connections."""

    async def reset(self) -> None:
        """Reset the workspace to a clean state.

        Closes and removes all registered MCPs, deletes all skills,
        and wipes per-session state (offloaded context / tool results
        and any data files). Constructor-time ``default_mcps`` and
        ``skill_paths`` are **not** re-seeded — reset returns the
        workspace to an empty state, not its initial state.

        The default implementation is a no-op. Subclasses with user
        state must override this.
        """

    async def __aenter__(self) -> Self:
        """Context manager support for ``async with``. Calls ``initialize()``
        and returns the workspace instance.
        """
        await self.initialize()
        self.is_alive = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Context manager support for ``async with``. Calls ``close()``
        and returns the workspace instance.
        """
        await self.close()
        self.is_alive = False

    # ── instructions ───────────────────────────────────────────────

    @abstractmethod
    async def get_instructions(self) -> str:
        """Workspace-specific system prompt fragment."""

    # ── for Agent: tool & resource discovery ───────────────────────

    @abstractmethod
    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools scoped to this workspace."""

    @abstractmethod
    async def list_mcps(self) -> list[MCPClient]:
        """Active MCP clients (each provides its own tools)."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """Skills available in this workspace."""

    # ── for Agent: offload ─────────────────────────────────────────

    @abstractmethod
    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist compressed context for agentic retrieval.

        Args:
            session_id: Unique session identifier used to
                partition offloaded data.
            msgs: Conversation messages to offload.

        Returns:
            Path or identifier for the offloaded data.
        """

    @abstractmethod
    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a tool result for agentic retrieval.

        Args:
            session_id: Unique session identifier used to
                partition offloaded data.
            tool_result: The tool result block to offload.

        Returns:
            Path or identifier for the offloaded data.
        """

    # ── for User: dynamic MCP management ───────────────────────────

    @abstractmethod
    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Dynamically register a new MCP server.

        Args:
            mcp_client: An :class:`MCPClient` instance describing
                the MCP server to add.

        Raises:
            ValueError: If an MCP with the same name already exists.
        """

    @abstractmethod
    async def remove_mcp(self, name: str) -> None:
        """Dynamically remove an MCP server by name.

        Args:
            name: Name of the MCP server to remove.
        """

    # ── for User: dynamic skill management ─────────────────────────

    @abstractmethod
    async def add_skill(self, skill_path: str) -> None:
        """Add a skill from a local directory path.

        The directory must contain a ``SKILL.md`` with ``name``
        and ``description`` in its YAML front matter.

        Args:
            skill_path: Absolute or relative path to the skill
                directory on the local filesystem.
        """

    @abstractmethod
    async def remove_skill(self, name: str) -> None:
        """Remove a skill by its agent-facing name.

        Args:
            name: The ``name`` field from the skill's
                ``SKILL.md`` front matter.

        Raises:
            KeyError: If the skill is not found in the workspace.
        """
