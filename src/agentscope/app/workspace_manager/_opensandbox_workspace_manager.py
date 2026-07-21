# -*- coding: utf-8 -*-
"""OpenSandboxWorkspaceManager -- lifecycle manager for OpenSandbox.

Mirrors :class:`DockerWorkspaceManager` and :class:`E2BWorkspaceManager`
in its public surface (``get_workspace`` / ``close`` / ``close_all``) so
callers do not branch on backend.

Differences from the Docker manager:

* No ``basedir`` / host workdir layout: OpenSandbox sandboxes carry
  their own filesystem state across pause/resume, so there is nothing
  to bind-mount on the host.
* No image build step: the manager passes an image name plus runtime
  bootstrap options to :class:`OpenSandboxWorkspace`.
* Reattachment uses OpenSandbox sandbox metadata. The workspace writes
  ``agentscope.workspace.id`` at create time and looks it up via
  ``SandboxManager.list_sandbox_infos`` inside
  :meth:`OpenSandboxWorkspace.initialize`. The manager itself stays
  metadata-blind and just forwards ``workspace_id``.
* ``user_id`` / ``agent_id`` are surfaced as extra sandbox metadata so
  operators can filter sandboxes in OpenSandbox. They do not
  participate in cache key resolution; the cache is keyed strictly by
  ``workspace_id``.
* Idle workspaces are evicted by a background sweeper task started in
  :meth:`__aenter__` and cancelled in :meth:`__aexit__`.
* ``close_all`` fans calls out with :func:`asyncio.gather` because
  ``sandbox.pause()`` is a remote round-trip per sandbox.
"""

import asyncio
import time
from typing import Any, Literal, Self

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace._opensandbox._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_TIMEOUT,
)
from ...workspace import OpenSandboxWorkspace

from ._base import IsolationPolicy, WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


class OpenSandboxWorkspaceManager(WorkspaceManagerBase):
    """Manages OpenSandbox workspaces with TTL-based caching.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        image: str = DEFAULT_IMAGE,
        api_key: str = "",
        domain: str = "",
        protocol: Literal["http", "https"] = "http",
        request_timeout_seconds: float | None = DEFAULT_REQUEST_TIMEOUT,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        resource: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        network_policy: Any | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the OpenSandbox workspace manager.

        Args:
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`, used
                when :meth:`get_workspace` is called without an explicit
                ``workspace_id``.
            image (`str`, defaults to `DEFAULT_IMAGE`):
                OpenSandbox image passed to every workspace this manager
                produces.
            api_key (`str`, defaults to `""`):
                OpenSandbox API key. ``""`` lets the SDK fall back to
                its environment-based configuration.
            domain (`str`, defaults to `""`):
                Optional OpenSandbox domain, for local or self-hosted
                deployments.
            protocol (`str`, defaults to `"http"`):
                Scheme used when OpenSandbox returns endpoint values as
                ``host:port`` without a protocol.
            request_timeout_seconds (`float | None`, optional):
                HTTP request timeout forwarded through
                ``ConnectionConfig``. Defaults to 600 seconds so the
                SDK HTTP streaming layer is not shorter than bootstrap
                command timeout.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive / resume timeout.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables applied when creating a sandbox.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the per-workspace
                ``agentscope.workspace.id`` / ``agentscope.user.id`` /
                ``agentscope.agent.id`` keys.
            resource (`dict[str, str] | None`, optional):
                Resource limits forwarded to OpenSandbox create.
            entrypoint (`list[str] | None`, optional):
                Optional sandbox entrypoint forwarded to OpenSandbox
                create.
            network_policy (`Any | None`, optional):
                Creation-time OpenSandbox network policy. Runtime egress
                patching is intentionally left for a follow-up.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. On
                reattach, the sandbox's persisted ``.mcp`` file wins.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted and
                its sandbox paused.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often the background sweeper wakes up to look for
                idle workspaces.
        """
        self._image = image
        self._api_key = api_key
        self._domain = domain
        self._protocol = protocol
        self._request_timeout_seconds = request_timeout_seconds
        self._timeout_seconds = timeout_seconds
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._sandbox_metadata = dict(sandbox_metadata or {})
        self._resource = dict(resource or {})
        self._entrypoint = list(entrypoint or [])
        self._network_policy = network_policy
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        super().__init__(isolation=isolation)

        # workspace_id -> (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[OpenSandboxWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> OpenSandboxWorkspace:
        """Construct an OpenSandbox workspace and run full initialize.

        ``workspace_id`` is always concrete here — :meth:`get_workspace`
        resolves ``None`` via :meth:`assign_workspace_id` before calling
        — and is forwarded so metadata-based reattachment works on the
        next cache miss.
        """
        ws = OpenSandboxWorkspace(
            workspace_id=workspace_id,
            image=self._image,
            api_key=self._api_key,
            domain=self._domain,
            protocol=self._protocol,
            request_timeout_seconds=self._request_timeout_seconds,
            timeout_seconds=self._timeout_seconds,
            gateway_port=self._gateway_port,
            env=self._env,
            sandbox_metadata={
                "agentscope.user.id": user_id,
                "agentscope.agent.id": agent_id,
                **self._sandbox_metadata,
            },
            resource=self._resource,
            entrypoint=self._entrypoint,
            network_policy=self._network_policy,
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
    ) -> OpenSandboxWorkspace:
        """Return an initialized workspace, reattaching on cache miss.

        On miss, the manager constructs ``OpenSandboxWorkspace`` with
        the requested ``workspace_id`` and relies on its ``initialize``
        method to find an existing sandbox by metadata, connect or
        resume it depending on state, or create a fresh sandbox
        otherwise.

        Idle eviction is not performed here; the background sweeper
        started by :meth:`__aenter__` handles that.

        Args:
            user_id (`str`):
                Owning user identifier (forwarded as sandbox metadata
                only — not part of the cache key).
            agent_id (`str`):
                Agent identifier (forwarded as sandbox metadata only —
                not part of the cache key).
            session_id (`str`):
                Session identifier (unused; sandboxes are per-workspace,
                sessions partition under ``sessions/<session_id>/``).
            workspace_id (`str | None`, optional):
                Stable workspace identifier — the cache key and the
                value stored in the sandbox's ``agentscope.workspace.id``
                metadata. When ``None`` the manager falls back to
                :meth:`assign_workspace_id` under its isolation policy.

        Returns:
            `OpenSandboxWorkspace`:
                A live, initialized workspace.
        """
        del session_id  # accepted for interface parity; not used here

        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id="",
            )

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

    async def close(self, workspace_id: str) -> None:
        """Close (= pause the sandbox) and evict a single workspace."""
        async with self._lock:
            entry = self._cache.pop(workspace_id, None)
        if entry is None:
            return
        ws, _ = entry
        await self._safe_close(ws)

    async def close_all(self) -> None:
        """Close every cached workspace in parallel.

        ``sandbox.pause()`` is a remote round-trip per sandbox; doing
        it sequentially on shutdown would produce unnecessary latency.
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

    async def __aenter__(self) -> Self:
        """Start the TTL sweeper task."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the TTL sweeper task, then close all cached workspaces."""
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        await self.close_all()

    async def _sweep_loop(self) -> None:
        """Periodically close idle workspaces.

        Runs until cancelled. Each tick pops expired entries and closes
        them outside the lock; failures are logged and swallowed so one
        bad sandbox does not poison the sweeper.
        """
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("OpenSandbox workspace sweeper tick failed")

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
    async def _safe_close(ws: OpenSandboxWorkspace) -> None:
        """Close a workspace, logging failures instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close OpenSandboxWorkspace %s",
                ws.workspace_id,
            )
