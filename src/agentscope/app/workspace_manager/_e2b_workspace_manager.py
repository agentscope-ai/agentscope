# -*- coding: utf-8 -*-
"""E2BWorkspaceManager — lifecycle manager for :class:`E2BWorkspace`.

Mirrors :class:`DockerWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so callers — notably :class:`agentscope.app._service.ChatService` —
do not branch on backend.

Differences from the Docker manager:

* No ``basedir`` / ``_workdir_for`` — E2B sandboxes carry their own
  filesystem state across pause/resume, so there is nothing to
  bind-mount and nothing to lay out on the host.
* No image build parameters (``base_image`` / ``node_version``); E2B
  attaches to a pre-built template plus a runtime bootstrap.
* Reattachment uses E2B sandbox metadata. The ``workspace_id`` is
  written into the sandbox's metadata at create time and looked up via
  ``AsyncSandbox.list(query=...)`` inside
  :meth:`E2BWorkspace.initialize`. The manager itself is metadata-blind
  — it just forwards ``workspace_id`` and lets the workspace handle the
  reattach.
* ``user_id`` / ``agent_id`` are surfaced as extra sandbox metadata
  (``agentscope.user.id`` / ``agentscope.agent.id``) so users can
  filter their own sandboxes in the E2B dashboard. They do **not**
  participate in cache key resolution; the cache is keyed strictly on
  ``workspace_id`` (same as Docker).
* Idle workspaces are evicted by a dedicated background sweeper task
  started in :meth:`__aenter__` and cancelled in :meth:`__aexit__` —
  not lazily on each :meth:`get_workspace` call.
* ``close_all`` fans calls out with :func:`asyncio.gather` because
  ``sandbox.pause()`` is a remote round-trip per sandbox; sequentialising
  it on app shutdown produces a noticeable stall.
"""

import asyncio
import time
from typing import Self

from agentscope._logging import logger
from agentscope.mcp import MCPClient
from agentscope.workspace import E2BWorkspace
from agentscope.workspace._e2b._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
)
from ._base import WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


class E2BWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`E2BWorkspace` instances with TTL-based caching.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
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
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the E2B workspace manager.

        Args:
            template (`str`, defaults to `DEFAULT_TEMPLATE`):
                E2B template id passed to every workspace this
                manager produces. Defaults to ``"base"``.
            api_key (`str`, defaults to `""`):
                E2B API key. ``""`` falls back to the ``E2B_API_KEY``
                env var on the SDK side.
            domain (`str`, defaults to `""`):
                Optional custom E2B domain (self-hosted etc.).
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive timeout passed to
                ``AsyncSandbox.create`` / ``AsyncSandbox.connect``.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into the sandbox at
                create time.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the per-workspace
                ``agentscope.workspace.id`` / ``agentscope.user.id`` /
                ``agentscope.agent.id`` keys. Useful for downstream
                E2B dashboard filtering.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. Ignored
                on subsequent reattachments — the sandbox's persisted
                ``.mcp`` file wins.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted
                and its sandbox paused.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up
                to look for idle workspaces. Defaults to 5 minutes.
        """
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
        self._ttl = ttl
        self._sweep_interval = sweep_interval

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[E2BWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    # ── metadata helper ───────────────────────────────────────────

    def _metadata_for(
        self,
        user_id: str,
        agent_id: str,
    ) -> dict[str, str]:
        """Build the extra sandbox metadata for ``(user_id, agent_id)``.

        ``E2BWorkspace`` always sets ``agentscope.workspace.id`` itself;
        we add the user/agent keys here so they show up alongside it
        in the E2B dashboard's metadata filter UI.
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
    ) -> E2BWorkspace:
        """Construct an :class:`E2BWorkspace` and run its full ``initialize``.

        ``workspace_id=None`` lets :class:`WorkspaceBase` allocate a
        fresh UUID — used by :meth:`create_workspace`. Otherwise the
        provided id is forwarded so reattachment by metadata works on
        the second call.
        """
        ws = E2BWorkspace(
            workspace_id=workspace_id,
            template=self._template,
            api_key=self._api_key,
            domain=self._domain,
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
    ) -> E2BWorkspace:
        """Return an initialised workspace, reattaching on cache miss.

        On miss the manager calls ``E2BWorkspace(workspace_id=…)`` and
        relies on its ``initialize`` to find any existing sandbox via
        ``AsyncSandbox.list(query=SandboxQuery(metadata=...))`` and
        ``connect`` to it (auto-resuming if paused) — or to ``create``
        a fresh sandbox otherwise.

        Eviction of idle workspaces is *not* performed here — the
        background sweeper started by :meth:`__aenter__` handles that.

        Args:
            user_id (`str`):
                Owning user identifier (forwarded as sandbox metadata
                only — not part of the cache key).
            agent_id (`str`):
                Agent identifier (forwarded as sandbox metadata only
                — not part of the cache key).
            session_id (`str`):
                Session identifier (unused; sandboxes are
                per-workspace, sessions partition under
                ``sessions/<session_id>/``).
            workspace_id (`str`):
                Stable workspace identifier — the cache key and the
                value stored in the sandbox's
                ``agentscope.workspace.id`` metadata.

        Returns:
            `E2BWorkspace`:
                A live, initialised workspace.
        """
        del session_id  # accepted for interface parity; not used here

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

        # Cache miss: build under the lock to prevent two concurrent
        # get_workspace(workspace_id=X) calls from creating two
        # workspaces (and thus two sandboxes) for the same id.
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
    ) -> E2BWorkspace:
        """Build a brand-new workspace and track it.

        A fresh ``workspace_id`` is allocated by
        :class:`WorkspaceBase`; the caller should persist
        ``workspace.workspace_id`` for later :meth:`get_workspace`
        calls.

        Args:
            user_id (`str`):
                Owning user identifier (forwarded as sandbox metadata).
            agent_id (`str`):
                Agent identifier (forwarded as sandbox metadata).
            session_id (`str`):
                Session identifier (accepted for parity; not used
                here).

        Returns:
            `E2BWorkspace`:
                The newly built workspace, already initialised.
        """
        del session_id  # accepted for interface parity; not used here

        ws = await self._build_and_start(
            workspace_id=None,
            user_id=user_id,
            agent_id=agent_id,
        )
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close (= pause the sandbox) and evict a single workspace.

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

        ``sandbox.pause()`` is a remote round-trip per sandbox; doing
        it sequentially on app shutdown produces a noticeable stall,
        so we fan the calls out with :func:`asyncio.gather`.
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
        """Periodically pause idle workspaces.

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
                logger.exception("E2B workspace sweeper tick failed")

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
    async def _safe_close(ws: E2BWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close E2BWorkspace %s",
                ws.workspace_id,
            )
