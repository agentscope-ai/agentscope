# -*- coding: utf-8 -*-
"""K8SWorkspaceManager — lifecycle manager for :class:`K8SWorkspace`.

Mirrors :class:`E2BWorkspaceManager` 1:1 in its public surface so
callers do not branch on backend.

Differences from the E2B manager:

* No E2B-specific parameters (template, api_key, domain, sandbox_metadata).
  Instead takes K8S-specific ones (image, namespace, kubeconfig, pvc_name).
* ``user_id`` / ``agent_id`` are surfaced as extra Pod labels
  (``agentscope.user.id`` / ``agentscope.agent.id``).
* ``close`` deletes the Pod. PVC (if configured) is preserved for
  the next ``get_workspace`` / ``create_workspace`` call.
* Idle workspaces are evicted by a background sweeper task.
"""

import asyncio
import time
from typing import Self

from agentscope._logging import logger
from agentscope.mcp import MCPClient
from agentscope.workspace._k8s import K8SWorkspace
from agentscope.workspace._k8s._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    DEFAULT_NAMESPACE,
)
from ._base import WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


class K8SWorkspaceManager(WorkspaceManagerBase):
    """Manages :class:`K8SWorkspace` instances with TTL-based caching.

    Use as an ``async with`` context manager: entering starts the TTL
    sweeper task, exiting stops the sweeper and closes every cached
    workspace.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        namespace: str = DEFAULT_NAMESPACE,
        kubeconfig: str | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        pvc_name: str | None = None,
        env: dict[str, str] | None = None,
        pod_labels: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the K8S workspace manager.

        Args:
            image: Container image for workspace Pods.
            namespace: Kubernetes namespace for Pods.
            kubeconfig: Path to kubeconfig file. ``None`` auto-detects
                (in-cluster first, then default kubeconfig).
            gateway_port: TCP port the in-Pod gateway listens on.
            pvc_name: PVC name to mount for persistence. ``None``
                means no persistence — Pods are ephemeral.
            env: Environment variables set inside Pods.
            pod_labels: Extra labels merged onto workspace Pods.
            extra_pip: Extra Python packages for the gateway venv.
            default_mcps: MCPs seeded into brand-new workspaces.
            skill_paths: Skill directories seeded into new workspaces.
            ttl: Seconds before an idle workspace is evicted.
            sweep_interval: How often the sweeper wakes up.
        """
        self._image = image
        self._namespace = namespace
        self._kubeconfig = kubeconfig
        self._gateway_port = gateway_port
        self._pvc_name = pvc_name
        self._env = dict(env or {})
        self._pod_labels = dict(pod_labels or {})
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        self._ttl = ttl
        self._sweep_interval = sweep_interval

        self._cache: dict[str, tuple[K8SWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    # ── label helper ─────────────────────────────────────────────

    def _labels_for(
        self,
        user_id: str,
        agent_id: str,
    ) -> dict[str, str]:
        """Build extra Pod labels for ``(user_id, agent_id)``."""
        return {
            "agentscope.user.id": user_id,
            "agentscope.agent.id": agent_id,
            **self._pod_labels,
        }

    # ── workspace construction ───────────────────────────────────

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        user_id: str,
        agent_id: str,
    ) -> K8SWorkspace:
        ws = K8SWorkspace(
            workspace_id=workspace_id,
            image=self._image,
            namespace=self._namespace,
            kubeconfig=self._kubeconfig,
            gateway_port=self._gateway_port,
            pvc_name=self._pvc_name,
            env=self._env,
            pod_labels=self._labels_for(user_id, agent_id),
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    # ── public API ───────────────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> K8SWorkspace:
        """Return an initialised workspace, reattaching on cache miss.

        Args:
            user_id: Owning user identifier (Pod label only).
            agent_id: Agent identifier (Pod label only).
            session_id: Session identifier (unused at manager level).
            workspace_id: Stable workspace identifier — the cache key.
        """
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
    ) -> K8SWorkspace:
        """Build a brand-new workspace and track it.

        Args:
            user_id: Owning user identifier.
            agent_id: Agent identifier.
            session_id: Session identifier (accepted for parity).
        """
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
        """Close (= delete Pod) and evict a single workspace."""
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

    # ── async context manager ────────────────────────────────────

    async def __aenter__(self) -> Self:
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        await self.close_all()

    # ── background sweeper ──────────────────────────────────────

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("K8S workspace sweeper tick failed")

    async def _sweep_once(self) -> None:
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
    async def _safe_close(ws: K8SWorkspace) -> None:
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close K8SWorkspace %s",
                ws.workspace_id,
            )
