# -*- coding: utf-8 -*-
"""The local workspace manager."""

import asyncio
import os
import time

from typing_extensions import deprecated

from ..._logging import logger
from ...workspace import LocalWorkspace
from ._base import WorkspaceManagerBase, IsolationPolicy


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages LocalWorkspace instances with TTL-based lazy lifecycle.

    Workspaces are keyed by ``workspace_id`` in the cache. On cache miss
    the manager reconstructs the workspace from ``basedir/agent_id`` — the
    workdir is deterministic for local workspaces so no storage lookup is
    needed. The derived workdir is always kept strictly inside the base
    directory (the same containment check
    :class:`DockerWorkspaceManager` applies to ``workspace_id``), so a
    hostile ``agent_id`` cannot point it at an arbitrary host directory.
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
                Root directory under which per-agent workdir are
                created.
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
        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[LocalWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        super().__init__(isolation=isolation)

    def _pop_expired(self, now: float) -> list[LocalWorkspace]:
        """Pop every cache entry whose last-access exceeds ``ttl``.

        Caller is responsible for closing the returned workspaces
        *outside* the manager lock so a slow ``close()`` does not stall
        unrelated ``get_workspace`` callers.
        """
        expired_ids = [
            wid for wid, (_, ts) in self._cache.items() if now - ts > self._ttl
        ]
        return [self._cache.pop(wid)[0] for wid in expired_ids]

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
        cache misses for the same ``workspace_id`` cannot create two
        workspaces.

        Raises:
            `ValueError`:
                If ``agent_id`` resolves outside the base directory.
        """
        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id="",
            )

        # Phase 1: cache hit + collect expired.
        async with self._lock:
            now = time.monotonic()
            expired = self._pop_expired(now)
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, now)
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
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            # Workdir is deterministic for local workspaces — no storage needed
            workdir = self._workdir_for(agent_id)
            ws = LocalWorkspace(
                workspace_id=workspace_id,
                workdir=workdir,
                default_mcps=self._default_mcps,
                skill_paths=self._skill_paths,
            )
            await ws.initialize()
            self._cache[workspace_id] = (ws, time.monotonic())
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
        """Create a new workspace for the given agent and return it.

        .. deprecated::
            Use :meth:`get_workspace` with ``workspace_id=None`` — it
            falls back to :meth:`assign_workspace_id` under the
            manager's isolation policy and reuses the cache path.
        """
        del user_id, session_id  # accepted for interface parity

        workdir = self._workdir_for(agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = LocalWorkspace(
            workdir=workdir,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache."""
        async with self._lock:
            entry = self._cache.pop(workspace_id, None)
        if entry is None:
            return
        ws, _ = entry
        await self._safe_close(ws)

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

    def _workdir_for(self, agent_id: str) -> str:
        """Resolve the workdir for *agent_id* under the base directory.

        Raises:
            `ValueError`:
                If ``agent_id`` resolves outside the base directory —
                the same containment check
                :class:`DockerWorkspaceManager` applies to
                ``workspace_id`` (a value that escapes ``basedir`` would
                hand the agent the host filesystem).
        """
        root = os.path.realpath(self._basedir)
        workdir = os.path.realpath(os.path.join(root, agent_id))
        if not self._is_strictly_inside(root, workdir):
            raise ValueError(
                f"agent_id {agent_id!r} escapes the workspace "
                f"base directory",
            )
        return workdir

    @staticmethod
    def _is_strictly_inside(root: str, path: str) -> bool:
        """Whether ``path`` is an entry strictly below ``root``.

        ``path.startswith(root + os.sep)`` cannot decide this: when
        ``root`` is a filesystem root the separator doubles (``"/"``
        becomes ``"//"`` and ``"D:\\"`` becomes ``"D:\\\\"``), so no path
        ever matches and every workspace id is rejected. The relative
        path carries the same relation and stays valid for every root.

        Args:
            root (`str`):
                The absolute, resolved directory expected to contain
                ``path``.
            path (`str`):
                The absolute, resolved candidate entry.

        Returns:
            `bool`:
                `True` if ``path`` lies below ``root`` rather than at or
                above it.
        """
        try:
            relative = os.path.relpath(path, root)
        except ValueError:
            # Different drives on Windows: nothing is below the root.
            return False
        return (
            relative != os.curdir
            and relative != os.pardir
            and not relative.startswith(os.pardir + os.sep)
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
