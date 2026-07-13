# -*- coding: utf-8 -*-
"""AppleContainerWorkspaceManager — lifecycle manager for
:class:`AppleContainerWorkspace`.

Mirrors :class:`DockerWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``close`` / ``close_all``) so that callers do not
branch on backend.

Differences from the Docker manager:

* No bind-mount workdir — container filesystem is the persistence
  layer. Container names are deterministic so reattachment works
  across process restarts.
* No Dockerfile generation — images are pulled as OCI images directly.
* Idle workspaces are evicted by a dedicated background sweeper task.
* ``close_all`` shuts containers down in parallel.
"""

import asyncio
import time
from typing import Self

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace._applecontainer._constants import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_CPUS,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_MEMORY,
)
from ...workspace._applecontainer._applecontainer_workspace import (
    AppleContainerWorkspace,
)
from ._base import IsolationPolicy, WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


class AppleContainerWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`AppleContainerWorkspace` instances with TTL-based
    caching.

    The manager owns a single set of container parameters
    (``base_image``, ``cpus``, ``memory``) shared by every workspace
    it produces.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        base_image: str = DEFAULT_BASE_IMAGE,
        cpus: int = DEFAULT_CPUS,
        memory: str = DEFAULT_MEMORY,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the Apple Container workspace manager.

        Args:
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                OCI image used for every workspace.
            cpus (`int`, defaults to `DEFAULT_CPUS`):
                Number of virtual CPUs per container.
            memory (`str`, defaults to `DEFAULT_MEMORY`):
                Memory allocation per container (e.g. ``"2G"``).
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-container gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables set in every workspace's
                container.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted
                and its container torn down.
            sweep_interval (`float`, defaults to
            `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up.
        """
        self._base_image = base_image
        self._cpus = cpus
        self._memory = memory
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        super().__init__(isolation=isolation)
        self._ttl = ttl
        self._sweep_interval = sweep_interval

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[
            str,
            tuple[AppleContainerWorkspace, float],
        ] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    # ── workspace construction ────────────────────────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
    ) -> AppleContainerWorkspace:
        """Create an :class:`AppleContainerWorkspace` and run its full
        ``initialize``.

        Args:
            workspace_id (`str`):
                Stable identifier forwarded to the workspace.

        Returns:
            `AppleContainerWorkspace`:
                A live, initialized workspace.
        """
        ws = AppleContainerWorkspace(
            workspace_id=workspace_id,
            base_image=self._base_image,
            gateway_port=self._gateway_port,
            cpus=self._cpus,
            memory=self._memory,
            env=self._env,
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    # ── public API ────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> AppleContainerWorkspace:
        """Return an initialised workspace, building one on cache miss.

        Args:
            user_id (`str`):
                Owning user identifier.
            agent_id (`str`):
                Agent identifier.
            session_id (`str`):
                Session identifier (accepted for interface parity;
                not used here).
            workspace_id (`str | None`, optional):
                Stable workspace identifier — used as the cache key
                and container name suffix. When ``None`` the manager
                falls back to :meth:`assign_workspace_id`.

        Returns:
            `AppleContainerWorkspace`:
                A live, initialised workspace.
        """
        del session_id  # accepted for interface parity; not used here

        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id="",
            )

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

        # Cache miss: build under the lock.
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            ws = await self._build_and_start(
                workspace_id=workspace_id,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache.

        No-op when the workspace_id is not tracked.

        Args:
            workspace_id (`str`):
                The workspace to close.
        """
        async with self._lock:
            entry = self._cache.pop(workspace_id, None)
        if entry is None:
            return
        ws, _ = entry
        await self._safe_close(ws)

    async def close_all(self) -> None:
        """Close every cached workspace in parallel."""
        async with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        if not entries:
            return
        await asyncio.gather(
            *(self._safe_close(ws) for ws, _ in entries),
            return_exceptions=True,
        )

    # ── async context manager ─────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the TTL sweeper task."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the TTL sweeper task, then close every cached workspace."""
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        await self.close_all()

    # ── background sweeper ───────────────────────────────────────

    async def _sweep_loop(self) -> None:
        """Periodically evict idle workspaces."""
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception(
                    "Apple Container workspace sweeper tick failed",
                )

    async def _sweep_once(self) -> None:
        """One sweeper tick: evict expired entries and close them."""
        now = time.monotonic()
        async with self._lock:
            expired_ids = [
                wid
                for wid, (_, ts) in self._cache.items()
                if now - ts > self._ttl
            ]
            evicted = [self._cache.pop(wid)[0] for wid in expired_ids]
        if not evicted:
            return
        await asyncio.gather(
            *(self._safe_close(ws) for ws in evicted),
            return_exceptions=True,
        )

    @staticmethod
    async def _safe_close(ws: AppleContainerWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close AppleContainerWorkspace %s",
                ws.workspace_id,
            )
