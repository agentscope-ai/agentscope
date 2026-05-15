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

from .workspace_base import WorkspaceBase
from .local_workspace import LocalWorkspace
from .config import MCPServerConfig
from .types import ExecutionResult, InternalEndpoint, SerializedWorkspaceState
from .exceptions import CapabilityError, UnsupportedOperation, WorkspaceError
from .workspace_manager_base import WorkspaceManagerBase
from .local_workspace_manager import LocalWorkspaceManager

__all__ = [
    # base
    "WorkspaceBase",
    # implementations
    "LocalWorkspace",
    # config
    "MCPServerConfig",
    # types
    "ExecutionResult",
    "InternalEndpoint",
    "SerializedWorkspaceState",
    # exceptions
    "CapabilityError",
    "UnsupportedOperation",
    "WorkspaceError",
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
