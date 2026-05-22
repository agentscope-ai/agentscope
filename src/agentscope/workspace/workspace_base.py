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

import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

if TYPE_CHECKING:
    from ..mcp import MCPClient
    from ..message import Msg, ToolResultBlock
    from ..skill import Skill
    from ..tool import ToolBase
    from .types import ExecutionResult


class WorkspaceBase(BaseModel):
    """Abstract base class for all workspace implementations.

    Serializable configuration fields are declared as pydantic Fields.
    Runtime state (connections, clients, etc.) uses PrivateAttr and is
    excluded from serialisation.  Use ``model_dump()`` /
    ``model_validate()`` for export/restore of workspace configuration.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── identity ──────────────────────────────────────────────────

    workspace_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="Unique identifier for this workspace instance.",
    )

    # ── runtime state (excluded from serialisation) ───────────────

    _started: bool = PrivateAttr(default=False)

    # ── lifecycle (developer) ──────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Provision resources, connect MCP servers, copy skills."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources and connections."""

    async def reset(self) -> None:
        """Reset the workspace to a clean state.

        Clears user-specific state such as session data, temporary files,
        and dynamically added MCPs/skills. Called by the pool manager
        before returning a workspace to the free queue.

        The default implementation is a no-op. Subclasses that manage
        per-user state should override this.
        """

    async def is_alive(self) -> bool:
        """Check if the workspace is still operational.

        Override in container/sandbox backends to perform a real
        liveness check. Defaults to ``True`` for local workspaces.
        """
        return True

    @abstractmethod
    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> "ExecutionResult":
        """Execute a shell command inside the workspace environment.

        For local workspaces this runs a subprocess on the host; for
        container/sandbox workspaces the command runs remotely.

        Args:
            command: Shell command string to execute.
            timeout: Maximum seconds to wait before aborting.
                ``None`` means no limit.

        Returns:
            An :class:`ExecutionResult` with exit code, stdout,
            and stderr.
        """

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
        tool_result: "ToolResultBlock",
        **kwargs: Any,
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
    async def add_mcp(self, mcp_client: "MCPClient") -> None:
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
