# -*- coding: utf-8 -*-
"""DaytonaWorkspaceManager — lifecycle manager for Daytona workspaces.

Mirrors :class:`E2BWorkspaceManager` and :class:`DockerWorkspaceManager`
in its public surface (``get_workspace`` / ``create_workspace`` /
``close`` / ``close_all``) so service-layer callers do not branch on
backend.

Daytona-specific behavior:

* Reattachment uses Daytona labels. The ``workspace_id`` is written to
  the sandbox's ``agentscope.workspace.id`` label at create time and
  looked up inside :meth:`DaytonaWorkspace.initialize`.
* ``user_id`` / ``agent_id`` are forwarded as additional labels for
  dashboard filtering. They do **not** participate in cache resolution;
  the cache key is strictly ``workspace_id``.
* The manager does not share an ``AsyncDaytona`` client. Each workspace
  owns its provider client so network resources and shutdown are scoped
  to that workspace.
* Idle workspaces are evicted by a background sweeper task started in
  :meth:`__aenter__` and cancelled in :meth:`__aexit__`.
* ``close_all`` fans calls out with :func:`asyncio.gather` because
  stopping Daytona sandboxes is a remote round-trip per workspace.
"""

import asyncio
import time
from typing import Self

from agentscope._logging import logger
from agentscope.mcp import MCPClient
from agentscope.workspace import DaytonaWorkspace
from agentscope.workspace._daytona._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_SWEEP_INTERVAL,
    DEFAULT_TIMEOUT,
)
from ._base import WorkspaceManagerBase


class DaytonaWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`DaytonaWorkspace` instances with TTL caching.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        api_url: str = "",
        target: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the Daytona workspace manager.

        Args:
            api_key (`str`, defaults to `""`):
                Daytona API key forwarded to every workspace. ``""``
                lets the Daytona SDK read credentials from the
                environment.
            api_url (`str`, defaults to `""`):
                Optional Daytona API URL for self-hosted or non-default
                deployments.
            target (`str`, defaults to `""`):
                Optional Daytona target / region.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Timeout forwarded to workspace create/start/recover/stop
                operations.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables passed to Daytona create params.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra labels merged with the per-workspace
                ``agentscope.workspace.id`` / ``agentscope.user.id`` /
                ``agentscope.agent.id`` labels.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. Persisted
                ``.mcp`` state wins on reattach.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted and
                its sandbox stopped.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up to
                look for idle workspaces.
        """
        self._api_key = api_key
        self._api_url = api_url
        self._target = target
        self._timeout_seconds = timeout_seconds
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._sandbox_metadata = dict(sandbox_metadata or {})
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[DaytonaWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    # ── metadata helper ───────────────────────────────────────────

    def _metadata_for(self, user_id: str, agent_id: str) -> dict[str, str]:
        """Build user/agent labels for Daytona dashboard filtering.

        ``DaytonaWorkspace`` always sets ``agentscope.workspace.id``;
        this manager adds user/agent labels so provider dashboards can
        be filtered without changing AgentScope cache semantics.
        """
        return {
            "agentscope.user.id": user_id,
            "agentscope.agent.id": agent_id,
            **self._sandbox_metadata,
        }

    # ── workspace construction ────────────────────────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        user_id: str,
        agent_id: str,
    ) -> DaytonaWorkspace:
        """Construct a :class:`DaytonaWorkspace` and initialize it.

        ``workspace_id=None`` lets :class:`WorkspaceBase` allocate a
        fresh UUID, used by :meth:`create_workspace`. Otherwise the
        provided id is forwarded so label-based reattachment works on
        cache miss.
        """
        ws = DaytonaWorkspace(
            workspace_id=workspace_id,
            api_key=self._api_key,
            api_url=self._api_url,
            target=self._target,
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

    # ── public API ────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> DaytonaWorkspace:
        """Return an initialized workspace, reattaching on cache miss.

        On miss the manager constructs ``DaytonaWorkspace`` with the
        requested ``workspace_id`` and lets its ``initialize`` find a
        matching sandbox by label or create a fresh sandbox otherwise.

        Args:
            user_id (`str`):
                Owning user identifier, forwarded as sandbox label only.
            agent_id (`str`):
                Agent identifier, forwarded as sandbox label only.
            session_id (`str`):
                Session identifier (unused; sessions partition under
                ``sessions/<session_id>/`` inside a workspace).
            workspace_id (`str`):
                Stable workspace identifier and cache key.

        Returns:
            `DaytonaWorkspace`:
                A live, initialized workspace.
        """
        del session_id

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

        # Cache miss: build under the lock to prevent two concurrent
        # calls for the same workspace_id from creating two sandboxes.
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
    ) -> DaytonaWorkspace:
        """Build a brand-new workspace and track it.

        A fresh ``workspace_id`` is allocated by
        :class:`WorkspaceBase`; the caller should persist
        ``workspace.workspace_id`` for later :meth:`get_workspace`
        calls.

        Args:
            user_id (`str`):
                Owning user identifier (forwarded as sandbox labels).
            agent_id (`str`):
                Agent identifier (forwarded as sandbox labels).
            session_id (`str`):
                Session identifier (accepted for parity; not used
                here).

        Returns:
            `DaytonaWorkspace`:
                The newly built workspace, already initialized.
        """
        del session_id

        ws = await self._build_and_start(
            workspace_id=None,
            user_id=user_id,
            agent_id=agent_id,
        )
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close (= gracefully stop the sandbox) and evict a workspace.

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
        """Close every cached workspace in parallel.

        ``sandbox.stop()`` is a remote round-trip per sandbox; doing it
        sequentially on app shutdown produces a noticeable stall, so we
        fan the calls out with :func:`asyncio.gather`.
        """
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
        """Periodically close idle workspaces.

        Runs forever until cancelled. Each tick pops every cache entry
        whose last-access is older than ``ttl`` and closes it outside
        the lock; exceptions during close are logged and swallowed so
        one bad sandbox does not poison the sweeper.
        """
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("Daytona workspace sweeper tick failed")

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
    async def _safe_close(ws: DaytonaWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close DaytonaWorkspace %s",
                ws.workspace_id,
            )
