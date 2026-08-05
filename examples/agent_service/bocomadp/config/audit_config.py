# -*- coding: utf-8 -*-
"""审计留痕配置 AuditConfig（``config.yaml`` 的 ``audit`` 节点）。

对应设计文档 ``config/audit_config.py``。``get_audit_config()`` 每次调用
重新解析，修改 ``config.yaml`` 后即时生效（热加载），配合
``middleware/factory.py`` 在每次 agent 组装时动态开启/关闭审计。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import BASE_DIR, load_config_yaml, resolve_path, yaml_section, yaml_val


@dataclass
class AuditConfig:
    """审计留痕配置分组。

    从 ``config.yaml`` 的 ``audit`` 节点构建，字段名与 YAML 键一一对应。
    """

    # 审计留痕开关：记录每次 agent 调用（谁/何时/用了哪些工具/输出摘要）
    enabled: bool = True
    # 日志路径（JSONL）
    log_path: Path = field(
        default_factory=lambda: BASE_DIR / "logs" / "audit.jsonl",
    )

    @classmethod
    def from_yaml(cls) -> "AuditConfig":
        """从 ``config.yaml`` 的 ``audit`` 节点构建配置。

        ``enabled`` 缺省为 ``True``；``log_path`` 经 ``resolve_path``
        归一化为绝对路径，缺省为 ``BASE_DIR / "logs" / "audit.jsonl"``。
        """
        data = load_config_yaml()
        section = yaml_section(data, ["audit"])

        return cls(
            enabled=bool(
                section.get("enabled")
                if section.get("enabled") is not None
                else True,
            ),
            log_path=resolve_path(
                yaml_val(data, ["audit", "log_path"])
                or BASE_DIR / "logs" / "audit.jsonl",
            ),
        )


def get_audit_config() -> AuditConfig:
    """返回审计留痕配置（每次读取最新 YAML，热加载入口）。"""
    return AuditConfig.from_yaml()
