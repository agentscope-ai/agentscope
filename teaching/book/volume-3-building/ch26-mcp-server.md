# 第 26 章 接入 MCP Server

> 本章你将：理解 Model Context Protocol、通过 Toolkit 接入 MCP 工具。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 26.1 什么是 MCP？

Model Context Protocol (MCP) 是一个开放协议，让 AI 应用能连接外部工具和数据源。AgentScope 的 `Toolkit` 支持直接注册 MCP 客户端。

```
Agent → Toolkit → MCP Client → MCP Server（外部工具）
```

---

## 26.2 接入 MCP Server

```python
from agentscope.tool import Toolkit
from agentscope.mcp import StdioStatefulClient

toolkit = Toolkit()

# 注册 MCP 客户端
mcp_client = StdioStatefulClient(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
)
await mcp_client.connect()
toolkit.register_mcp_client(mcp_client)

# MCP 工具会自动出现在 toolkit 的工具列表中
print(toolkit.get_tool_schemas())
```

---

## 26.3 试一试

1. 用 MCP 文件系统服务器让 Agent 读写文件
2. 用 MCP Web 搜索服务器让 Agent 搜索网络

---

## 26.4 检查点

你现在已经能：

- 理解 MCP 协议的作用
- 通过 `Toolkit.register_mcp_client()` 接入 MCP 工具
- 让 Agent 使用 MCP 提供的外部工具

---

## 下一章预告
