# -*- coding: utf-8 -*-
"""Workspace manager implementations."""
import asyncio
import os
import time
from abc import ABC, abstractmethod

from ...workspace import WorkspaceBase, LocalWorkspace


class WorkspaceManagerBase(ABC):
    """Abstract base for workspace managers."""

    @abstractmethod
    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceBase:
        """Return an initialized workspace.

        Args:
            user_id: The user id.
            agent_id: The agent id.
            session_id: The session id.
            workspace_id: The workspace id (reconnection credential).
        """

    @abstractmethod
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> WorkspaceBase:
        """Create a new workspace and return it."""

    @abstractmethod
    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache."""

    @abstractmethod
    async def close_all(self) -> None:
        """Close all cached workspaces (called on app shutdown)."""


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages LocalWorkspace instances with TTL-based lazy lifecycle.

    Workspaces are keyed by ``workspace_id`` in the cache.  On cache miss
    the manager reconstructs the workspace from ``basedir/agent_id`` — the
    workdir is deterministic for local workspaces so no storage lookup is
    needed.

    Args:
        basedir: Root directory under which per-agent workdirs are created.
        default_mcps: MCP clients seeded into brand-new workspaces.
        skill_paths: Skill directories seeded into brand-new workspaces.
        ttl: Seconds before an idle cached workspace is evicted.
    """

    def __init__(
        self,
        basedir: str,
        default_mcps: list | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
    ) -> None:
        self._basedir = os.path.abspath(basedir)
        self._default_mcps = default_mcps or []
        self._skill_paths = skill_paths or []
        self._ttl = ttl
        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[LocalWorkspace, float]] = {}
        self._lock = asyncio.Lock()

    def _evict_expired(self, now: float) -> list[LocalWorkspace]:
        expired_ids = [
            wid for wid, (_, ts) in self._cache.items() if now - ts > self._ttl
        ]
        evicted = []
        for wid in expired_ids:
            ws, _ = self._cache.pop(wid)
            evicted.append(ws)
        return evicted

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> LocalWorkspace:
        """Return an initialized workspace, reconstructing from
        disk on cache miss."""
        async with self._lock:
            now = time.monotonic()
            for ws in self._evict_expired(now):
                await ws.close()

            if workspace_id in self._cache:
                ws, _ = self._cache[workspace_id]
                self._cache[workspace_id] = (ws, now)
                return ws

            # Workdir is deterministic for local workspaces — no storage needed
            workdir = os.path.join(self._basedir, agent_id)
            ws = LocalWorkspace(workdir=workdir, skill_paths=self._skill_paths)
            await ws.initialize()
            self._cache[workspace_id] = (ws, now)
            return ws

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> LocalWorkspace:
        """Create a new workspace for the given agent and return it."""
        async with self._lock:
            workdir = os.path.join(self._basedir, agent_id)
            os.makedirs(workdir, exist_ok=True)
            ws = LocalWorkspace(
                workdir=workdir,
                default_mcps=self._default_mcps,
                skill_paths=self._skill_paths,
            )
            await ws.initialize()
            self._cache[ws.id] = (ws, time.monotonic())
            return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache."""
        async with self._lock:
            if workspace_id in self._cache:
                ws, _ = self._cache.pop(workspace_id)
                await ws.close()

    async def close_all(self) -> None:
        """Close all cached workspaces."""
        async with self._lock:
            for ws, _ in self._cache.values():
                await ws.close()
            self._cache.clear()
