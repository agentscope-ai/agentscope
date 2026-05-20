# -*- coding: utf-8 -*-
"""WorkspaceWithMCP — workspace base for container/sandbox MCP backends.

Inherits from :class:`WorkspaceBase` and adds in-container MCP gateway
management.  Both :class:`DockerWorkspace` and :class:`E2BWorkspace`
inherit from this class so that ``add_mcp``, ``remove_mcp``,
``_start_gateway``, ``_wait_for_gateway``,
``_ensure_gateway_python_deps``, ``_build_gateway_config``, and
``list_mcps`` are defined once.

Subclasses must implement:

* ``_exec(command, *, timeout)`` — execute a shell command remotely
  (inherited from :class:`WorkspaceBase`)
* ``_write_remote(path, data)`` — write bytes to the remote filesystem
* ``_resolve_base_url(port)`` — return the gateway's reachable base URL
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

import httpx

from .._logging import logger
from .workspace_base import WorkspaceBase

if TYPE_CHECKING:
    from ..mcp import MCPClient
    from .config import MCPServerConfig


class _RestToolProxy:
    """A callable that invokes a tool via the gateway REST API."""

    def __init__(
        self,
        name: str,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        """Create a REST-based tool proxy.

        Args:
            name: Name of the tool on the gateway.
            base_url: Gateway base URL (e.g.
                ``http://127.0.0.1:5600``).
            headers: HTTP headers (auth token, etc.).
        """
        self._name = name
        self._base_url = base_url
        self._headers = headers

    async def __call__(self, **kwargs: Any) -> Any:
        """POST the tool call to the gateway and return its JSON result.

        The gateway returns JSON-serializable data from upstream MCP
        tools, so the result is not guaranteed to be a string.
        """
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self._base_url}/tools/call",
                json={"name": self._name, "arguments": kwargs},
                headers=self._headers,
                timeout=60.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"tool call {self._name!r} failed "
                    f"({resp.status_code}): {resp.text}",
                )
            return resp.json().get("result")


class _RestGatewayClient:
    """Lightweight REST-based gateway client.

    Presents the same interface shape used by ``Toolkit.register_mcp``
    (``list_tools``, ``get_tool``, ``is_connected``, ``close``) but
    communicates with the in-container gateway via JSON REST calls
    instead of MCP protocol. This avoids issues with proxies that don't
    support streaming HTTP / SSE connections (e.g. E2B).
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        """Create a REST gateway client.

        Args:
            base_url: Gateway base URL (e.g.
                ``http://127.0.0.1:5600``).
            headers: HTTP headers sent with every request
                (auth token, platform headers, etc.).
        """
        self._base_url = base_url
        self._headers = headers

    @property
    def is_connected(self) -> bool:
        """Always True — REST client is stateless."""
        return True

    async def connect(self) -> None:
        """No-op for REST client."""

    async def close(self) -> None:
        """No-op for REST client."""

    async def list_tools(self) -> list[Any]:
        """Fetch tool schemas from the gateway REST API."""
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{self._base_url}/tools/list",
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
        tools: list[dict[str, Any]] = resp.json()

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

    async def get_tool(self, name: str) -> _RestToolProxy:
        """Return a callable proxy for the named tool.

        ``Toolkit.register_mcp`` calls ``get_tool`` after discovering
        tool schemas with ``list_tools``.

        Args:
            name: Exact tool name as registered on the gateway.
        """
        tools = await self.list_tools()
        for t in tools:
            t_name = t.name if hasattr(t, "name") else t.get("name")
            if t_name == name:
                return _RestToolProxy(
                    name=name,
                    base_url=self._base_url,
                    headers=self._headers,
                )
        raise ValueError(f"Tool {name!r} not found")


class WorkspaceWithMCP(WorkspaceBase):
    """Intermediate base for container/sandbox workspaces with MCP.

    Inherits ``_exec`` from :class:`WorkspaceBase` (remains abstract
    here — concrete subclasses like ``DockerWorkspace`` and
    ``E2BWorkspace`` must provide the implementation).
    """

    _gateway_token: str
    _gateway_mcp_client: Any
    _gateway_base_url: str
    _mcp_servers: "list[MCPServerConfig]"
    _gateway_port: int

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
        self._gateway_mcp_client: (
            _RestGatewayClient | None
        ) = None
        self._gateway_base_url = ""

    # ── hooks for subclasses ──────────────────────────────────────

    @abstractmethod
    async def _write_remote(self, path: str, data: bytes) -> None:
        """Write *data* to *path* inside the container / sandbox."""

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

        # Authenticate subsequent REST calls using the RFC 6750
        # Bearer token scheme.  The token was generated above and
        # written into the gateway config; the in-container
        # ``_TokenAuth`` middleware validates it on every request
        # (except ``/health``).
        headers = {**self._platform_headers()}
        if self._gateway_token:
            headers["Authorization"] = f"Bearer {self._gateway_token}"

        self._gateway_mcp_client = _RestGatewayClient(
            base_url=self._gateway_base_url,
            headers=headers,
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
