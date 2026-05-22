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

from .docker_workspace import InternalEndpoint
from .local_workspace import LocalWorkspace
from .local_workspace_manager import LocalWorkspaceManager
from .types import ExecutionResult, SerializedWorkspaceState
from .workspace_base import WorkspaceBase
from .workspace_manager_base import WorkspaceManagerBase

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

# Optional imports — don't fail if docker/e2b not installed
try:
    from .docker_workspace import DockerWorkspace

    __all__.append("DockerWorkspace")
except ImportError:
    DockerWorkspace = None  # type: ignore[assignment,misc]

try:
    from .docker_workspace_manager import DockerWorkspaceManager

    __all__.append("DockerWorkspaceManager")
except ImportError:
    DockerWorkspaceManager = None  # type: ignore[assignment,misc]

try:
    from .e2b_workspace import E2BWorkspace

    __all__.append("E2BWorkspace")
except ImportError:
    E2BWorkspace = None  # type: ignore[assignment,misc]

try:
    from .e2b_workspace_manager import E2BWorkspaceManager

    __all__.append("E2BWorkspaceManager")
except ImportError:
    E2BWorkspaceManager = None  # type: ignore[assignment,misc]
