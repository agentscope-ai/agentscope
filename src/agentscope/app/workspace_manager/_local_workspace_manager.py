# -*- coding: utf-8 -*-
"""The local workspace manager."""

import asyncio
import hashlib
import os
import time

from typing_extensions import deprecated

from ..._logging import logger
from ...workspace import LocalWorkspace
from ._base import WorkspaceManagerBase, IsolationPolicy


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages LocalWorkspace instances with TTL-based lazy lifecycle.

    Workspaces are scoped by ``(user_id, workspace_id)`` in both the cache
    and the filesystem. This keeps explicit workspace identifiers useful
    for same-user team sharing without making them cross-user bearer keys.
    """

    def __init__(
        self,
        basedir: str,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        default_mcps: list | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
    ) -> None:
        """Initialize the local workspace manager.

        Args:
            basedir (`str`):
                Root directory under which user/workspace-scoped workdirs
                are created.
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`. See
                :class:`DockerWorkspaceManager` for semantics.
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
        # (user_id, workspace_id) -> (workspace, last_access_monotonic)
        self._cache: dict[
            tuple[str, str],
            tuple[LocalWorkspace, float],
        ] = {}
        self._lock = asyncio.Lock()
        super().__init__(isolation=isolation)

    @staticmethod
    def _path_component(value: str) -> str:
        """Return a filesystem-safe, opaque component for an identifier."""
        return hashlib.blake2b(
            value.encode("utf-8"),
            digest_size=16,
        ).hexdigest()

    def _workdir_for(self, user_id: str, workspace_id: str) -> str:
        """Resolve a workdir scoped to one user and workspace.

        Legacy ``basedir/agent_id`` directories are intentionally ignored:
        they contain no ownership metadata and cannot be migrated safely.
        """
        return os.path.join(
            self._basedir,
            self._path_component(user_id),
            self._path_component(workspace_id),
        )

    def _pop_expired(self, now: float) -> list[LocalWorkspace]:
        """Pop every cache entry whose last-access exceeds ``ttl``.

        Caller is responsible for closing the returned workspaces
        *outside* the manager lock so a slow ``close()`` does not stall
        unrelated ``get_workspace`` callers.
        """
        expired_keys = [
            key for key, (_, ts) in self._cache.items() if now - ts > self._ttl
        ]
        return [self._cache.pop(key)[0] for key in expired_keys]

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> LocalWorkspace:
        """Return an initialized workspace, reconstructing from
        disk on cache miss.

        Mirrors the Docker / E2B managers' double-check pattern: a
        first lock acquisition handles the cache-hit fast path and
        collects expired entries; expired entries are then closed in
        parallel *outside* the lock; on a miss a second acquisition
        runs ``initialize()`` while holding the lock so two concurrent
        cache misses for the same user and ``workspace_id`` cannot create
        two workspaces.
        """
        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
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
        # calls for the same user/workspace pair from creating two
        # workspaces.
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                ws, _ = cached
                self._cache[cache_key] = (ws, time.monotonic())
                return ws

            # Workdir is deterministic; no storage lookup is needed.
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

    @deprecated(
        "LocalWorkspaceManager.create_workspace is deprecated; "
        "use get_workspace(workspace_id=None) instead.",
        category=None,
    )
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> LocalWorkspace:
        """Return the isolated workspace for the given agent.

        .. deprecated::
            Use :meth:`get_workspace` with ``workspace_id=None`` — it
            falls back to :meth:`assign_workspace_id` under the
            manager's isolation policy and reuses the cache path.
        """
        return await self.get_workspace(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )

    async def close(self, workspace_id: str) -> None:
        """Close and evict every user-scoped entry for a workspace id."""
        async with self._lock:
            keys = [key for key in self._cache if key[1] == workspace_id]
            entries = [self._cache.pop(key) for key in keys]
        if not entries:
            return
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
