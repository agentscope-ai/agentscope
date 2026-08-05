# -*- coding: utf-8 -*-
"""企业工具工厂。

``create_app`` 的 ``extra_agent_tools`` 参数要求一个
``async (user_id, agent_id, session_id) -> list[ToolBase]`` 工厂。
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
