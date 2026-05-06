# -*- coding: utf-8 -*-
"""Sandbox layer for AgentScope.

Three layers in one package:

Layer 1 (bottom):  ``SandboxConnection`` — backend primitives
                   (exec, read, write, destroy, ...)
                   + module-level ``create_connection`` registry for dispatch.
Layer 2 (middle):  ``Sandbox`` — agent-facing proxy
                   (tools, skills, MCP, file facade).
Layer 3 (top):     ``SandboxManager`` + ``SandboxPool`` — session lifecycle
                   & pooling.
"""

# --- Layer 1: backend primitives ---
from .connection import (
    SandboxConnection,
    create_connection,
    get_connection_class,
    register_connection_class,
)
from .exceptions import CapabilityError, SandboxError, UnsupportedOperation
from .types import (
    ExecResult,
    ExposedPortEndpoint,
    SandboxConnectionCapabilities,
    SandboxCreateOptions,
    SerializedSandboxState,
)

# --- Config ---
from .config import (
    BackendParams,
    DockerBackendParams,
    E2BBackendParams,
    LocalBackendParams,
    McpGatewayConfig,
    McpServerConfig,
    SandboxConfig,
    SkillConfig,
    ToolDef,
)

# --- Layer 2: agent-side proxy ---
from .sandbox import FileAccessor, Sandbox

# --- Layer 2.5: MCP gateway ---
from .gateway import MCPGateway

# --- Layer 3: manager & pool ---
from .manager import SandboxManager, SandboxPool

# --- Backends (register themselves on import) ---
from . import local_temp as _local_temp  # noqa: F401

try:
    from .docker import DockerSandboxConnection  # noqa: F401
except ImportError:
    DockerSandboxConnection = None  # type: ignore[assignment,misc]

__all__ = [
    # exceptions
    "CapabilityError",
    "SandboxError",
    "UnsupportedOperation",
    # types
    "ExecResult",
    "ExposedPortEndpoint",
    "SandboxConnectionCapabilities",
    "SandboxCreateOptions",
    "SerializedSandboxState",
    # layer 1
    "SandboxConnection",
    "create_connection",
    "get_connection_class",
    "register_connection_class",
    # config
    "BackendParams",
    "DockerBackendParams",
    "E2BBackendParams",
    "LocalBackendParams",
    "McpGatewayConfig",
    "McpServerConfig",
    "SandboxConfig",
    "SkillConfig",
    "ToolDef",
    # layer 2
    "FileAccessor",
    "Sandbox",
    # gateway
    "MCPGateway",
    # layer 3
    "SandboxManager",
    "SandboxPool",
    # backends
    "DockerSandboxConnection",
]
