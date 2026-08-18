# -*- coding: utf-8 -*-
"""共享 PVC 模式：温池 + session 独立 Pod，所有 session 共享一个 agent 级 RWX PVC。

架构
----

::

    PVC: as-ws-{agent_hash} (ReadWriteMany)
         │
         ├── shared/skills/              ← 所有 Pod 共享
         ├── shared/.mcp                 ← 所有 Pod 共享
         │
         ├── sessions/{sess_A}/          ← Pod slot-0 独占
         │   ├── data/
         │   └── {project}/
         │
         └── sessions/{sess_B}/          ← Pod slot-1 独占
             ├── data/
             └── {project}/

温池
----

Manager 根据 ``max_active_pods`` 预创建 N 个 Pod（slot-0 .. slot-N-1），
标记 ``agentscope.pool.slot=available``。分配时通过 K8s resourceVersion
乐观锁绑定 session，释放后归还池中。TTL 到期自动回收 slot。

零框架改动 — 所有逻辑通过子类覆盖实现，不动 ``agentscope`` 一行代码。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

from agentscope._logging import logger
from agentscope.app.workspace_manager import (
    K8sWorkspaceManager,
    IsolationPolicy,
)
from agentscope.workspace import K8sWorkspace
from agentscope.workspace._k8s._k8s_backend import K8sBackend
from agentscope.workspace._k8s._constants import (
    POD_WORKDIR,
    _k8s_safe_name,
)

# ── reuse parent's utility constants ───────────────────────────────
from agentscope.workspace._utils import (
    DEFAULT_MCP_FILE,
    DEFAULT_SKILLS_DIR,
)

# ── user-data layout constants ─────────────────────────────────────
#: Workspace-relative root of the user working area.
DEFAULT_USER_DATA_DIR = "user-data"

#: Workspace-relative working directory for temporary files.
DEFAULT_USER_SCRATCH_DIR = "scratch"

#: Workspace-relative directory for final deliverables.
DEFAULT_USER_OUTPUTS_DIR = "outputs"

#: Workspace-relative directory for user-uploaded files (sandbox-safe,
#: inside workdir so that dual-PVC session PVC and shared-PVC
#: ``/workspace/sessions/{id}`` both isolate uploads per session).
DEFAULT_USER_UPLOADS_DIR = "uploads"


def _pool_slot_index(pod_name: str) -> int | None:
    """从池 Pod 名 ``as-ws-{hash}-{i}`` 解析 slot 序号。

    非池命名（session 按需 Pod）或解析失败返回 ``None``。
    """
    try:
        return int(pod_name.rsplit("-", 1)[1])
    except (ValueError, IndexError):
        return None


_K8S_LABEL_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9-_.]")


def _k8s_safe_label(value: str, max_len: int = 63) -> str:
    """清洗 K8s label 值：仅保留字母数字与 ``- _ .``，首尾必须字母数字。

    原始 agent_id / session_id / workspace_id 可能含空格、Unicode
    连字符（如 U+2011）等非法字符，直接写入 label 会被 API server
    以 422 拒绝。写入与按 label 查询两侧必须使用本函数保证一致。
    """
    cleaned = _K8S_LABEL_UNSAFE_RE.sub("-", value)
    cleaned = cleaned.strip("-._")
    return (cleaned or "x")[:max_len].rstrip("-._")


class SharedPvcK8sWorkspace(K8sWorkspace):
    """K8sWorkspace 子类：session Pod，共享 agent 级 PVC + 温池。

    覆盖父类方法实现：

    - Pod 名 = pool slot 名（温池）或 session 级（按需）
    - PVC 名 = agent 级（``as-ws-{agent_hash}``），所有 session 共享
    - workdir = ``/workspace/sessions/{session_id}``（路径隔离）
    - skills/.mcp → ``/workspace/shared/``（共享）
    - 释放时归还 slot 到池（温池）或删除 Pod（按需）
    """

    def __init__(
        self,
        *,
        # ── 新增: 共享 PVC 参数 ──
        shared_pvc_name: str = "",
        session_id: str = "",
        shared_pvc_access_mode: str = "ReadWriteMany",
        pod_name: str = "",
        # ── 懒缩容支持（由 Manager 注入，普通构造可省略）──
        agent_id: str = "",
        pool_size_provider: Any = None,
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
        self._assigned_pod_name: str = pod_name

        # ── 懒缩容：归还 slot 时与当前池大小比较 ──
        self._agent_id: str = agent_id
        self._pool_size_provider: Any = pool_size_provider

        # ── 覆盖工作目录为 session 子目录 ──
        self.workdir = f"{POD_WORKDIR}/sessions/{self._session_id}"

        # ── 更新 instructions：描述真实的共享 PVC 布局 ──
        # 不能直接用 DEFAULT_WORKSPACE_INSTRUCTIONS，因为它的
        # 模板把 skills/ 放在 workdir 下，而共享 PVC 下 skills
        # 在 /workspace/shared/skills/，与 workdir 不在同一路径。
        self.instructions = (
            "<workspace>你可以访问一个位于{workdir}的{backend}工作区，"
            "其目录结构如下："
            "\n\n```"
            "\n/workspace/"
            "\n├── shared/            # 所有会话共享"
            "\n│   ├── skills/        # 可复用的技能目录"
            "\n│   └── .mcp           # MCP 配置"
            "\n└── sessions/"
            "\n    └── <session>/    # 你的工作目录"
            "\n        ├── data/      # 卸载的多模态文件——系统管理"
            "\n        ├── sessions/  # 卸载的会话上下文与工具结果——系统管理"
            "\n        └── user-data/ # 用户工作目录"
            "\n            ├── scratch/   # 临时文件工作目录"
            "\n            ├── uploads/    # 用户上传的文件——自动转换为 .md"
            "\n            └── outputs/    # 最终交付物——完成的文件写到这里，不要询问"
            "\n```"
            "\n\n你的工作目录是{workdir}。"
            "这个工作区是你的个人工作环境，你有责任保持其整洁、结构清晰、"
            "并便于长期维护。"
            "\n\n### 文件管理"
            "\n- 临时和中间文件（草稿、实验、构建产物、调试脚本）放在"
            "`user-data/scratch/`。"
            "\n- 你生成的任务最终成果——报告、文档、代码、导出文件、图片——"
            "**必须**直接写入`user-data/outputs/`。不要询问用户保存位置，"
            "自动应用此规则。"
            "\n- 如果交付物最初在`scratch/`中开始（例如迭代过程中），"
            "完成后请将成品文件复制到`outputs/`。"
            "\n- 在脚本和命令中，优先使用相对路径（如`hello.txt`、"
            "`../outputs/report.md`），避免硬编码绝对路径。"
            "\n\n### 上传文件"
            "\n- 用户上传的文件会自动转换为 Markdown，转换结果是与原文件同名的 .md，"
            "存放在`user-data/uploads/`目录下（例如 report.xlsx → report.md）。"
            "\n- 分析上传文件时，必须优先读取同名 .md 版本——不要自行解析原始二进制文件"
            "（如 .xlsx/.docx/.pdf 等），也不要为了读取文件而 pip install 安装包。"
            "\n- 如果同名 .md 不存在，可以使用当前环境中已有的包自行解析原始二进制文件"
            "——但禁止安装新包。"
            "\n\n### 项目目录"
            "\n- 为每个任务或项目在工作目录下创建专属子目录。"
            "\n- 项目子目录命名要简洁、有描述性，并以创建日期的绝对时间前缀，"
            "例如`20240315_web-scraper`，以便长期可辨识。"
            "\n- 始终在项目根目录创建`README.md`，记录："
            "\n  - 项目内容概述"
            "\n  - 创建日期的绝对时间"
            "\n  - 有助于以后恢复工作的关键决策或上下文"
            "\n\n### 技能"
            "\n- 技能是位于/workspace/shared/skills/的共享资源。"
            "\n- 每个技能都有一个SKILL.md，包含完整说明——使用Read工具查看。"
            "\n\n### Python 环境"
            "\n- 沙箱预置了Python虚拟环境`/root/.agentscope/.venv`。"
            "\n- 执行Python脚本前，先激活该环境：`source /root/.agentscope/.venv/bin/activate`"
            "\n</workspace>"
        ).format(
            backend="Kubernetes-based (shared-PVC)",
            workdir=self.workdir,
        )

    # ── user-data paths (relative to workdir) ────────────────────

    @property
    def _user_data_dir(self) -> str:
        """``${workdir}/user-data`` — user working area root."""
        return self.get_backend().join_path(self.workdir, DEFAULT_USER_DATA_DIR)

    @property
    def _user_workspace_dir(self) -> str:
        """``${workdir}/user-data/scratch`` — temp file working directory."""
        return self.get_backend().join_path(
            self._user_data_dir,
            DEFAULT_USER_SCRATCH_DIR,
        )

    @property
    def _user_outputs_dir(self) -> str:
        """``${workdir}/user-data/outputs`` — final deliverables."""
        return self.get_backend().join_path(
            self._user_data_dir,
            DEFAULT_USER_OUTPUTS_DIR,
        )

    @property
    def _user_uploads_dir(self) -> str:
        """``${workdir}/user-data/uploads`` — user-uploaded files."""
        return self.get_backend().join_path(
            self._user_data_dir,
            DEFAULT_USER_UPLOADS_DIR,
        )

    async def _ensure_workspace_layout(self) -> None:
        """Create the standard workspace directories plus user-data/.

        The parent layout covers ``data/``, ``skills/``, ``sessions/``
        and the gateway home; on top of that this session workspace
        guarantees ``user-data/scratch/``, ``user-data/outputs/`` and
        ``user-data/uploads/`` exist so the model always has a writable
        working area, a destination for final deliverables and a home
        for user-uploaded files (used by the upload endpoint).
        """
        await super()._ensure_workspace_layout()
        backend = self.get_backend()
        await backend.exec_shell(
            [
                "mkdir",
                "-p",
                self._user_data_dir,
                self._user_workspace_dir,
                self._user_outputs_dir,
                self._user_uploads_dir,
            ],
            cwd="/",
        )

    # ── 覆盖 provisioning: 支持池 Pod 名 ─────────────────────

    async def _provision_backend(self) -> None:
        """创建或附加到 K8s Pod，支持温池预创建的 Pod。

        与父类的唯一区别：当 ``_assigned_pod_name`` 非空时，
        使用池 Pod 名而非从 workspace_id 派生。
        """
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_config

        if self._kubeconfig:
            await k8s_config.load_kube_config(
                config_file=self._kubeconfig,
            )
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                await k8s_config.load_kube_config()

        self._api_client = k8s_client.ApiClient()
        self._v1 = k8s_client.CoreV1Api(self._api_client)

        if self._assigned_pod_name:
            self._pod_name = self._assigned_pod_name
        else:
            self._pod_name = _k8s_safe_name(self.workspace_id)

        await self._ensure_namespace()
        await self._ensure_pvc()
        await self._ensure_pod()
        await self._wait_pod_running()

        self._backend = K8sBackend(
            api_client=self._api_client,
            namespace=self._namespace,
            pod_name=self._pod_name,
            container_name="workspace",
            workdir=POD_WORKDIR,
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
                    "agentscope.workspace.id": _k8s_safe_label(self.workspace_id),
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
                    "agentscope.workspace.id": _k8s_safe_label(self.workspace_id),
                },
            ),
            spec=k8s_client.V1PodSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_pod(self._namespace, pod)

    async def _teardown_backend(self) -> None:
        """温池模式：归还 slot；按需模式：删除 Pod。

        共享 PVC 由 agent 级别管理，不在 session 结束时清理。
        """
        from kubernetes_asyncio.client.rest import ApiException

        if self._v1 is not None and self._pod_name:
            if self._assigned_pod_name:
                # ── 温池：懒缩容判断 ──
                # slot 序号 >= 当前池大小时直接删除 Pod（热更新缩容后
                # 在下一次会话结束时自然收敛），否则 patch 归还池中。
                slot_index = _pool_slot_index(self._pod_name)
                target_size: int | None = None
                if slot_index is not None and self._pool_size_provider:
                    try:
                        target_size = await self._pool_size_provider(
                            self._agent_id,
                        )
                    except Exception:
                        # 池大小查询失败 → 保守归还，不误删
                        target_size = None
                if (
                    slot_index is not None
                    and target_size is not None
                    and slot_index >= target_size
                ):
                    # ── 懒缩容：删除超出池大小的 slot Pod ──
                    try:
                        await self._v1.delete_namespaced_pod(
                            self._pod_name,
                            self._namespace,
                        )
                        logger.info(
                            "SharedPvcK8sWorkspace: shrunk pool slot %r "
                            "(index=%d >= pool_size=%d)",
                            self._pod_name,
                            slot_index,
                            target_size,
                        )
                    except ApiException as e:
                        if e.status != 404:
                            logger.warning(
                                "SharedPvcK8sWorkspace: shrink slot %r "
                                "failed: %s",
                                self._pod_name,
                                e,
                            )
                else:
                    # ── patch label 归还 slot ──
                    try:
                        await self._v1.patch_namespaced_pod(
                            self._pod_name,
                            self._namespace,
                            {"metadata": {"labels": {
                                "agentscope.pool.slot": "available",
                            }}},
                        )
                        logger.info(
                            "SharedPvcK8sWorkspace: returned slot %r to pool",
                            self._pod_name,
                        )
                    except ApiException as e:
                        if e.status == 404:
                            logger.warning(
                                "SharedPvcK8sWorkspace: pool pod %r gone, "
                                "cannot return slot",
                                self._pod_name,
                            )
                        else:
                            logger.warning(
                                "SharedPvcK8sWorkspace: slot return "
                                "failed: %s",
                                e,
                            )
            else:
                # ── 按需：删除 session Pod ──
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
    """管理 :class:`SharedPvcK8sWorkspace` 实例 + 温池。

    与父类 :class:`K8sWorkspaceManager` 的区别：

    - 隔离策略固定为 ``PER_SESSION``（每个 session 独立 Pod）
    - PVC 名称由 ``user_id::agent_id`` hash 派生（agent 级共享）
    - 缓存 key 仍是 session-scoped workspace_id
    - 温池：预创建 Pod，快速分配，TTL 回收
    """

    # ── 池 label 常量 ──────────────────────────────────────
    POOL_LABEL_AGENT = "agentscope.pool.agent"
    POOL_LABEL_SLOT = "agentscope.pool.slot"
    POOL_SLOT_AVAILABLE = "available"

    # ── 池大小缓存 TTL（秒）──
    # 短 TTL 平衡热更新时效性与 Redis 往返开销
    POOL_SIZE_CACHE_TTL = 5.0

    def __init__(
        self,
        *,
        shared_pvc_access_mode: str = "ReadWriteMany",
        # ── 池化 ──
        max_active_pods: int = 5,
        pool_wait_timeout: float = 60.0,
        # ── 池配置数据源（Redis） ──
        # 连接参数由工厂注入（来自 AppConfig），禁止直读裸环境变量
        redis_host: str = "localhost",
        redis_port: int = 6379,
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

        # ── 池化 ──
        self._max_active_pods = max_active_pods
        self._pool_wait_timeout = pool_wait_timeout
        self._redis_host = redis_host
        self._redis_port = redis_port
        # 懒加载长连接 + 池大小短 TTL 缓存，避免每个新会话
        # 都新建/关闭一条 Redis 连接
        self._redis_client: Any = None
        self._redis_lock = asyncio.Lock()
        self._pool_size_cache: dict[str, tuple[float, int]] = {}
        # K8s 客户端（懒加载，用于池管理）
        self._k8s_api_client: Any = None
        self._k8s_v1: Any = None
        self._k8s_lock = asyncio.Lock()

    # ── 覆盖 get_workspace ──────────────────────────────────────

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> SharedPvcK8sWorkspace:
        """返回 session-scoped workspace。温池模式先拿 slot。

        Args:
            user_id (`str`): 用户 ID。
            agent_id (`str`): 智能体 ID（用于生成 PVC 名和池 key）。
            session_id (`str`): 会话 ID（用于 Pod 名和 workdir 子目录）。
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

        # agent 级 PVC 名称
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

        # ── 缓存未命中 ──────────────────────────────────────────
        pool_size = await self._get_pool_size(agent_id)
        pod_name = ""

        if pool_size > 0:
            # 确保池 Pod 存在
            await self._ensure_pool(agent_hash, agent_id)
            # 从池中获取 slot
            pod_name = await self._acquire_slot(
                agent_hash,
                session_id,
            )
            logger.info(
                "SharedPvcK8sWorkspaceManager: acquired slot %r "
                "for session %r (agent=%r)",
                pod_name,
                session_id,
                agent_id,
            )

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
                pod_name=pod_name,
                agent_id=agent_id,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws  # type: ignore[return-value]

    async def _build_and_start(
        self,
        *,
        workspace_id: str | None,
        shared_pvc_name: str,
        session_id: str,
        pod_name: str = "",
        agent_id: str = "",
    ) -> SharedPvcK8sWorkspace:
        """构造 :class:`SharedPvcK8sWorkspace` 并初始化。

        Args:
            pod_name: 非空表示使用温池 Pod，空表示按需创建。
            agent_id: 用于归还 slot 时的懒缩容判断。
        """
        from agentscope.workspace._utils import DEFAULT_WORKSPACE_INSTRUCTIONS

        ws = SharedPvcK8sWorkspace(
            workspace_id=workspace_id,
            shared_pvc_name=shared_pvc_name,
            session_id=session_id,
            shared_pvc_access_mode=self._shared_pvc_access_mode,
            pod_name=pod_name,
            agent_id=agent_id,
            pool_size_provider=self._get_pool_size,
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

    # ── 池管理 ──────────────────────────────────────────────

    async def _k8s_connect(self) -> None:
        """懒加载 K8s 客户端（用于池 Pod 管理）。"""
        if self._k8s_v1 is not None:
            return
        async with self._k8s_lock:
            if self._k8s_v1 is not None:
                return
            from kubernetes_asyncio import client as k8s_client
            from kubernetes_asyncio import config as k8s_config

            if self._kubeconfig:
                await k8s_config.load_kube_config(
                    config_file=self._kubeconfig,
                )
            else:
                try:
                    k8s_config.load_incluster_config()
                except k8s_config.ConfigException:
                    await k8s_config.load_kube_config()
            self._k8s_api_client = k8s_client.ApiClient()
            self._k8s_v1 = k8s_client.CoreV1Api(self._k8s_api_client)

    async def _get_pool_size(self, agent_id: str) -> int:
        """获取 agent 维度的池大小。

        优先级：Redis per-agent → .env 全局默认。
        短 TTL 进程内缓存 + 懒加载长连接，避免每个新会话都
        新建/关闭一条 Redis 连接；Redis 异常时静默降级。
        """
        now = time.monotonic()
        cached = self._pool_size_cache.get(agent_id)
        if cached is not None and now - cached[0] < self.POOL_SIZE_CACHE_TTL:
            return cached[1]

        try:
            r = await self._get_redis()
            val = await r.hget(
                f"agentscope:pool:{agent_id}",
                "max_active_pods",
            )
            if val is not None:
                result = int(val)
                self._pool_size_cache[agent_id] = (now, result)
                return result
        except Exception:
            # Redis 不可用 → 不缓存，回退全局默认
            pass
        return self._max_active_pods

    async def _get_redis(self) -> Any:
        """懒加载复用的 Redis 长连接。

        连接参数由工厂注入（来自 AppConfig），与消息总线/存储同源；
        连接异常由 redis-py 自动重连，失败在调用侧捕获降级。
        """
        if self._redis_client is None:
            async with self._redis_lock:
                if self._redis_client is None:
                    import redis.asyncio as aioredis

                    self._redis_client = aioredis.Redis(
                        host=self._redis_host,
                        port=self._redis_port,
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    )
        return self._redis_client

    def invalidate_pool_size(self, agent_id: str) -> None:
        """失效指定 agent 的池大小进程内缓存。

        管理 API（PUT/DELETE /agents/{id}/concurrency）写入 Redis 后
        调用，让新配置立即生效而无需等缓存 TTL 过期。
        """
        self._pool_size_cache.pop(agent_id, None)

    async def _ensure_pool(
        self,
        agent_hash: str,
        agent_id: str,
    ) -> None:
        """确保 agent 的池 Pod 已创建并运行。

        检查当前池状态，补建缺少的 Pod。
        首次调用时还会确保共享 PVC 存在。
        """
        await self._k8s_connect()
        pool_size = await self._get_pool_size(agent_id)
        if pool_size <= 0:
            return

        # ── 确保共享 PVC 存在（池 Pod 依赖它） ──
        shared_pvc_name = _k8s_safe_name(agent_hash)
        await self._ensure_shared_pvc(shared_pvc_name, agent_id)

        # 查询现有池 Pod
        from kubernetes_asyncio.client.rest import ApiException

        try:
            pods = await self._k8s_v1.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=(
                    f"{self.POOL_LABEL_AGENT}={agent_hash}"
                ),
            )
        except ApiException as e:
            logger.warning(
                "SharedPvcK8sWorkspaceManager: list pool pods failed: %s",
                e,
            )
            return

        existing_names = {
            p.metadata.name
            for p in pods.items
            if p.metadata is not None
        }

        # 补建缺失的 Pod
        for i in range(pool_size):
            pod_name = f"as-ws-{agent_hash}-{i}"
            if pod_name in existing_names:
                continue
            logger.info(
                "SharedPvcK8sWorkspaceManager: creating pool pod %r "
                "(agent=%r)",
                pod_name,
                agent_id,
            )
            await self._create_pool_pod(pod_name, agent_hash, agent_id)

    async def _ensure_shared_pvc(self, pvc_name: str, agent_id: str) -> None:
        """确保共享 PVC 存在，不存在则创建。

        逻辑与 :meth:`SharedPvcK8sWorkspace._ensure_pvc` 一致。
        """
        await self._k8s_connect()
        from kubernetes_asyncio.client.rest import ApiException

        try:
            pvc = await self._k8s_v1.read_namespaced_persistent_volume_claim(
                pvc_name,
                self._namespace,
            )
            if pvc.metadata and pvc.metadata.deletion_timestamp is not None:
                logger.info(
                    "SharedPvcK8sWorkspaceManager: PVC %r is being "
                    "deleted, waiting...",
                    pvc_name,
                )
                # 等待删除完成
                import asyncio as _asyncio

                deadline = _asyncio.get_event_loop().time() + 120.0
                while _asyncio.get_event_loop().time() < deadline:
                    try:
                        await self._k8s_v1.read_namespaced_persistent_volume_claim(
                            pvc_name,
                            self._namespace,
                        )
                    except ApiException as e2:
                        if e2.status == 404:
                            break
                        raise
                    await _asyncio.sleep(2)
                await self._create_shared_pvc(pvc_name, agent_id)
        except ApiException as e:
            if e.status == 404:
                await self._create_shared_pvc(pvc_name, agent_id)
            elif e.status == 409:
                # 并发创建的竞态，PVC 已存在
                return
            else:
                raise

    async def _create_shared_pvc(self, pvc_name: str, agent_id: str) -> None:
        """创建共享 PVC。"""
        await self._k8s_connect()
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio.client.rest import ApiException

        access_modes = [self._shared_pvc_access_mode]
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
                    "agentscope.agent_id": _k8s_safe_label(agent_id),
                },
            ),
            spec=k8s_client.V1PersistentVolumeClaimSpec(**spec_kwargs),
        )
        try:
            await self._k8s_v1.create_namespaced_persistent_volume_claim(
                self._namespace,
                pvc,
            )
        except ApiException as e:
            if e.status == 409:
                # 并发创建竞态，PVC 已由其他实例创建
                return
            raise
        logger.info(
            "SharedPvcK8sWorkspaceManager: created shared PVC %r",
            pvc_name,
        )

    async def _create_pool_pod(
        self,
        pod_name: str,
        agent_hash: str,
        agent_id: str = "",
    ) -> None:
        """创建单个池 Pod。

        与 session Pod 的区别：
        - label 带 pool 标记
        - working_dir 固定为 /workspace
        - 初始 slot=available
        """
        await self._k8s_connect()
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio.client.rest import ApiException

        # 容器 spec（与 SharedPvcK8sWorkspace._create_pod 对齐）
        container_env = None
        if self._env:
            container_env = [
                k8s_client.V1EnvVar(name=k, value=v)
                for k, v in self._env.items()
            ]

        container = k8s_client.V1Container(
            name="workspace",
            image=self._image,
            image_pull_policy=self._image_pull_policy,
            command=["sleep", "infinity"],
            working_dir=POD_WORKDIR,
            ports=[
                k8s_client.V1ContainerPort(
                    container_port=self._gateway_port,
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

        # 共享 PVC volume
        shared_pvc_name = _k8s_safe_name(agent_hash)
        volumes = [
            k8s_client.V1Volume(
                name="workspace-data",
                persistent_volume_claim=(
                    k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=shared_pvc_name,
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
                name=pod_name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "agentscope",
                    "agentscope.workspace": "true",
                    "agentscope.agent_id": _k8s_safe_label(agent_id),
                    self.POOL_LABEL_AGENT: agent_hash,
                    self.POOL_LABEL_SLOT: self.POOL_SLOT_AVAILABLE,
                },
            ),
            spec=k8s_client.V1PodSpec(**spec_kwargs),
        )
        try:
            await self._k8s_v1.create_namespaced_pod(self._namespace, pod)
        except ApiException as e:
            if e.status == 409:
                # 并发创建竞态，Pod 已由其他请求创建
                return
            raise

    async def _acquire_slot(
        self,
        agent_hash: str,
        session_id: str,
    ) -> str:
        """从池中获取一个可用 slot。

        通过 K8s label selector 查找 available Pod，
        用 resourceVersion 乐观锁绑定到 session。
        池满时退避等待，超时后报错。

        Returns:
            获取到的 Pod 名称。

        Raises:
            RuntimeError: 超时未获取到 slot。
        """
        await self._k8s_connect()
        from kubernetes_asyncio.client.rest import ApiException

        label_available = (
            f"{self.POOL_LABEL_AGENT}={agent_hash},"
            f"{self.POOL_LABEL_SLOT}={self.POOL_SLOT_AVAILABLE}"
        )

        deadline = time.monotonic() + self._pool_wait_timeout
        backoff = 1.0

        while time.monotonic() < deadline:
            try:
                pods = await self._k8s_v1.list_namespaced_pod(
                    namespace=self._namespace,
                    label_selector=label_available,
                )
            except ApiException as e:
                logger.warning(
                    "SharedPvcK8sWorkspaceManager: list available "
                    "slots failed: %s",
                    e,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue

            available = [
                p
                for p in pods.items
                if p.status is not None
                and p.status.phase == "Running"
                and p.metadata is not None
            ]

            for pod in available:
                pod_name = pod.metadata.name
                try:
                    # 带 resourceVersion 前置条件的条件 patch：
                    # 并发抢同一 slot 时版本不匹配返回 409，试下一个，
                    # 避免后写覆盖前写导致两个会话绑定同一 Pod
                    await self._k8s_v1.patch_namespaced_pod(
                        pod_name,
                        self._namespace,
                        {
                            "metadata": {
                                "resourceVersion": (
                                    pod.metadata.resource_version
                                ),
                                "labels": {
                                    self.POOL_LABEL_SLOT: _k8s_safe_label(
                                        session_id,
                                    ),
                                },
                            },
                        },
                    )
                    return pod_name  # type: ignore[no-any-return]
                except ApiException as e:
                    if e.status == 409:
                        # 被其他实例抢了，试下一个
                        continue
                    raise

            # 没有可用 slot → 退避等待
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

        raise RuntimeError("沙箱资源已满，请稍后重试")

    async def cleanup_pool(self, agent_id: str) -> None:
        """删除 agent 对应的所有池资源（Pod + PVC）。

        在删除智能体时调用，清理该 agent 的温池 Pod 和共享 PVC。
        通过 ``agentscope.agent_id`` 标签精确匹配，不依赖 hash。
        不抛异常，仅打日志。
        """
        await self._k8s_connect()
        from kubernetes_asyncio.client.rest import ApiException

        agent_label = f"agentscope.agent_id={_k8s_safe_label(agent_id)}"

        # 1. 删除所有池 Pod
        try:
            pods = await self._k8s_v1.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=(f"{self.POOL_LABEL_AGENT},{agent_label}"),
            )
            for pod in (pods.items or []):
                if pod.metadata is None:
                    continue
                try:
                    await self._k8s_v1.delete_namespaced_pod(
                        pod.metadata.name,
                        self._namespace,
                    )
                    logger.info(
                        "SharedPvcK8sWorkspaceManager: deleted pool pod "
                        "%r (agent=%r)",
                        pod.metadata.name,
                        agent_id,
                    )
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(
                            "SharedPvcK8sWorkspaceManager: delete pool "
                            "pod %r failed: %s",
                            pod.metadata.name,
                            e,
                        )
        except ApiException as e:
            logger.warning(
                "SharedPvcK8sWorkspaceManager: list pool pods for "
                "cleanup failed: %s",
                e,
            )

        # 2. 删除共享 PVC
        try:
            pvcs = await self._k8s_v1.list_namespaced_persistent_volume_claim(
                namespace=self._namespace,
                label_selector=agent_label,
            )
            for pvc in (pvcs.items or []):
                if pvc.metadata is None:
                    continue
                try:
                    await self._k8s_v1.delete_namespaced_persistent_volume_claim(
                        pvc.metadata.name,
                        self._namespace,
                    )
                    logger.info(
                        "SharedPvcK8sWorkspaceManager: deleted shared PVC "
                        "%r (agent=%r)",
                        pvc.metadata.name,
                        agent_id,
                    )
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(
                            "SharedPvcK8sWorkspaceManager: delete shared PVC "
                            "%r failed: %s",
                            pvc.metadata.name,
                            e,
                        )
        except ApiException as e:
            logger.warning(
                "SharedPvcK8sWorkspaceManager: list PVCs for "
                "cleanup failed: %s",
                e,
            )
