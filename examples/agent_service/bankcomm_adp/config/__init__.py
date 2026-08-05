# -*- coding: utf-8 -*-
"""配置包：按模块拆分配置读取。

结构：
    config/
    ├── __init__.py               # 汇总导出（兼容 from ..config import ...）
    ├── base.py                   # 公共加载（.env / config.yaml）与通用工具
    ├── settings_config.py        # 全局配置（Settings）
    ├── audit_config.py           # 审计留痕配置（AuditConfig）
    └── cross_search_config.py    # cross_search_tool 专属配置

各配置统一采用 ``@dataclass + from_yaml()`` 读取模式，并通过
``get_xxx_config()`` 工厂函数获取（见 :class:`Settings` 与
:class:`AuditConfig`、:class:`CrossSearchConfig`）。

读取优先级：config.yaml（含 $VAR 环境变量展开）> 代码默认值。
"""
from .audit_config import AuditConfig, get_audit_config
from .base import load_config_yaml
from .cross_search_config import CrossSearchConfig, get_cross_search_config
from .settings_config import Settings, get_settings

__all__ = [
    "Settings",
    "AuditConfig",
    "CrossSearchConfig",
    "get_audit_config",
    "get_cross_search_config",
    "get_settings",
    "load_config_yaml",
]
