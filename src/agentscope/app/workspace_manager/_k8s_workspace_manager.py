# -*- coding: utf-8 -*-
"""K8sWorkspaceManager — lifecycle manager for :class:`K8sWorkspace`.

Mirrors :class:`E2BWorkspaceManager` 1:1 in its public surface
(``get_workspace`` / ``create_workspace`` / ``close`` / ``close_all``)
so callers do not branch on backend.

Differences from the E2B manager:

* Reattachment uses a deterministic Pod/PVC name derived from
  ``workspace_id`` (via ``_k8s_safe_name``).
  :meth:`K8sWorkspace.initialize` handles rediscovery.
* ``close()`` deletes the Pod; PVC deletion is governed by the
  workspace's ``_delete_pvc_on_close`` attribute.
* Idle workspaces are evicted by a dedicated background sweeper task.
"""

import asyncio
import time
from typing import Any, Self

from typing_extensions import deprecated

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace import K8sWorkspace
from ...workspace._k8s._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
)
from ._base import WorkspaceManagerBase, IsolationPolicy

DEFAULT_SWEEP_INTERVAL = 300.0


class K8sWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`K8sWorkspace` instances with TTL-based caching.

    Use as an ``async with`` context manager: entering starts the TTL
    sweeper, exiting stops it and closes every cached workspace.
    """

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        namespace: str = "agentscope",
        kubeconfig: str | None = None,
        image: str = DEFAULT_IMAGE,
        image_pull_policy: str = "IfNotPresent",
        image_pull_secrets: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        node_selector: dict[str, str] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        service_account: str | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        extra_pip: list[str] | None = None,
        storage_class: str | None = None,
        storage_size: str = "1Gi",
        env: dict[str, str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
        delete_pvc_on_close: bool = False,
    ) -> None:
        """Initialize the K8s workspace manager.

        Args:
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`. See
                :class:`DockerWorkspaceManager` for semantics.
            namespace (`str`, defaults to ``"agentscope"``):
                K8s namespace for workspace Pods and PVCs.
            kubeconfig (`str | None`, optional):
                Path to kubeconfig file. ``None`` uses in-cluster
                config.
            image (`str`, defaults to ``"python:3.11-slim"``):
                Container image for workspace Pods.
            image_pull_policy (`str`, defaults to ``"IfNotPresent"``):
                K8s imagePullPolicy.
            image_pull_secrets (`list[str] | None`, optional):
                Names of K8s image pull secrets.
            resources (`dict[str, Any] | None`, optional):
                K8s ResourceRequirements dict.
            node_selector (`dict[str, str] | None`, optional):
                K8s nodeSelector.
            tolerations (`list[dict[str, Any]] | None`, optional):
                K8s tolerations list.
            service_account (`str | None`, optional):
                K8s serviceAccountName.
            gateway_port (`int`, defaults to `5600`):
                Port the gateway listens on inside the Pod.
            extra_pip (`list[str] | None`, optional):
                Extra packages for the gateway venv.
            storage_class (`str | None`, optional):
                K8s StorageClass name.
            storage_size (`str`, defaults to ``"1Gi"``):
                PVC size.
            env (`dict[str, str] | None`, optional):
                Environment variables for workspace containers.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded into new workspaces.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into new workspaces.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle workspace is evicted.
            sweep_interval (`float`, defaults to `300.0`):
                How often the sweeper runs.
            delete_pvc_on_close (`bool`, defaults to ``False``):
                Default ``delete_pvc_on_close`` value for newly
                created workspaces.
        """
        self._namespace = namespace
        self._kubeconfig = kubeconfig
        self._image = image
        self._image_pull_policy = image_pull_policy
        self._image_pull_secrets = list(image_pull_secrets or [])
        self._resources = resources
        self._node_selector = node_selector
        self._tolerations = tolerations
        self._service_account = service_account
        self._gateway_port = gateway_port
        self._extra_pip = list(extra_pip or [])
        self._storage_class = storage_class
        self._storage_size = storage_size
        self._env = dict(env or {})
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        self._delete_pvc_on_close = delete_pvc_on_close
        super().__init__(isolation=isolation)

        # workspace_id → (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[K8sWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task[None] | None = None

    # ── workspace construction ────────────────────────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
    ) -> K8sWorkspace:
        """Construct a :class:`K8sWorkspace` and run ``initialize``."""
        ws = K8sWorkspace(
            workspace_id=workspace_id,
            kubeconfig=self._kubeconfig,
            namespace=self._namespace,
            image=self._image,
            image_pull_policy=self._image_pull_policy,
            image_pull_secrets=self._image_pull_secrets,
            resources=self._resources,
            node_selector=self._node_selector,
            tolerations=self._tolerations,
            service_account=self._service_account,
            gateway_port=self._gateway_port,
            extra_pip=self._extra_pip,
            storage_class=self._storage_class,
            storage_size=self._storage_size,
            delete_pvc_on_close=self._delete_pvc_on_close,
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
    ) -> K8sWorkspace:
        """Return an initialised workspace, reattaching on cache miss.

        Args:
            user_id (`str`):
                Owning user identifier.
            agent_id (`str`):
                Agent identifier.
            session_id (`str`):
                Session identifier (unused; Pods are per-workspace).
            workspace_id (`str | None`, optional):
                Stable workspace identifier — the cache key. When
                ``None`` the manager falls back to
                :meth:`assign_workspace_id`.

        Returns:
            `K8sWorkspace`:
                A live, initialised workspace.
        """
        del session_id

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

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            ws = await self._build_and_start(
                workspace_id=workspace_id,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws

    @deprecated(
        "K8sWorkspaceManager.create_workspace is deprecated; "
        "use get_workspace(workspace_id=None) instead.",
        category=None,
    )
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> K8sWorkspace:
        """Build a brand-new workspace and track it.

        .. deprecated::
            Use :meth:`get_workspace` with ``workspace_id=None`` — it
            falls back to :meth:`assign_workspace_id` under the
            manager's isolation policy and reuses the cache path.

        Args:
            user_id (`str`):
                Owning user identifier.
            agent_id (`str`):
                Agent identifier.
            session_id (`str`):
                Session identifier (unused).

        Returns:
            `K8sWorkspace`:
                The newly built workspace, already initialised.
        """
        del session_id, user_id, agent_id

        ws = await self._build_and_start(workspace_id=None)
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
        return ws

    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace.

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

    # ── async context manager ─────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Start the TTL sweeper task."""
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the TTL sweeper, then close every cached workspace."""
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
        """Periodically evict idle workspaces."""
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("K8s workspace sweeper tick failed")

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
    async def _safe_close(ws: K8sWorkspace) -> None:
        """Close a workspace, logging any failure instead of raising.

        Args:
            ws (`K8sWorkspace`):
                The workspace to close.
        """
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close K8sWorkspace %s",
                ws.workspace_id,
            )
