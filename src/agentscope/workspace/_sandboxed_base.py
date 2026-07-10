# -*- coding: utf-8 -*-
"""SandboxedWorkspaceBase — shared implementation for gateway-backed
sandbox workspaces (Docker, E2B, K8s, …).

Extends :class:`WorkspaceBase` with a template-method lifecycle around
an in-sandbox MCP gateway. Subclasses only need to provide:

- :meth:`_provision_backend` — attach/create the sandbox, bind
  ``self._backend``.
- :meth:`_teardown_backend` — destroy/pause the sandbox.
- :meth:`_bootstrap_commands` — shell commands to install the gateway
  venv on first use (Docker skips this — the image already has it).
- gateway-path class attributes (see below).

Everything gateway-related — bootstrap, launch, health poll, MCP
add/remove routing, ``.mcp`` persistence, reset — lives here.
"""

import asyncio
import json
import shlex
from abc import abstractmethod

from .._logging import logger
from ..mcp import MCPClient
from ._base import WorkspaceBase
from ._gateway_client import GatewayClient
from ._utils import (
    DEFAULT_GATEWAY_LOG,
    DEFAULT_GATEWAY_SCRIPT,
    DEFAULT_GATEWAY_VENV,
    DEFAULT_GLOB_HELPER_SCRIPT,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)


class SandboxedWorkspaceBase(WorkspaceBase):
    """Base class for workspaces backed by an in-sandbox MCP gateway.

    Subclasses only need to set:

    - :attr:`workdir` — agent-visible root inside the sandbox.
    - :attr:`_gateway_home` — directory holding the venv, script, log.
    - :attr:`gateway_port` — TCP port the gateway listens on.

    Every other gateway path (venv, python, script, log, glob helper)
    is derived from :attr:`_gateway_home` via
    :meth:`BackendBase.join_path`.
    """

    workdir: str
    """Agent-visible root directory for workspace file operations."""

    gateway_port: int
    """TCP port the in-sandbox gateway listens on."""

    _gateway_home: str
    """Sandbox-side directory holding the gateway venv, script, log."""

    _gateway: GatewayClient | None
    """Workspace-side gateway facade. ``None`` before init / after close."""

    _bootstrap_cmd_timeout: float = 1800.0
    """Per-command timeout applied to every :meth:`_setup_mcp_gateway`
    bootstrap step. Subclasses lower this for lighter base images
    (E2B uses 600 s; K8s inherits the wider default for apt-get).
    """

    @property
    def _gateway_venv(self) -> str:
        """Sandbox-side path of the gateway venv root."""
        return self.get_backend().join_path(
            self._gateway_home,
            DEFAULT_GATEWAY_VENV,
        )

    @property
    def _gateway_python(self) -> str:
        """Sandbox-side path of the gateway venv python interpreter."""
        return self.get_backend().join_path(
            self._gateway_venv,
            "bin",
            "python",
        )

    @property
    def _gateway_script(self) -> str:
        """Sandbox-side path of the gateway entry script."""
        return self.get_backend().join_path(
            self._gateway_home,
            DEFAULT_GATEWAY_SCRIPT,
        )

    @property
    def _gateway_log(self) -> str:
        """Sandbox-side path of the gateway stdout/stderr log."""
        return self.get_backend().join_path(
            self._gateway_home,
            DEFAULT_GATEWAY_LOG,
        )

    @property
    def _glob_helper_path(self) -> str:
        """Standalone glob helper script used by the builtin Glob tool."""
        return self.get_backend().join_path(
            self._gateway_home,
            DEFAULT_GLOB_HELPER_SCRIPT,
        )

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

    # ── subclass hooks ────────────────────────────────────────────

    @abstractmethod
    async def _provision_backend(self) -> None:
        """Provision the sandbox and bind ``self._backend``.

        Called once per :meth:`initialize`. Must leave ``self._backend``
        as a live :class:`BackendBase` ready for
        ``exec_shell`` / ``write_file`` / ``read_file``.
        """

    @abstractmethod
    async def _teardown_backend(self) -> None:
        """Destroy or pause the sandbox.

        Called once per :meth:`close`, after the gateway facade has
        been closed. Must be idempotent and swallow exceptions.
        """

    def _bootstrap_commands(self) -> list[str]:
        """Shell commands that provision the gateway venv on first use.

        Runs only when :attr:`_gateway_script` is missing. Subclasses
        whose image already ships the venv (e.g. Docker) leave this
        empty; the base's fast-path skips the whole bootstrap block.
        """
        return []

    # ── lifecycle template methods ────────────────────────────────

    async def initialize(self) -> None:
        """Provision the sandbox, restore MCPs, start the gateway.

        Idempotent — a no-op when already alive.
        """
        logger.info(
            "Initialize workspace (id=%s) from %s ...",
            self.workspace_id,
            self.__class__.__name__,
        )

        if self.is_alive:
            return

        # Set up the backend connection
        await self._provision_backend()
        assert (
            self._backend is not None
        ), "_provision_backend must set self._backend before returning"

        # Set up the workspace layout
        await self._ensure_workspace_layout()

        # Set up the MCP gateway server
        await self._setup_mcp_gateway()

        # Set up the skills if not exists
        await self._setup_skills()

        self.is_alive = True

        logger.info(
            "Finished initializing workspace (id=%s) from %s.",
            self.workspace_id,
            self.__class__.__name__,
        )

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
            for mcp in list(self._mcps):
                try:
                    await mcp.close()
                except Exception as e:
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        mcp.name,
                        e,
                    )
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
        return list(self._mcps)

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
            if any(m.name == mcp_client.name for m in self._mcps):
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            spec = mcp_client.model_dump(mode="json")
            gw_client = self._gateway.make_client(spec)
            await gw_client.connect()
            self._mcps.append(gw_client)
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
            for i, mcp in enumerate(self._mcps):
                if mcp.name == name:
                    self._mcps.pop(i)
                    try:
                        await mcp.close()
                    except Exception as e:
                        logger.warning("MCP %r close failed: %s", name, e)
                    await self._save_mcp_file()
                    return
            logger.warning("MCP %r not found in workspace", name)

    # ── workspace layout helpers ──────────────────────────────────

    async def _ensure_workspace_layout(self) -> None:
        """Create the standard workspace directories inside the sandbox."""
        backend = self.get_backend()
        await backend.exec_shell(
            [
                "mkdir",
                "-p",
                self.workdir,
                self._data_dir,
                self._skills_dir,
                self._sessions_dir,
                self._gateway_home,
            ],
            cwd="/",
        )

        # Seed ``.mcp`` on first use. If a persisted file already
        # exists we validate it: a partial/corrupted write (crash mid
        # ``_save_mcp_file``) would otherwise brick the gateway on the
        # next startup because it expects a JSON list.  When the
        # payload is unusable we log and reseed the defaults instead
        # of failing initialization.
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self.default_mcps],
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")

        if await backend.file_exists(self._mcp_file):
            try:
                existing = await backend.read_file(self._mcp_file)
                parsed = json.loads(existing.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.warning(
                    "%s: %s is corrupted (%s); reseeding defaults",
                    type(self).__name__,
                    self._mcp_file,
                    e,
                )
            else:
                if isinstance(parsed, list):
                    return
                logger.warning(
                    "%s: %s does not contain a JSON list "
                    "(got %s); reseeding defaults",
                    type(self).__name__,
                    self._mcp_file,
                    type(parsed).__name__,
                )
        await backend.write_file(self._mcp_file, payload)

    # ── gateway lifecycle helpers ─────────────────────────────────

    async def _setup_mcp_gateway(self) -> None:
        """Bootstrap (once) and launch the in-sandbox gateway.

        Steps:

        1. Bootstrap the venv + script if :attr:`_gateway_script` is
           missing (Docker's image already has it → fast-path).
        2. Kill any leftover gateway from a previous resume.
        3. Launch a fresh gateway pointed at ``.mcp``.
        4. Bind :attr:`_gateway` and poll ``/health``.
        """
        backend = self.get_backend()

        # Provision the venv + script on first use. Fast-path skips
        # when the script already exists (Docker image build, E2B
        # resume, K8s PVC remount).
        if not await backend.file_exists(self._gateway_script):
            logger.info(
                "%s: bootstrapping workspace_id=%r",
                type(self).__name__,
                self.workspace_id,
            )
            for cmd in self._bootstrap_commands():
                r = await backend.exec_shell(
                    ["sh", "-c", cmd],
                    timeout=self._bootstrap_cmd_timeout,
                )
                if not r.ok():
                    raise RuntimeError(
                        f"{type(self).__name__} bootstrap failed "
                        f"(exit {r.exit_code}) for: {cmd!r}\n"
                        f"stderr: {r.stderr.decode(errors='replace')}\n"
                        f"stdout: {r.stdout.decode(errors='replace')}",
                    )
            # Glob helper first, gateway script last — a partial
            # bootstrap leaves _gateway_script absent so the next
            # initialize retries cleanly.
            if self._glob_helper_path is not None:
                await backend.write_file(
                    self._glob_helper_path,
                    _read_glob_helper_bytes(),
                )
            await backend.write_file(
                self._gateway_script,
                _read_gateway_script_bytes(),
            )

        # Launch. ``pkill`` clears any gateway left running by a
        # previous resume so the new one can bind the port cleanly.
        # The gateway reads ``.mcp`` directly — no separate config file.
        launch_cmd = (
            "pkill -f _mcp_gateway_app.py || true; "
            f"nohup {shlex.quote(self._gateway_python)} -u "
            f"{shlex.quote(self._gateway_script)} "
            f"--config {shlex.quote(self._mcp_file)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(self._gateway_log)} 2>&1 &"
        )
        await backend.exec_shell(["sh", "-c", launch_cmd])

        # Wait for ``/health``; on timeout, tail the gateway log.
        self._gateway = GatewayClient(
            backend=backend,
            gateway_port=self.gateway_port,
            timeout=30.0,
            gateway_log_path=self._gateway_log,
        )
        health_timeout = 30.0
        deadline = asyncio.get_event_loop().time() + health_timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        else:
            try:
                log = await backend.read_file(self._gateway_log)
                tail = log[-2000:].decode(errors="replace")
            except Exception:
                tail = "<no gateway log available>"
            raise RuntimeError(
                f"gateway did not become healthy within {health_timeout}s. "
                f"Tail of {self._gateway_log}:\n{tail}",
            )

        # Replace the seed specs with the gateway-side wrappers — same
        # set, name-for-name — so subsequent list_mcps / add / remove
        # operate on the live proxies.
        self._mcps = list(await self._gateway.list_mcps())
