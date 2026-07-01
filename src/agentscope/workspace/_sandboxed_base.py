# -*- coding: utf-8 -*-
"""SandboxedWorkspaceBase — shared implementation for gateway-backed
sandbox workspaces (Docker, E2B, and future K8s / Daytona etc.).

Extends :class:`WorkspaceBase` with a full template-method lifecycle
around an in-sandbox MCP gateway. Concrete subclasses only need to
provide:

- :meth:`_provision_backend` — create / attach the sandbox and bind
  ``self._backend`` (a :class:`BackendBase` implementation).
- :meth:`_teardown_backend` — destroy / pause the sandbox and release
  backend-specific resources.
- five gateway-path class attributes (see below) pinned to whatever
  paths the subclass's bootstrap places files at.

Every gateway concern — writing config, launching the process,
polling ``/health``, dispatching :meth:`add_mcp` / :meth:`remove_mcp`
through :class:`GatewayClient`, restoring / persisting the ``.mcp``
file, wiping state on :meth:`reset` — lives here so it is written
exactly once.
"""

import asyncio
import json
import shlex
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING

from .._logging import logger
from ..mcp import MCPClient
from ._base import WorkspaceBase
from ._gateway_client import GatewayClient

if TYPE_CHECKING:
    from ._gateway_client import GatewayMCPClient


class SandboxedWorkspaceBase(WorkspaceBase):
    """Base class for workspaces backed by an in-sandbox MCP gateway.

    Subclasses set the following class attributes (usually to module
    constants defined by the backend's bootstrap):

    - :attr:`gateway_port` — TCP port the gateway listens on.
    - :attr:`_gateway_home` — directory that holds gateway files.
    - :attr:`_gateway_config` — path to ``gateway.config.json``.
    - :attr:`_gateway_log` — path to the gateway log.
    - :attr:`_gateway_script` — path to the gateway entry script.
    - :attr:`_gateway_python` — path to the gateway venv's python.
    """

    gateway_port: int
    """TCP port the in-sandbox gateway listens on."""

    _gateway_home: str
    """Sandbox-side directory holding the gateway config and log."""

    _gateway_config: str
    """Sandbox-side path of ``gateway.config.json``."""

    _gateway_log: str
    """Sandbox-side path of the gateway stdout/stderr log."""

    _gateway_script: str
    """Sandbox-side path of the gateway entry script."""

    _gateway_python: str
    """Sandbox-side path of the gateway venv python interpreter."""

    _gateway: GatewayClient | None
    """Workspace-side gateway facade. ``None`` before init / after close."""

    _gateway_clients: "dict[str, GatewayMCPClient]"
    """Gateway-wrapped MCP handles keyed by ``MCPClient.name``."""

    _gateway_token: str
    """Bearer token minted per :meth:`initialize`; never persisted."""

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Initialise sandbox-workspace state.

        Args:
            workspace_id (`str | None`, optional):
                Existing identifier; ``None`` mints a fresh UUID.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered when no persisted ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded on first start.
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )
        self._gateway = None
        self._gateway_clients = {}
        self._gateway_token = ""

    # ── subclass hooks ────────────────────────────────────────────

    @abstractmethod
    async def _provision_backend(self) -> None:
        """Provision the sandbox and bind ``self._backend``.

        Called once per :meth:`initialize`. Implementations must
        leave ``self._backend`` as a live :class:`BackendBase`
        instance ready to accept ``exec_shell`` / ``write_file`` /
        ``read_file`` calls. Any first-time bootstrap (installing the
        gateway venv, uploading scripts) should happen here.
        """

    @abstractmethod
    async def _teardown_backend(self) -> None:
        """Destroy or pause the sandbox and release resources.

        Called once per :meth:`close`, *after* the gateway facade has
        been closed. Implementations should be idempotent and swallow
        exceptions so ``close`` never raises.
        """

    # ── lifecycle template methods ────────────────────────────────

    async def initialize(self) -> None:
        """Provision the sandbox, restore MCPs, start the gateway.

        Idempotent — a no-op when already alive.
        """
        if self.is_alive:
            return

        await self._provision_backend()
        assert (
            self._backend is not None
        ), "_provision_backend must set self._backend before returning"

        self._mcps = await self._restore_or_seed_mcps()
        self._gateway_token = uuid.uuid4().hex

        # Kill any leftover gateway from a previous resume. Each init
        # mints a fresh bearer token, so a stale gateway would accept
        # old-token requests but reject new ones. Idempotent (``|| true``).
        await self._backend.exec_shell(
            ["sh", "-c", "pkill -f _mcp_gateway_app.py || true"],
        )

        await self._write_gateway_config()
        await self._start_gateway_process()

        self._gateway = GatewayClient(
            backend=self._backend,
            gateway_port=self.gateway_port,
            token=self._gateway_token,
            timeout=30.0,
        )
        await self._wait_for_gateway()

        # The gateway loaded the same set we just wrote — name-for-name.
        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        # ``_save_mcp_file`` is a persistence-gated no-op when the
        # workspace is ephemeral; ``_seed_skills`` short-circuits when
        # ``skills/`` already has entries.
        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def close(self) -> None:
        """Close the gateway facade, then tear down the sandbox.

        Idempotent — errors are swallowed so ``close`` is always
        safe to call.
        """
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None
        self._gateway_clients.clear()

        try:
            await self._teardown_backend()
        finally:
            self._backend = None
            self.is_alive = False

    async def reset(self) -> None:
        """Wipe workspace state; keep the sandbox and gateway alive.

        Deregisters every MCP from the gateway, clears local handles,
        and wipes ``.mcp``, ``skills/``, ``sessions/``, and ``data/``.
        ``default_mcps`` / ``skill_paths`` are not re-seeded.
        """
        backend = self.get_backend()
        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as e:
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        e,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            for path in (
                self._sessions_dir,
                self._data_dir,
                self._skills_dir,
            ):
                await backend.delete_path(path)

            # Empty out .mcp so a restart won't fall back to default_mcps.
            await self._save_mcp_file()

    # ── MCP management (gateway-routed) ───────────────────────────

    async def list_mcps(self) -> list[MCPClient]:
        """Gateway-wrapped MCP handles, one per registered MCP."""
        return list(self._gateway_clients.values())

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server through the in-sandbox gateway.

        Args:
            mcp_client (`MCPClient`):
                The MCP to register.

        Raises:
            `ValueError`:
                If an MCP with the same name already exists.
            `RuntimeError`:
                If the gateway is not attached or rejects the
                registration.
        """
        if self._gateway is None:
            raise RuntimeError("Workspace has no MCP gateway attached.")
        async with self._mcp_lock:
            if mcp_client.name in self._gateway_clients:
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            spec = mcp_client.model_dump(mode="json")
            gw_client = self._gateway.make_client(spec)
            await gw_client.connect()
            self._mcps.append(mcp_client)
            self._gateway_clients[gw_client.name] = gw_client
            await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        """Deregister an MCP server by name.

        Args:
            name (`str`):
                MCP name to remove. Unknown names log a warning and
                return silently.

        Raises:
            `RuntimeError`:
                If the gateway is not attached.
        """
        if self._gateway is None:
            raise RuntimeError("Workspace has no MCP gateway attached.")
        async with self._mcp_lock:
            gw_client = self._gateway_clients.pop(name, None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as e:
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    # ── gateway lifecycle helpers ─────────────────────────────────

    async def _write_gateway_config(self) -> None:
        """Drop ``gateway.config.json`` into the sandbox.

        Carries the freshly minted bearer token plus the MCP server
        specs the gateway should bring up at startup.
        """
        backend = self.get_backend()
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await backend.exec_shell(["mkdir", "-p", self._gateway_home])
        await backend.write_file(
            self._gateway_config,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway inside the sandbox as a detached process."""
        backend = self.get_backend()
        cmd = (
            f"nohup {shlex.quote(self._gateway_python)} -u "
            f"{shlex.quote(self._gateway_script)} "
            f"--config {shlex.quote(self._gateway_config)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(self._gateway_log)} 2>&1 &"
        )
        await backend.exec_shell(["sh", "-c", cmd])

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers ``/health`` with 200.

        On timeout, dump the tail of the gateway log so startup
        failures are visible in the raised error.

        Args:
            timeout (`float`, defaults to 30.0):
                Maximum seconds to wait for readiness.

        Raises:
            RuntimeError: If the gateway does not become healthy
                before the deadline.
        """
        assert self._gateway is not None
        backend = self.get_backend()
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        try:
            log = await backend.read_file(self._gateway_log)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {self._gateway_log}:\n{tail}",
        )
