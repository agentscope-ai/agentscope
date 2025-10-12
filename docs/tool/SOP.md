# SOP：src/agentscope/tool 模块

## 一、功能定义（Scope）
- 层次：工具系统与 MCP 接入的统一入口。
- 作用：
  - 注册/分组/移除工具函数；
  - 生成函数调用 JSON Schema（含 `partial`/MCP），剔除干扰字段；
  - 调用工具（支持同步/异步/生成器与流式），产出 `ToolResponse`，并在需要时做后处理；
  - 管理工具组启停与说明（notes），供 Agent 动态装配。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/tool/_toolkit.py
- 类：`Toolkit`
  - 核心方法：
    - `create_tool_group(name, description, active=False, notes=None)`：新增分组；`basic` 组常驻且不可删除；
    - `update_tool_groups(group_names, active)` / `remove_tool_groups(group_names)`：启停/移除分组；
    - `register_tool_function(tool_func, group_name='basic', preset_kwargs=None, func_description=None, json_schema=None, include_long_description=True, include_var_positional=False, include_var_keyword=False, postprocess_func=None)`：
      - 支持普通函数、`functools.partial` 与 `MCPToolFunction`；
      - 解析 docstring 生成 schema；对 `preset_kwargs` 从 schema 中移除并从 required 剔除；
      - 返回值可为 `ToolResponse` 或（异步）生成器；
    - `remove_tool_function(name)`：按名移除；
    - `call_tool_function(tool_call)`：按块执行工具；若返回生成器，逐块产出并可在 `postprocess_func` 后处理；
    - `register_mcp_client(mcp_client, group_name='basic', enable_funcs=None, disable_funcs=None, preset_kwargs_mapping=None, postprocess_func=None)`：批量将 MCP 工具注册为本地函数。

### 文件：src/agentscope/tool/_response.py
- 类：`ToolResponse`：
  - 字段：`content`（文本/多模态块）、`metadata`（自由字段，如 `success`/`response_msg`）、`is_last`（流式终止）。

### 文件：src/agentscope/tool/_registered_tool_function.py
- 类：`RegisteredToolFunction`
  - 记录：函数源、schema、预置参数、扩展模型（结构化输出）、归属分组、后处理器等。

## 三、与其他组件的交互关系
- Agent（ReAct）：行动阶段统一入口；结构化输出时由 finish 函数产出 `response_msg` 并透传。
- MCP：通过 `mcp` 模块提供的客户端装配到 `Toolkit`。

## 四、Docs‑First 变更流程与验收
1) 在本 SOP 增补变更点与影响的函数；
2) 更新 `CLAUDE.md` 的工具注册与调用链映射；
3) `todo.md` 写入：普通/partial/MCP 三类注册、流式与后处理路径的用例；
4) 获批后修改与合入。
