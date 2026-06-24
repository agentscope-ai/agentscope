# -*- coding: utf-8 -*-
"""DaytonaWorkspaceManager lifecycle manager."""

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
    """Manages :class:`DaytonaWorkspace` instances with TTL caching."""

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
        """Initialize the Daytona workspace manager."""
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

        self._cache: dict[str, tuple[DaytonaWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    def _metadata_for(self, user_id: str, agent_id: str) -> dict[str, str]:
        """Build user/agent labels for Daytona dashboard filtering."""
        return {
            "agentscope.user.id": user_id,
            "agentscope.agent.id": agent_id,
            **self._sandbox_metadata,
        }

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        user_id: str,
        agent_id: str,
    ) -> DaytonaWorkspace:
        """Construct and initialize one Daytona workspace."""
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

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> DaytonaWorkspace:
        """Return an initialized workspace, reattaching on cache miss."""
        del session_id

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
    ) -> DaytonaWorkspace:
        """Build a brand-new workspace and track it."""
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
        """Start the TTL sweeper."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the sweeper and close all workspaces."""
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        await self.close_all()

    async def _sweep_loop(self) -> None:
        """Periodically close idle workspaces."""
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
    async def _safe_close(ws: DaytonaWorkspace) -> None:
        """Close a workspace, logging failures."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close DaytonaWorkspace %s",
                ws.workspace_id,
            )
