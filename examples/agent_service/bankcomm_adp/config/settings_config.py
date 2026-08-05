# -*- coding: utf-8 -*-
"""全局配置模块（Settings）。

与 ``audit_config.py``、``cross_search_config.py`` 对称，负责从单一
``config.yaml`` 中提取全局配置（``app_name`` / ``workspace_dir``）。

配置值通过 ``$VAR`` / ``${VAR}`` 环境变量引用展开（见
:func:`base.expand_env_vars`）。读取优先级（高到低）：:

    ① config.yaml 全局节点（含 $VAR 展开）
    ② 代码默认值
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import BASE_DIR, expand_env_vars, load_config_yaml, resolve_path, yaml_val


@dataclass
class Settings:
    """扩展包全局配置（config.yaml / 环境变量展开）。

    与 :class:`AuditConfig`、:class:`CrossSearchConfig` 采用相同的读取模式：
    由 ``from_yaml`` 一次性构建，字符串值支持 ``$VAR`` 展开。
    """

    # ---------- 服务 ----------
    app_name: str = "交通银行智能体平台"
    # ---------- 运行时目录 ----------
    workspace_dir: Path = field(
        default_factory=lambda: BASE_DIR / "workspaces",
    )

    @classmethod
    def from_yaml(cls) -> "Settings":
        """从 ``config.yaml`` 全局节点构建配置。"""
        data = expand_env_vars(load_config_yaml())

        return cls(
            app_name=(
                str(yaml_val(data, ["app_name"], ""))
                or "交通银行智能体平台"
            ),
            workspace_dir=resolve_path(
                yaml_val(data, ["workspace_dir"])
                or BASE_DIR / "workspaces",
            ),
        )


def get_settings() -> Settings:
    """返回全局配置（每次读取最新 YAML）。"""
    return Settings.from_yaml()
