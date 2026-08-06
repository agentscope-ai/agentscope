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

from agentscope.tool import FunctionTool, ToolBase

from .placeholder import (
    query_employee_info,
    query_internal_doc,
    submit_it_ticket,
)


async def build_enterprise_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[ToolBase]:
    """返回当前会话可用的企业内部工具。

    可在此根据 user_id / agent_id 做差异化授权：
    例如某些工具只对特定部门开放。
    """
    return [
        FunctionTool(query_employee_info, is_read_only=True),
        FunctionTool(query_internal_doc, is_read_only=True),
        FunctionTool(submit_it_ticket),
    ]
