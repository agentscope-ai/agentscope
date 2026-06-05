# -*- coding: utf-8 -*-
"""RLWorkspaceManager — pooling-based lifecycle manager for
:class:`E2BWorkspace`.

Compared with :class:`E2BWorkspaceManager`, this manager replaces the
ID-keyed TTL cache with a pre-warming **pool** (see
:class:`WorkspacePool`).  The design follows the Pooling Design spec:

* **Cache disabled**: enabling pooling automatically disables the
  per-workspace-id cache so resources are not double-tracked.
* **Instance lifecycle**: CREATING → POOLED → ACTIVE → RESETTING →
  (healthy → POOLED | unhealthy → DESTROYED).
* **Scheduling**: ``min_idle`` / ``max_idle`` / ``total`` /
  ``create_batch_size`` govern capacity and pre-warming.
* **Maintenance**: ``max_reuse`` caps how many times a single sandbox
  is recycled; a background health-check loop probes idle instances.
* **Reset**: gateway restart + workspace file cleanup + env-var wipe.
* **Cost control**: idle (POOLED) sandboxes are paused via
  ``E2BWorkspace.pause()`` so E2B billing stops.  On checkout the
  sandbox is resumed via ``E2BWorkspace.resume()``.

The public API (``get_workspace`` / ``create_workspace`` / ``close`` /
``close_all``) matches :class:`WorkspaceManagerBase` so callers —
notably :func:`agentscope.app._service.get_agent` — do not branch on
backend.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Self

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace import E2BWorkspace
from ...workspace._e2b._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
)
from ._workspace_manager import WorkspaceManagerBase
from ._workspace_pool import PooledEntry, WorkspacePool


class RLWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`E2BWorkspace` instances with a **pooling** strategy.

    Unlike :class:`E2BWorkspaceManager` which keeps a per-workspace-id
    TTL cache, this manager maintains a pool of pre-warmed E2B sandboxes.
    On ``get_workspace`` or ``create_workspace``, an idle sandbox is
    checked out from the pool, bound to the caller's session, and
    returned after use via ``close``.  The sandbox is then reset,
    health-checked, paused, and recycled back into the pool (or
    destroyed if unhealthy or past ``max_reuse``).

    Idle sandboxes in the pool are kept in E2B's **paused** state so no
    runtime billing accrues while they wait for the next checkout.

    Use the manager as an ``async with`` context manager: entering it
    starts the pool (pre-warming + health-check loop), exiting it
    stops everything and destroys all sandboxes.
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
        # ── pooling parameters ─────────────────────────────────
        min_idle: int = 1,
        max_idle: int = 3,
        total: int = 10,
        create_batch_size: int = 2,
        max_reuse: int = 50,
    ) -> None:
        """Initialize the RL workspace manager.

        Args:
            template (`str`, defaults to `DEFAULT_TEMPLATE`):
                E2B template id passed to every workspace this
                manager produces.
            api_key (`str`, defaults to `""`):
                E2B API key. ``""`` falls back to the ``E2B_API_KEY``
                env var on the SDK side.
            domain (`str`, defaults to `""`):
                Optional custom E2B domain (self-hosted etc.).
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive timeout.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into the sandbox at
                create time.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with per-workspace keys.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            min_idle (`int`, defaults to `1`):
                Minimum number of idle instances to maintain. When
                the idle count drops below this on ``acquire``, a
                batch replenishment is triggered.
            max_idle (`int`, defaults to `3`):
                Target idle count after batch replenishment.
            total (`int`, defaults to `10`):
                Hard cap on total managed instances (idle + active).
            create_batch_size (`int`, defaults to `2`):
                Maximum concurrent ``factory()`` calls per
                replenishment batch.
            max_reuse (`int`, defaults to `50`):
                Maximum times a single sandbox can be recycled.
                ``0`` means unlimited.
        """
        # ── E2B workspace configuration ────────────────────────
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

        # ── active session tracking (replaces cache) ───────────
        # workspace_id → PooledEntry  (only ACTIVE entries)
        self._active: dict[str, PooledEntry[E2BWorkspace]] = {}
        self._lock = asyncio.Lock()

        # ── the pool ───────────────────────────────────────────
        self._pool = WorkspacePool[E2BWorkspace](
            factory=self._factory,
            reset_fn=self._reset_workspace,
            health_check_fn=self._health_check,
            close_fn=self._close_workspace,
            pause_fn=self._pause_workspace,
            resume_fn=self._resume_workspace,
            min_idle=min_idle,
            max_idle=max_idle,
            total=total,
            create_batch_size=create_batch_size,
            max_reuse=max_reuse,
        )

    # ── pool callbacks ─────────────────────────────────────────────

    async def _factory(self) -> E2BWorkspace:
        """Create and initialize a new E2BWorkspace for the pool.

        The workspace gets a unique ``workspace_id`` and is fully
        bootstrapped (sandbox created, gateway started, health-checked).
        It is returned in a *running* state; the pool's ``pause_fn``
        will pause it before placing it in the idle queue.
        """
        ws = E2BWorkspace(
            workspace_id=None,  # auto-generate UUID
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
            "RLWorkspaceManager: created pooled workspace %s",
            ws.workspace_id,
        )
        return ws

    async def _reset_workspace(self, ws: E2BWorkspace) -> None:
        """Reset an E2BWorkspace to a clean state for reuse.

        Delegates to :meth:`E2BWorkspace.light_reset_for_pool` which
        performs a full gateway restart and data wipe while the
        sandbox is still running.
        """
        await ws.light_reset_for_pool(
            default_mcps=self._default_mcps or None,
            skill_paths=self._skill_paths or None,
        )

    @staticmethod
    async def _health_check(ws: E2BWorkspace) -> bool:
        """Check if a workspace is healthy by probing the gateway.

        Called while the workspace is in a *running* state (after
        resume, or before pause during release).
        """
        return await ws.gateway_health()

    @staticmethod
    async def _pause_workspace(ws: E2BWorkspace) -> None:
        """Pause the sandbox so E2B stops billing."""
        await ws.pause()

    @staticmethod
    async def _resume_workspace(ws: E2BWorkspace) -> None:
        """Resume a paused sandbox and bring the gateway back up."""
        await ws.resume()

    @staticmethod
    async def _close_workspace(ws: E2BWorkspace) -> None:
        """Permanently close an E2BWorkspace.

        Works regardless of whether the sandbox is currently running
        or paused.
        """
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "RLWorkspaceManager: failed to close workspace %s",
                ws.workspace_id,
            )

    # ── public API ─────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> E2BWorkspace:
        """Return a workspace, checking out from the pool if not active.

        If ``workspace_id`` is already active (i.e. previously checked
        out and not yet closed), the existing workspace is returned
        directly.  Otherwise a workspace is checked out from the pool
        (resumed from paused state) and bound to ``workspace_id``.

        Args:
            user_id (`str`): Owning user identifier.
            agent_id (`str`): Agent identifier.
            session_id (`str`): Session identifier (unused).
            workspace_id (`str`): Workspace identifier to bind to.

        Returns:
            `E2BWorkspace`: A live, initialised workspace.
        """
        del session_id  # accepted for interface parity; not used here

        # Fast path: already checked out.
        async with self._lock:
            entry = self._active.get(workspace_id)
            if entry is not None:
                return entry.workspace

        # Pool checkout (the pool handles resume + health check).
        entry = await self._pool.acquire()
        ws = entry.workspace

        # Update sandbox metadata for this user/agent binding.
        ws.sandbox_metadata.update(
            {
                "agentscope.user.id": user_id,
                "agentscope.agent.id": agent_id,
                "agentscope.workspace.id": workspace_id,
            },
        )

        async with self._lock:
            # Double-check: another concurrent call may have bound it.
            existing = self._active.get(workspace_id)
            if existing is not None:
                # Release this checkout back to pool asynchronously.
                asyncio.create_task(self._pool.release(entry))
                return existing.workspace
            self._active[workspace_id] = entry

        logger.info(
            "RLWorkspaceManager: checked out workspace %s "
            "(sandbox %s) for workspace_id=%s",
            ws.workspace_id,
            ws.sandbox_id,
            workspace_id,
        )
        return ws

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> E2BWorkspace:
        """Check out a workspace from the pool for a new session.

        A fresh ``workspace_id`` is generated so the caller can persist
        it for later :meth:`get_workspace` calls.

        Args:
            user_id (`str`): Owning user identifier.
            agent_id (`str`): Agent identifier.
            session_id (`str`): Session identifier (unused).

        Returns:
            `E2BWorkspace`: A pooled workspace, now bound to the caller.
        """
        workspace_id = uuid.uuid4().hex
        return await self.get_workspace(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
        )

    async def close(self, workspace_id: str) -> None:
        """Release a workspace back to the pool.

        The workspace is reset, health-checked, paused, and returned
        to the idle pool — or destroyed if unhealthy or past
        ``max_reuse``.

        No-op when the workspace_id is not tracked.

        Args:
            workspace_id (`str`): The workspace to release.
        """
        async with self._lock:
            entry = self._active.pop(workspace_id, None)
        if entry is None:
            return

        logger.info(
            "RLWorkspaceManager: releasing workspace_id=%s back to pool",
            workspace_id,
        )
        await self._pool.release(entry)

    async def close_all(self) -> None:
        """Release every active workspace back to the pool.

        Each workspace is reset and returned to the pool (or destroyed
        if unhealthy).
        """
        async with self._lock:
            entries = list(self._active.items())
            self._active.clear()

        if not entries:
            return

        await asyncio.gather(
            *(self._pool.release(entry) for _, entry in entries),
            return_exceptions=True,
        )

    # ── async context manager ──────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the pool (pre-warming + health-check loop)."""
        await self._pool.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Release all active workspaces, then stop and drain the pool."""
        await self.close_all()
        await self._pool.stop()
