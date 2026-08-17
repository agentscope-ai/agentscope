# -*- coding: utf-8 -*-
"""K8s 沙箱工作区配置。

所有配置项通过环境变量注入，统一使用 ``ADP_K8S_`` 前缀。

必需配置（无默认值，缺少时工厂函数会报错）：
    - ``ADP_K8S_KUBECONFIG``: kubeconfig 文件路径（平台不在集群内）

可选配置：
    - ``ADP_K8S_NAMESPACE``: K8s 命名空间，默认 ``"agentscope"``
    - ``ADP_K8S_IMAGE``: 沙箱镜像地址，默认 ``"python:3.11-slim"``
                        （启用预构建镜像可跳过 bootstrap，大幅加速启动）
    - ``ADP_K8S_STORAGE_CLASS``: PVC 使用的 StorageClass
    - ``ADP_K8S_STORAGE_SIZE``: PVC 大小，默认 ``"10Gi"``
    - ``ADP_K8S_DELETE_PVC_ON_CLOSE``: 关闭时是否删除 PVC
    - ``ADP_K8S_TTL``: 空闲超时（秒），默认 ``1800``（30 分钟）
    - ``ADP_K8S_SWEEP_INTERVAL``: 回收扫描间隔（秒），默认 ``300``
    - ``ADP_K8S_MAX_ACTIVE_PODS``: 温池大小，默认 ``5``（0=不池化）
    - ``ADP_K8S_POOL_WAIT_TIMEOUT``: 池耗尽等待超时（秒），默认 ``60``
    - ``ADP_K8S_RESOURCES_CPU_REQUEST``: CPU 请求，默认 ``"500m"``
    - ``ADP_K8S_RESOURCES_CPU_LIMIT``: CPU 限制
    - ``ADP_K8S_RESOURCES_MEM_REQUEST``: 内存请求，默认 ``"512Mi"``
    - ``ADP_K8S_RESOURCES_MEM_LIMIT``: 内存限制
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

# examples/agent_service/ 目录
_BASE_DIR = Path(__file__).resolve().parent.parent.parent


@lru_cache
def _load_dotenv() -> None:
    """从 ``_BASE_DIR/.env`` 加载配置（不覆盖已有环境变量）。"""
    import os

    env_file = _BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str | None = None) -> str | None:
    """读取 ``ADP_K8S_`` 前缀的环境变量。"""
    import os

    _load_dotenv()
    return os.environ.get(f"ADP_K8S_{name}", default)


def _env_int(name: str, default: int) -> int:
    val = _env(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = _env(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class K8sWorkspaceConfig:
    """K8s 沙箱工作区完整配置。

    各字段均从环境变量读取并带有合理默认值；``kubeconfig`` 无默认值，
    缺少时 :func:`build_k8s_workspace_manager` 会抛出 ``ValueError``。
    """

    # ── 连接 ──
    kubeconfig: str = field(
        default_factory=lambda: _env("KUBECONFIG", ""),
    )
    namespace: str = field(
        default_factory=lambda: _env("NAMESPACE", "agentscope"),
    )

    # ── 镜像 ──
    image: str = field(
        default_factory=lambda: _env("IMAGE", "python:3.11-slim"),
    )
    image_pull_policy: str = field(
        default_factory=lambda: _env("IMAGE_PULL_POLICY", "IfNotPresent"),
    )

    # ── 资源限制 ──
    cpu_request: str = field(
        default_factory=lambda: _env("RESOURCES_CPU_REQUEST", "500m"),
    )
    cpu_limit: str = field(
        default_factory=lambda: _env("RESOURCES_CPU_LIMIT", "2"),
    )
    memory_request: str = field(
        default_factory=lambda: _env("RESOURCES_MEM_REQUEST", "512Mi"),
    )
    memory_limit: str = field(
        default_factory=lambda: _env("RESOURCES_MEM_LIMIT", "2Gi"),
    )

    # ── 存储 ──
    storage_class: str | None = field(
        default_factory=lambda: _env("STORAGE_CLASS"),
    )
    storage_size: str = field(
        default_factory=lambda: _env("STORAGE_SIZE", "10Gi"),
    )
    delete_pvc_on_close: bool = field(
        default_factory=lambda: _env_bool("DELETE_PVC_ON_CLOSE", False),
    )

    # ── 共享 PVC（PER_SESSION Pod + agent 级共享存储）──
    shared_pvc_enabled: bool = field(
        default_factory=lambda: _env_bool("SHARED_PVC_ENABLED", False),
    )
    shared_pvc_access_mode: str = field(
        default_factory=lambda: _env("SHARED_PVC_ACCESS_MODE", "ReadWriteMany"),
    )

    # ── 缓存 ──
    ttl: float = field(
        default_factory=lambda: _env_float("TTL", 1800.0),
    )
    sweep_interval: float = field(
        default_factory=lambda: _env_float("SWEEP_INTERVAL", 300.0),
    )

    # ── 池化 ──
    max_active_pods: int = field(
        default_factory=lambda: _env_int("MAX_ACTIVE_PODS", 5),
    )
    pool_wait_timeout: float = field(
        default_factory=lambda: _env_float("POOL_WAIT_TIMEOUT", 60.0),
    )

    @property
    def resources(self) -> dict[str, Any] | None:
        """组装 K8s ResourceRequirements dict。"""
        req: dict[str, str] = {}
        lim: dict[str, str] = {}
        if self.cpu_request or self.memory_request:
            if self.cpu_request:
                req["cpu"] = self.cpu_request
            if self.memory_request:
                req["memory"] = self.memory_request
        if self.cpu_limit or self.memory_limit:
            if self.cpu_limit:
                lim["cpu"] = self.cpu_limit
            if self.memory_limit:
                lim["memory"] = self.memory_limit
        result: dict[str, Any] = {}
        if req:
            result["requests"] = req
        if lim:
            result["limits"] = lim
        return result if result else None

    def validate(self) -> None:
        """检查必需配置项是否已提供。

        Raises:
            ValueError: 缺少 ``kubeconfig``。
        """
        if not self.kubeconfig:
            raise ValueError(
                "环境变量 ADP_K8S_KUBECONFIG 未设置——"
                "K8s 沙箱需要 kubeconfig 文件路径",
            )


@lru_cache
def is_k8s_enabled() -> bool:
    """是否启用 K8s 沙箱模式（读取 ``ADP_K8S_ENABLED``，默认启用）。

    生产环境默认开启；本地开发可设置 ``ADP_K8S_ENABLED=false``
    退回到 LocalWorkspaceManager。仅读取环境变量，不触发配置校验。
    """
    return _env_bool("ENABLED", True)


@lru_cache
def get_k8s_workspace_config() -> K8sWorkspaceConfig:
    """获取 K8s 沙箱配置单例并校验。"""
    cfg = K8sWorkspaceConfig()
    cfg.validate()
    return cfg
