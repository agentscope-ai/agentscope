# -*- coding: utf-8 -*-
"""DockerWorkspaceManager — lifecycle manager for :class:`DockerWorkspace`.

Mirrors :class:`LocalWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so that callers — notably :class:`agentscope.app._service.ChatService` —
do not branch on backend.

Differences from the local manager (allowed to surface only via the
constructor):

* Workdir layout is two levels — ``<basedir>/<user_id>/<agent_id>`` —
  and is bind-mounted to ``/workspace`` inside each container, so the
  agent always sees a flat ``/workspace`` regardless of host layout.
* ``workspace_id`` is forwarded into :class:`DockerWorkspace` so the
  container name (``as_ws_<workspace_id>``) is stable across process
  restarts. A cache miss after a restart deterministically re-attaches
  to the same container slot via ``containers.create_or_replace``.
* Idle workspaces are evicted by a dedicated background sweeper task
  started in :meth:`__aenter__` and cancelled in :meth:`__aexit__` —
  not lazily on each :meth:`get_workspace` call. This keeps idle
  resource consumption bounded even when no traffic is arriving.
* ``close_all`` shuts containers down in parallel
  (:func:`asyncio.gather`) — Docker ``kill + delete`` is slow enough
  that linear teardown on shutdown is noticeable.
"""

import asyncio
import os
import time
from typing import Self

from typing_extensions import deprecated

from ..._logging import logger
from ..._utils._common import _generate_id
from ...mcp import MCPClient
from ...workspace import DockerWorkspace
from ...workspace._docker._make_dockerfile import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_GATEWAY_PORT,
)
from ._base import WorkspaceManagerBase, IsolationPolicy
from ._prewarm import PrewarmConfig, WorkspacePrewarmMixin

DEFAULT_SWEEP_INTERVAL = 300.0


class DockerWorkspaceManager(
    WorkspacePrewarmMixin[DockerWorkspace],
    WorkspaceManagerBase,
):
    """Manages :class:`DockerWorkspace` instances with TTL-based caching.

    The manager owns a single set of image-build parameters
    (``base_image`` / ``node_version`` / ``extra_pip``) shared by every
    workspace it produces; the resulting image is content-hashed so
    rebuilds are skipped on cache hits.

    Use the manager as an ``async with`` context manager: entering it
    starts the TTL sweeper task, exiting it stops the sweeper and then
    closes every cached workspace via :meth:`close_all`.
    """

    def __init__(
        self,
        basedir: str,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        base_image: str = DEFAULT_BASE_IMAGE,
        node_version: str = "20",
        extra_pip: list[str] | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
        prewarm: PrewarmConfig | None = None,
    ) -> None:
        """Initialize the docker workspace manager.

        Args:
            basedir (`str`):
                Host root under which per-workspace workdir are
                created (``<basedir>/<workspace_id>``). Each workdir
                is bind-mounted to ``/workspace`` inside its container.
                Keying on the workspace id — rather than on
                ``(user, agent)`` — is what lets a container be built
                before anyone knows who will get it. Workspaces built
                under the older ``<basedir>/<user_id>/<agent_id>``
                layout keep mounting that directory.
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`.
                ``PER_SESSION`` → fresh UUID (one workspace per
                session); ``PER_AGENT`` / ``PER_USER`` → deterministic
                hash so sessions of the same (user, agent) or same
                user share a workspace. Explicit ``workspace_id`` on
                session creation always wins over this policy.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                Base Docker image; must provide ``python3``.
            node_version (`str`, defaults to `"20"`):
                Major Node.js version (e.g. ``"20"``) to bake into
                the image.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv at image-build time.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-container gateway listens on (always
                exposed to a randomly assigned host port).
            env (`dict[str, str] | None`, optional):
                Environment variables to set inside every workspace's
                container.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. Ignored
                on subsequent restarts of a workdir that already
                persists ``.mcp``.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted
                and its container torn down.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up
                to look for idle workspaces. Defaults to 5 minutes.
            prewarm (`PrewarmConfig | None`, optional):
                Keep this many containers built and idle, ready to be
                handed to the next session that needs one. ``None``
                disables pre-warming.
        """
        self._basedir = os.path.abspath(basedir)
        self._base_image = base_image
        self._node_version = node_version
        self._extra_pip = list(extra_pip or [])
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        WorkspacePrewarmMixin.__init__(self, prewarm=prewarm)
        WorkspaceManagerBase.__init__(self, isolation=isolation)
        self._ttl = ttl
        self._sweep_interval = sweep_interval

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[DockerWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    # ── isolation helpers ─────────────────────────────────────────

    def _workdir_for(
        self,
        workspace_id: str,
        user_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Resolve the host workdir bind-mounted into ``workspace_id``.

        Keyed on the workspace id, so the directory can be created
        alongside a pre-warmed container, long before the
        ``(user, agent)`` that will own it is known.

        A session may name its own ``workspace_id``, and this path is
        bind-mounted read-write into the container, so a value that
        escapes ``basedir`` would hand the container the host
        filesystem. Anything not landing strictly inside is rejected.

        Args:
            workspace_id (`str`):
                The workspace whose bind mount is being resolved.
            user_id (`str`, defaults to `""`):
                Owner of the workspace, for the legacy layout below.
                Empty for a workspace nobody owns yet.
            agent_id (`str`, defaults to `""`):
                Agent of the workspace, for the legacy layout below.

        Raises:
            `ValueError`:
                If ``workspace_id`` resolves outside ``basedir``.
        """
        root = os.path.realpath(self._basedir)
        workdir = os.path.realpath(os.path.join(root, workspace_id))
        if not workdir.startswith(root + os.sep):
            raise ValueError(
                f"workspace_id {workspace_id!r} escapes the workspace "
                f"base directory",
            )
        if os.path.isdir(workdir) or not (user_id and agent_id):
            return workdir

        # Workspaces built before the id-keyed layout live under
        # ``<basedir>/<user_id>/<agent_id>``. Keep mounting such a
        # directory where it stands: several workspace ids may share
        # one, so no rename can move them all. Legacy paths escaping
        # ``basedir`` are declined rather than rejected, leaving the
        # caller with an ordinary empty workspace.
        legacy = os.path.realpath(os.path.join(root, user_id, agent_id))
        if legacy.startswith(root + os.sep) and os.path.isdir(legacy):
            return legacy
        return workdir

    # ── workspace construction ────────────────────────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None = None,
        user_id: str = "",
        agent_id: str = "",
    ) -> DockerWorkspace:
        """Create a :class:`DockerWorkspace` and run its full
        ``initialize``.

        ``workspace_id`` names both the container and its host workdir,
        so the same id round-trips through the cache and re-attaches
        after a restart. ``None`` mints one. ``user_id``/``agent_id``
        are forwarded only to find a legacy workdir — see
        :meth:`_workdir_for`.
        """
        workspace_id = workspace_id or _generate_id()
        workdir = self._workdir_for(workspace_id, user_id, agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = DockerWorkspace(
            workspace_id=workspace_id,
            host_workdir=workdir,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
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
        workspace_id: str | None = None,
    ) -> DockerWorkspace:
        """Return an initialised workspace, building one on cache miss.

        On miss the manager calls ``DockerWorkspace(workspace_id=…)``
        with a deterministic workdir derived from ``(user_id,
        agent_id)``. Image build, container creation and gateway
        startup all happen inside the workspace's ``initialize``.

        Eviction of idle workspaces is *not* performed here — the
        background sweeper started by :meth:`__aenter__` handles that.

        Args:
            user_id (`str`):
                Owning user identifier.
            agent_id (`str`):
                Agent identifier (controls the workdir).
            session_id (`str`):
                Session identifier (unused for isolation; sessions
                share a workdir and partition under
                ``sessions/<session_id>/``).
            workspace_id (`str | None`, optional):
                Stable workspace identifier — used both as the cache
                key and the container name suffix. When unset the
                manager falls back to :meth:`assign_workspace_id`;
                the session flow should pre-resolve this so container
                names stay stable across restarts.

        Returns:
            `DockerWorkspace`:
                A live, initialised workspace.
        """
        del session_id  # accepted for interface parity; not used here

        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
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
        # workspaces for the same id.
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
        "DockerWorkspaceManager.create_workspace is deprecated; "
        "use get_workspace(workspace_id=None) instead.",
        category=None,
    )
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> DockerWorkspace:
        """Build a brand-new workspace and track it.

        .. deprecated::
            Use :meth:`get_workspace` with ``workspace_id=None`` — it
            falls back to :meth:`assign_workspace_id` under the
            manager's isolation policy and reuses the cache path.

        Args:
            user_id (`str`):
                Owning user identifier.
            agent_id (`str`):
                Agent identifier (controls the workdir).
            session_id (`str`):
                Session identifier (accepted for parity; not used
                here).

        Returns:
            `DockerWorkspace`:
                The newly built workspace, already initialised.
        """
        del user_id, agent_id, session_id  # kept for interface parity

        ws = await self._build_and_start()
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache.

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

        Docker ``kill + delete`` is slow per container; doing it
        sequentially on app shutdown produces a noticeable stall, so
        we fan the calls out with :func:`asyncio.gather`.
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

    # ── pre-warming hooks ─────────────────────────────────────────

    async def _create_prewarmed(self) -> DockerWorkspace:
        """Build a container for nobody in particular."""
        ws = await self._build_and_start()
        logger.info(
            "DockerWorkspaceManager: pre-warmed workspace %s",
            ws.workspace_id,
        )
        return ws

    async def _adopt_prewarmed(self, workspace: DockerWorkspace) -> None:
        """Track a handed-out container under the ordinary TTL cache."""
        async with self._lock:
            self._cache[workspace.workspace_id] = (
                workspace,
                time.monotonic(),
            )

    # ── async context manager ─────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the TTL sweeper task and fill the pre-warm buffer."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        self._start_prewarm()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the sweeper and buffer, then close every workspace."""
        await self._stop_prewarm()
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
        """Periodically evict idle workspaces.

        Runs forever until cancelled. Each tick pops every cache entry
        whose last-access is older than ``ttl`` and closes it outside
        the lock; exceptions during close are logged and swallowed so
        one bad container does not poison the sweeper.
        """
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("Docker workspace sweeper tick failed")

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
    async def _safe_close(ws: DockerWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close DockerWorkspace %s",
                ws.workspace_id,
            )
