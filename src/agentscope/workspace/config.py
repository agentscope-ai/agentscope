# -*- coding: utf-8 -*-
"""Configuration dataclasses for workspace backends."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class MCPServerConfig:
    """One MCP server to manage inside a workspace.

    Supports two transport types:

    - ``"stdio"`` (default): spawn a local process via command + args.
    - ``"http"``: connect to an HTTP MCP server (SSE or Streamable HTTP).

    For stdio: ``command`` (and optionally ``args``) are required.
    For http: ``url`` is required; ``headers`` and ``timeout`` are optional.
    """

    name: str
    transport: str = "stdio"  # "stdio" | "http"

    # stdio fields
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    # http fields
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
