# -*- coding: utf-8 -*-
"""BubblewrapWorkspaceManager -- lifecycle manager for Bubblewrap."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Self

from typing_extensions import deprecated

from ..._logging import logger
from ..._utils._common import _generate_id
from ...mcp import MCPClient
from ...workspace import BubblewrapWorkspace
from ...workspace._bubblewrap._constants import DEFAULT_GATEWAY_PORT
from ._base import IsolationPolicy, WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


def _safe_component(value: str) -> str:
    """Hash an external identifier into a filesystem-safe component."""
    return hashlib.blake2b(
        value.encode("utf-8"),
        digest_size=16,
    ).hexdigest()


class BubblewrapWorkspaceManager(WorkspaceManagerBase):
    """Manage Bubblewrap workspaces with TTL caching.

    Explicit ``workspace_id`` values must be globally unique across users;
    the cache is keyed by workspace id while the host path also includes the
    user id.
    """

    def __init__(
        self,
        basedir: str,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        gateway_port: int | None = DEFAULT_GATEWAY_PORT,
        share_net: bool = True,
        env: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the Bubblewrap workspace manager.

        Args:
            basedir (`str`):
                Host root for per-user/per-workspace workdirs.
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for assigned workspace ids.
            gateway_port (`int | None`, optional):
                In-sandbox MCP gateway port. ``None`` lets each workspace
                allocate an available loopback port during initialization.
            share_net (`bool`, defaults to `True`):
                Must be ``True`` for the current Bubblewrap workspace TCP
                gateway design.
            env (`dict[str, str] | None`, optional):
                Extra environment variables for every workspace.
            extra_pip (`list[str] | None`, optional):
                Extra gateway venv requirements.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded into new workspaces.
            skill_paths (`list[str] | None`, optional):
                Skill dirs seeded into new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle workspace is evicted.
            sweep_interval (`float`, defaults to `300.0`):
                Seconds between sweeper ticks.
        """
        if not basedir.strip():
            raise ValueError("basedir must not be empty.")
        BubblewrapWorkspace._validate_gateway_port(gateway_port)
        if not share_net:
            raise ValueError(
                "BubblewrapWorkspaceManager currently requires "
                "share_net=True because BubblewrapWorkspace uses a TCP MCP "
                "gateway across separate bwrap executions.",
            )

        self._basedir = os.path.abspath(basedir)
        self._gateway_port = gateway_port
        self._share_net = share_net
        self._env = dict(env or {})
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        super().__init__(isolation=isolation)

        self._cache: dict[str, tuple[BubblewrapWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task[None] | None = None

    def _workdir_for(self, user_id: str, workspace_id: str) -> str:
        """Resolve the host workdir for ``(user_id, workspace_id)``."""
        path = os.path.join(
            self._basedir,
            _safe_component(user_id),
            _safe_component(workspace_id),
        )
        basedir = os.path.realpath(self._basedir)
        real_path = os.path.realpath(path)
        if os.path.commonpath([basedir, real_path]) != basedir:
            raise PermissionError("Bubblewrap workdir escapes basedir.")
        return path

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> BubblewrapWorkspace:
        """Construct and initialize a Bubblewrap workspace."""
        del agent_id
        workdir = self._workdir_for(user_id, workspace_id)
        os.makedirs(workdir, mode=0o700, exist_ok=True)
        os.chmod(workdir, 0o700)
        ws = BubblewrapWorkspace(
            workspace_id=workspace_id,
            host_workdir=workdir,
            gateway_port=self._gateway_port,
            share_net=self._share_net,
            env=self._env,
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> BubblewrapWorkspace:
        """Return an initialized workspace, creating it on cache miss."""
        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )

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

    @deprecated(
        "BubblewrapWorkspaceManager.create_workspace is deprecated; "
        "use get_workspace(workspace_id=None) instead.",
        category=None,
    )
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> BubblewrapWorkspace:
        """Build a brand-new workspace and track it."""
        del session_id
        workspace_id = _generate_id()
        ws = await self._build_and_start(
            workspace_id=workspace_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace."""
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

    async def __aenter__(self) -> Self:
        """Start the TTL sweeper task."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the sweeper, then close cached workspaces."""
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        await self.close_all()

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
                logger.exception("Bubblewrap workspace sweeper tick failed")

    async def _sweep_once(self) -> None:
        """One sweeper tick: evict expired entries."""
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
    async def _safe_close(ws: BubblewrapWorkspace) -> None:
        """Close a workspace, logging failures instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close BubblewrapWorkspace %s",
                ws.workspace_id,
            )
