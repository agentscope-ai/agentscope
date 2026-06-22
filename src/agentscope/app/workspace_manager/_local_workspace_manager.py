# -*- coding: utf-8 -*-
"""The local workspace manager."""

import asyncio
import os
import time

from ..._logging import logger
from ..._utils._common import _generate_id
from ...workspace import LocalWorkspace
from ._base import WorkspaceManagerBase


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages LocalWorkspace instances with TTL-based lazy lifecycle.

    Workspaces are keyed by ``workspace_id`` in the cache. On cache miss
    the manager reconstructs the workspace from ``basedir/agent_id`` — the
    workdir is deterministic for local workspaces so no storage lookup is
    needed.
    """

    def __init__(
        self,
        basedir: str,
        default_mcps: list | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
    ) -> None:
        """Initialize the local workspace manager.

        Args:
            basedir (`str`):
                Root directory under which per-agent workdir are
                created.
            default_mcps (`list | None`, optional):
                MCP clients seeded into brand-new workspaces.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted.
        """
        self._basedir = os.path.abspath(basedir)
        self._default_mcps = default_mcps or []
        self._skill_paths = skill_paths or []
        self._ttl = ttl
        # (user_id, workspace_id) → (workspace, last_access_monotonic)
        self._cache: dict[tuple[str, str], tuple[LocalWorkspace, float]] = {}
        self._lock = asyncio.Lock()

    def _pop_expired(self, now: float) -> list[LocalWorkspace]:
        """Pop every cache entry whose last-access exceeds ``ttl``.

        Caller is responsible for closing the returned workspaces
        *outside* the manager lock so a slow ``close()`` does not stall
        unrelated ``get_workspace`` callers.
        """
        expired_ids = [
            key
            for key, (_, ts) in self._cache.items()
            if now - ts > self._ttl
            and self._active_run_counts().get(key, 0) == 0
        ]
        return [self._cache.pop(key)[0] for key in expired_ids]

    def _workdir_for(self, user_id: str, workspace_id: str) -> str:
        """Return the stable physical path for an owned workspace."""
        return os.path.join(self._basedir, user_id, workspace_id)

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> LocalWorkspace:
        """Return an initialized workspace, reconstructing from
        disk on cache miss.

        Mirrors the Docker / E2B managers' double-check pattern: a
        first lock acquisition handles the cache-hit fast path and
        collects expired entries; expired entries are then closed in
        parallel *outside* the lock; on a miss a second acquisition
        runs ``initialize()`` while holding the lock so two concurrent
        cache misses for the same ``workspace_id`` cannot create two
        workspaces.
        """
        cache_key = (user_id, workspace_id)

        # Phase 1: cache hit + collect expired.
        async with self._lock:
            now = time.monotonic()
            expired = self._pop_expired(now)
            cached = self._cache.get(cache_key)
            if cached is not None:
                ws, _ = cached
                self._cache[cache_key] = (ws, now)
                hit: LocalWorkspace | None = ws
            else:
                hit = None

        # Phase 2: close expired entries outside the lock, in parallel,
        # so a slow stdio MCP shutdown does not block unrelated callers.
        if expired:
            await asyncio.gather(
                *(self._safe_close(ws) for ws in expired),
                return_exceptions=True,
            )

        if hit is not None:
            return hit

        # Phase 3: build under the lock to prevent two concurrent
        # get_workspace(workspace_id=X) calls from creating two
        # workspaces for the same id.
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                ws, _ = cached
                self._cache[cache_key] = (ws, time.monotonic())
                return ws

            # Workdir is deterministic for local workspaces — no storage needed
            workdir = self._workdir_for(user_id, workspace_id)
            ws = LocalWorkspace(
                workspace_id=workspace_id,
                workdir=workdir,
                default_mcps=self._default_mcps,
                skill_paths=self._skill_paths,
            )
            await ws.initialize()
            self._cache[cache_key] = (ws, time.monotonic())
            return ws

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> LocalWorkspace:
        """Create a new workspace for the given agent and return it."""
        del agent_id, session_id  # accepted for interface parity

        workspace_id = _generate_id()
        workdir = self._workdir_for(user_id, workspace_id)
        os.makedirs(workdir, exist_ok=True)
        ws = LocalWorkspace(
            workspace_id=workspace_id,
            workdir=workdir,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        async with self._lock:
            self._cache[(user_id, ws.workspace_id)] = (ws, time.monotonic())
        return ws

    async def close(
        self,
        workspace_id: str,
        user_id: str | None = None,
    ) -> None:
        """Close and evict a single workspace from the cache."""
        async with self._lock:
            keys = [
                key
                for key in self._cache
                if key[1] == workspace_id
                and (user_id is None or key[0] == user_id)
            ]
            entries = [self._cache.pop(key) for key in keys]
        await asyncio.gather(
            *(self._safe_close(ws) for ws, _ in entries),
            return_exceptions=True,
        )

    async def close_all(self) -> None:
        """Close every cached workspace in parallel.

        Stdio MCP shutdown can be slow per workspace; doing it
        sequentially on app shutdown produces a noticeable stall, so
        we fan the calls out with :func:`asyncio.gather` (mirrors the
        Docker / E2B managers).
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

    @staticmethod
    async def _safe_close(ws: LocalWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close LocalWorkspace %s",
                ws.workspace_id,
            )
