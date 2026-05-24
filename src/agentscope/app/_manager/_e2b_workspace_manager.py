# -*- coding: utf-8 -*-
"""E2BWorkspaceManager — lifecycle manager for :class:`E2BWorkspace`.

Mirrors :class:`DockerWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so callers — notably :func:`agentscope.app._service.get_agent` —
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
* ``close_all`` fans calls out with :func:`asyncio.gather` because
  ``sandbox.pause()`` is a remote round-trip per sandbox; sequentialising
  it on app shutdown produces a noticeable stall.
"""

import asyncio
import time
from typing import Any

from ...mcp import MCPClient
from ...workspace._e2b import E2BWorkspace
from ...workspace._e2b._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
)
from ._workspace_manager import WorkspaceManagerBase


class E2BWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`E2BWorkspace` instances with TTL-based caching.

    Args:
        template: E2B template id passed to every workspace this
            manager produces. Defaults to ``"base"``.
        api_key: E2B API key. ``""`` falls back to the ``E2B_API_KEY``
            env var on the SDK side.
        domain: Optional custom E2B domain (self-hosted etc.).
        timeout_seconds: Sandbox keep-alive timeout passed to
            ``AsyncSandbox.create`` / ``AsyncSandbox.connect``.
        gateway_port: TCP port the in-sandbox gateway listens on.
        env: Environment variables baked into the sandbox at create
            time.
        sandbox_metadata: Extra metadata merged with the per-workspace
            ``agentscope.workspace.id`` / ``agentscope.user.id`` /
            ``agentscope.agent.id`` keys. Useful for downstream E2B
            dashboard filtering.
        extra_pip: Extra Python packages to install into the gateway
            venv during bootstrap.
        default_mcps: MCP clients seeded into brand-new workspaces.
            Ignored on subsequent reattachments — the sandbox's
            persisted ``.mcp`` file wins.
        skill_paths: Skill directories seeded into brand-new
            workspaces.
        ttl: Seconds before an idle cached workspace is evicted and
            its sandbox paused.
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
    ) -> None:
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

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[E2BWorkspace, float]] = {}
        self._lock = asyncio.Lock()

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

    # ── TTL helper ────────────────────────────────────────────────

    def _evict_expired(self, now: float) -> list[E2BWorkspace]:
        """Pop entries whose last-access is older than ``ttl``.

        Args:
            now: Current ``time.monotonic()`` reading.

        Returns:
            The evicted :class:`E2BWorkspace` instances. Caller is
            responsible for calling :meth:`E2BWorkspace.close` on each
            (kept off the lock so the pause RPC does not stall the
            next ``get_workspace`` caller).
        """
        expired_ids = [
            wid for wid, (_, ts) in self._cache.items() if now - ts > self._ttl
        ]
        evicted: list[E2BWorkspace] = []
        for wid in expired_ids:
            ws, _ = self._cache.pop(wid)
            evicted.append(ws)
        return evicted

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
        **_: Any,
    ) -> E2BWorkspace:
        """Return an initialised workspace, reattaching on cache miss.

        On miss the manager calls ``E2BWorkspace(workspace_id=…)`` and
        relies on its ``initialize`` to find any existing sandbox via
        ``AsyncSandbox.list(query=SandboxQuery(metadata=...))`` and
        ``connect`` to it (auto-resuming if paused) — or to ``create``
        a fresh sandbox otherwise.

        Args:
            user_id: Owning user identifier (forwarded as sandbox
                metadata only — not part of the cache key).
            agent_id: Agent identifier (forwarded as sandbox metadata
                only — not part of the cache key).
            session_id: Session identifier (unused; sandboxes are
                per-workspace, sessions partition under
                ``sessions/<session_id>/``).
            workspace_id: Stable workspace identifier — the cache key
                and the value stored in the sandbox's
                ``agentscope.workspace.id`` metadata.

        Returns:
            A live, initialised :class:`E2BWorkspace`.
        """
        del session_id  # accepted for interface parity; not used here

        async with self._lock:
            now = time.monotonic()
            evicted = self._evict_expired(now)

            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, now)

        # Close evicted workspaces *outside* the lock — pause is a
        # remote round-trip and we don't want it to block the cache
        # hit path.
        for ws in evicted:
            try:
                await ws.close()
            except Exception:
                pass

        if cached is not None:
            return cached[0]

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
        **_: Any,
    ) -> E2BWorkspace:
        """Build a brand-new workspace and track it.

        A fresh ``workspace_id`` is allocated by
        :class:`WorkspaceBase`; the caller should persist
        ``workspace.workspace_id`` for later :meth:`get_workspace`
        calls.

        Args:
            user_id: Owning user identifier (forwarded as sandbox
                metadata).
            agent_id: Agent identifier (forwarded as sandbox metadata).
            session_id: Session identifier (accepted for parity; not
                used here).

        Returns:
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
            workspace_id: The workspace to close.
        """
        async with self._lock:
            entry = self._cache.pop(workspace_id, None)
        if entry is None:
            return
        ws, _ = entry
        try:
            await ws.close()
        except Exception:
            pass

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
            *(ws.close() for ws, _ in entries),
            return_exceptions=True,
        )
