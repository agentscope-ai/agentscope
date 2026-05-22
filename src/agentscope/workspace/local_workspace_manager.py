# -*- coding: utf-8 -*-
"""LocalWorkspaceManager — manages :class:`LocalWorkspace` instances.

Workspaces are keyed by ``workspace_id`` in an in-memory TTL cache.

Usage::

    manager = LocalWorkspaceManager(basedir="/data/workspaces")
    await manager.initialize()

    ws = await manager.create_workspace(
        user_id="u1", agent_id="agent-42", session_id="s1",
    )
    ws_id = ws.workspace_id   # caller persists this

    ws = await manager.get_workspace(ws_id)

    await manager.close_all()
"""

import asyncio
import os
import time
from typing import Any

from .._logging import logger
from ..mcp import MCPClient
from .local_workspace import LocalWorkspace
from .types import SerializedWorkspaceState
from .workspace_base import WorkspaceBase
from .workspace_manager_base import WorkspaceManagerBase


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages local-filesystem workspaces with TTL-based caching.

    Args:
        basedir: Root directory under which per-agent workdirs live.
        default_mcps: MCP clients seeded into brand-new workspaces.
        skill_paths: Skill directories seeded into brand-new workspaces.
        ttl: Seconds before an idle cached workspace is evicted.
    """

    def __init__(
        self,
        basedir: str = "/tmp/agentscope_workspaces",  # noqa: S108
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
    ) -> None:
        super().__init__()
        self._basedir = os.path.abspath(basedir)
        self._default_mcps: list[MCPClient] = list(
            default_mcps or [],
        )
        self._skill_paths: list[str] = list(skill_paths or [])
        self._ttl = ttl
        # workspace_id -> (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[LocalWorkspace, float]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        os.makedirs(self._basedir, exist_ok=True)
        logger.info(
            "LocalWorkspaceManager: initialized at %s",
            self._basedir,
        )

    async def _do_close(self) -> None:
        pass

    # --- TTL eviction ---

    def _evict_expired(
        self,
        now: float,
    ) -> list[LocalWorkspace]:
        """Remove and return workspaces that have exceeded TTL.

        Args:
            now: Current monotonic timestamp.
        """
        expired_ids = [
            wid for wid, (_, ts) in self._cache.items() if now - ts > self._ttl
        ]
        evicted: list[LocalWorkspace] = []
        for wid in expired_ids:
            ws, _ = self._cache.pop(wid)
            self._workspaces.pop(wid, None)
            evicted.append(ws)
        return evicted

    # --- workspace CRUD ---

    async def _do_create(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        """Create a new local workspace.

        The workdir is ``basedir/agent_id`` (deterministic).
        """
        async with self._lock:
            workdir = kwargs.get("workdir") or os.path.join(
                self._basedir,
                agent_id,
            )
            os.makedirs(workdir, exist_ok=True)
            ws = LocalWorkspace(
                workdir=workdir,
                default_mcps=list(
                    kwargs.get("mcps", self._default_mcps),
                ),
                skill_paths=list(
                    kwargs.get(
                        "skill_paths",
                        self._skill_paths,
                    ),
                ),
            )
            await ws.initialize()
            self._cache[ws.workspace_id] = (
                ws,
                time.monotonic(),
            )
            return ws

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        """Look up a workspace by its ID, creating it on cache miss.

        Performs TTL eviction before lookup.  If the workspace is in
        the local cache its last-access timestamp is refreshed.
        """
        async with self._lock:
            now = time.monotonic()
            for ws in self._evict_expired(now):
                await ws.close()

            if workspace_id in self._cache:
                ws, _ = self._cache[workspace_id]
                self._cache[workspace_id] = (ws, now)
                return ws
            if workspace_id in self._workspaces:
                return self._workspaces[workspace_id]

        return await self.create_workspace(
            user_id,
            agent_id,
            session_id,
            **kwargs,
        )

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace."""
        async with self._lock:
            if workspace_id in self._cache:
                ws, _ = self._cache.pop(workspace_id)
                # Remove from parent tracking to prevent double-close
                # in super().close().
                self._workspaces.pop(workspace_id, None)
                await ws.close()
                return

        # Fall back to parent tracking if not in local cache.
        await super().close(workspace_id)

    async def close_all(self) -> None:
        """Close all cached and tracked workspaces."""
        async with self._lock:
            for ws, _ in self._cache.values():
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(
                        "Error closing cached workspace: %s",
                        e,
                    )
            self._cache.clear()

        await super().close_all()

    async def restore(
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        workdir = state.payload.get("workdir", "")
        if not workdir:
            raise ValueError(
                "Cannot restore: 'workdir' missing from state payload",
            )
        ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=list(self._skill_paths),
            default_mcps=list(self._default_mcps),
        )
        await ws.initialize()
        self._workspaces[ws.workspace_id] = ws
        self._cache[ws.workspace_id] = (
            ws,
            time.monotonic(),
        )
        return ws
