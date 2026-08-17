# -*- coding: utf-8 -*-
"""K8sWorkspaceManager 工厂函数。

根据环境变量配置创建预配置的 workspace 管理器实例。

使用方式::

    from bocomadp.workspace import build_k8s_workspace_manager

    manager = build_k8s_workspace_manager()
"""
from __future__ import annotations

from agentscope.app.workspace_manager import (
    IsolationPolicy,
    K8sWorkspaceManager,
)
from bocomadp.config import get_app_config

from ._shared_pvc import SharedPvcK8sWorkspaceManager
from .config import get_k8s_workspace_config


def build_k8s_workspace_manager() -> (
    K8sWorkspaceManager | SharedPvcK8sWorkspaceManager
):
    """创建预配置的 K8s 沙箱管理器。

    隔离策略根据 ``ADP_K8S_SHARED_PVC_ENABLED`` 决定：

    - **共享 PVC 模式** （``SHARED_PVC_ENABLED=true``）：
      创建 :class:`SharedPvcK8sWorkspaceManager`。
      每个会话独立 Pod，所有会话共享一个 agent 级 RWX PVC，
      以子目录隔离 session 数据。

    - **传统模式** （默认）：
      创建 :class:`K8sWorkspaceManager`，``PER_AGENT`` 隔离。
      每个智能体一个 Pod + PVC，所有会话排队使用（RWO）。

    所有 K8s 连接 / 资源 / 存储参数从环境变量 ``ADP_K8S_*`` 读取，
    详见 :class:`K8sWorkspaceConfig`。

    Returns:
        已配置但未启动的 workspace 管理器。
        需作为 ``async with`` 上下文或传入 ``create_app()`` 使用。

    Raises:
        ValueError: 缺少必需的 ``ADP_K8S_KUBECONFIG`` 环境变量。
    """
    cfg = get_k8s_workspace_config()

    if cfg.shared_pvc_enabled:
        # Redis 连接参数与消息总线/存储同源（AppConfig），
        # 不直读裸 REDIS_HOST/REDIS_PORT 环境变量
        redis_cfg = get_app_config().redis
        return SharedPvcK8sWorkspaceManager(
            shared_pvc_access_mode=cfg.shared_pvc_access_mode,
            # ── K8s 连接 ──
            kubeconfig=cfg.kubeconfig,
            namespace=cfg.namespace,
            # ── Pod 配置 ──
            image=cfg.image,
            image_pull_policy=cfg.image_pull_policy,
            resources=cfg.resources,
            # ── 存储 ──
            storage_class=cfg.storage_class,
            storage_size=cfg.storage_size,
            delete_pvc_on_close=cfg.delete_pvc_on_close,
            # ── TTL 缓存 ──
            ttl=cfg.ttl,
            sweep_interval=cfg.sweep_interval,
            # ── 池化 ──
            max_active_pods=cfg.max_active_pods,
            pool_wait_timeout=cfg.pool_wait_timeout,
            redis_host=redis_cfg.host,
            redis_port=redis_cfg.port,
        )

    return K8sWorkspaceManager(
        isolation=IsolationPolicy.PER_AGENT,
        # ── K8s 连接 ──
        kubeconfig=cfg.kubeconfig,
        namespace=cfg.namespace,
        # ── Pod 配置 ──
        image=cfg.image,
        image_pull_policy=cfg.image_pull_policy,
        resources=cfg.resources,
        # ── 存储 ──
        storage_class=cfg.storage_class,
        storage_size=cfg.storage_size,
        delete_pvc_on_close=cfg.delete_pvc_on_close,
        # ── TTL 缓存 ──
        ttl=cfg.ttl,
        sweep_interval=cfg.sweep_interval,
    )
