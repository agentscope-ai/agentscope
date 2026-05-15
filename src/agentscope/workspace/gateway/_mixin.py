# -*- coding: utf-8 -*-
"""GatewayMixin — shared host-side logic for the in-container MCP gateway.

Both :class:`DockerWorkspace` and :class:`E2BWorkspace` mix this in so
that ``add_mcp``, ``remove_mcp``, ``_start_gateway``,
``_wait_for_gateway``, ``_ensure_gateway_python_deps``,
``_build_gw_config``, and ``list_mcps`` are defined once.

Subclasses must implement two hooks:

* ``_gw_write_remote(path, data)`` — write bytes to the remote filesystem
* ``_gw_resolve_base_url(port)``  — return the gateway's reachable base URL
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import abstractmethod
from typing import Any, TYPE_CHECKING

import httpx

from ..._logging import logger

if TYPE_CHECKING:
    from ..config import MCPServerConfig
    from ..types import ExecutionResult
    from ...mcp import MCPClient


class _RestToolProxy:
    """A callable that invokes a tool via the gateway REST API."""

    def __init__(
        self,
        name: str,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        self._name = name
        self._base_url = base_url
        self._headers = headers

    async def __call__(self, **kwargs: Any) -> str:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._base_url}/api/call",
                json={"name": self._name, "arguments": kwargs},
                headers=self._headers,
                timeout=60.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"tool call {self._name!r} failed "
                    f"({resp.status_code}): {resp.text}",
                )
            return resp.json().get("result", "")


class _RestGatewayClient:
    """Lightweight REST-based gateway client.

    Presents the same interface that tests expect from ``MCPClient``
    (``list_tools``, ``get_tool``, ``is_connected``) but communicates
    with the in-container gateway via simple JSON REST calls instead
    of MCP protocol.  This avoids issues with proxies that don't
    support streaming HTTP / SSE connections (e.g. E2B).
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        self._base_url = base_url
        self._headers = headers
        self._connected = True
        self._cached_tools: list[dict[str, Any]] | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the client considers itself connected."""
        return self._connected

    async def connect(self) -> None:
        """Mark the client as connected."""
        self._connected = True

    async def close(  # pylint: disable=unused-argument
        self,
        ignore_errors: bool = True,
    ) -> None:
        """Mark the client as disconnected."""
        self._connected = False

    async def list_tools(self) -> list[Any]:
        """Fetch tool schemas from the gateway REST API."""
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{self._base_url}/api/tools",
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
        tools: list[dict[str, Any]] = resp.json()
        self._cached_tools = tools

        try:
            import mcp.types as mtypes

            return [
                mtypes.Tool(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {}),
                )
                for t in tools
            ]
        except ImportError:
            return list(tools)

    def invalidate_cache(self) -> None:
        """Clear the cached tool list so the next query re-fetches."""
        self._cached_tools = None

    async def get_tool(self, name: str) -> _RestToolProxy:
        """Return a callable proxy for the named tool."""
        if self._cached_tools is None:
            await self.list_tools()
        for t in self._cached_tools:
            if t["name"] == name:
                return _RestToolProxy(
                    name=name,
                    base_url=self._base_url,
                    headers=self._headers,
                )
        raise ValueError(f"Tool {name!r} not found")


class GatewayMixin:
    """Mixin that adds in-container MCP gateway management.

    Expects the concrete workspace to also expose:

    * ``_exec(command, *, timeout=None) -> ExecutionResult``
    * ``_mcp_servers: list[MCPServerConfig]``
    * ``_gateway_port: int``
    """

    # These attributes are initialised by the concrete workspace's
    # ``__init__`` — the mixin only reads/writes them.
    _gateway_token: str
    _gateway_mcpc: "MCPClient | _RestGatewayClient | None"
    _gateway_base_url: str
    _mcp_servers: list[Any]
    _gateway_port: int

    # ── hooks for subclasses ──────────────────────────────────────

    @abstractmethod
    async def _gw_write_remote(self, path: str, data: bytes) -> None:
        """Write *data* to *path* inside the container / sandbox."""

    @abstractmethod
    async def _gw_resolve_base_url(self, port: int) -> str:
        """Return the HTTP(S) base URL reachable from the host."""

    def _gw_platform_headers(self) -> dict[str, str]:
        """Extra HTTP headers required by the hosting platform.

        Override in subclasses that need platform-level auth to reach
        exposed container ports (e.g. E2B's ``X-Access-Token``).
        """
        return {}

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> "ExecutionResult":
        """Execute a command inside the workspace (provided by subclass)."""
        raise NotImplementedError

    # ── public API ────────────────────────────────────────────────

    async def list_mcps(self) -> list[Any]:
        """Return the gateway MCP client (if started) as a list."""
        if self._gateway_mcpc:
            return [self._gateway_mcpc]
        return []

    async def add_mcp(self, config: "MCPServerConfig") -> None:
        """Register a new MCP server via the gateway admin API."""
        if not self._gateway_base_url:
            raise RuntimeError(
                "Gateway not started. Either configure mcp_servers in "
                "the constructor or initialize the workspace first.",
            )
        body: dict[str, Any] = {"name": config.name}
        if config.transport == "http":
            body["transport"] = "http"
            body["url"] = config.url
        else:
            body["transport"] = "stdio"
            body["command"] = config.command
            body["args"] = list(config.args or [])
            if config.env:
                body["env"] = dict(config.env)

        headers: dict[str, str] = {**self._gw_platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._gateway_base_url}/admin/add",
                json=body,
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"add_mcp failed ({resp.status_code}): {resp.text}",
                )

        if self._gateway_mcpc:
            if isinstance(self._gateway_mcpc, _RestGatewayClient):
                self._gateway_mcpc.invalidate_cache()
            await self._gateway_mcpc.list_tools()

        logger.info(
            "%s: added MCP %r",
            type(self).__name__,
            config.name,
        )

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP server via the gateway admin API."""
        if not self._gateway_base_url:
            raise RuntimeError("Gateway not started")

        headers: dict[str, str] = {**self._gw_platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._gateway_base_url}/admin/remove",
                json={"name": name},
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"remove_mcp failed ({resp.status_code}): {resp.text}",
                )

        if self._gateway_mcpc:
            if isinstance(self._gateway_mcpc, _RestGatewayClient):
                self._gateway_mcpc.invalidate_cache()
            await self._gateway_mcpc.list_tools()

        logger.info("%s: removed MCP %r", type(self).__name__, name)

    # ── gateway lifecycle (called from workspace.initialize) ──────

    async def _start_gateway(self) -> None:
        self._gateway_token = uuid.uuid4().hex

        await self._ensure_gateway_python_deps()

        gw_config = self._build_gw_config()
        config_path = "/tmp/.agentscope_gw_config.json"  # noqa: S108
        await self._gw_write_remote(
            config_path,
            json.dumps(gw_config).encode(),
        )

        import importlib.resources as _res

        gw_src = (
            _res.files("agentscope.workspace.gateway")
            .joinpath("_server.py")
            .read_text()
        )
        gw_script = "/tmp/_in_container_gateway.py"  # noqa: S108
        await self._gw_write_remote(gw_script, gw_src.encode())

        launch = (
            'PY="$(command -v python3 2>/dev/null || command -v python)"; '
            f'nohup "$PY" -u {gw_script}'
            f" --config {config_path} --port {self._gateway_port}"
            " > /tmp/gw.log 2>&1 &"
        )
        await self._exec(launch, timeout=5)

        self._gateway_base_url = await self._gw_resolve_base_url(
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

        gw_headers = {**self._gw_platform_headers()}
        if self._gateway_token:
            gw_headers["Authorization"] = f"Bearer {self._gateway_token}"

        self._gateway_mcpc = _RestGatewayClient(
            base_url=self._gateway_base_url,
            headers=gw_headers,
        )
        logger.info("%s: gateway connected (REST)", type(self).__name__)

    # ── internal helpers ──────────────────────────────────────────

    def _build_gw_config(self) -> dict[str, Any]:
        servers = []
        for s in self._mcp_servers:
            entry: dict[str, Any] = {"name": s.name}
            if s.transport == "http":
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
        platform_hdrs = self._gw_platform_headers()
        async with httpx.AsyncClient(verify=False) as client:
            for i in range(retries):
                try:
                    resp = await client.get(
                        health_url,
                        headers=platform_hdrs,
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
