# -*- coding: utf-8 -*-
"""共享 PVC 模式：每个 session 独立 Pod，所有 session 共享一个 agent 级 RWX PVC。

架构
----

::

    PVC: as-ws-{agent_hash} (ReadWriteMany)
         │
         ├── shared/skills/              ← 所有 Pod 共享
         ├── shared/.mcp                 ← 所有 Pod 共享
         │
         ├── sessions/{sess_A}/          ← Pod-A 独占
         │   ├── data/
         │   └── {project}/
         │
         └── sessions/{sess_B}/          ← Pod-B 独占
             ├── data/
             └── {project}/

零框架改动 — 所有逻辑通过子类覆盖实现，不动 ``agentscope`` 一行代码。
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


class SharedPvcK8sWorkspace(K8sWorkspace):
    """K8sWorkspace 子类：每个 session 一个 Pod，共享 agent 级 PVC。

    覆盖 4 个父类方法（:meth:`_ensure_pvc`、:meth:`_create_pvc`、
    :meth:`_create_pod`、:meth:`_teardown_backend`）实现：

    - Pod 名 = session 级（``as-ws-{session_workspace_id}``）
    - PVC 名 = agent 级（``as-ws-{agent_hash}``），所有 session 共享
    - workdir = ``/workspace/sessions/{session_id}``（路径隔离）
    - skills/.mcp → ``/workspace/shared/``（共享）
    """

    def __init__(
        self,
        *,
        # ── 新增: 共享 PVC 参数 ──
        shared_pvc_name: str = "",
        session_id: str = "",
        shared_pvc_access_mode: str = "ReadWriteMany",
        # ── 透传给父类的所有参数 ──
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
        # 先让父类初始化（设置 workdir = POD_WORKDIR 等）
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

        # ── 覆盖共享 PVC 状态 ──
        self._shared_pvc_name: str = shared_pvc_name
        self._session_id: str = session_id
        self._shared_pvc_access_mode: str = shared_pvc_access_mode

        # ── 覆盖工作目录为 session 子目录 ──
        self.workdir = f"{POD_WORKDIR}/sessions/{self._session_id}"

        # ── 更新 instructions（workdir 变了） ──
        self.instructions = (
            instructions or "Workspace directory: {workdir}"
        ).format(
            backend="Kubernetes-based (shared-PVC)",
            workdir=self.workdir,
        )

    # ── 覆盖 property: skills/.mcp 指向共享区 ──────────────────

    @property
    def _skills_dir(self) -> str:
        """``/workspace/shared/skills`` — 所有 session 共享。"""
        return self.get_backend().join_path(
            POD_WORKDIR, "shared", DEFAULT_SKILLS_DIR,
        )

    @property
    def _mcp_file(self) -> str:
        """``/workspace/shared/.mcp`` — 所有 session 共享。"""
        return self.get_backend().join_path(
            POD_WORKDIR, "shared", DEFAULT_MCP_FILE,
        )

    # ── 覆盖 4 个 K8s 资源管理方法 ────────────────────────────

    async def _ensure_pvc(self) -> None:
        """使用 agent 级 PVC 名而非 Pod 名。

        其余逻辑与父类相同：exists → 检查 deletion_timestamp；
        not found → 创建。
        """
        from kubernetes_asyncio.client.rest import ApiException

        pvc_name = self._shared_pvc_name  # ← 唯一改动点
        try:
            pvc = await self._v1.read_namespaced_persistent_volume_claim(
                pvc_name,
                self._namespace,
            )
            if pvc.metadata and pvc.metadata.deletion_timestamp is not None:
                logger.info(
                    "SharedPvcK8sWorkspace: PVC %r is being deleted, "
                    "waiting...",
                    pvc_name,
                )
                await self._wait_pvc_deleted(pvc_name)
                await self._create_pvc(pvc_name)
        except ApiException as e:
            if e.status == 404:
                await self._create_pvc(pvc_name)
            else:
                raise

    async def _create_pvc(self, pvc_name: str) -> None:
        """使用可配置的 access mode（默认 ReadWriteMany）。

        其余逻辑与父类相同。
        """
        from kubernetes_asyncio import client as k8s_client

        access_modes = [self._shared_pvc_access_mode]  # ← 唯一改动点

        spec_kwargs: dict[str, Any] = {
            "access_modes": access_modes,
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

    async def _create_pod(self) -> None:
        """Pod 挂载 agent 级共享 PVC，working_dir 指向 session 子目录。

        与父类有两处不同：
        1. volume claim_name → ``self._shared_pvc_name``
        2. working_dir → ``self.workdir``（session 子目录）
        """
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
            working_dir=self.workdir,  # ← session 子目录
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
                k8s_client.V1VolumeMount(
                    name="workspace-data",
                    mount_path=POD_WORKDIR,
                ),
            ],
            env=container_env,
        )

        claim_name = self._shared_pvc_name  # ← agent 级 PVC

        volumes = [
            k8s_client.V1Volume(
                name="workspace-data",
                persistent_volume_claim=(
                    k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=claim_name,
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
                },
            ),
            spec=k8s_client.V1PodSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_pod(self._namespace, pod)

    async def _teardown_backend(self) -> None:
        """删除 session Pod，但**绝不**删除共享 PVC。

        共享 PVC 由 agent 级别管理，不在 session 结束时清理。
        """
        if self._v1 is not None and self._pod_name:
            try:
                await self._v1.delete_namespaced_pod(
                    self._pod_name,
                    self._namespace,
                )
            except Exception as e:
                logger.warning(
                    "SharedPvcK8sWorkspace: Pod delete failed: %s", e,
                )

            # 共享模式下绝不删除 PVC
            # （即使 _delete_pvc_on_close=True 也忽略，
            #  因为其他 session 可能正在使用）

        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass
            self._api_client = None
            self._v1 = None


# ── Manager ────────────────────────────────────────────────────────


class SharedPvcK8sWorkspaceManager(K8sWorkspaceManager):
    """管理 :class:`SharedPvcK8sWorkspace` 实例。

    与父类 :class:`K8sWorkspaceManager` 的区别：

    - 隔离策略固定为 ``PER_SESSION``（每个 session 独立 Pod）
    - PVC 名称由 ``user_id::agent_id`` hash 派生（agent 级共享）
    - 缓存 key 仍是 session-scoped workspace_id
    """

    def __init__(
        self,
        *,
        shared_pvc_access_mode: str = "ReadWriteMany",
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
        """初始化共享 PVC 模式的 Manager。

        Args:
            shared_pvc_access_mode (`str`, defaults to ``"ReadWriteMany"``):
                K8s PVC access mode。集群需支持对应存储（NFS/CephFS）。
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
        self._shared_pvc_access_mode = shared_pvc_access_mode

    # ── 覆盖 get_workspace ──────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> SharedPvcK8sWorkspace:
        """返回 session-scoped workspace，PVC 由 agent 级共享。

        Args:
            user_id (`str`): 用户 ID。
            agent_id (`str`): 智能体 ID（用于生成 PVC 名）。
            session_id (`str`): 会话 ID（用于生成 Pod 名和 workdir 子目录）。
            workspace_id (`str | None`, optional):
                Stable workspace identifier。``None`` 时自动生成。

        Returns:
            `SharedPvcK8sWorkspace`: 已初始化的 workspace。
        """
        if workspace_id is None:
            workspace_id = self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )

        # agent 级 PVC 名称（= 父类 PER_AGENT 的 workspace_id 命名规则）
        agent_hash = hashlib.blake2b(
            f"{user_id}::{agent_id}".encode("utf-8"),
            digest_size=8,
        ).hexdigest()
        shared_pvc_name = _k8s_safe_name(agent_hash)

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
                shared_pvc_name=shared_pvc_name,
                session_id=session_id,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws  # type: ignore[return-value]

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        shared_pvc_name: str,
        session_id: str,
    ) -> SharedPvcK8sWorkspace:
        """构造 :class:`SharedPvcK8sWorkspace` 并初始化。"""
        from agentscope.workspace._utils import DEFAULT_WORKSPACE_INSTRUCTIONS

        ws = SharedPvcK8sWorkspace(
            workspace_id=workspace_id,
            shared_pvc_name=shared_pvc_name,
            session_id=session_id,
            shared_pvc_access_mode=self._shared_pvc_access_mode,
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
