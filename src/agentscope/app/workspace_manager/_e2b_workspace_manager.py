# -*- coding: utf-8 -*-
"""E2BWorkspaceManager — lifecycle manager for :class:`E2BWorkspace`.

Mirrors :class:`DockerWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so callers — notably :class:`agentscope.app._service.ChatService` —
do not branch on backend.

Differences from the Docker manager:

* No ``basedir`` / ``_workdir_for`` — E2B sandboxes carry their own
  filesystem state across pause/resume, so there is nothing to
  bind-mount and nothing to lay out on the host.
* No image build parameters (``base_image`` / ``node_version``); E2B
  attaches to a pre-built template plus a runtime bootstrap.
* Reattachment uses E2B sandbox metadata. The ``workspace_id`` is
  written into the sandbox's metadata at create time and looked up via
  ``AsyncSandbox.list(query=...)`` inside
  :meth:`E2BWorkspace.initialize`. The manager itself is metadata-blind
  — it just forwards ``workspace_id`` and lets the workspace handle the
  reattach.
* ``user_id`` / ``agent_id`` are surfaced as extra sandbox metadata
  (``agentscope.user.id`` / ``agentscope.agent.id``) so users can
  filter their own sandboxes in the E2B dashboard. They do **not**
  participate in cache key resolution; the cache is keyed strictly on
  ``workspace_id`` (same as Docker).
* Idle workspaces are evicted by a dedicated background sweeper task
  started in :meth:`__aenter__` and cancelled in :meth:`__aexit__` —
  not lazily on each :meth:`get_workspace` call.
* ``close_all`` fans calls out with :func:`asyncio.gather` because
  ``sandbox.pause()`` is a remote round-trip per sandbox; sequentialising
  it on app shutdown produces a noticeable stall.
"""

import asyncio
import time
import uuid
from typing import Self

from agentscope._logging import logger
from agentscope.mcp import MCPClient
from agentscope.workspace import E2BWorkspace
from agentscope.workspace._e2b._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
)

from ._base import WorkspaceManagerBase
from ._workspace_pool import PooledEntry, WorkspacePool

DEFAULT_SWEEP_INTERVAL = 300.0


class E2BWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`E2BWorkspace` instances with TTL-based caching.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        *,
        template: str = DEFAULT_TEMPLATE,
        api_key: str = "",
        domain: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
        # ── pooling parameters (disabled by default) ──────────
        pool_enabled: bool = False,
        pool_min_ready: int = 1,
        pool_max_ready: int = 3,
        pool_capacity: int = 10,
        pool_batch_size: int = 2,
    ) -> None:
        """Initialize the E2B workspace manager.

        Args:
            template (`str`, defaults to `DEFAULT_TEMPLATE`):
                E2B template id passed to every workspace this
                manager produces. Defaults to ``"base"``.
            api_key (`str`, defaults to `""`):
                E2B API key. ``""`` falls back to the ``E2B_API_KEY``
                env var on the SDK side.
            domain (`str`, defaults to `""`):
                Optional custom E2B domain (self-hosted etc.).
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive timeout passed to
                ``AsyncSandbox.create`` / ``AsyncSandbox.connect``.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into the sandbox at
                create time.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the per-workspace
                ``agentscope.workspace.id`` / ``agentscope.user.id`` /
                ``agentscope.agent.id`` keys. Useful for downstream
                E2B dashboard filtering.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. Ignored
                on subsequent reattachments — the sandbox's persisted
                ``.mcp`` file wins.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted
                and its sandbox paused. Only used when
                ``pool_enabled=False``.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up
                to look for idle workspaces. Defaults to 5 minutes.
                Only used when ``pool_enabled=False``.
            pool_enabled (`bool`, defaults to `False`):
                When ``True``, use a pre-warming pool instead of the
                TTL cache. Each sandbox is used exactly once
                (``max_reuse=1``) and destroyed on release; the pool
                background loop creates fresh replacements.
            pool_min_ready (`int`, defaults to `1`):
                Pool: minimum number of ready-to-use instances kept
                on standby. When the count drops below this threshold,
                the pool automatically creates new instances in the
                background.
            pool_max_ready (`int`, defaults to `3`):
                Pool: target number of ready-to-use instances after
                replenishment. The pool will create instances up to
                this count when triggered.
            pool_capacity (`int`, defaults to `10`):
                Pool: maximum total instances managed by the pool
                (both in-use and standby combined). Requests beyond
                this limit trigger overflow creation.
            pool_batch_size (`int`, defaults to `2`):
                Pool: how many instances to create concurrently per
                replenishment cycle.
        """
        self._template = template
        self._api_key = api_key
        self._domain = domain
        self._timeout_seconds = timeout_seconds
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._sandbox_metadata = dict(sandbox_metadata or {})
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        self._pool_enabled = pool_enabled

        # ── TTL-cache mode (pool_enabled=False) ───────────────
        self._cache: dict[str, tuple[E2BWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

        # ── Pool mode (pool_enabled=True) ─────────────────────
        self._active: dict[str, PooledEntry[E2BWorkspace]] = {}
        self._pool: WorkspacePool[E2BWorkspace] | None = None
        if pool_enabled:
            self._pool = WorkspacePool[E2BWorkspace](
                factory=self._pool_factory,
                reset_fn=None,
                health_check_fn=self._pool_health_check,
                close_fn=self._pool_close,
                pause_fn=self._pool_pause,
                resume_fn=self._pool_resume,
                pool_min_ready=pool_min_ready,
                pool_max_ready=pool_max_ready,
                pool_capacity=pool_capacity,
                pool_batch_size=pool_batch_size,
                max_reuse=1,
            )

    # ── metadata helper ───────────────────────────────────────────

    def _metadata_for(
        self,
        user_id: str,
        agent_id: str,
    ) -> dict[str, str]:
        """Build the extra sandbox metadata for ``(user_id, agent_id)``."""
        return {
            "agentscope.user.id": user_id,
            "agentscope.agent.id": agent_id,
            **self._sandbox_metadata,
        }

    # ── workspace construction (TTL-cache mode) ───────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        user_id: str,
        agent_id: str,
    ) -> E2BWorkspace:
        """Construct an E2BWorkspace and run ``initialize``."""
        ws = E2BWorkspace(
            workspace_id=workspace_id,
            template=self._template,
            api_key=self._api_key,
            domain=self._domain,
            timeout_seconds=self._timeout_seconds,
            gateway_port=self._gateway_port,
            env=self._env,
            sandbox_metadata=self._metadata_for(user_id, agent_id),
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    # ── pool callbacks (pool_enabled=True) ────────────────────────

    async def _pool_factory(self) -> E2BWorkspace:
        """Create and initialize a fresh E2BWorkspace for the pool."""
        ws = E2BWorkspace(
            workspace_id=None,
            template=self._template,
            api_key=self._api_key,
            domain=self._domain,
            timeout_seconds=self._timeout_seconds,
            gateway_port=self._gateway_port,
            env=self._env,
            sandbox_metadata=dict(self._sandbox_metadata),
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        logger.info(
            "E2BWorkspaceManager[pool]: created workspace %s",
            ws.workspace_id,
        )
        return ws

    @staticmethod
    async def _pool_health_check(ws: E2BWorkspace) -> bool:
        """Probe gateway health."""
        return await ws.gateway_health()

    @staticmethod
    async def _pool_pause(ws: E2BWorkspace) -> None:
        """Pause sandbox to stop billing."""
        await ws.pause()

    @staticmethod
    async def _pool_resume(ws: E2BWorkspace) -> None:
        """Resume a paused sandbox."""
        await ws.resume()

    @staticmethod
    async def _pool_close(ws: E2BWorkspace) -> None:
        """Permanently destroy a workspace."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "E2BWorkspaceManager[pool]: failed to close %s",
                ws.workspace_id,
            )

    # ── public API ────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> E2BWorkspace:
        """Return an initialised workspace.

        In TTL-cache mode, reattaches on cache miss. In pool mode,
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
    ) -> E2BWorkspace:
        """Build or check out a workspace."""
        del session_id

        if self._pool_enabled:
            return await self._pool_create_workspace(user_id, agent_id)

        # ── TTL-cache mode ────────────────────────────────────
        ws = await self._build_and_start(
            workspace_id=None,
            user_id=user_id,
            agent_id=agent_id,
        )
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
        user_id: str,
        agent_id: str,
        workspace_id: str,
    ) -> E2BWorkspace:
        """Return an active workspace, checking out from the pool if needed.

        If ``workspace_id`` is already active, its workspace is returned
        directly. Otherwise a fresh entry is acquired from the pool,
        sandbox metadata is updated, and the entry is registered as
        active.
        """
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
                self._pool.release_background(entry)
                return existing.workspace
            ws.sandbox_metadata.update(
                {
                    "agentscope.user.id": user_id,
                    "agentscope.agent.id": agent_id,
                    "agentscope.workspace.id": workspace_id,
                },
            )
            self._active[workspace_id] = entry

        logger.info(
            "E2BWorkspaceManager[pool]: checked out %s for workspace_id=%s",
            ws.workspace_id,
            workspace_id,
        )
        return ws

    async def _pool_create_workspace(
        self,
        user_id: str,
        agent_id: str,
    ) -> E2BWorkspace:
        """Create a new workspace by generating an id and checking out."""
        workspace_id = uuid.uuid4().hex
        return await self._pool_get_workspace(
            user_id,
            agent_id,
            workspace_id,
        )

    async def _pool_close_workspace(self, workspace_id: str) -> None:
        """Close a single active workspace and release it back to the pool."""
        async with self._lock:
            entry = self._active.pop(workspace_id, None)
        if entry is None:
            return
        assert self._pool is not None
        await self._pool.release(entry)

    async def _pool_close_all(self) -> None:
        """Close every active workspace and release them back to the pool."""
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
        """Periodically pause idle workspaces."""
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("E2B workspace sweeper tick failed")

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
    async def _safe_close(ws: E2BWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close E2BWorkspace %s",
                ws.workspace_id,
            )
