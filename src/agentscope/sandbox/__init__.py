# -*- coding: utf-8 -*-
"""Sandbox layer for AgentScope.

Three layers in one package:

Layer 1 (bottom):  ``SandboxConnection`` — backend primitives
                   (exec, read, write, destroy, ...)
                   + module-level ``create_sandbox_connection`` registry
                   for dispatch.
Layer 2 (middle):  ``Sandbox`` — agent-facing proxy
                   (tools, skills, MCP, file facade).
Layer 3 (top):     ``SandboxManager`` — session lifecycle, pooling.
"""

# --- Layer 1: backend primitives ---
from .connection import (
    SandboxConnection,
    create_sandbox_connection,
    get_sandbox_connection_type,
    register_sandbox_connection_type,
)
from .exceptions import CapabilityError, SandboxError, UnsupportedOperation
from .types import (
    SandboxExecutionResult,
    SandboxInternalEndpoint,
    SandboxConnectionCapabilities,
    SandboxInitializationConfig,
    SerializedSandboxState,
)

# --- Config ---
from .backend_config import (
    BackendParams,
    DockerBackendParams,
    E2BBackendParams,
    LocalBackendParams,
)
from .config import (
    MCPServerConfig,
    SandboxConfig,
    SkillsConfig,
    ToolDefinition,
)
from .mcp_gateway import MCPGatewayConfig

# --- Layer 2: agent-side proxy ---
from .sandbox import FileAccessor, Sandbox

# --- Layer 2.5: MCP gateway ---
from .mcp_gateway import MCPGateway

# --- Layer 3: manager ---
from .sandbox_manager import SandboxManager

# --- Backends (register themselves on import) ---
from . import local_temp as _local_temp  # noqa: F401

try:
    from .docker import DockerSandboxConnection  # noqa: F401
except ImportError:
    DockerSandboxConnection = None  # type: ignore[assignment,misc]

try:
    from .e2b import E2BSandboxConnection  # noqa: F401
except ImportError:
    E2BSandboxConnection = None  # type: ignore[assignment,misc]

__all__ = [
    # exceptions
    "CapabilityError",
    "SandboxError",
    "UnsupportedOperation",
    # types
    "SandboxExecutionResult",
    "SandboxInternalEndpoint",
    "SandboxConnectionCapabilities",
    "SandboxInitializationConfig",
    "SerializedSandboxState",
    # layer 1
    "SandboxConnection",
    "create_sandbox_connection",
    "get_sandbox_connection_type",
    "register_sandbox_connection_type",
    # backend config
    "BackendParams",
    "DockerBackendParams",
    "E2BBackendParams",
    "LocalBackendParams",
    # sandbox config
    "MCPGatewayConfig",
    "MCPServerConfig",
    "SandboxConfig",
    "SkillsConfig",
    "ToolDefinition",
    # layer 2
    "FileAccessor",
    "Sandbox",
    # gateway
    "MCPGateway",
    # layer 3
    "SandboxManager",
    # backends
    "DockerSandboxConnection",
    "E2BSandboxConnection",
]
