# -*- coding: utf-8 -*-
"""企业内部系统工具。

通过 ``create_app(extra_agent_tools=...)`` 注入，agent 可按需调用。
推荐做法：把内部系统封装为独立 MCP Server，通过 ``mcp_hubs`` 挂载，
比直接写 Python 工具函数更解耦、可独立迭代。
"""
from .cross_search import cross_search_tool
from .enterprise import build_enterprise_tools

__all__ = [
    "build_enterprise_tools",
    "cross_search_tool",
]
