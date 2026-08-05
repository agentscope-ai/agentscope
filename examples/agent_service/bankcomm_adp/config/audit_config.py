# -*- coding: utf-8 -*-
"""审计留痕（Audit）配置模块。

与 ``cross_search_config.py`` 对称，负责从单一 ``config.yaml`` 中提取
``audit`` 节点（``enabled`` / ``log_path``）。

读取优先级（高到低）：:

    ① config.yaml 的 audit 节点
    ② 代码默认值
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
        """从 ``config.yaml`` 的 ``audit`` 节点构建配置。"""
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
    """返回审计留痕配置（每次读取最新 YAML）。"""
    return AuditConfig.from_yaml()
