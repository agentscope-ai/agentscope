# -*- coding: utf-8 -*-
"""K8s 沙箱工作区管理模块。

提供预配置的 :class:`K8sWorkspaceManager` 工厂函数，
所有配置通过 ``ADP_K8S_*`` 环境变量注入，无需修改 AgentScope 框架源码。

使用方式::

    from bocomadp.workspace import build_k8s_workspace_manager, is_k8s_enabled

    if is_k8s_enabled():
        manager = build_k8s_workspace_manager()

    app = create_app(
        ...,
        workspace_manager=manager,
    )
"""

from .factory import build_k8s_workspace_manager
from .config import K8sWorkspaceConfig, get_k8s_workspace_config, is_k8s_enabled
from .whitelist import WhitelistWorkspaceManager

__all__ = [
    "build_k8s_workspace_manager",
    "K8sWorkspaceConfig",
    "get_k8s_workspace_config",
    "is_k8s_enabled",
    "WhitelistWorkspaceManager",
]
