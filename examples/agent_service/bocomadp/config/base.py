# -*- coding: utf-8 -*-
"""公共配置加载层（对应设计文档 ``config/base.py``）。

职责：
- 路径定位（``BASE_DIR`` / ``CONFIG_YAML_FILE`` / ``DOTENV_FILE``），与启动工作目录无关；
- ``.env`` 加载（``_load_dotenv_once``，进程内只加载一次，不覆盖外部环境变量）；
- YAML 读取（``load_config_yaml``，缓存原始解析结果）；
- 环境变量展开（``expand_env_vars``，支持 ``$VAR`` / ``${VAR}`` 两种写法）；
- 类型归一化工具（``resolve_path`` / ``yaml_section`` / ``yaml_val``）。

读取优先级（高 → 低）：

    ① 进程环境变量（os.environ）
    ② .env 文件（agent_service/.env）
    ③ config.yaml（含 $VAR 环境变量展开）   —— 主配置源
    ④ 代码默认值                             —— 最低

①② 通过 ``expand_env_vars`` 注入到 ``config.yaml`` 的 ``$VAR`` 引用中；
④ 仅在该节点缺失或非法时兜底。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# examples/agent_service/ 目录（base.py 位于 bocomadp/config/ 下，向上三级）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 配置数据文件（单一）：agent_service/config.yaml
CONFIG_YAML_FILE = BASE_DIR / "config.yaml"
# .env 文件（可选，自动加载）
DOTENV_FILE = BASE_DIR / ".env"


@lru_cache
def _load_dotenv_once() -> None:
    """从 ``agent_service/.env`` 加载环境变量（首次调用缓存）。

    按 ``KEY=VALUE`` 逐行解析，使用 ``setdefault``，因此**不覆盖**已存在的
    环境变量（外部注入的环境变量优先级更高）。``#`` 注释行与无 ``=`` 的行跳过。
    """
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
    """加载 ``config.yaml``（不存在则返回空 dict）。

    注意：该缓存**仅缓存 YAML 原始解析结果**；环境变量展开（``expand_env_vars``）
    在缓存之外执行，因此即使 YAML 被缓存，环境变量的变更依然实时生效。
    """
    if not CONFIG_YAML_FILE.is_file():
        return {}
    with CONFIG_YAML_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def expand_env_vars(value: Any) -> Any:
    """展开 YAML 值中的 ``$VAR`` / ``${VAR}`` 环境变量引用。

    支持：
        - 字符串 ``"$CROSS_SEARCH_API_URL"`` / ``"${VAR}"``
        - 列表、字典中的字符串（递归展开）

    未定义的环境变量原样保留（不报错，留待运行时/后续校验兜底）。
    展开前先调用 ``_load_dotenv_once()``，保证 ``.env`` 中的值可被引用。
    """
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


def yaml_section(data: dict[str, Any], path: list[str]) -> dict[str, Any]:
    """按层级路径取 YAML 子节点（非 dict 返回 ``{}``）。"""
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
    """按层级路径取 YAML 标量值（缺省返回默认值）。"""
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return default
        if key not in node:
            return default
        node = node.get(key)
    return node


def int_or(default: Any, value: Any) -> Any:
    """把值解析为 int，失败则返回 ``default``。

    ``value`` 为 ``None`` 时直接返回 ``default``（便于调用方统一写法
    ``int_or(None, cfg if cfg is not None else 10)``）。
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_or(default: Any, value: Any) -> Any:
    """把值解析为 float，失败则返回 ``default``。

    ``value`` 为 ``None`` 时直接返回 ``default``。
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def split_list(value: Any) -> list[str]:
    """把 YAML 列表（或逗号分隔字符串 / 单个标量）解析为 str 列表。

    - ``list`` → 逐项转 str 并过滤空串
    - ``str``  → 按 ``,`` 切分并去除空白
    - 其他标量 → 转成单元素列表（空则 ``[]``）
    """
    if value is None:
        return []
    items: list[Any]
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return items
    else:
        items = [value]
    return [str(item).strip() for item in items if str(item).strip()]


def str_dict(value: Any) -> dict[str, str]:
    """把 YAML 字典解析为 ``dict[str, str]``，非 dict 返回空字典。"""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}
