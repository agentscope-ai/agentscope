# 第二十四章：集成 MCP Server——从连接到 Agent 调用的完整实战

**难度**：中级

> 第九章我们追踪了 MCP 模块的客户端层次和工具注册流程。本章你要亲自动手——启动一个本地 MCP Server，用 AgentScope 的客户端连接它，发现工具并注册到 Toolkit，最后让 ReActAgent 直接调用 MCP 工具完成任务。写完之后，你会对"MCP 协议从连接到工具执行"的全链路有肌肉记忆。

---

## 1. 实战目标

完成本章后，你将：

1. 用 `FastMCP` 创建一个本地 MCP Server，暴露计算工具
2. 用 `StdIOStatefulClient`（`_stdio_stateful_client.py` 第 11-77 行）通过标准 IO 连接 Server
3. 用 `HttpStatelessClient`（`_http_stateless_client.py` 第 16-153 行）通过 HTTP 连接 Server
4. 理解 `MCPToolFunction`（`_mcp_function.py` 第 15-115 行）如何封装远程工具为可调用对象
5. 通过 `Toolkit.register_mcp_client`（`_toolkit.py` 第 1035-1178 行）批量注册 MCP 工具
6. 在 ReActAgent 中集成 MCP 工具并完成端到端测试

---

## 2. 第一步：最小可用版本

### 2.1 创建本地 MCP Server

先创建一个最简单的 MCP Server，提供三个计算工具。创建文件 `examples/mcp_calc_server.py`：

```python
# -*- coding: utf-8 -*-
"""A local MCP server with calculator tools."""
from mcp.server import FastMCP


mcp = FastMCP("Calculator")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together.

    Args:
        a: The first number.
        b: The second number.
    """
    return a + b


@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers.

    Args:
        a: The first number.
        b: The second number.
    """
    return a * b


@mcp.tool()
def get_info() -> str:
    """Return server info as a JSON string."""
    import json
    return json.dumps({"name": "Calculator", "version": "1.0", "tools": 3})
```

StdIO 模式不需要单独启动 Server——客户端会自动启动子进程。如果要用 HTTP 模式，在文件末尾加 `mcp.run(transport="streamable-http")`。

### 2.2 MCP 客户端类的继承体系

打开 `src/agentscope/mcp/`，核心类关系：

```
MCPClientBase (abstract)           # _client_base.py 第 18 行
├── StatefulClientBase (abstract)  # _stateful_client_base.py 第 16 行
│   ├── StdIOStatefulClient        # _stdio_stateful_client.py 第 11 行
│   └── HttpStatefulClient         # _http_stateful_client.py 第 11 行
└── HttpStatelessClient            # _http_stateless_client.py 第 16 行
```

两类客户端的关键区别：

- **Stateful**：维持长会话，工具调用复用同一 session。需手动 `connect()` / `close()`。适合需要状态的 Server。
- **Stateless**：每次工具调用独立创建 session。无需 `connect()`，生命周期自动管理。

### 2.3 StdIO 连接——最简单的方式

```python
import asyncio
from agentscope.mcp import StdIOStatefulClient


async def connect_stdio() -> None:
    # StdIOStatefulClient.__init__（第 31-77 行）
    # 将参数包装为 StdioServerParameters 传给 stdio_client
    client = StdIOStatefulClient(
        name="calc_stdio",
        command="python",
        args=["examples/mcp_calc_server.py"],
    )

    # StatefulClientBase.connect（_stateful_client_base.py 第 46-70 行）
    # 创建 AsyncExitStack -> 启动子进程 -> 创建 ClientSession -> initialize
    await client.connect()

    # 列出可用工具
    tools = await client.list_tools()
    for tool in tools:
        print(f"  {tool.name}: {tool.description}")

    # StatefulClientBase.close（第 72-95 行）——必须手动关闭
    await client.close()


asyncio.run(connect_stdio())
```

运行后输出：

```
  add: Add two numbers together.
  multiply: Multiply two numbers.
  get_info: Return server info as a JSON string.
```

`_validate_connection`（`_stateful_client_base.py` 第 164-176 行）在每次操作前检查连接状态。未连接或 session 为 None 都会抛出 `RuntimeError`。

---

## 3. 第二步：注册并测试

### 3.1 获取可调用的工具函数

`get_callable_function`（`_stateful_client_base.py` 第 112-162 行）返回 `MCPToolFunction` 对象：

1. **第 142-143 行**：如果尚未缓存工具列表，先调用 `list_tools()`
2. **第 145-154 行**：查找目标工具名，找不到抛出 `ValueError`
3. **第 156-162 行**：创建 `MCPToolFunction`，绑定当前 session

`MCPToolFunction.__call__`（`_mcp_function.py` 第 82-115 行）有两条路径：

- **有 `session`**（Stateful，第 99-104 行）：直接用现有 session 调用 `call_tool`
- **有 `client_gen`**（Stateless，第 88-97 行）：临时创建 session，调用后自动关闭

```python
async def call_single_tool() -> None:
    client = StdIOStatefulClient(
        name="calc_stdio",
        command="python",
        args=["examples/mcp_calc_server.py"],
    )
    await client.connect()

    # wrap_tool_result=True（默认）-> 返回 ToolResponse
    add_func = await client.get_callable_function("add")
    result = await add_func(a=3, b=5)
    print(result.content[0].text)  # "8"

    # wrap_tool_result=False -> 返回原始 mcp.types.CallToolResult
    add_raw = await client.get_callable_function("add", wrap_tool_result=False)
    raw = await add_raw(a=1, b=2)
    print(raw.content[0].text)  # "3"

    await client.close()


asyncio.run(call_single_tool())
```

### 3.2 注册到 Toolkit

`Toolkit.register_mcp_client`（`_toolkit.py` 第 1035-1178 行）将 MCP Server 的全部工具一次性注册：

1. **第 1100-1107 行**：如果是 Stateful 客户端，检查是否已 `connect()`
2. **第 1142 行**：调用 `list_tools()` 获取工具列表
3. **第 1144-1150 行**：根据 `enable_funcs` / `disable_funcs` 过滤
4. **第 1155-1172 行**：获取 `MCPToolFunction` 并注册到 Toolkit

```python
import asyncio
from agentscope.mcp import StdIOStatefulClient
from agentscope.tool import Toolkit


async def register_mcp_tools() -> None:
    client = StdIOStatefulClient(
        name="calc_stdio",
        command="python",
        args=["examples/mcp_calc_server.py"],
    )
    await client.connect()

    toolkit = Toolkit()
    await toolkit.register_mcp_client(client)

    # 验证
    schemas = toolkit.get_json_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "add" in names and "multiply" in names
    print(f"Registered tools: {names}")

    await client.close()


asyncio.run(register_mcp_tools())
```

### 3.3 选择性注册与移除

```python
    # 只注册指定工具
    await toolkit.register_mcp_client(client, enable_funcs=["add", "multiply"])

    # 排除指定工具
    await toolkit.register_mcp_client(client, disable_funcs=["get_info"])

    # 预设参数：a 固定为 1，LLM 只需提供 b
    await toolkit.register_mcp_client(
        client,
        preset_kwargs_mapping={"add": {"a": 1}},
    )

    # 按客户端名称移除所有工具（_toolkit.py 第 649-683 行）
    await toolkit.remove_mcp_clients(["calc_stdio"])
```

---

## 4. 第三步：进阶功能

### 4.1 HttpStatelessClient——无需管理连接

当 MCP Server 以 HTTP 模式运行时，使用 `HttpStatelessClient`（`_http_stateless_client.py` 第 16-153 行）。它的特点：

- `stateful = False`（第 25 行），每次工具调用独立创建 session
- `get_callable_function`（第 92-137 行）传 `client_gen` 而非 `session`
- 无需 `connect()` / `close()`

```python
import asyncio
from agentscope.mcp import HttpStatelessClient
from agentscope.tool import Toolkit
from agentscope.message import ToolUseBlock


async def use_http_stateless() -> None:
    client = HttpStatelessClient(
        name="calc_http",
        transport="streamable_http",
        url="http://localhost:8000/mcp",
        timeout=10,
    )

    # 列出工具——内部临时创建 session（list_tools 第 139-152 行）
    tools = await client.list_tools()
    print([t.name for t in tools])

    # 注册并调用——无需 connect/close
    toolkit = Toolkit()
    await toolkit.register_mcp_client(client)

    res = await toolkit.call_tool_function(
        ToolUseBlock(type="tool_use", id="t1", name="add", input={"a": 3, "b": 7}),
    )
    async for chunk in res:
        print(chunk.content[0].text)  # "10"


asyncio.run(use_http_stateless())
```

### 4.2 HttpStatefulClient 与多客户端

`HttpStatefulClient`（`_http_stateful_client.py` 第 11-85 行）继承 `StatefulClientBase`，用 `connect()` / `close()` 管理长连接。`__init__`（第 31-84 行）根据 `transport` 选择 `streamablehttp_client` 或 `sse_client`。

```python
import asyncio
from agentscope.mcp import HttpStatefulClient


async def use_http_stateful() -> None:
    client = HttpStatefulClient(
        name="calc_stateful",
        transport="streamable_http",  # 或 "sse"（URL 以 /sse 结尾）
        url="http://localhost:8000/mcp",
        timeout=10,
        sse_read_timeout=300,
    )
    await client.connect()
    add_func = await client.get_callable_function("add")
    result = await add_func(a=10, b=20)
    print(result.content[0].text)  # "30"
    await client.close()


asyncio.run(use_http_stateful())
```

当注册多个 Stateful 客户端时，必须按 **LIFO（后进先出）** 顺序关闭。`_stdio_stateful_client.py` 第 23-28 行的文档注释链接了 python-sdk issue 解释原因。

---

## 5. 第四步：错误处理

### 5.1 连接失败的捕获

`StatefulClientBase.connect`（`_stateful_client_base.py` 第 46-70 行）在 try/except 中管理 `AsyncExitStack`。连接失败时自动清理已获取的资源，不需要调用 `close()`：

```python
async def handle_connection_error() -> None:
    client = StdIOStatefulClient(
        name="bad", command="nonexistent_command", args=[],
    )
    try:
        await client.connect()
    except FileNotFoundError as e:
        print(f"启动失败: {e}")  # command 不存在
    except Exception as e:
        print(f"连接错误: {e}")


asyncio.run(handle_connection_error())
```

### 5.2 工具调用超时

`get_callable_function` 接受 `execution_timeout` 参数。`MCPToolFunction.__init__`（`_mcp_function.py` 第 64-67 行）将其转换为 `timedelta`，在 `__call__` 中传给 `call_tool` 的 `read_timeout_seconds`：

```python
    # 设置 5 秒超时
    add_func = await client.get_callable_function("add", execution_timeout=5.0)
    result = await add_func(a=1, b=2)
```

### 5.3 状态校验

`StatefulClientBase` 内置三重状态检查：

- `connect`（第 48-49 行）：已连接时抛出 `RuntimeError("already connected")`
- `_validate_connection`（第 164-176 行）：未连接或 session 为 None 时抛出 `RuntimeError("not established")`
- `close`（第 80-81 行）：未连接时抛出 `RuntimeError("not connected")`

```python
async def handle_state_errors() -> None:
    client = StdIOStatefulClient(
        name="calc", command="python",
        args=["examples/mcp_calc_server.py"],
    )

    # 未连接就调用 -> RuntimeError
    try:
        await client.list_tools()
    except RuntimeError as e:
        assert "not established" in str(e)

    await client.connect()

    # 重复连接 -> RuntimeError
    try:
        await client.connect()
    except RuntimeError as e:
        assert "already connected" in str(e)

    await client.close()

    # 关闭后再关闭 -> RuntimeError
    try:
        await client.close()
    except RuntimeError as e:
        assert "not connected" in str(e)


asyncio.run(handle_state_errors())
```

---

## 6. 第五步：集成测试

### 6.1 用 ReActAgent 调用 MCP 工具

完整的端到端流程——连接 MCP Server、注册工具、让 ReActAgent 自主选择并调用：

```python
import asyncio
from agentscope.mcp import StdIOStatefulClient
from agentscope.agents import ReActAgent
from agentscope.models import ModelManager, ModelConfig
from agentscope.message import Msg


async def test_react_with_mcp() -> None:
    # 1. 连接 MCP 客户端
    mcp_client = StdIOStatefulClient(
        name="calc",
        command="python",
        args=["examples/mcp_calc_server.py"],
    )
    await mcp_client.connect()

    # 2. 注册 MCP 工具到 agent 的 Toolkit
    agent = ReActAgent(
        name="math_agent",
        model_config_name="my_model",
        max_iters=5,
    )
    await agent.toolkit.register_mcp_client(mcp_client)

    # 3. 发送任务
    response = await agent(Msg(
        name="user",
        content="请计算 15 加 27 等于多少，然后把结果乘以 3。",
    ))
    print(response.content)

    # 4. 清理
    await mcp_client.close()


# 配置模型（根据实际环境修改）
ModelManager.add_model_config(ModelConfig(
    config_name="my_model",
    model_type="openai_chat",
    model_name="gpt-4o-mini",
))

asyncio.run(test_react_with_mcp())
```

### 6.2 混合注册 MCP 工具与本地工具

Toolkit 可以同时容纳 MCP 工具和普通 Python 函数。`register_tool_function`（`_toolkit.py` 第 382-388 行）通过 `isinstance(tool_func, MCPToolFunction)` 区分两者。多个 MCP Server 提供同名工具时，用 `namesake_strategy`（第 1053-1058 行）控制冲突策略：`raise`（默认报错）、`override`（覆盖）、`skip`（跳过）、`rename`（自动加后缀）。

---

## 7. PR 检查清单

提交 MCP 集成相关 PR 前的验证步骤：

- [ ] MCP Server 可以独立启动并通过 `list_tools` 返回正确工具列表
- [ ] Stateful 客户端在 `connect()` 后使用，`close()` 后不再调用
- [ ] 多个 Stateful 客户端按 LIFO 顺序关闭
- [ ] Stateless 客户端无需 `connect()` / `close()`
- [ ] `remove_mcp_clients` 能正确清理已注册的工具
- [ ] 工具调用超时参数 `execution_timeout` 正确传递
- [ ] 通过 `pytest tests/ -v` 全部通过
- [ ] 通过 `pre-commit run --all-files`
- [ ] MCP Server 的工具函数有完整的 docstring（`FastMCP` 依赖它生成 schema）

---

## 8. 下一章预告

下一章我们将深入 AgentScope 的 RAG 组件——接入向量数据库，构建一个能检索文档并回答问题的 RAG Agent。
