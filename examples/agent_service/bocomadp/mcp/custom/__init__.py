"""自定义 MCP —— 在此目录下建 ``.py`` 文件，导出 MCPClient 实例即可自动注册。

示例 ``custom/my_service.py``::

    from agentscope.mcp import MCPClient, HttpMCPConfig
    my_api = MCPClient(
        name="my-service",
        mcp_config=HttpMCPConfig(url="https://my.internal/mcp"),
        is_stateful=False,
    )

重启后 McpRegistry.load_custom() 会自动扫描注册。
"""
