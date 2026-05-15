# -*- coding: utf-8 -*-
"""WorkspaceManagerBase — abstract manager for workspace lifecycle.

Manages creation, tracking, and destruction of workspace instances.
Used by agent services (FastAPI, etc.) to share configuration and
pool workspaces across requests.

Optional pool support for RL rollout scenarios: keep warm workspace
instances and acquire/release them instead of creating fresh ones
each time.

Usage::

    manager = DockerWorkspaceManager(image="my-image")
    await manager.initialize()

    # Normal per-user/agent usage:
    workspace = await manager.get_workspace(user_id, agent_id)
    agent = Agent(..., workspace=workspace)

    # Pool usage (RL rollout):
    manager.enable_pool(capacity=8)
    await manager.warm_up_pool()
    ws = await manager.acquire_from_pool()
    # ... do work ...
    await manager.release_to_pool(ws)

    await manager.close()
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from .workspace_base import WorkspaceBase
from .types import SerializedWorkspaceState
from .._logging import logger


class WorkspaceManagerBase(ABC):
    """Abstract base for workspace managers.

    Includes optional warm-pool support (call :meth:`enable_pool`).
    """

    def __init__(self) -> None:
        # Pool state — inactive until ``enable_pool()`` is called.
        self._pool_capacity: int = 0
        self._pool_free: asyncio.Queue[str] = asyncio.Queue()
        self._pool_in_use: set[str] = set()
        self._pool_lock = asyncio.Lock()
        self._pool_enabled = False
        self._pool_workspaces: dict[str, WorkspaceBase] = {}

    # ── abstract: subclass-provided ───────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the manager (connect clients, warm pools, etc.)."""

    @abstractmethod
    async def close(self) -> None:
        """Close all managed workspaces and release resources.

        Implementations MUST call ``await self._close_pool()`` to
        clean up any pool workspaces.
        """

    @abstractmethod
    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        """Create or retrieve a workspace for a user/agent pair.

        Args:
            user_id: User identifier for isolation.
            agent_id: Agent identifier for workspace scoping.

        Returns:
            An initialized workspace ready for the agent to use.
        """

    @abstractmethod
    async def restore(
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        """Restore a previously-exported workspace.

        Args:
            state: Serialized state from ``workspace.export_state()``.

        Returns:
            A reconnected workspace instance.
        """

    # ── context manager ───────────────────────────────────────────

    async def __aenter__(self) -> "WorkspaceManagerBase":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── pool: configuration ───────────────────────────────────────

    def enable_pool(self, *, capacity: int = 4) -> "WorkspaceManagerBase":
        """Enable warm-pool mode with ``capacity`` pre-created workspaces.

        Returns ``self`` for chaining.

        Raises:
            ValueError: If *capacity* is not a positive integer.
        """
        if capacity <= 0:
            raise ValueError(
                f"Pool capacity must be positive, got {capacity}",
            )
        self._pool_capacity = capacity
        self._pool_enabled = True
        return self

    @property
    def pool_enabled(self) -> bool:
        """Whether :meth:`enable_pool` has been called."""
        return self._pool_enabled

    # ── pool: lifecycle ───────────────────────────────────────────

    async def warm_up_pool(self) -> None:
        """Pre-create ``capacity`` workspaces and fill the free queue.

        Safe to call only once; raises if the pool already has workspaces.
        """
        if not self._pool_enabled:
            raise RuntimeError("Call enable_pool() first")
        async with self._pool_lock:
            if self._pool_workspaces:
                raise RuntimeError(
                    "Pool already warmed. Call resize_pool() to adjust size, "
                    "or _close_pool() first to reset.",
                )
            for _ in range(self._pool_capacity):
                ws = await self._create_for_pool()
                self._pool_workspaces[ws.workspace_id] = ws
                await self._pool_free.put(ws.workspace_id)
            logger.info(
                "Pool: warmed %d workspaces",
                self._pool_capacity,
            )

    async def acquire_from_pool(
        self,
        *,
        timeout: float | None = None,
    ) -> WorkspaceBase:
        """Acquire a free workspace from the pool.

        Raises ``RuntimeError`` if no workspace is available within
        the timeout.
        """
        if not self._pool_enabled:
            raise RuntimeError("Pool not enabled")
        try:
            ws_id = await asyncio.wait_for(
                self._pool_free.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "acquire_from_pool timed out — no free workspace",
            ) from exc
        self._pool_in_use.add(ws_id)
        return self._pool_workspaces[ws_id]

    async def release_to_pool(self, workspace: WorkspaceBase) -> None:
        """Return a workspace to the pool; replace it if dead.

        Before returning a live workspace to the free queue, calls
        :meth:`_reset_for_pool` to clear user-specific state (env vars,
        temp files, MCP tool credentials, etc.).  If the reset fails the
        workspace is destroyed and replaced.
        """
        ws_id = workspace.workspace_id
        async with self._pool_lock:
            self._pool_in_use.discard(ws_id)
            alive = await workspace.is_alive()
            if alive:
                try:
                    await self._reset_for_pool(workspace)
                except Exception as e:
                    logger.warning(
                        "Pool: reset failed for %s, replacing: %s",
                        ws_id,
                        e,
                    )
                    alive = False
            if alive:
                await self._pool_free.put(ws_id)
            else:
                self._pool_workspaces.pop(ws_id, None)
                try:
                    await workspace.close()
                except Exception:
                    pass
                new_ws = await self._create_for_pool()
                self._pool_workspaces[new_ws.workspace_id] = new_ws
                await self._pool_free.put(new_ws.workspace_id)
                logger.info(
                    "Pool: replaced dead workspace %s → %s",
                    ws_id,
                    new_ws.workspace_id,
                )

    async def resize_pool(self, new_size: int) -> None:
        """Grow or shrink the pool to ``new_size``.

        Raises:
            ValueError: If *new_size* is not positive.
        """
        if new_size <= 0:
            raise ValueError(
                f"Pool size must be positive, got {new_size}",
            )
        async with self._pool_lock:
            delta = new_size - self._pool_capacity
            self._pool_capacity = new_size
            if delta > 0:
                for _ in range(delta):
                    ws = await self._create_for_pool()
                    self._pool_workspaces[ws.workspace_id] = ws
                    await self._pool_free.put(ws.workspace_id)
            elif delta < 0:
                for _ in range(-delta):
                    if not self._pool_free.empty():
                        ws_id = await self._pool_free.get()
                        ws = self._pool_workspaces.pop(ws_id, None)
                        if ws:
                            try:
                                await ws.close()
                            except Exception as e:
                                logger.warning(
                                    "Pool: error closing workspace %s: %s",
                                    ws_id,
                                    e,
                                )

    def get_pool_state(self) -> dict[str, int]:
        """Counts for capacity, free queue, and in-use workspaces."""
        return {
            "capacity": self._pool_capacity,
            "free": self._pool_free.qsize(),
            "in_use": len(self._pool_in_use),
        }

    # ── pool: internal ────────────────────────────────────────────

    async def _create_for_pool(self) -> WorkspaceBase:
        """Create a workspace for pool use.

        Override in subclasses to customize pool workspace creation.
        By default raises ``NotImplementedError``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement _create_for_pool(). "
            "Override this method to support pool mode.",
        )

    async def _reset_for_pool(self, workspace: WorkspaceBase) -> None:
        """Reset a workspace to a clean state before returning it to the pool.

        Override in subclasses to clear user-specific state such as
        environment variables, temporary files, MCP tool credentials,
        workspace files, etc.  Called by :meth:`release_to_pool` before
        the workspace is made available to the next consumer.

        The default implementation is a no-op.  Subclasses that manage
        sensitive per-user state **should** override this.
        """

    async def _close_pool(self) -> None:
        """Close all pool workspaces. Call from ``close()``."""
        if not self._pool_workspaces:
            return
        tasks = [ws.close() for ws in self._pool_workspaces.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Pool: error closing workspace: %s", r)
        self._pool_workspaces.clear()
        self._pool_in_use.clear()
        while not self._pool_free.empty():
            try:
                self._pool_free.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._pool_enabled = False
