# -*- coding: utf-8 -*-
"""WorkspaceWithMCP — workspace base for MCP-enhanced workspaces.

Inherits from :class:`WorkspaceBase` and adds in-workspace MCP gateway
management.
Subclasses must implement:

* ``_exec(command, *, timeout)`` — execute a shell command inside
  the workspace (inherited from :class:`WorkspaceBase`)
* ``_write_remote(path, data)`` — write bytes to the workspace
  filesystem
* ``_resolve_base_url(port)`` — return the gateway's reachable base
  URL
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

import httpx
import mcp.types as _mcp_types

from ..mcp import MCPClient
from ..mcp._config import HttpMCPConfig
from ..message import TextBlock, ToolResultState
from ..permission import PermissionBehavior, PermissionDecision
from ..tool._base import ToolBase
from ..tool._response import ToolChunk
from .._logging import logger
from .workspace_base import WorkspaceBase

if TYPE_CHECKING:
    from .config import MCPServerConfig


# ── REST-backed ToolBase / MCPClient implementations ─────────────


class _RestMCPTool(ToolBase):
    """ToolBase adapter that invokes a tool via the gateway REST API.

    Mirrors the interface of :class:`MCPTool` (name mangling,
    ``is_mcp``, permission checks, ``ToolChunk`` return) but
    transports calls over plain HTTP POST to the in-workspace
    gateway instead of an MCP session.
    """

    is_mcp: bool = True
    is_state_injected: bool = False

    def __init__(
        self,
        mcp_name: str,
        tool: _mcp_types.Tool,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        """Create a REST-backed MCP tool.

        Args:
            mcp_name: Logical MCP client name (used in the
                ``mcp__<name>__<tool>`` mangled tool name).
            tool: MCP tool schema returned by the gateway.
            base_url: Gateway base URL.
            headers: HTTP headers (auth token, etc.).
        """
        self.mcp_name = mcp_name
        self.name = f"mcp__{mcp_name}__{tool.name}"
        self.description = tool.description or ""
        self.input_schema = {
            "type": "object",
            "properties": tool.inputSchema.get("properties", {}),
            "required": tool.inputSchema.get("required", []),
        }
        self.is_concurrency_safe = False
        self.is_external_tool = False
        self.is_read_only = False
        if tool.annotations and hasattr(tool.annotations, "readOnlyHint"):
            self.is_read_only = tool.annotations.readOnlyHint or False

        self._tool_name = tool.name
        self._base_url = base_url
        self._headers = headers

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions — same policy as :class:`MCPTool`."""
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Read-only MCP tool. Allowing execution.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="MCP tools must be explicitly allowed by the user.",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """POST the tool call to the gateway and return a ToolChunk."""
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._base_url}/tools/call",
                json={"name": self._tool_name, "arguments": kwargs},
                headers=self._headers,
                timeout=60.0,
            )
            if resp.status_code >= 400:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"tool call {self._tool_name!r} failed "
                                f"({resp.status_code}): {resp.text}"
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            result = resp.json().get("result", "")

        return ToolChunk(
            content=[TextBlock(text=str(result))],
            state=ToolResultState.RUNNING,
        )


class _RestGatewayClient(MCPClient):
    """MCPClient subclass for the in-workspace MCP gateway."""

    def model_post_init(self, __context: Any) -> None:
        """Skip MCP client init — the REST gateway is always reachable."""
        self._is_connected = True

    async def list_tools(self) -> list[_mcp_types.Tool]:
        """Fetch tool schemas from the gateway REST API."""
        config: HttpMCPConfig = self.mcp_config  # type: ignore[assignment]
        headers = dict(config.headers or {})
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{config.url}/tools/list",
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
        tools: list[dict[str, Any]] = resp.json()
        result = [
            _mcp_types.Tool(
                name=t["name"],
                description=t.get("description", ""),
                inputSchema=t.get("inputSchema", {}),
            )
            for t in tools
        ]
        self._cached_tools = result
        return result

    async def get_tool(  # type: ignore[override]
        self,
        name: str,
        execution_timeout: float | None = None,
    ) -> _RestMCPTool:
        """Return a :class:`_RestMCPTool` proxy for the named tool.

        Args:
            name: Exact tool name as registered on the gateway.
            execution_timeout: Unused (kept for API compatibility).
        """
        if self._cached_tools is None:
            await self.list_tools()

        config: HttpMCPConfig = self.mcp_config  # type: ignore[assignment]
        headers = dict(config.headers or {})
        for tool in self._cached_tools:  # type: ignore[union-attr]
            if tool.name == name:
                return _RestMCPTool(
                    mcp_name=self.name,
                    tool=tool,
                    base_url=config.url,
                    headers=headers,
                )
        raise ValueError(f"Tool {name!r} not found")


class WorkspaceWithMCP(WorkspaceBase):
    """Intermediate base for MCP-enhanced workspaces."""

    # Class-level type annotations help mypy when running with
    # ``--follow-imports=skip`` (used in pre-commit).  Actual values
    # are always set in ``__init__``.

    def __init__(
        self,
        *,
        mcp_servers: "list[MCPServerConfig] | None" = None,
        gateway_port: int = 5600,
    ) -> None:
        """Initialise MCP gateway state for a workspace.

        Args:
            mcp_servers: MCP servers to run through the workspace
                gateway.
            gateway_port: Port that the in-workspace gateway listens on.
        """
        self._mcp_servers = list(mcp_servers or [])
        self._gateway_port = gateway_port
        self._gateway_token = ""
        self._gateway_mcp_client: _RestGatewayClient | None = None
        self._gateway_base_url = ""

    # ── lifecycle ─────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Start the in-workspace MCP gateway if servers are configured."""
        if self._mcp_servers:
            await self._start_gateway()

    # ── hooks for subclasses ──────────────────────────────────────

    @abstractmethod
    async def _write_remote(self, path: str, data: bytes) -> None:
        """Write *data* to *path* inside the workspace environment."""

    @abstractmethod
    async def _resolve_base_url(self, port: int) -> str:
        """Return the HTTP(S) base URL reachable from the host."""

    def _platform_headers(self) -> dict[str, str]:
        """Extra HTTP headers required by the hosting platform.

        Override in subclasses that need platform-level auth to reach
        exposed container ports (e.g. E2B's ``X-Access-Token``).
        """
        return {}

    # ── public API ────────────────────────────────────────────────

    async def list_mcps(self) -> list[Any]:
        """Return the gateway MCP client (if started) as a list."""
        if self._gateway_mcp_client:
            return [self._gateway_mcp_client]
        return []

    async def add_mcp(self, config: "MCPServerConfig") -> None:
        """Register a new MCP server via the gateway admin API.

        Args:
            config: MCP server configuration to add.
        """
        if not self._gateway_base_url:
            raise RuntimeError(
                "Gateway not started. Either configure mcp_servers in "
                "the constructor or initialize the workspace first.",
            )
        body: dict[str, Any] = {"name": config.name}
        if config.protocol == "http":
            body["transport"] = "http"
            body["url"] = config.url
        else:
            body["transport"] = "stdio"
            body["command"] = config.command
            body["args"] = list(config.args or [])
            if config.env:
                body["env"] = dict(config.env)

        headers: dict[str, str] = {**self._platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._gateway_base_url}/mcp/add",
                json=body,
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"add_mcp failed ({resp.status_code}): {resp.text}",
                )

        if self._gateway_mcp_client:
            await self._gateway_mcp_client.list_tools()

        logger.info(
            "%s: added MCP %r",
            type(self).__name__,
            config.name,
        )

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP server via the gateway admin API.

        Args:
            name: Name of the MCP server to remove.
        """
        if not self._gateway_base_url:
            raise RuntimeError("Gateway not started")

        headers: dict[str, str] = {**self._platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._gateway_base_url}/mcp/remove",
                json={"name": name},
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"remove_mcp failed ({resp.status_code}): {resp.text}",
                )

        if self._gateway_mcp_client:
            await self._gateway_mcp_client.list_tools()

        logger.info("%s: removed MCP %r", type(self).__name__, name)

    # ── gateway lifecycle (called from workspace.initialize) ──────

    async def _start_gateway(self) -> None:
        """Start the gateway.

        The gateway uses Bearer token auth — the token is generated
        per-workspace and written into the gateway config. Requests
        from the host must include this token to authenticate.
        """
        self._gateway_token = uuid.uuid4().hex

        await self._ensure_gateway_python_deps()

        gateway_config = self._build_gateway_config()
        config_path = "/tmp/.agentscope_gw_config.json"  # noqa: S108
        await self._write_remote(
            config_path,
            json.dumps(gateway_config).encode(),
        )

        import importlib.resources as _res

        gateway_src = (
            _res.files("agentscope.workspace")
            .joinpath("mcp_gateway_app.py")
            .read_text()
        )
        gateway_script = "/tmp/_in_container_gateway.py"  # noqa: S108
        await self._write_remote(gateway_script, gateway_src.encode())

        launch = (
            'PY="$(command -v python3 2>/dev/null || command -v python)"; '
            f'nohup "$PY" -u {gateway_script}'
            f" --config {config_path} --port {self._gateway_port}"
            " > /tmp/gw.log 2>&1 &"
        )
        await self._exec(launch, timeout=5)

        self._gateway_base_url = await self._resolve_base_url(
            self._gateway_port,
        )

        try:
            await self._wait_for_gateway(
                f"{self._gateway_base_url}/health",
            )
        except RuntimeError:
            gw_log = await self._exec("cat /tmp/gw.log 2>/dev/null || true")
            log_text = gw_log.stdout.decode(errors="replace").strip()
            logger.error(
                "Gateway failed to start. /tmp/gw.log:\n%s",
                log_text or "(empty)",
            )
            raise

        headers = {**self._platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        self._gateway_mcp_client = _RestGatewayClient(
            name="gateway",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url=self._gateway_base_url,
                headers=headers,
                verify=False,
            ),
        )
        logger.info("%s: gateway connected (REST)", type(self).__name__)

    # ── internal helpers ──────────────────────────────────────────

    def _build_gateway_config(self) -> dict[str, Any]:
        """Build the JSON config dict for the in-container gateway."""
        servers = []
        for s in self._mcp_servers:
            entry: dict[str, Any] = {"name": s.name}
            if s.protocol == "http":
                entry["transport"] = "http"
                entry["url"] = s.url
            else:
                entry["transport"] = "stdio"
                entry["command"] = s.command
                entry["args"] = list(s.args or [])
                if s.env:
                    entry["env"] = dict(s.env)
            servers.append(entry)
        return {"token": self._gateway_token, "servers": servers}

    async def _wait_for_gateway(
        self,
        health_url: str,
        *,
        retries: int = 30,
        interval: float = 1.0,
    ) -> None:
        """Poll the gateway health endpoint until it responds 200.

        Args:
            health_url: Full URL to the ``/health`` endpoint.
            retries: Maximum number of polling attempts.
            interval: Seconds between attempts.

        Raises:
            RuntimeError: If the gateway does not become ready
                within the retry budget.
        """
        platform_headers = self._platform_headers()
        async with httpx.AsyncClient(verify=False) as client:
            for i in range(retries):
                try:
                    resp = await client.get(
                        health_url,
                        headers=platform_headers,
                        timeout=2.0,
                    )
                    if resp.status_code == 200:
                        logger.info(
                            "Gateway ready after %d attempts",
                            i + 1,
                        )
                        return
                except httpx.RequestError:
                    pass
                await asyncio.sleep(interval)
        raise RuntimeError(
            f"In-container gateway failed to start "
            f"after {retries} attempts ({health_url})",
        )

    async def _ensure_gateway_python_deps(self) -> None:
        """Ensure ``mcp``, ``uvicorn``, ``starlette`` exist remotely.

        Debian-based images (e.g. ``python:3.11-slim``) use PEP 668; plain
        ``pip install`` may fail unless ``--break-system-packages`` is used.
        """
        probe = await self._exec(
            "python3 -c 'import mcp,uvicorn,starlette' 2>/dev/null || "
            "python -c 'import mcp,uvicorn,starlette' 2>/dev/null",
        )
        if probe.is_ok():
            return
        install = await self._exec(
            "(python3 -m pip install --no-cache-dir --break-system-packages "
            "--timeout 300 --retries 8 -q -U pip && "
            "for _a in 1 2 3; do "
            "python3 -m pip install --no-cache-dir --break-system-packages "
            "--timeout 300 --retries 8 -q uvicorn starlette mcp "
            "&& break || sleep 5; done) "
            "||(python3 -m pip install --no-cache-dir "
            "--timeout 300 --retries 8 -q -U pip && "
            "for _a in 1 2 3; do "
            "python3 -m pip install --no-cache-dir "
            "--timeout 300 --retries 8 -q uvicorn starlette mcp "
            "&& break || sleep 5; done) "
            "||(python -m pip install --no-cache-dir --break-system-packages "
            "--timeout 300 --retries 8 -q -U pip && "
            "for _a in 1 2 3; do "
            "python -m pip install --no-cache-dir --break-system-packages "
            "--timeout 300 --retries 8 -q uvicorn starlette mcp "
            "&& break || sleep 5; done)",
            timeout=600,
        )
        if not install.is_ok():
            raise RuntimeError(
                "Failed to install in-container gateway dependencies "
                "(mcp, uvicorn, starlette). pip stderr:\n"
                + install.stderr.decode(errors="replace"),
            )
        probe2 = await self._exec(
            "python3 -c 'import mcp,uvicorn,starlette' 2>/dev/null || "
            "python -c 'import mcp,uvicorn,starlette' 2>/dev/null",
        )
        if not probe2.is_ok():
            raise RuntimeError(
                "Gateway dependencies still not importable after pip install.",
            )
