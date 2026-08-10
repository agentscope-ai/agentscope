# -*- coding: utf-8 -*-
"""跨知识搜索工具（``cross_search_tool``）的配置模块。

每个工具对应一个 ``*_config.py``，负责从单一 ``config.yaml`` 中提取
本工具的配置节点（``cross_search``）。

配置值通过 ``$VAR`` / ``${VAR}`` 环境变量引用展开（见
:func:`base.expand_env_vars`），例如 ``api_url: $CROSS_SEARCH_API_URL``。
读取优先级（高到低）：:

    ① config.yaml 的 cross_search 节点（含 $VAR 展开）
    ② 代码默认值

配置采用**扁平结构**（直接 ``cross_search.search_type``、``cross_search.text_top_n``
等，无 ``search`` / ``spaces`` / ``rerank`` 子层）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import (
    expand_env_vars,
    float_or,
    int_or,
    load_config_yaml,
    split_list,
    str_dict,
    yaml_section,
)


def _int_list(val: Any) -> list[int]:
    """把 YAML 列表（或单个值 / 逗号字符串）解析为 int 列表。"""
    if val is None:
        return []
    items: list[Any]
    if isinstance(val, list):
        items = val
    elif isinstance(val, str):
        items = [item.strip() for item in val.split(",") if item.strip()]
    else:
        items = [val]
    result: list[int] = []
    for item in items:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


@dataclass
class CrossSearchConfig:
    """跨知识搜索配置分组。

    从 ``config.yaml`` 的 ``cross_search`` 节点（扁平结构）构建，
    字段名与 YAML 键一一对应。每个字段均可被调用参数覆盖
    （见 ``bocomadp/tools/cross_search.py``）。
    """

    api_url: str = ""
    user_code: str = ""
    user_role: str = ""
    caller: str = ""
    search_type: str = "0"
    space_code_list: list[str] = field(default_factory=list)
    team_space_code_list: list[str] = field(default_factory=list)
    psnl_space_code_id: str = ""
    customized_tag_list: list[str] = field(default_factory=list)
    psnl_category_id_list: list[str] = field(default_factory=list)
    text_top_n: int = 10
    vector_top_n: int = 10
    attach_flag: int = 1
    rerank_flag: int = 1
    rewrite_flag: str = "OFF"
    rerank_top_n: int = 5
    rerank_rule_code: str = ""
    qa_type: list[int] = field(default_factory=lambda: [0])
    vector_min_score: float | None = None
    text_min_score: float | None = None
    source_org_id_list: list[str] = field(default_factory=list)
    source_system_list: list[str] = field(default_factory=list)
    pub_time_start: str = ""
    pub_time_end: str = ""
    second_search_kb_codes: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0

    @classmethod
    def from_yaml(cls) -> "CrossSearchConfig":
        """从 ``config.yaml`` 的 ``cross_search`` 节点构建配置。"""
        data = load_config_yaml()
        # 展开 YAML 值中的 $VAR / ${VAR} 环境变量引用
        cs = expand_env_vars(yaml_section(data, ["cross_search"]))

        return cls(
            api_url=str(cs.get("api_url") or ""),
            user_code=str(cs.get("user_code") or ""),
            user_role=str(cs.get("user_role") or ""),
            caller=str(cs.get("caller") or ""),
            search_type=str(cs.get("search_type") or "0"),
            space_code_list=split_list(cs.get("space_code_list", [])),
            team_space_code_list=split_list(
                cs.get("team_space_code_list", []),
            ),
            psnl_space_code_id=str(cs.get("psnl_space_code_id") or ""),
            customized_tag_list=split_list(
                cs.get("customized_tag_list", []),
            ),
            psnl_category_id_list=split_list(
                cs.get("psnl_category_id_list", []),
            ),
            text_top_n=int_or(
                None,
                cs.get("text_top_n") if cs.get("text_top_n") is not None else 10,
            ),
            vector_top_n=int_or(
                None,
                cs.get("vector_top_n") if cs.get("vector_top_n") is not None else 10,
            ),
            attach_flag=int_or(
                None,
                cs.get("attach_flag") if cs.get("attach_flag") is not None else 1,
            ),
            rerank_flag=int_or(
                None,
                cs.get("rerank_flag") if cs.get("rerank_flag") is not None else 1,
            ),
            rewrite_flag=str(cs.get("rewrite_flag") or "OFF"),
            rerank_top_n=int_or(
                None,
                cs.get("rerank_top_n") if cs.get("rerank_top_n") is not None else 5,
            ),
            rerank_rule_code=str(cs.get("rerank_rule_code") or ""),
            qa_type=_int_list(
                cs.get("qa_type")
                if cs.get("qa_type") is not None
                else [0],
            ),
            vector_min_score=float_or(None, cs.get("vector_min_score")),
            text_min_score=float_or(None, cs.get("text_min_score")),
            source_org_id_list=split_list(cs.get("source_org_id_list", [])),
            source_system_list=split_list(cs.get("source_system_list", [])),
            pub_time_start=str(cs.get("pub_time_start") or ""),
            pub_time_end=str(cs.get("pub_time_end") or ""),
            second_search_kb_codes=split_list(
                cs.get("second_search_kb_codes", []),
            ),
            headers=str_dict(cs.get("headers", {})),
            timeout=float_or(
                None,
                cs.get("timeout") if cs.get("timeout") is not None else 30.0,
            ),
        )


def get_cross_search_config() -> CrossSearchConfig:
    """返回跨知识搜索工具配置（每次读取最新 YAML / 环境值）。"""
    return CrossSearchConfig.from_yaml()
