# -*- coding: utf-8 -*-
"""The workspace module in AgentScope.

Provides agent workspaces backed by local filesystem, Docker
containers, or E2B cloud sandboxes.

Three workspace implementations:

- :class:`LocalWorkspace` — local directory, MCP clients on host.
- :class:`DockerWorkspace` — Docker container with in-container
  MCP gateway.
- :class:`E2BWorkspace` — E2B cloud sandbox with in-container
  MCP gateway.

Two workspace managers (for agent-service deployments):

- :class:`LocalWorkspaceManager`
- :class:`DockerWorkspaceManager`
"""

from ._docker_workspace import InternalEndpoint
from ._local_workspace import LocalWorkspace
from ._types import ExecutionResult, SerializedWorkspaceState
from ._workspace_base import WorkspaceBase
from ._manager import (
    WorkspaceManagerBase,
    LocalWorkspaceManager,
    DockerWorkspaceManager,
    E2BWorkspaceManager,
)

__all__ = [
    # base
    "WorkspaceBase",
    # implementations
    "LocalWorkspace",
    # types
    "ExecutionResult",
    "InternalEndpoint",
    "SerializedWorkspaceState",
    # managers
    "WorkspaceManagerBase",
    "LocalWorkspaceManager",
]
