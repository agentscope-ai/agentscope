# -*- coding: utf-8 -*-
"""企业工具主动构建工厂（bocomadp）。

采用**主动 build** 而非 custom/ 被动扫描：
- 企业工具属于确定性注入组件，由 :func:`build_enterprise_tools` 显式构建，
  每会话按需装配，行为可控、可观测；
- ``FunctionTool`` 显式包装保留 ``is_read_only`` 语义（查询类工具只读）；
- 由 ``main.py`` 的通用工具构建入口（``build_agent_tools``）调用，
  与 ``ToolRegistry`` 自动扫描的内置工具合并注入。
"""
from __future__ import annotations

import logging

from agentscope.tool import FunctionTool, ToolBase

from ..deerflow.custom_params import get_custom_params
from .cross_search import cross_search_tool  # 已是 FunctionTool 实例（带注入中间件）
from .placeholder import (
    query_employee_info,
    query_internal_doc,
    submit_it_ticket,
)

logger = logging.getLogger(__name__)


async def build_enterprise_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[ToolBase]:
    """返回当前会话可用的企业内部工具。

    可在此根据 user_id / agent_id 做差异化授权：
    例如某些工具只对特定部门开放。

    检索开关（对齐 deer-flow custom_params，显式才生效）：

    - ``vector_search_switch`` 显式 ``False`` → 不挂载 cross_search
      检索工具；未传 / ``True`` 保持默认挂载。
    - ``online_search_switch`` 显式 ``True`` → 挂载在线搜索工具
      （预留：当前 bocomadp 尚无对应工具，仅记录日志）。
    - ``personal_search_switch`` 由 cross_search 工具的覆盖中间件在
      参数层处理（显式 ``False`` 时清空个人空间参数），见
      :mod:`bocomadp.tools.cross_search`。

    本函数在 run 任务内由框架 AgentToolFactory 调用，custom_params
    ContextVar 已随 ``asyncio.create_task`` 复制进来，可直接读取。
    """
    params = get_custom_params()
    tools: list[ToolBase] = [
        FunctionTool(query_employee_info, is_read_only=True),
        FunctionTool(query_internal_doc, is_read_only=True),
        FunctionTool(submit_it_ticket),
    ]

    # vector_search_switch 显式 False → 移除检索工具（未传默认挂载）
    vector_switch = params.get("vector_search_switch")
    if vector_switch is False:
        logger.info(
            "enterprise tools: cross_search disabled by "
            "vector_search_switch=false (session=%s)",
            session_id,
        )
    else:
        tools.append(cross_search_tool)

    # online_search_switch 显式 True → 挂在线搜索工具（预留：当前无）
    if params.get("online_search_switch") is True:
        logger.info(
            "enterprise tools: online_search_switch=true requested "
            "(session=%s); no online search tool is registered yet, "
            "ignored",
            session_id,
        )

    return tools
