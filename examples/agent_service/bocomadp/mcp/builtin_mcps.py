"""内置 MCP —— 在此模块级导出 MCPClient 实例，McpRegistry.load_builtin() 会自动扫描。

每个 MCPClient 实例需带 name + mcp_config。
带 API key 的 MCP 建议从环境变量读取，避免硬编码。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
except ImportError:
    # 语法检查环境无 agentscope 时占位
    MCPClient = None  # type: ignore
    StdioMCPConfig = None  # type: ignore
    HttpMCPConfig = None  # type: ignore


if MCPClient is not None:
    # Playwright 浏览器 MCP（stdio）
    browser_use = MCPClient(
        name="browser-use",
        mcp_config=StdioMCPConfig(
            command="npx",
            args=["@playwright/mcp@latest"],
        ),
        is_stateful=True,
    )

    # 高德地图 MCP（http），按需配置 AMAP_API_KEY
    if os.getenv("AMAP_API_KEY"):
        amap = MCPClient(
            name="amap",
            mcp_config=HttpMCPConfig(
                url=f"https://mcp.amap.com/mcp?key={os.environ['AMAP_API_KEY']}",
            ),
            is_stateful=False,
        )
