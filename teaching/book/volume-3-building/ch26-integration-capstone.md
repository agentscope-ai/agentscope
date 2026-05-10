# 第二十六章：终章——集成实战（卷三 Capstone）

**难度**：高级

> 前四章你从零造出了四个模块：SQLite 查询工具（ch20）、FastLLM Model Provider（ch21）、SQLite 记忆后端（ch22）、Plan-Execute Agent（ch23）。现在是检验时刻——把它们组装成一个完整的系统，跑通端到端测试，处理跨模块的边界错误，做基本的性能观察。写完之后，你会对"模块如何在一个真实系统中协作"有完整的实战经验。

---

## 1. 实战目标

完成本章后，你将：

1. 梳理 ch20-ch23 四个模块的接口契约，识别集成点
2. 编写 `build_system` 工厂函数，把 Tool + Model + Memory + Agent 组装起来
3. 用 Mock Model 跑通完整的端到端流程
4. 测试跨模块边界错误（Model 失败、Tool 失败、Memory 异常）
5. 观察流式输出、记忆持久化等基本性能特征
6. 完成卷三综合 PR 检查清单

---

## 2. 第一步：模块清单

### 2.1 回顾四个模块

| 章节 | 模块 | 文件 | 关键接口 |
|------|------|------|----------|
| ch20 | SQLite 查询工具 | `tool/_db_query.py` | `query_sqlite(db_path, sql) -> ToolResponse` |
| ch21 | FastLLM Model | `model/_fastllm_model.py` | `FastLLMChatModel(model_name, api_key, stream, base_url)` |
| ch21 | FastLLM Formatter | `formatter/_fastllm_formatter.py` | `FastLLMChatFormatter()` |
| ch22 | SQLite Memory | `memory/_working_memory/_sqlite_memory.py` | `SQLiteMemory(db_path)` |
| ch23 | PlanExecute Agent | `agent/_plan_execute_agent.py` | `PlanExecuteAgent(name, sys_prompt, model, formatter, toolkit, memory)` |

### 2.2 集成点分析

```
PlanExecuteAgent
  ├── model: FastLLMChatModel      # Agent 通过 model(prompt) 调用
  ├── formatter: FastLLMChatFormatter  # Agent 通过 formatter.format(msgs) 转换
  ├── toolkit: Toolkit              # Agent 通过 toolkit.call_tool_function 执行
  │   └── query_sqlite              # Toolkit 通过 name 查找并调用
  └── memory: SQLiteMemory          # Agent 通过 memory.add/get_memory 存取
```

数据流：

```
用户 Msg -> memory.add(msg)
         -> formatter.format([sys_prompt, *memory.get_memory()])
         -> model(prompt) -> ChatResponse
         -> 解析出 ToolUseBlock -> toolkit.call_tool_function(tc)
         -> ToolResponse -> memory.add(tool_result)
         -> 回复 Msg -> memory.add(reply_msg)
```

### 2.3 前置检查

确认所有模块已注册：

```python
# 验证四个模块都可以正常 import
from agentscope.tool._db_query import query_sqlite, query_sqlite_streaming
from agentscope.model._fastllm_model import FastLLMChatModel
from agentscope.formatter._fastllm_formatter import FastLLMChatFormatter
from agentscope.memory._working_memory._sqlite_memory import SQLiteMemory
from agentscope.agent._plan_execute_agent import PlanExecuteAgent
```

如果任何一个 import 失败，回到对应章节完成注册步骤。

---

## 3. 第二步：组装系统

### 3.1 工厂函数

创建 `tests/capstone_system_test.py`：

```python
# -*- coding: utf-8 -*-
"""Volume 3 capstone: integrate all custom modules."""
import os
import sqlite3
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.agent._plan_execute_agent import PlanExecuteAgent
from agentscope.formatter._fastllm_formatter import FastLLMChatFormatter
from agentscope.memory._working_memory._sqlite_memory import SQLiteMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from agentscope.model._fastllm_model import FastLLMChatModel
from agentscope.tool import Toolkit, ToolResponse
from agentscope.tool._db_query import query_sqlite, query_sqlite_streaming


def _mock_resp(content="Hello!", tool_calls=None):
    """Create a mock API response."""
    msg = MagicMock(content=content, tool_calls=tool_calls)
    return MagicMock(
        choices=[MagicMock(message=msg)], id="cap-123",
        usage=MagicMock(prompt_tokens=10, completion_tokens=5),
    )


def _plan(d):
    """Create a mock model response that carries a structured plan."""
    r = MagicMock()
    r.metadata = d
    r.content = []
    r.id = "cap-plan"
    return r


def build_system(db_path: str, model: FastLLMChatModel,
                 formatter: FastLLMChatFormatter,
                 streaming: bool = False) -> PlanExecuteAgent:
    """Assemble all custom modules into a PlanExecuteAgent.

    Args:
        db_path (`str`): Path to the SQLite database for the query tool.
        model (`FastLLMChatModel`): The model provider instance.
        formatter (`FastLLMChatFormatter`): The formatter instance.
        streaming (`bool`): Whether to register the streaming tool variant.

    Returns:
        `PlanExecuteAgent`: A fully assembled agent.
    """
    toolkit = Toolkit()
    if streaming:
        toolkit.register_tool_function(
            query_sqlite_streaming,
            preset_kwargs={"db_path": db_path},
        )
    else:
        toolkit.register_tool_function(
            query_sqlite,
            preset_kwargs={"db_path": db_path},
        )

    memory = SQLiteMemory(db_path=":memory:")

    agent = PlanExecuteAgent(
        name="capstone_agent",
        sys_prompt="你是一个数据分析助手。使用工具查询数据库并回答问题。",
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        memory=memory,
        max_replans=2,
    )
    return agent
```

要点：

- `preset_kwargs={"db_path": db_path}` 隐藏数据库路径（ch20 第 470-476 行），LLM 只看到 `sql` 参数
- `SQLiteMemory(db_path=":memory:")` 用内存数据库存对话历史，测试结束自动清理
- `max_replans=2` 允许计划修订两次，对应 ch23 第 4.1 节的错误恢复机制
- `build_system` 返回完整组装的 Agent，所有依赖通过参数注入

---

## 4. 第三步：端到端测试

### 4.1 准备测试数据库

```python
class CapstoneE2ETest(IsolatedAsyncioTestCase):
    """End-to-end integration test for all volume 3 modules."""

    async def asyncSetUp(self) -> None:
        # 创建测试数据库
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
            "product TEXT, quantity INTEGER, price REAL)")
        conn.executemany(
            "INSERT INTO orders (product, quantity, price) VALUES (?, ?, ?)",
            [("Widget", 10, 9.99),
             ("Gadget", 5, 24.99),
             ("Doohickey", 3, 49.99)],
        )
        conn.commit()
        conn.close()

        # 创建 mock model
        self.model = FastLLMChatModel(
            model_name="fastllm-v1", api_key="test-key")
        self.model.client = MagicMock()
        self.formatter = FastLLMChatFormatter()
```

### 4.2 完整计划执行流程

```python
    async def test_full_plan_execute(self) -> None:
        """测试完整的 Plan -> Execute -> Reply 流程。"""
        tc = MagicMock(id="tc1")
        tc.function.name = "query_sqlite"
        tc.function.arguments = '{"sql": "SELECT * FROM orders"}'

        self.model.client.chat.completions.create = AsyncMock(side_effect=[
            _plan({"goal": "查询订单", "steps": [
                {"step_number": 1, "description": "查询所有订单",
                 "tool_name": "query_sqlite",
                 "tool_args": {"sql": "SELECT * FROM orders"}},
                {"step_number": 2, "description": "总结订单信息",
                 "tool_name": None, "tool_args": None},
            ]}),
            MagicMock(metadata=None, content=[
                TextBlock(type="text", text="共 3 笔订单。"),
            ], id="cap-r1"),
        ])

        agent = build_system(
            self.db_path, self.model, self.formatter)
        result = await agent(Msg("user", "帮我查一下订单情况", "user"))

        # 验证回复包含步骤结果
        text = result.get_text_content()
        self.assertIn("Step 1", text)
        self.assertIn("Widget", text)
        self.assertIn("Step 2", text)

        # 验证对话历史被写入 Memory
        memory_msgs = await agent.memory.get_memory()
        self.assertGreaterEqual(len(memory_msgs), 2)

        await agent.memory.close()
```

### 4.3 流式工具测试

```python
    async def test_streaming_tool(self) -> None:
        """测试流式工具在系统中的工作。"""
        self.model.client.chat.completions.create = AsyncMock(side_effect=[
            _plan({"goal": "流式查询", "steps": [
                {"step_number": 1, "description": "流式查询订单",
                 "tool_name": "query_sqlite_streaming",
                 "tool_args": {"sql": "SELECT product FROM orders"}},
            ]}),
        ])

        agent = build_system(
            self.db_path, self.model, self.formatter, streaming=True)
        result = await agent(Msg("user", "列出产品", "user"))

        text = result.get_text_content()
        self.assertIn("Step 1", text)
        self.assertIn("Widget", text)

        await agent.memory.close()
```

### 4.4 多轮对话测试

```python
    async def test_multi_turn(self) -> None:
        """测试多轮对话的 Memory 持久性。"""
        self.model.client.chat.completions.create = AsyncMock(side_effect=[
            # 第一轮：查订单数
            _plan({"goal": "统计", "steps": [
                {"step_number": 1, "description": "统计订单数",
                 "tool_name": "query_sqlite",
                 "tool_args": {"sql": "SELECT COUNT(*) FROM orders"}},
            ]}),
            # 第二轮：查总金额
            _plan({"goal": "总金额", "steps": [
                {"step_number": 1, "description": "计算总金额",
                 "tool_name": "query_sqlite",
                 "tool_args": {"sql": "SELECT SUM(price*quantity) FROM orders"}},
            ]}),
        ])

        agent = build_system(
            self.db_path, self.model, self.formatter)

        # 第一轮
        r1 = await agent(Msg("user", "有多少订单", "user"))
        self.assertIn("3", r1.get_text_content())

        # 第二轮——Memory 中已有第一轮的记录
        r2 = await agent(Msg("user", "总金额是多少", "user"))
        self.assertIn("Step 1", r2.get_text_content())

        # Memory 包含两轮的所有消息
        msgs = await agent.memory.get_memory()
        self.assertGreaterEqual(len(msgs), 4)

        await agent.memory.close()
```

---

## 5. 第四步：错误场景测试

### 5.1 Model 调用失败——Plan Fallback

`_plan` 结构化输出调用失败时 fallback 到单步计划（ch23 第 138-141 行）：

```python
    async def test_model_failure_fallback(self) -> None:
        """Model 调用失败时 fallback 到单步计划。"""
        self.model.client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API unavailable"))

        agent = build_system(
            self.db_path, self.model, self.formatter)
        result = await agent(Msg("user", "你好", "user"))

        # Fallback 到单步推理，不应崩溃
        self.assertIsNotNone(result)
        self.assertIn("Step 1", result.get_text_content())

        await agent.memory.close()
```

### 5.2 Tool 不存在——触发 Replan

`_execute_step` 找不到工具名时抛 `ValueError`（ch23 第 161 行），`_execute_plan` 捕获后尝试 `_replan`：

```python
    async def test_missing_tool_triggers_replan(self) -> None:
        """计划引用不存在的工具时触发 replan。"""
        self.model.client.chat.completions.create = AsyncMock(side_effect=[
            # 第一次：计划中引用不存在的工具
            _plan({"goal": "查询", "steps": [
                {"step_number": 1, "description": "调用不存在的工具",
                 "tool_name": "nonexistent_tool",
                 "tool_args": {"x": 1}},
            ]}),
            # 第二次：replan 成功，改为直接推理
            _plan({"goal": "直接回答", "steps": [
                {"step_number": 1, "description": "直接回答",
                 "tool_name": None, "tool_args": None},
            ]}),
            # 第三次：推理步骤的 model 调用
            MagicMock(metadata=None, content=[
                TextBlock(type="text", text="我无法执行该操作。"),
            ], id="cap-r2"),
        ])

        agent = build_system(
            self.db_path, self.model, self.formatter, streaming=False)
        result = await agent(Msg("user", "执行任务", "user"))

        self.assertIsNotNone(result)
        self.assertIn("Step 1", result.get_text_content())

        await agent.memory.close()
```

### 5.3 SQL 错误——Tool 返回错误信息

`query_sqlite`（ch20 第 386-388 行）对 SQL 错误返回 `ToolResponse`，不抛异常：

```python
    async def test_sql_error_in_tool(self) -> None:
        """SQL 语法错误被 Tool 内部捕获，返回错误信息。"""
        self.model.client.chat.completions.create = AsyncMock(side_effect=[
            _plan({"goal": "查询", "steps": [
                {"step_number": 1, "description": "执行 SQL",
                 "tool_name": "query_sqlite",
                 "tool_args": {"sql": "SELECTT * FROM orders"}},
            ]}),
        ])

        agent = build_system(
            self.db_path, self.model, self.formatter)
        result = await agent(Msg("user", "执行错误SQL", "user"))

        # Tool 内部返回错误信息，不会崩溃
        text = result.get_text_content()
        self.assertIn("Step 1", text)

        await agent.memory.close()
```

### 5.4 Memory 异常

Memory 连接关闭后再写入，`_write_op` 包装（ch22 第 419-426 行）会抛 `RuntimeError`：

```python
    async def test_memory_closed(self) -> None:
        """Memory 连接关闭后写入应抛出 RuntimeError。"""
        agent = build_system(
            self.db_path, self.model, self.formatter)
        await agent.memory.add(Msg("user", "hi", "user"))
        await agent.memory.close()

        with self.assertRaises(RuntimeError):
            await agent.memory.add(Msg("user", "after close", "user"))
```

---

## 6. 第五步：性能与调优

### 6.1 流式 vs 非流式

流式工具（`query_sqlite_streaming`）在大结果集场景有优势，小查询差异不大。基准测试思路：

```python
import time

async def benchmark_query(db_path: str, sql: str, streaming: bool) -> float:
    """Measure tool execution time."""
    toolkit = Toolkit()
    func = query_sqlite_streaming if streaming else query_sqlite
    toolkit.register_tool_function(func, preset_kwargs={"db_path": db_path})
    name = "query_sqlite_streaming" if streaming else "query_sqlite"
    start = time.monotonic()
    res = await toolkit.call_tool_function(
        ToolUseBlock(type="tool_use", id="bm1", name=name, input={"sql": sql}))
    async for _ in res: pass
    return time.monotonic() - start
```

### 6.2 关键调优点

1. **Memory 批量写入**：`add` 支持一次写入多条消息（ch22 第 102-138 行），比逐条 add 减少锁竞争
2. **Tool 批大小**：`query_sqlite_streaming` 的 `batch_size` 参数（ch20 第 245 行）控制每次 yield 的行数
3. **Replan 次数**：`max_replans` 限制修订次数（ch23 第 98 行），避免无限循环

### 6.3 日志

所有模块使用 `from .._logging import logger`。集成时统一配置：

```python
import logging
logging.getLogger("agentscope").setLevel(logging.DEBUG)
```

---

## 7. PR 检查清单

这是卷三的综合检查清单，涵盖 ch20-ch26：

- [ ] **Tool（ch20）**：`query_sqlite` 有完整 type hints 和 docstring，返回 `ToolResponse`，流式版标记 `stream=True` / `is_last`
- [ ] **Model（ch21）**：`FastLLMChatModel` 继承 `ChatModelBase`，支持非流式/流式/结构化输出
- [ ] **Formatter（ch21）**：`FastLLMChatFormatter` 继承 `OpenAIChatFormatter`，`support_vision = False`
- [ ] **Memory（ch22）**：`SQLiteMemory` 继承 `MemoryBase`，五个抽象方法 + mark 系统，写操作有 lock + rollback
- [ ] **Agent（ch23）**：`PlanExecuteAgent` 继承 `AgentBase`，`_plan` 有 fallback，`_execute_plan` 支持 replan
- [ ] **集成（ch26）**：`build_system` 组装所有模块，端到端测试 + 错误场景测试通过
- [ ] 所有测试通过 `pytest tests/ -v` 和 `pre-commit run --all-files`

---

## 8. 卷三回顾与卷四预告

### 卷三回顾

从 ch19 到 ch26，你完成了从开发环境搭建到完整系统集成的全流程：

1. **ch19**：开发环境——fork、虚拟环境、pre-commit、测试运行
2. **ch20**：Tool 实战——同步函数、流式生成器、错误处理、preset_kwargs
3. **ch21**：Model 实战——非流式/流式/结构化输出、Formatter 适配
4. **ch22**：Memory 实战——aiosqlite 异步后端、mark 系统、共享数据库
5. **ch23**：Agent 实战——PlanExecuteAgent、计划修订、错误恢复
6. **ch24**：MCP Server——外部工具协议接入
7. **ch25**：高级扩展——中间件、工具分组、Agent Skills
8. **ch26**：集成实战——组装、端到端测试、错误边界、性能观察

核心收获：

- 你理解了 AgentScope 的四个核心抽象（Tool、Model、Memory、Agent）各自如何被框架加载和使用
- 你能从零写出符合框架规范的模块，并通过 `_AgentMeta`（`_agent_meta.py` 第 159-174 行）等机制自动注册
- 你掌握了跨模块错误的诊断方法：每个模块都有独立的错误处理（Tool 内部捕获、Model 的 try/except、Memory 的 rollback、Agent 的 replan），但集成时需要关注错误在模块边界的传播

### 卷四预告

卷四（"为什么这样设计"）从"怎么用"转向"为什么"。你将带着卷三的实战经验，深入理解设计决策背后的权衡：

- **ch27：消息为什么是唯一接口**——为什么所有组件用 `Msg` 通信？
- **ch28：为什么不用装饰器**——为什么 `register_tool_function` 用显式注册？
- **ch29：God Class 的辩护**——为什么 `ReActAgent`（`_react_agent.py` 第 98-1138 行）是一个大类？

卷四是"知其然"到"知其所以然"的跨越。有了卷三的代码肌肉记忆，你将真正理解这些设计为什么经得起生产环境的考验。
