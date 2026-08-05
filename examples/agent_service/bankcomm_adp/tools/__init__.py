# -*- coding: utf-8 -*-
"""企业内部系统工具占位。

通过 ``create_app(extra_agent_tools=...)`` 注入，agent 可按需调用。
推荐做法：把内部系统封装为独立 MCP Server，通过 ``mcp_hubs`` 挂载，
比直接写 Python 工具函数更解耦、可独立迭代。本模块仅提供快速占位。
"""
from .enterprise import build_enterprise_tools
from .placeholder import (
    query_employee_info,
    query_internal_doc,
    submit_it_ticket,
)

__all__ = [
    "build_enterprise_tools",
    "query_employee_info",
    "query_internal_doc",
    "submit_it_ticket",
]
