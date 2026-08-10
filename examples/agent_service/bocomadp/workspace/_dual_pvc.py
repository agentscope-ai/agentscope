# -*- coding: utf-8 -*-
"""双 PVC 模式：Agent 级共享 PVC + Session 级独立 PVC。

架构
----

::

    Agent PVC: as-ws-{agent_hash}          (ReadWriteMany)
        ├── skills/                         ← 所有 session 共享
        └── .mcp                            ← MCP 注册共享

    Session PVC: as-ws-{session_hash}      (ReadWriteOnce)
        ├── data/                           ← session 私有
        └── sessions/                       ← session 私有

    Pod 内挂载:
        /workspace-shared/     → Agent PVC    (skills + .mcp)
        /workspace/            → Session PVC  (workdir + data + sessions)

与共享 PVC 模式的区别：
    - 共享 PVC：一个 agent 级 PVC，sessions 在子目录隔离（可互相看到）
    - 双 PVC：每个 session 独立 PVC，完全不可见对方文件
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from agentscope._logging import logger
from agentscope.app.workspace_manager import (
    K8sWorkspaceManager,
    IsolationPolicy,
)
from agentscope.workspace import K8sWorkspace
from agentscope.workspace._k8s._constants import (
    POD_WORKDIR,
    _k8s_safe_name,
)

# ── reuse parent's utility constants ───────────────────────────────
from agentscope.workspace._utils import (
    DEFAULT_MCP_FILE,
    DEFAULT_SKILLS_DIR,
)


# ── mount paths ────────────────────────────────────────────────────

AGENT_PVC_MOUNT = "/workspace-shared"
"""Agent 级共享 PVC 在 Pod 内的挂载路径。"""

SESSION_PVC_MOUNT = POD_WORKDIR
"""Session 级独立 PVC 挂载路径 = ``/workspace``。"""


class DualPvcK8sWorkspace(K8sWorkspace):
    """双 PVC K8s 沙箱工作区。

    每个 session 拥有独立的 Pod 和独立的 session PVC，
    skills 和 .mcp 存储在 agent 级共享 PVC 上。

    覆盖 5 个父类方法：
    - :meth:`_skills_dir` / :meth:`_mcp_file` → 指向 agent PVC
    - :meth:`_ensure_pvc` → 确保两个 PVC 都存在
    - :meth:`_create_pod` → 挂载两个 PVC
    - :meth:`_teardown_backend` → 删除 Pod + session PVC
    """

    # ── init ───────────────────────────────────────────────────

    def __init__(
        self,
        *,
        # ── 新增：双 PVC 参数 ──
        agent_pvc_name: str = "",
        session_pvc_name: str = "",
        agent_pvc_access_mode: str = "ReadWriteMany",
        session_pvc_access_mode: str = "ReadWriteOnce",
        # ── 透传给父类的参数 ──
        workspace_id: str | None = None,
        kubeconfig: str | None = None,
        namespace: str = "agentscope",
        image: str = "python:3.11-slim",
        image_pull_policy: str = "IfNotPresent",
        image_pull_secrets: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        node_selector: dict[str, str] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        service_account: str | None = None,
        gateway_port: int = 5600,
        extra_pip: list[str] | None = None,
        storage_class: str | None = None,
        storage_size: str = "1Gi",
        delete_pvc_on_close: bool = False,
        env: dict[str, str] | None = None,
        instructions: str = "",
        default_mcps: list[Any] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        super().__init__(
            workspace_id=workspace_id,
            kubeconfig=kubeconfig,
            namespace=namespace,
            image=image,
            image_pull_policy=image_pull_policy,
            image_pull_secrets=image_pull_secrets,
            resources=resources,
            node_selector=node_selector,
            tolerations=tolerations,
            service_account=service_account,
            gateway_port=gateway_port,
            extra_pip=extra_pip,
            storage_class=storage_class,
            storage_size=storage_size,
            delete_pvc_on_close=delete_pvc_on_close,
            env=env,
            instructions=instructions,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

        # ── 双 PVC 状态 ──
        self._agent_pvc_name: str = agent_pvc_name
        self._session_pvc_name: str = session_pvc_name
        self._agent_pvc_access_mode: str = agent_pvc_access_mode
        self._session_pvc_access_mode: str = session_pvc_access_mode

        # workdir = /workspace (session PVC 挂载点，父类已设置)
        # self.workdir = POD_WORKDIR

    # ── 覆盖 property：skills/.mcp → agent PVC ─────────────────

    @property
    def _skills_dir(self) -> str:
        """``/workspace-shared/skills`` — agent 级 PVC 共享。"""
        return self.get_backend().join_path(
            AGENT_PVC_MOUNT,
            DEFAULT_SKILLS_DIR,
        )

    @property
    def _mcp_file(self) -> str:
        """``/workspace-shared/.mcp`` — agent 级 PVC 共享。"""
        return self.get_backend().join_path(
            AGENT_PVC_MOUNT,
            DEFAULT_MCP_FILE,
        )

    # ── 覆盖 PVC 管理 ─────────────────────────────────────────

    async def _ensure_pvc(self) -> None:
        """确保 agent + session 两个 PVC 都已就绪。

        agent PVC: 跨 session 共享，不在此处删除。
        session PVC: session 专属，若被删除则重建。
        """
        from kubernetes_asyncio.client.rest import ApiException

        for pvc_name, access_mode in (
            (self._agent_pvc_name, self._agent_pvc_access_mode),
            (self._session_pvc_name, self._session_pvc_access_mode),
        ):
            try:
                pvc = await self._v1.read_namespaced_persistent_volume_claim(
                    pvc_name,
                    self._namespace,
                )
                if pvc.metadata and pvc.metadata.deletion_timestamp is not None:
                    logger.info(
                        "DualPvcK8sWorkspace: PVC %r deleting, waiting...",
                        pvc_name,
                    )
                    await self._wait_pvc_deleted(pvc_name)
                    await self._create_pvc(pvc_name, access_mode)
            except ApiException as e:
                if e.status == 404:
                    await self._create_pvc(pvc_name, access_mode)
                else:
                    raise

    async def _create_pvc(
        self,
        pvc_name: str,
        access_mode: str,
    ) -> None:
        """创建单个 PVC（支持灵活指定 access mode）。"""
        from kubernetes_asyncio import client as k8s_client

        spec_kwargs: dict[str, Any] = {
            "access_modes": [access_mode],
            "resources": k8s_client.V1VolumeResourceRequirements(
                requests={"storage": self._storage_size},
            ),
        }
        if self._storage_class is not None:
            spec_kwargs["storage_class_name"] = self._storage_class

        pvc = k8s_client.V1PersistentVolumeClaim(
            metadata=k8s_client.V1ObjectMeta(
                name=pvc_name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "agentscope",
                    "agentscope.workspace.id": self.workspace_id,
                },
            ),
            spec=k8s_client.V1PersistentVolumeClaimSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_persistent_volume_claim(
            self._namespace,
            pvc,
        )
        logger.info(
            "DualPvcK8sWorkspace: PVC %r created (access_mode=%s)",
            pvc_name,
            access_mode,
        )

    # ── 覆盖 workspace layout：创建 skills 符号链接 ───────────

    async def _ensure_workspace_layout(self) -> None:
        """创建标准目录后，将 agent PVC 的 skills + .mcp 链接到
        ``/workspace/``，使沙箱内的 AI 能自然发现共享资源。
        """
        await super()._ensure_workspace_layout()

        backend = self.get_backend()

        # skills → /workspace-shared/skills
        skills_link = backend.join_path(self.workdir, DEFAULT_SKILLS_DIR)
        skills_target = self._skills_dir

        if not await backend.file_exists(skills_link):
            await backend.exec_shell(
                ["ln", "-sfn", skills_target, skills_link],
                cwd="/",
            )
            logger.info(
                "DualPvcK8sWorkspace: symlink %s -> %s created",
                skills_link,
                skills_target,
            )

        # .mcp → /workspace-shared/.mcp
        mcp_link = backend.join_path(self.workdir, DEFAULT_MCP_FILE)
        mcp_target = self._mcp_file

        if not await backend.file_exists(mcp_link):
            await backend.exec_shell(
                ["ln", "-sfn", mcp_target, mcp_link],
                cwd="/",
            )
            logger.info(
                "DualPvcK8sWorkspace: symlink %s -> %s created",
                mcp_link,
                mcp_target,
            )

    # ── 覆盖 Pod 创建：双卷挂载 ────────────────────────────────

    async def _create_pod(self) -> None:
        """Pod 挂载 agent + session 两个 PVC。"""
        from kubernetes_asyncio import client as k8s_client

        container_env = None
        if self.env:
            container_env = [
                k8s_client.V1EnvVar(name=k, value=v)
                for k, v in self.env.items()
            ]

        container = k8s_client.V1Container(
            name="workspace",
            image=self._image,
            image_pull_policy=self._image_pull_policy,
            command=["sleep", "infinity"],
            working_dir=self.workdir,
            ports=[
                k8s_client.V1ContainerPort(
                    container_port=self.gateway_port,
                ),
            ],
            resources=(
                k8s_client.V1ResourceRequirements(**self._resources)
                if self._resources
                else None
            ),
            volume_mounts=[
                # Agent PVC → /workspace-shared（skills + .mcp）
                k8s_client.V1VolumeMount(
                    name="workspace-shared",
                    mount_path=AGENT_PVC_MOUNT,
                ),
                # Session PVC → /workspace（workdir + data + sessions）
                k8s_client.V1VolumeMount(
                    name="workspace",
                    mount_path=SESSION_PVC_MOUNT,
                ),
            ],
            env=container_env,
        )

        volumes = [
            k8s_client.V1Volume(
                name="workspace-shared",
                persistent_volume_claim=(
                    k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self._agent_pvc_name,
                    )
                ),
            ),
            k8s_client.V1Volume(
                name="workspace",
                persistent_volume_claim=(
                    k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self._session_pvc_name,
                    )
                ),
            ),
        ]

        spec_kwargs: dict[str, Any] = {
            "restart_policy": "OnFailure",
            "containers": [container],
            "volumes": volumes,
        }
        if self._node_selector:
            spec_kwargs["node_selector"] = self._node_selector
        if self._tolerations:
            spec_kwargs["tolerations"] = [
                k8s_client.V1Toleration(**t) for t in self._tolerations
            ]
        if self._service_account:
            spec_kwargs["service_account_name"] = self._service_account
        if self._image_pull_secrets:
            spec_kwargs["image_pull_secrets"] = [
                k8s_client.V1LocalObjectReference(name=s)
                for s in self._image_pull_secrets
            ]

        pod = k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=self._pod_name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "agentscope",
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": self.workspace_id,
                    "agentscope.pvc.agent": self._agent_pvc_name,
                    "agentscope.pvc.session": self._session_pvc_name,
                },
            ),
            spec=k8s_client.V1PodSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_pod(self._namespace, pod)

    # ── 覆盖清理：删 Pod + session PVC，保留 agent PVC ────────

    async def _teardown_backend(self) -> None:
        """删除 session Pod 和 session PVC。

        agent PVC 不删除——它由 agent 生命周期管理。
        """
        if self._v1 is not None:
            # 1. 删除 Pod
            if self._pod_name:
                try:
                    await self._v1.delete_namespaced_pod(
                        self._pod_name,
                        self._namespace,
                    )
                    logger.info(
                        "DualPvcK8sWorkspace: Pod %r deleted",
                        self._pod_name,
                    )
                except Exception as e:
                    logger.warning(
                        "DualPvcK8sWorkspace: Pod delete failed: %s", e,
                    )

            # 2. 删除 session PVC（session 结束即清理）
            if self._session_pvc_name:
                try:
                    await self._v1.delete_namespaced_persistent_volume_claim(
                        self._session_pvc_name,
                        self._namespace,
                    )
                    logger.info(
                        "DualPvcK8sWorkspace: session PVC %r deleted",
                        self._session_pvc_name,
                    )
                except Exception as e:
                    logger.warning(
                        "DualPvcK8sWorkspace: session PVC delete failed: %s",
                        e,
                    )

            # 3. agent PVC 保留——不删除

        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass
            self._api_client = None
            self._v1 = None


# ── Manager ────────────────────────────────────────────────────────


class DualPvcK8sWorkspaceManager(K8sWorkspaceManager):
    """管理 :class:`DualPvcK8sWorkspace` 实例。

    与父类 :class:`K8sWorkspaceManager` 的区别：

    - 隔离策略固定为 ``PER_SESSION``（每个 session 独立 Pod）
    - 每个 session 创建独立的 session PVC（完全隔离）
    - agent PVC 由 ``user_id::agent_id`` hash 派生（跨 session 共享 skills + .mcp）
    - 默认 agent PVC = ReadWriteMany，session PVC = ReadWriteOnce
    """

    def __init__(
        self,
        *,
        agent_pvc_access_mode: str = "ReadWriteMany",
        session_pvc_access_mode: str = "ReadWriteOnce",
        # ── 透传给父类的参数 ──
        kubeconfig: str | None = None,
        namespace: str = "agentscope",
        image: str = "python:3.11-slim",
        image_pull_policy: str = "IfNotPresent",
        image_pull_secrets: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        node_selector: dict[str, str] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        service_account: str | None = None,
        gateway_port: int = 5600,
        extra_pip: list[str] | None = None,
        storage_class: str | None = None,
        storage_size: str = "1Gi",
        env: dict[str, str] | None = None,
        default_mcps: list[Any] | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        sweep_interval: float = 300.0,
        delete_pvc_on_close: bool = False,
    ) -> None:
        """初始化双 PVC 模式的 Manager。

        Args:
            agent_pvc_access_mode (`str`, defaults to ``"ReadWriteMany"``):
                agent 级 PVC 的 access mode。需集群支持多 Pod 并发挂载。
            session_pvc_access_mode (`str`, defaults to ``"ReadWriteOnce"``):
                session 级 PVC 的 access mode。RWO 即可（单 Pod 独占）。
            （其余参数同 :class:`K8sWorkspaceManager`）
        """
        # 强制 PER_SESSION — 每个 session 一个独立 Pod
        super().__init__(
            isolation=IsolationPolicy.PER_SESSION,
            kubeconfig=kubeconfig,
            namespace=namespace,
            image=image,
            image_pull_policy=image_pull_policy,
            image_pull_secrets=image_pull_secrets,
            resources=resources,
            node_selector=node_selector,
            tolerations=tolerations,
            service_account=service_account,
            gateway_port=gateway_port,
            extra_pip=extra_pip,
            storage_class=storage_class,
            storage_size=storage_size,
            env=env,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
            ttl=ttl,
            sweep_interval=sweep_interval,
            delete_pvc_on_close=delete_pvc_on_close,
        )
        self._agent_pvc_access_mode = agent_pvc_access_mode
        self._session_pvc_access_mode = session_pvc_access_mode

    # ── 覆盖 get_workspace ──────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> DualPvcK8sWorkspace:
        """返回 session-scoped workspace，双 PVC 架构。

        Args:
            user_id (`str`): 用户 ID。
            agent_id (`str`): 智能体 ID（用于生成 agent PVC 名）。
            session_id (`str`): 会话 ID（用于生成 session PVC 名）。
            workspace_id (`str | None`, optional):
                Stable workspace identifier。``None`` 时自动生成。

        Returns:
            `DualPvcK8sWorkspace`: 已初始化的 workspace。
        """
        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )

        # agent 级 PVC 名称（跨 session 共享 skills + .mcp）
        agent_hash = hashlib.blake2b(
            f"{user_id}::{agent_id}".encode("utf-8"),
            digest_size=8,
        ).hexdigest()
        agent_pvc_name = _k8s_safe_name(agent_hash)

        # session 级 PVC 名称（独立，session 间完全隔离）
        session_hash = hashlib.blake2b(
            f"{user_id}::{agent_id}::{session_id}".encode("utf-8"),
            digest_size=8,
        ).hexdigest()
        session_pvc_name = _k8s_safe_name(session_hash)

        # ── 缓存查找（session-scoped key） ──
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws  # type: ignore[return-value]

        # ── 缓存未命中 → 创建新 Pod ──
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws  # type: ignore[return-value]

            ws = await self._build_and_start(
                workspace_id=workspace_id,
                agent_pvc_name=agent_pvc_name,
                session_pvc_name=session_pvc_name,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws  # type: ignore[return-value]

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        agent_pvc_name: str,
        session_pvc_name: str,
    ) -> DualPvcK8sWorkspace:
        """构造 :class:`DualPvcK8sWorkspace` 并初始化。"""
        from agentscope.workspace._utils import DEFAULT_WORKSPACE_INSTRUCTIONS

        ws = DualPvcK8sWorkspace(
            workspace_id=workspace_id,
            agent_pvc_name=agent_pvc_name,
            session_pvc_name=session_pvc_name,
            agent_pvc_access_mode=self._agent_pvc_access_mode,
            session_pvc_access_mode=self._session_pvc_access_mode,
            # ── 透传 Manager 配置 ──
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
            instructions=DEFAULT_WORKSPACE_INSTRUCTIONS,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws
