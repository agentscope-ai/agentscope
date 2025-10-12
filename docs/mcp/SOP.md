# SOP：src/agentscope/mcp 模块

## 一、功能定义（Scope）
- 层次：MCP 客户端层（HTTP/SSE/StdIO），将远端工具以统一方式暴露给 Toolkit。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/mcp/_http_stateless_client.py
- 类：`HttpStatelessClient`

### 文件：src/agentscope/mcp/_client_base.py
- 类：`MCPClientBase`、`StatefulClientBase`

### 文件：src/agentscope/mcp/_mcp_function.py
- 类：`MCPToolFunction`

## 三、与其他组件的交互关系
- Toolkit：注册 MCP 工具以供 ReAct 调用；统一生成 JSON Schema。

## 四、变更流程
同 AGENTS.md。
