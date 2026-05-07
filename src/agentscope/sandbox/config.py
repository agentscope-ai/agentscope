# -*- coding: utf-8 -*-
"""Single-source-of-truth configuration for one Sandbox instance.

Users write *one* ``SandboxConfig``; the Sandbox layer internally merges
implied ports / volumes / env before handing them to the Connection.
"""

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Backend parameter sets
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BackendParams:
    """User-facing backend config — typed fields for each vendor.

    ``Sandbox._merge_infra_requirements()`` flattens these into a
    vendor-neutral ``SandboxCreateOptions`` (with an opaque ``extra``
    dict) before passing them to ``SandboxConnection.create()``.
    This keeps the user config statically typed while the connection
    layer stays backend-agnostic.
    """

    type: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DockerBackendParams(BackendParams):
    """Parameters for the Docker ``SandboxConnection`` backend."""

    type: str = "docker"
    image: str = "ubuntu:22.04"


@dataclass(slots=True)
class E2BBackendParams(BackendParams):
    """Parameters for the E2B ``SandboxConnection`` backend."""

    type: str = "e2b"
    template: str = "base"
    api_key: str = ""
    domain: str = ""
    timeout: int = 300
    envs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LocalBackendParams(BackendParams):
    """Parameters for the local temp-dir ``SandboxConnection`` backend."""

    type: str = "local_temp"
    base_dir: str = "/tmp"


# ---------------------------------------------------------------------------
# MCP / Skills / Tools
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class McpServerConfig:
    """One MCP server to start inside the sandbox."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class McpGatewayConfig:
    """Gateway settings (listen port merged into ``exposed_ports``)."""

    enabled: bool = False
    port: int = 5600
    mcp_name: str = "sandbox"


@dataclass(slots=True)
class SkillConfig:
    """Where skills live in the sandbox and optional host bind-mount."""

    skills_dir: str = "/root/skills"
    persist: bool = False
    host_dir: str | None = None


@dataclass(slots=True)
class ToolDef:
    """Static tool registered at sandbox start.

    ``handler`` is a shell command executed inside the sandbox when
    :meth:`Sandbox.call_tool` is invoked. The tool arguments are
    serialized as JSON and appended to the command, e.g.
    ``echo '{"msg": "hi"}'``.  When ``handler`` is ``None`` the tool
    is metadata-only and cannot be called.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: str | None = None


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SandboxConfig:
    """One config drives everything: backend + tool/skill/MCP + env.

    ``backend.type`` selects the registered ``SandboxConnection`` subclass.
    Ports, volumes, and env that are *implied* by MCP gateway / skills are
    merged automatically in ``Sandbox.start()``.

    ``endpoint`` is optional (remote tunnel / control-plane URL); merged into
    ``SandboxCreateOptions.extra`` for providers that need it.
    """

    backend: BackendParams

    endpoint: str | None = None

    exposed_ports: list[int] = field(default_factory=list)
    volumes: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    startup_commands: list[str] = field(default_factory=list)

    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    mcp_gateway: McpGatewayConfig = field(default_factory=McpGatewayConfig)

    skills: SkillConfig | None = None
    tools: list[ToolDef] = field(default_factory=list)
