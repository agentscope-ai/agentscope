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
import io
import os
import tarfile
import time
import uuid
from typing import Self

from agentscope._logging import logger
from agentscope.mcp import MCPClient
from agentscope.workspace._docker import DockerWorkspace
from agentscope.workspace._docker._make_dockerfile import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_GATEWAY_PORT,
)

from ._base import WorkspaceManagerBase
from ._workspace_pool import PooledEntry, WorkspacePool

DEFAULT_SWEEP_INTERVAL = 300.0


def _safe_extract_tar(
    tar_obj: tarfile.TarFile,
    dest_dir: str,
) -> int:
    """Extract *tar_obj* into *dest_dir* with safety filtering.

    The archive produced by ``docker get_archive`` wraps the target
    directory itself as the first entry (e.g. ``workspace/``).  This
    function strips that leading prefix so that archive contents land
    directly inside *dest_dir*.

    Safety guarantees:

    * Symlinks and hardlinks are silently skipped (prevents symlink
      escape).
    * Members whose resolved path falls outside *dest_dir* after
      ``os.path.realpath`` are skipped (prevents path traversal).
    * Absolute paths and ``..`` components are rejected.

    Args:
        tar_obj: An open :class:`tarfile.TarFile` to read from.
            The caller is responsible for closing it afterwards.
        dest_dir: Absolute host directory to extract into.  Must
            already exist.

    Returns:
        Number of members actually extracted.
    """
    members = tar_obj.getmembers()

    # Detect the archive-root directory prefix to strip.
    prefix = ""
    for m in members:
        if m.isdir():
            prefix = m.name.rstrip("/") + "/"
            break

    real_dest = os.path.realpath(dest_dir) + os.sep
    extracted = 0

    for member in members:
        # Skip the root directory entry itself.
        if member.name.rstrip("/") == prefix.rstrip("/"):
            continue

        # Strip the prefix so contents land directly in dest_dir.
        if prefix and member.name.startswith(prefix):
            member.name = member.name[len(prefix) :]
        if not member.name:
            continue

        # Reject absolute paths and parent-directory references.
        if member.name.startswith("/") or ".." in member.name.split("/"):
            continue

        # Reject symlinks and hardlinks (symlink escape risk).
        if member.issym() or member.islnk():
            logger.warning(
                "_safe_extract_tar: skipping symlink/hardlink: %s",
                member.name,
            )
            continue

        # Verify the resolved path stays inside dest_dir.
        resolved = os.path.realpath(os.path.join(dest_dir, member.name))
        if not resolved.startswith(real_dest):
            logger.warning(
                "_safe_extract_tar: skipping path traversal: %s",
                member.name,
            )
            continue

        if member.isfile():
            tar_obj.extract(member, path=dest_dir)
            extracted += 1
        elif member.isdir():
            os.makedirs(
                os.path.join(dest_dir, member.name),
                exist_ok=True,
            )
            extracted += 1

    return extracted


class DockerWorkspaceManager(WorkspaceManagerBase):
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
        basedir: str = "",
        *,
        base_image: str = DEFAULT_BASE_IMAGE,
        node_version: str = "20",
        extra_pip: list[str] | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
        # ── pooling parameters (disabled by default) ──────────
        pool_enabled: bool = False,
        pool_min_ready: int = 1,
        pool_max_ready: int = 3,
        pool_capacity: int = 10,
        pool_batch_size: int = 2,
    ) -> None:
        """Initialize the docker workspace manager.

        Args:
            basedir (`str`):
                Host root under which per-user/per-agent workdir are
                created (``<basedir>/<user_id>/<agent_id>``). Each
                workdir is bind-mounted to ``/workspace`` inside its
                container. Only used in TTL-cache mode; pool mode
                creates ephemeral containers without bind mounts.
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
                and its container torn down. Only used when
                ``pool_enabled=False``.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often (seconds) the background sweeper wakes up
                to look for idle workspaces. Only used when
                ``pool_enabled=False``.
            pool_enabled (`bool`, defaults to `False`):
                When ``True``, use a pre-warming pool instead of the
                TTL cache. Each container is used exactly once
                (``max_reuse=1``) and destroyed on release; the pool
                background loop creates fresh replacements.
            pool_min_ready (`int`, defaults to `1`):
                Pool: minimum number of ready-to-use instances kept
                on standby. When the count drops below this threshold,
                the pool automatically creates new instances in the
                background.
            pool_max_ready (`int`, defaults to `3`):
                Pool: target number of ready-to-use instances after
                replenishment. The pool will create instances up to
                this count when triggered.
            pool_capacity (`int`, defaults to `10`):
                Pool: maximum total instances managed by the pool
                (both in-use and standby combined). Requests beyond
                this limit trigger overflow creation.
            pool_batch_size (`int`, defaults to `2`):
                Pool: how many instances to create concurrently per
                replenishment cycle.
        """
        self._basedir = os.path.abspath(basedir) if basedir else ""
        self._base_image = base_image
        self._node_version = node_version
        self._extra_pip = list(extra_pip or [])
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        self._pool_enabled = pool_enabled

        # ── TTL-cache mode (pool_enabled=False) ───────────────
        self._cache: dict[str, tuple[DockerWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

        # ── Pool mode (pool_enabled=True) ─────────────────────
        # workspace_id → (pool entry, host workdir or "")
        self._active: dict[str, tuple[PooledEntry[DockerWorkspace], str]] = {}
        self._pool: WorkspacePool[DockerWorkspace] | None = None
        if pool_enabled:
            self._pool = WorkspacePool[DockerWorkspace](
                factory=self._pool_factory,
                reset_fn=None,
                health_check_fn=self._pool_health_check,
                close_fn=self._pool_close,
                pause_fn=self._pool_pause,
                resume_fn=self._pool_resume,
                pool_min_ready=pool_min_ready,
                pool_max_ready=pool_max_ready,
                pool_capacity=pool_capacity,
                pool_batch_size=pool_batch_size,
                max_reuse=1,
            )

    # ── isolation helpers ─────────────────────────────────────────

    def _workdir_for(self, user_id: str, agent_id: str) -> str:
        """Resolve the host workdir for ``(user_id, agent_id)``.

        Two-level layout — ``<basedir>/<user_id>/<agent_id>`` — so
        different users never share a bind-mount even when their
        ``agent_id`` collides.
        """
        return os.path.join(self._basedir, user_id, agent_id)

    # ── workspace construction (TTL-cache mode) ───────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> DockerWorkspace:
        """Create a :class:`DockerWorkspace` for ``(user_id, agent_id)``
        and run its full ``initialize``.

        ``workspace_id`` is forwarded so the container name is
        deterministic and the same id round-trips through the cache.
        """
        workdir = self._workdir_for(user_id, agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = DockerWorkspace(
            workspace_id=workspace_id,
            workdir=workdir,
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

    # ── pool callbacks (pool_enabled=True) ────────────────────────

    async def _pool_factory(self) -> DockerWorkspace:
        """Create and initialize a fresh DockerWorkspace for the pool."""
        ws = DockerWorkspace(
            workspace_id=None,
            workdir=None,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        logger.info(
            "DockerWorkspaceManager[pool]: created workspace %s",
            ws.workspace_id,
        )
        return ws

    @staticmethod
    async def _pool_health_check(ws: DockerWorkspace) -> bool:
        """Probe gateway health."""
        return await ws.gateway_health()

    @staticmethod
    async def _pool_pause(ws: DockerWorkspace) -> None:
        """Pause (freeze) the container."""
        await ws.pause()

    @staticmethod
    async def _pool_resume(ws: DockerWorkspace) -> None:
        """Resume a paused container."""
        await ws.resume()

    @staticmethod
    async def _pool_close(ws: DockerWorkspace) -> None:
        """Permanently destroy a workspace."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "DockerWorkspaceManager[pool]: failed to close %s",
                ws.workspace_id,
            )

    # ── tar-based sync helpers (pool mode) ─────────────────────────

    @staticmethod
    async def _sync_host_to_container(
        ws: DockerWorkspace,
        host_workdir: str,
    ) -> None:
        """Tar the host workdir and upload into the container."""
        if not os.path.isdir(host_workdir):
            return
        entries = os.listdir(host_workdir)
        if not entries:
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for entry in entries:
                tf.add(os.path.join(host_workdir, entry), arcname=entry)

        await ws.upload_tar(buf.getvalue())
        logger.info(
            "DockerWorkspaceManager[pool]: synced host -> container "
            "(%d entries)",
            len(entries),
        )

    @staticmethod
    async def _sync_container_to_host(
        ws: DockerWorkspace,
        host_workdir: str,
    ) -> None:
        """Download the container workspace tar and extract to host."""
        os.makedirs(host_workdir, exist_ok=True)

        try:
            tar_obj = await ws.download_tar()
        except Exception:
            logger.warning(
                "DockerWorkspaceManager[pool]: download_tar failed, "
                "skipping sync-back",
            )
            return

        try:
            extracted = _safe_extract_tar(tar_obj, host_workdir)
        finally:
            tar_obj.close()

        logger.info(
            "DockerWorkspaceManager[pool]: synced container -> host %s "
            "(%d entries)",
            host_workdir,
            extracted,
        )

    # ── public API ────────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
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
            workspace_id (`str`):
                Stable workspace identifier — used both as the cache
                key and the container name suffix.

        Returns:
            `DockerWorkspace`:
                A live, initialised workspace.
        """
        del session_id  # accepted for interface parity; not used here

        if self._pool_enabled:
            return await self._pool_get_workspace(
                user_id,
                agent_id,
                workspace_id,
            )

        # ── TTL-cache mode ────────────────────────────────────
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

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> DockerWorkspace:
        """Build a brand-new workspace and track it.
        A fresh ``workspace_id`` is allocated by
        :class:`DockerWorkspace` itself; the caller should persist
        ``workspace.workspace_id`` for later :meth:`get_workspace`
        calls.

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
        del session_id  # accepted for interface parity; not used here
        if self._pool_enabled:
            return await self._pool_create_workspace(user_id, agent_id)

        # ── TTL-cache mode ────────────────────────────────────
        workdir = self._workdir_for(user_id, agent_id)
        os.makedirs(workdir, exist_ok=True)
        ws = DockerWorkspace(
            workdir=workdir,
            base_image=self._base_image,
            node_version=self._node_version,
            extra_pip=self._extra_pip,
            gateway_port=self._gateway_port,
            env=self._env,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
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
        if self._pool_enabled:
            return await self._pool_close_workspace(workspace_id)

        # ── TTL-cache mode ────────────────────────────────────
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
        if self._pool_enabled:
            return await self._pool_close_all()

        # ── TTL-cache mode ────────────────────────────────────
        async with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        if not entries:
            return
        await asyncio.gather(
            *(self._safe_close(ws) for ws, _ in entries),
            return_exceptions=True,
        )

    # ── pool-mode public API helpers ──────────────────────────────

    async def _pool_get_workspace(
        self,
        user_id: str,
        agent_id: str,
        workspace_id: str,
    ) -> DockerWorkspace:
        """Return an active workspace, checking out from the pool if needed.

        If ``workspace_id`` is already active, its workspace is returned
        directly. Otherwise a fresh entry is acquired from the pool,
        the host workdir is synced into the container, and the entry is
        registered as active.
        """
        async with self._lock:
            slot = self._active.get(workspace_id)
            if slot is not None:
                return slot[0].workspace

        assert self._pool is not None
        entry = await self._pool.acquire()
        ws = entry.workspace

        host_workdir = (
            self._workdir_for(user_id, agent_id) if self._basedir else ""
        )

        async with self._lock:
            existing = self._active.get(workspace_id)
            if existing is not None:
                self._pool.release_background(entry)
                return existing[0].workspace
            self._active[workspace_id] = (entry, host_workdir)

        # Sync host workdir -> container so pool-mode workspaces get the
        # same directory structure as bind-mount (TTL-cache) mode.
        if host_workdir:
            os.makedirs(host_workdir, exist_ok=True)
            try:
                await self._sync_host_to_container(ws, host_workdir)
            except Exception:
                logger.exception(
                    "DockerWorkspaceManager[pool]: host->container sync "
                    "failed for workspace_id=%s, rolling back",
                    workspace_id,
                )
                # Roll back: remove from active tracking and release the
                # entry back to the pool so it is not leaked.
                async with self._lock:
                    self._active.pop(workspace_id, None)
                self._pool.release_background(entry)
                raise

        logger.info(
            "DockerWorkspaceManager[pool]: checked out %s for workspace_id=%s",
            ws.workspace_id,
            workspace_id,
        )
        return ws

    async def _pool_create_workspace(
        self,
        user_id: str,
        agent_id: str,
    ) -> DockerWorkspace:
        """Create a new workspace by generating an id and checking out."""
        workspace_id = uuid.uuid4().hex
        return await self._pool_get_workspace(
            user_id,
            agent_id,
            workspace_id,
        )

    async def _pool_close_workspace(self, workspace_id: str) -> None:
        """Close a single active workspace and release it back to the pool.

        Syncs the container filesystem back to the host workdir before
        releasing so that data persists across pool cycles.
        """
        async with self._lock:
            slot = self._active.pop(workspace_id, None)
        if slot is None:
            return
        entry, host_workdir = slot

        # Sync container -> host before releasing so data persists.
        if host_workdir:
            try:
                await self._sync_container_to_host(
                    entry.workspace,
                    host_workdir,
                )
            except Exception:
                logger.exception(
                    "DockerWorkspaceManager[pool]: container->host sync "
                    "failed for workspace_id=%s",
                    workspace_id,
                )

        assert self._pool is not None
        await self._pool.release(entry)

    async def _pool_close_all(self) -> None:
        """Close every active workspace and release them back to the pool.

        Syncs all active containers back to their host workdirs before
        releasing.
        """
        async with self._lock:
            slots = list(self._active.items())
            self._active.clear()
        if not slots:
            return

        # Sync all active containers back to host before releasing.
        for wid, (entry, host_workdir) in slots:
            if host_workdir:
                try:
                    await self._sync_container_to_host(
                        entry.workspace,
                        host_workdir,
                    )
                except Exception:
                    logger.exception(
                        "DockerWorkspaceManager[pool]: container->host "
                        "sync failed for workspace_id=%s during close_all",
                        wid,
                    )

        assert self._pool is not None
        await asyncio.gather(
            *(self._pool.release(entry) for _, (entry, _) in slots),
            return_exceptions=True,
        )

    # ── async context manager ─────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the pool or TTL sweeper."""
        if self._pool_enabled:
            assert self._pool is not None
            await self._pool.start()
        else:
            if self._sweep_task is None:
                self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the pool or TTL sweeper, then close everything."""
        if self._pool_enabled:
            await self.close_all()
            assert self._pool is not None
            await self._pool.stop()
        else:
            if self._sweep_task is not None:
                self._sweep_task.cancel()
                try:
                    await self._sweep_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._sweep_task = None
            await self.close_all()

    # ── background sweeper (TTL-cache mode only) ──────────────────

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
