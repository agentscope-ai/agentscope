# -*- coding: utf-8 -*-
"""DockerWorkspaceManager — lifecycle manager for :class:`DockerWorkspace`.

Mirrors :class:`LocalWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so that callers — notably :func:`agentscope.app._service.get_agent` —
do not branch on backend.

Differences from the local manager (allowed to surface only via the
constructor):

* Workdir layout is two levels — ``<basedir>/<user_id>/<agent_id>`` —
  and is bind-mounted to ``/workspace`` inside each container, so the
  agent always sees a flat ``/workspace`` regardless of host layout.
* ``workspace_id`` is forwarded into :class:`DockerWorkspace` so the
  container name (``as_ws_<workspace_id>``) is stable across process
  restarts. A cache miss after a restart deterministically re-attaches
  to the same container slot via ``containers.create_or_replace``.
* Idle workspaces are evicted by a dedicated background sweeper task
  started in :meth:`__aenter__` and cancelled in :meth:`__aexit__` —
  not lazily on each :meth:`get_workspace` call. This keeps idle
  resource consumption bounded even when no traffic is arriving.
* ``close_all`` shuts containers down in parallel
  (:func:`asyncio.gather`) — Docker ``kill + delete`` is slow enough
  that linear teardown on shutdown is noticeable.
"""

import asyncio
import os
import time
import uuid
from typing import Self

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace._docker import DockerWorkspace
from ...workspace._docker._make_dockerfile import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_GATEWAY_PORT,
)
from ._workspace_manager import WorkspaceManagerBase
from ._workspace_pool import PooledEntry, WorkspacePool

DEFAULT_SWEEP_INTERVAL = 300.0


class DockerWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`DockerWorkspace` instances with TTL-based caching.

    The manager owns a single set of image-build parameters
    (``base_image`` / ``node_version`` / ``extra_pip``) shared by every
    workspace it produces; the resulting image is content-hashed so
    rebuilds are skipped on cache hits.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        basedir: str = "",
        *,
        base_image: str = DEFAULT_BASE_IMAGE,
        node_version: str = "20",
        extra_pip: list[str] | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
        # ── pooling parameters (disabled by default) ──────────
        pool_enabled: bool = False,
        min_idle: int = 1,
        max_idle: int = 3,
        total: int = 10,
        create_batch_size: int = 2,
        max_reuse: int = 50,
    ) -> None:
        """Initialize the docker workspace manager.

        Args:
            basedir (`str`):
                Host root under which per-user/per-agent workdir are
                created (``<basedir>/<user_id>/<agent_id>``). Each
                workdir is bind-mounted to ``/workspace`` inside its
                container. Only used in TTL-cache mode; pool mode
                creates ephemeral containers without bind mounts.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                Base Docker image; must provide ``python3``.
            node_version (`str`, defaults to `"20"`):
                Major Node.js version (e.g. ``"20"``) to bake into
                the image.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv at image-build time.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-container gateway listens on (always
                exposed to a randomly assigned host port).
            env (`dict[str, str] | None`, optional):
                Environment variables to set inside every workspace's
                container.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. Ignored
                on subsequent restarts of a workdir that already
                persists ``.mcp``.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted
                and its container torn down. Only used when
                ``pool_enabled=False``.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up
                to look for idle workspaces. Only used when
                ``pool_enabled=False``.
            pool_enabled (`bool`, defaults to `False`):
                When ``True``, use a pre-warming pool instead of the
                TTL cache. The pool provides stronger isolation by
                destroying and recreating containers on each release
                (via :meth:`DockerWorkspace.heavy_reset_for_pool`).
            min_idle (`int`, defaults to `1`):
                Pool: minimum idle instances to maintain.
            max_idle (`int`, defaults to `3`):
                Pool: target idle count after replenishment.
            total (`int`, defaults to `10`):
                Pool: hard cap on total managed instances.
            create_batch_size (`int`, defaults to `2`):
                Pool: concurrent factory calls per replenishment.
            max_reuse (`int`, defaults to `50`):
                Pool: max recycling count per container.
        """
        self._basedir = os.path.abspath(basedir) if basedir else ""
        self._base_image = base_image
        self._node_version = node_version
        self._extra_pip = list(extra_pip or [])
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        self._pool_enabled = pool_enabled

        # ── TTL-cache mode (pool_enabled=False) ───────────────
        self._cache: dict[str, tuple[DockerWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

        # ── Pool mode (pool_enabled=True) ─────────────────────
        self._active: dict[str, PooledEntry[DockerWorkspace]] = {}
        self._pool: WorkspacePool[DockerWorkspace] | None = None
        if pool_enabled:
            self._pool = WorkspacePool[DockerWorkspace](
                factory=self._pool_factory,
                reset_fn=self._pool_reset,
                health_check_fn=self._pool_health_check,
                close_fn=self._pool_close,
                pause_fn=self._pool_pause,
                resume_fn=self._pool_resume,
                min_idle=min_idle,
                max_idle=max_idle,
                total=total,
                create_batch_size=create_batch_size,
                max_reuse=max_reuse,
            )

    # ── isolation helpers ─────────────────────────────────────────

    def _workdir_for(self, user_id: str, agent_id: str) -> str:
        """Resolve the host workdir for ``(user_id, agent_id)``."""
        return os.path.join(self._basedir, user_id, agent_id)

    # ── workspace construction (TTL-cache mode) ───────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> DockerWorkspace:
        """Create a :class:`DockerWorkspace` and run ``initialize``."""
        workdir = self._workdir_for(user_id, agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = DockerWorkspace(
            workspace_id=workspace_id,
            workdir=workdir,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    # ── pool callbacks (pool_enabled=True) ────────────────────────

    async def _pool_factory(self) -> DockerWorkspace:
        """Create and initialize a fresh DockerWorkspace for the pool."""
        ws = DockerWorkspace(
            workspace_id=None,
            workdir=None,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        logger.info(
            "DockerWorkspaceManager[pool]: created workspace %s",
            ws.workspace_id,
        )
        return ws

    async def _pool_reset(self, ws: DockerWorkspace) -> None:
        """Heavy reset: destroy container and recreate for strong isolation."""
        await ws.heavy_reset_for_pool(
            default_mcps=self._default_mcps or None,
            skill_paths=self._skill_paths or None,
        )

    @staticmethod
    async def _pool_health_check(ws: DockerWorkspace) -> bool:
        """Probe gateway health."""
        return await ws.gateway_health()

    @staticmethod
    async def _pool_pause(ws: DockerWorkspace) -> None:
        """Pause (freeze) the container."""
        await ws.pause()

    @staticmethod
    async def _pool_resume(ws: DockerWorkspace) -> None:
        """Resume a paused container."""
        await ws.resume()

    @staticmethod
    async def _pool_close(ws: DockerWorkspace) -> None:
        """Permanently destroy a workspace."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "DockerWorkspaceManager[pool]: failed to close %s",
                ws.workspace_id,
            )

    # ── public API ────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> DockerWorkspace:
        """Return an initialised workspace.

        In TTL-cache mode, builds on cache miss. In pool mode,
        checks out from the pool if not already active.
        """
        del session_id

        if self._pool_enabled:
            return await self._pool_get_workspace(
                user_id,
                agent_id,
                workspace_id,
            )

        # ── TTL-cache mode ────────────────────────────────────
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            ws = await self._build_and_start(
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=agent_id,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> DockerWorkspace:
        """Build or check out a workspace."""
        del session_id

        if self._pool_enabled:
            return await self._pool_create_workspace(user_id, agent_id)

        # ── TTL-cache mode ────────────────────────────────────
        workdir = self._workdir_for(user_id, agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = DockerWorkspace(
            workdir=workdir,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close / release a workspace."""
        if self._pool_enabled:
            return await self._pool_close_workspace(workspace_id)

        # ── TTL-cache mode ────────────────────────────────────
        async with self._lock:
            entry = self._cache.pop(workspace_id, None)
        if entry is None:
            return
        ws, _ = entry
        await self._safe_close(ws)

    async def close_all(self) -> None:
        """Close / release every tracked workspace."""
        if self._pool_enabled:
            return await self._pool_close_all()

        # ── TTL-cache mode ────────────────────────────────────
        async with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        if not entries:
            return
        await asyncio.gather(
            *(self._safe_close(ws) for ws, _ in entries),
            return_exceptions=True,
        )

    # ── pool-mode public API helpers ──────────────────────────────

    async def _pool_get_workspace(
        self,
        user_id: str,  # noqa: W0613
        agent_id: str,  # noqa: W0613
        workspace_id: str,
    ) -> DockerWorkspace:
        del user_id, agent_id
        async with self._lock:
            entry = self._active.get(workspace_id)
            if entry is not None:
                return entry.workspace

        assert self._pool is not None
        entry = await self._pool.acquire()
        ws = entry.workspace

        async with self._lock:
            existing = self._active.get(workspace_id)
            if existing is not None:
                asyncio.create_task(self._pool.release(entry))
                return existing.workspace
            self._active[workspace_id] = entry

        logger.info(
            "DockerWorkspaceManager[pool]: checked out %s for workspace_id=%s",
            ws.workspace_id,
            workspace_id,
        )
        return ws

    async def _pool_create_workspace(
        self,
        user_id: str,
        agent_id: str,
    ) -> DockerWorkspace:
        workspace_id = uuid.uuid4().hex
        return await self._pool_get_workspace(
            user_id,
            agent_id,
            workspace_id,
        )

    async def _pool_close_workspace(self, workspace_id: str) -> None:
        async with self._lock:
            entry = self._active.pop(workspace_id, None)
        if entry is None:
            return
        assert self._pool is not None
        await self._pool.release(entry)

    async def _pool_close_all(self) -> None:
        async with self._lock:
            entries = list(self._active.items())
            self._active.clear()
        if not entries:
            return
        assert self._pool is not None
        await asyncio.gather(
            *(self._pool.release(entry) for _, entry in entries),
            return_exceptions=True,
        )

    # ── async context manager ─────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the pool or TTL sweeper."""
        if self._pool_enabled:
            assert self._pool is not None
            await self._pool.start()
        else:
            if self._sweep_task is None:
                self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the pool or TTL sweeper, then close everything."""
        if self._pool_enabled:
            await self.close_all()
            assert self._pool is not None
            await self._pool.stop()
        else:
            if self._sweep_task is not None:
                self._sweep_task.cancel()
                try:
                    await self._sweep_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._sweep_task = None
            await self.close_all()

    # ── background sweeper (TTL-cache mode only) ──────────────────

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
                logger.exception("Docker workspace sweeper tick failed")

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
    async def _safe_close(ws: DockerWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close DockerWorkspace %s",
                ws.workspace_id,
            )
