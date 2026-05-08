# -*- coding: utf-8 -*-
"""Configuration for the logical Sandbox layer.

Backend-specific parameters live in :mod:`.backend_config`.
"""

from dataclasses import dataclass, field
from typing import Any

from .backend_config import BackendParams
from .mcp_gateway import MCPGatewayConfig


@dataclass(slots=True)
class MCPServerConfig:
    """One MCP server to start inside the sandbox."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SkillsConfig:
    """Where skills live in the sandbox and optional host bind-mount."""

    skills_dir: str = "/root/skills"
    persist: bool = False
    host_dir: str | None = None


@dataclass(slots=True)
class ToolDefinition:
    """Static tool registered at sandbox start.

    Attributes:
        name: Unique tool name.
        description: Human-readable description of what the tool does.
        parameters: JSON Schema describing the tool's input arguments,
            used for validation and for exposing the tool via MCP.
        shell_cmd: Shell command executed inside the sandbox when
            :meth:`Sandbox.call_tool` is invoked. The tool arguments are
            serialized as JSON and appended to the command, e.g.
            ``echo '{"msg": "hi"}'``.  When ``shell_cmd`` is ``None``
            the tool is metadata-only and cannot be called.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    shell_cmd: str | None = None


@dataclass(slots=True)
class SandboxConfig:
    """One config drives everything: backend + tool/skill/MCP + env.

    ``backend.type`` selects the registered ``SandboxConnection`` subclass.
    Ports, volumes, and env that are *implied* by MCP gateway / skills are
    merged automatically in ``Sandbox.start()``.

    ``endpoint`` is optional (remote tunnel / control-plane URL); merged into
    ``SandboxInitializationConfig.extra`` for providers that need it.
    """

    backend: BackendParams

    endpoint: str | None = None

    exposed_ports: list[int] = field(default_factory=list)
    volumes: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    startup_commands: list[str] = field(default_factory=list)

    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    mcp_gateway: MCPGatewayConfig = field(default_factory=MCPGatewayConfig)

    skills: SkillsConfig | None = None
    tools: list[ToolDefinition] = field(default_factory=list)
