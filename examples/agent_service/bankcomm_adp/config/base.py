# -*- coding: utf-8 -*-
"""配置公共基座：.env / config.yaml 加载，以及通用工具函数。

配置以单一 ``config.yaml`` 为主源；字符串值支持 ``$VAR`` / ``${VAR}``
环境变量引用展开（取值来源为 ``.env`` 文件或进程环境变量）。
读取优先级（高到低）：:

    ① config.yaml（含 $VAR 环境变量展开）   —— 主配置源
    ② 代码默认值                             —— 最低

本模块只承载公共加载与解析工具；各配置项按模块拆分：
- 全局配置：:mod:`bankcomm_adp.config.settings_config`
- 审计配置：:mod:`bankcomm_adp.config.audit_config`
- 跨知识搜索配置：:mod:`bankcomm_adp.config.cross_search_config`
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# examples/agent_service/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 配置数据文件（单一）：agent_service/config.yaml
CONFIG_FILE = BASE_DIR / "config.yaml"
# .env 文件（可选，自动加载）
DOTENV_FILE = BASE_DIR / ".env"


@lru_cache
def _load_dotenv_once() -> None:
    """从 ``agent_service/.env`` 加载环境变量（首次调用缓存）。

    按 ``KEY=VALUE`` 逐行解析，使用 ``setdefault``，因此**不覆盖**已存在的
    环境变量（外部注入的环境变量优先级更高）。``#`` 注释行与无 ``=`` 的行跳过。
    """
    import os

    if not DOTENV_FILE.is_file():
        return
    for raw_line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache
def load_config_yaml() -> dict[str, Any]:
    """加载 ``config.yaml``（不存在则返回空 dict）。"""
    if not CONFIG_FILE.is_file():
        return {}
    with CONFIG_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def expand_env_vars(value: Any) -> Any:
    """展开 YAML 值中的 ``$VAR`` / ``${VAR}`` 环境变量引用。

    支持：
        - 字符串 ``"$CROSS_SEARCH_API_URL"`` / ``"${VAR}"``
        - 列表、字典中的字符串（递归展开）

    未定义的环境变量原样保留（不报错）。
    """
    import os
    import re

    _load_dotenv_once()

    pattern = re.compile(r"\$(?:{(\w+)}|(\w+))")

    def _expand(text: str) -> str:
        def _repl(m: "re.Match[str]") -> str:
            key = m.group(1) or m.group(2)
            matched = m.group(0) or ""
            return os.environ.get(key, matched)

        return pattern.sub(_repl, text)

    if isinstance(value, str):
        return _expand(value)
    if isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {str(k): expand_env_vars(v) for k, v in value.items()}
    return value


def resolve_path(value: object) -> Path:
    """把配置中的路径归一化为绝对路径（相对路径基于 ``BASE_DIR`` 解析）。

    这样无论从哪个工作目录启动，路径都一致指向 ``config.yaml`` 所在目录。
    """
    p = Path(str(value))
    return p if p.is_absolute() else BASE_DIR / p


def split_list(val: Any) -> list[str]:
    """把 YAML 列表（或兼容的逗号字符串）归一化为 str 列表。"""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(item) for item in val]
    if isinstance(val, str):
        return [item.strip() for item in val.split(",") if item.strip()]
    return [str(val)]


def str_dict(val: Any) -> dict[str, str]:
    """把 YAML 映射归一化为 str->str 字典。"""
    if not isinstance(val, dict):
        return {}
    return {str(k): str(v) for k, v in val.items()}


def yaml_section(data: dict[str, Any], path: list[str]) -> dict[str, Any]:
    """按层级路径取 YAML 子节点。"""
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def yaml_val(
    data: dict[str, Any],
    path: list[str],
    default: Any = None,
) -> Any:
    """按层级路径取 YAML 标量值。"""
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return default
        if key not in node:
            return default
        node = node.get(key)
    return node


def int_or(raw: str | None, default: Any) -> int:
    """整型解析，非法时回退默认值。"""
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    try:
        return int(default)
    except (TypeError, ValueError):
        return 0


def float_or(raw: str | None, default: Any) -> float | None:
    """浮点型解析，非法时回退默认值（允许 None）。"""
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    if default is None:
        return None
    try:
        return float(default)
    except (TypeError, ValueError):
        return None
