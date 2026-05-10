# 第二十章：造一个新 Tool——从零到流式的数据库查询工具

**难度**：入门

> 第八章我们追踪了 Toolkit 的注册、调用和流式机制。本章你要亲自动手——从最简单的同步查询函数开始，一步步加上流式输出、错误处理，最后接入 ReActAgent 完成集成。写完之后，你会对"一个 Tool 函数从声明到被 Agent 调用"的全链路有肌肉记忆。

---

## 1. 实战目标

完成本章后，你将：

1. 写出一个符合 AgentScope 规范的同步 Tool 函数
2. 理解 `_parse_tool_function` 如何从 docstring + type hints 生成 JSON Schema
3. 用 `Toolkit.register_tool_function` 完成注册
4. 把同步函数改造成 `AsyncGenerator`，实现流式输出
5. 在 `call_tool_function` 的 try/except 中理解错误如何变成 `ToolResponse`
6. 编写单元测试并在 ReActAgent 中完成端到端验证

---

## 2. 第一步：最小可用工具

### 2.1 Tool 函数的三个约束

打开 `src/agentscope/types/_tool.py`，可以看到 `ToolFunction` 的类型签名（第 20-36 行）：

```python
ToolFunction = Callable[
    ...,
    Union[
        ToolResponse,                                    # 同步函数
        Awaitable[ToolResponse],                         # 异步函数
        Generator[ToolResponse, None, None],             # 同步生成器
        AsyncGenerator[ToolResponse, None],              # 异步生成器
        Coroutine[Any, Any, AsyncGenerator[...]],        # async 函数返回 async gen
        Coroutine[Any, Any, Generator[...]],             # async 函数返回 sync gen
    ],
]
```

总结三个硬性要求：

1. **返回值**必须是 `ToolResponse`、`Generator[ToolResponse, ...]` 或 `AsyncGenerator[ToolResponse, ...]` 之一
2. **参数必须有 type hints**——`_parse_tool_function` 用 `inspect.signature` 提取参数类型来构建 JSON Schema
3. **docstring 必须遵循 Google 风格**——函数描述变成 `description`，`Args` 段变成每个参数的 `description`

### 2.2 最小 SQLite 查询工具

创建文件 `src/agentscope/tool/_db_query.py`：

```python
# -*- coding: utf-8 -*-
"""A simple SQLite query tool for agentscope."""
import sqlite3

from .._logging import logger
from ..message import TextBlock
from ._response import ToolResponse


def query_sqlite(
    db_path: str,
    sql: str,
) -> ToolResponse:
    """Execute a SELECT query on a SQLite database and return the results.

    Args:
        db_path (`str`):
            The path to the SQLite database file.
        sql (`str`):
            The SQL query to execute. Only SELECT statements are allowed.

    Returns:
        `ToolResponse`:
            The query results as a formatted text table.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        # Format as markdown table
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        body = "\n".join(
            "| " + " | ".join(str(cell) for cell in row) + " |"
            for row in rows
        )
        result = f"{header}\n{separator}\n{body}\n\n({len(rows)} rows)"

        return ToolResponse(
            content=[TextBlock(type="text", text=result)],
        )
    finally:
        conn.close()
```

几个要点：

- 参数 `db_path: str` 和 `sql: str` 都有 type hint
- docstring 用 Google 风格写 `Args` 段，每个参数用反引号包裹类型
- 返回值是 `ToolResponse`，content 里放 `TextBlock`
- 用 try/finally 确保连接关闭

---

## 3. 第二步：注册并测试

### 3.1 `_parse_tool_function` 做了什么

当你调用 `Toolkit.register_tool_function(query_sqlite)` 时，内部调用 `_parse_tool_function`（`src/agentscope/_utils/_common.py` 第 339-455 行）。工作流程：

1. **第 362 行**：`docstring_parser.parse()` 解析 docstring，提取 short_description 和 params
2. **第 377-432 行**：遍历 `inspect.signature(tool_func).parameters`，跳过 `self`/`cls`，根据 type hint 构建 Pydantic 字段
3. **第 434-438 行**：`create_model("_StructuredOutputDynamicClass", **fields)` 动态创建模型
4. **第 439-442 行**：`model.model_json_schema()` 生成 JSON Schema，移除 `title` 字段
5. **第 444-455 行**：组装 `{"type": "function", "function": {"name": ..., "parameters": ...}}`

对 `query_sqlite`，生成的 JSON Schema：

```json
{"type": "function", "function": {"name": "query_sqlite",
  "description": "Execute a SELECT query...",
  "parameters": {"properties": {"db_path": {"type": "string",
    "description": "The path to the SQLite database file."},
    "sql": {"type": "string", "description": "The SQL query..."}},
    "required": ["db_path", "sql"], "type": "object"}}}
```

### 3.2 注册到 Toolkit

```python
from agentscope.tool import Toolkit

toolkit = Toolkit()
toolkit.register_tool_function(query_sqlite)

# 验证 schema 生成正确
schemas = toolkit.get_json_schemas()
assert schemas[0]["function"]["name"] == "query_sqlite"
```

`register_tool_function`（`_toolkit.py` 第 274-534 行）关键步骤：

1. **第 367-369 行**：检查 group 是否存在（默认 `"basic"` 永远可用）
2. **第 416-425 行**：调用 `_parse_tool_function` 生成 schema
3. **第 441-459 行**：从 schema 移除 `preset_kwargs` 中的参数
4. **第 461-473 行**：创建 `RegisteredToolFunction` 存入 `self.tools`
5. **第 475-534 行**：处理同名冲突（raise/override/skip/rename）

### 3.3 编写单元测试

创建 `tests/tool_db_query_test.py`。遵循 `tests/toolkit_basic_test.py` 的模式——用 `IsolatedAsyncioTestCase`，通过 `Toolkit.call_tool_function` 调用，用 `async for` 消费结果：

```python
# -*- coding: utf-8 -*-
"""Test the SQLite query tool."""
import os, sqlite3, tempfile
from unittest import IsolatedAsyncioTestCase
from agentscope.message import ToolUseBlock, TextBlock
from agentscope.tool import ToolResponse, Toolkit
from agentscope.tool._db_query import query_sqlite


class DBQueryTest(IsolatedAsyncioTestCase):
    """Test the SQLite query tool."""

    def setUp(self) -> None:
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
        conn.executemany("INSERT INTO users (name, age) VALUES (?, ?)",
            [("Alice", 30), ("Bob", 25)])
        conn.commit()
        conn.close()

    async def test_query_sqlite_basic(self) -> None:
        toolkit = Toolkit()
        toolkit.register_tool_function(query_sqlite)

        # 验证 schema
        schemas = toolkit.get_json_schemas()
        self.assertEqual(schemas[0]["function"]["name"], "query_sqlite")

        # 调用工具
        res = await toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", id="t1", name="query_sqlite",
                input={"db_path": self.db_path, "sql": "SELECT * FROM users"}),
        )
        async for chunk in res:
            text = chunk.content[0]["text"]
            self.assertIn("Alice", text)
            self.assertIn("2 rows", text)

    async def test_query_sqlite_not_registered(self) -> None:
        """未注册的工具名返回 FunctionNotFoundError。"""
        toolkit = Toolkit()
        res = await toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", id="t2", name="query_sqlite",
                input={"db_path": self.db_path, "sql": "SELECT 1"}),
        )
        async for chunk in res:
            self.assertIn("FunctionNotFoundError", chunk.content[0]["text"])

    def tearDown(self) -> None:
        os.close(self.db_fd)
        os.unlink(self.db_path)
```

运行：`pytest tests/tool_db_query_test.py -v`

---

## 4. 第三步：流式输出

当查询结果很大时，一次性返回全部数据会阻塞 Agent。AgentScope 支持三种流式返回方式：

1. **`Generator[ToolResponse, None, None]`**——同步生成器
2. **`AsyncGenerator[ToolResponse, None]`**——异步生成器
3. **async 函数返回生成器**——`async def` 内 `return async_generator_func(...)`

### 4.1 流式包装机制

`src/agentscope/tool/_async_wrapper.py` 提供三个 wrapper，把不同返回类型统一成 `AsyncGenerator[ToolResponse, None]`：

- **`_object_wrapper`**（第 38-47 行）：单个 `ToolResponse` -> yield 一次的 async generator
- **`_sync_generator_wrapper`**（第 50-60 行）：同步 generator -> async generator
- **`_async_generator_wrapper`**（第 63-109 行）：async generator 直通，额外处理 `CancelledError`

`call_tool_function`（`_toolkit.py` 第 1016-1033 行）根据 `isinstance` 检查选择对应 wrapper。无论你的函数返回什么，框架都把它变成统一的 async generator。

### 4.2 改造为流式查询

在 `_db_query.py` 中添加流式版本：

```python
from typing import AsyncGenerator


async def query_sqlite_streaming(
    db_path: str,
    sql: str,
    batch_size: int = 100,
) -> AsyncGenerator[ToolResponse, None]:
    """Execute a SELECT query on a SQLite database and stream the results.

    Args:
        db_path (`str`):
            The path to the SQLite database file.
        sql (`str`):
            The SQL query to execute. Only SELECT statements are allowed.
        batch_size (`int`, defaults to `100`):
            The number of rows per streaming chunk.

    Yields:
        `ToolResponse`:
            The query results streamed in batches.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]

        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"

        # First chunk: header
        yield ToolResponse(
            content=[TextBlock(type="text", text=f"{header}\n{separator}\n")],
            stream=True,
            is_last=False,
        )

        # Subsequent chunks: rows in batches
        batch = []
        total = 0
        for row in cursor:
            batch.append(
                "| " + " | ".join(str(cell) for cell in row) + " |"
            )
            total += 1

            if len(batch) >= batch_size:
                yield ToolResponse(
                    content=[
                        TextBlock(type="text", text="\n".join(batch) + "\n")
                    ],
                    stream=True,
                    is_last=False,
                )
                batch = []

        # Remaining rows + summary
        remaining = "\n".join(batch)
        if remaining:
            remaining += "\n"

        yield ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"{remaining}\n({total} rows)",
                )
            ],
            stream=True,
            is_last=True,
        )
    finally:
        conn.close()
```

注意每个 chunk 的标记：

- `stream=True`：表示这是流式输出的一部分
- `is_last=False`：中间 chunk
- `is_last=True`：最后一个 chunk，告诉消费者数据结束

### 4.3 流式工具的测试

```python
async def test_query_sqlite_streaming(self) -> None:
    toolkit = Toolkit()
    toolkit.register_tool_function(query_sqlite_streaming)

    chunks = []
    res = await toolkit.call_tool_function(
        ToolUseBlock(type="tool_use", id="t3", name="query_sqlite_streaming",
            input={"db_path": self.db_path, "sql": "SELECT * FROM users"}),
    )
    async for chunk in res:
        chunks.append(chunk)

    # 流式至少两个 chunk：header + 数据
    self.assertGreaterEqual(len(chunks), 2)
    self.assertTrue(chunks[-1].is_last)  # 最后一个 chunk 标记结束
    full_text = "".join(c.content[0]["text"] for c in chunks)
    self.assertIn("Alice", full_text)
```

---

## 5. 第四步：错误处理

### 5.1 框架的错误捕获

`call_tool_function`（`_toolkit.py` 第 970-1014 行）用 try/except 包裹工具调用。`McpError` 和通用 `Exception` 各有一个 except 分支，都会把异常信息包装进 `ToolResponse(content=[TextBlock(type="text", text=f"Error: {e}")])`。

关键点：**所有异常都被捕获并转化为 `ToolResponse`**，不会让 Agent 崩溃。LLM 收到包含错误信息的 `ToolResultBlock`，可以自行判断是否重试。

### 5.2 在工具内部主动处理错误

虽然框架兜底了，但好的工具应在业务层面做校验。改进 `query_sqlite`，在执行前加两层检查：

```python
def query_sqlite(db_path: str, sql: str) -> ToolResponse:
    """..."""  # docstring 同上，省略
    import os

    # 校验 1：文件是否存在
    if not os.path.isfile(db_path):
        return ToolResponse(content=[
            TextBlock(type="text", text=f"Error: Database file not found: {db_path}")
        ])

    # 校验 2：只允许 SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return ToolResponse(content=[
            TextBlock(type="text",
                text=f"Error: Only SELECT statements are allowed. Got: {sql.strip()[:50]}")
        ])

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        # ... 格式化同第一步 ...
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"{header}\n{separator}\n{body}\n\n({len(rows)} rows)")],
        )
    except sqlite3.OperationalError as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"SQL Error: {e}")])
    finally:
        conn.close()
```

原则：**用 `ToolResponse` 返回错误信息，而非抛异常**。这样 LLM 能看到具体的错误描述并自行决策。

### 5.3 错误处理的测试

补充两个错误场景的测试用例：

```python
async def test_query_sqlite_error_not_found(self) -> None:
    """数据库文件不存在时返回错误信息。"""
    toolkit = Toolkit()
    toolkit.register_tool_function(query_sqlite)
    res = await toolkit.call_tool_function(
        ToolUseBlock(type="tool_use", id="test_4",
            name="query_sqlite",
            input={"db_path": "/nonexistent/path.db", "sql": "SELECT 1"}),
    )
    async for chunk in res:
        self.assertIn("not found", chunk.content[0]["text"])

async def test_query_sqlite_error_non_select(self) -> None:
    """非 SELECT 语句被拒绝。"""
    toolkit = Toolkit()
    toolkit.register_tool_function(query_sqlite)
    res = await toolkit.call_tool_function(
        ToolUseBlock(type="tool_use", id="test_5",
            name="query_sqlite",
            input={"db_path": self.db_path, "sql": "DROP TABLE users"}),
    )
    async for chunk in res:
        self.assertIn("Only SELECT", chunk.content[0]["text"])
```

---

## 6. 第五步：集成测试

### 6.1 用 ReActAgent 调用

```python
import asyncio, sqlite3, tempfile, os
from agentscope.agents import ReActAgent
from agentscope.models import ModelManager, ModelConfig
from agentscope.message import Msg

async def test_integration() -> None:
    # 准备测试数据库
    fd, db_path = tempfile.mkstemp(suffix=".db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE products (id INT, name TEXT, price REAL)")
    conn.executemany("INSERT INTO products VALUES (?, ?, ?)",
        [(1, "Widget", 9.99), (2, "Gadget", 24.99)])
    conn.commit()
    conn.close()

    ModelManager.add_model_config(ModelConfig(
        config_name="test_model", model_type="openai_chat",
        model_name="gpt-4o-mini",
    ))

    agent = ReActAgent(name="db_agent", model_config_name="test_model")
    agent.toolkit.register_tool_function(query_sqlite)

    response = await agent(Msg(
        name="user",
        content=f"查询 {db_path} 里有哪些产品，价格分别是多少？",
    ))
    print(response.content)
    os.close(fd)
    os.unlink(db_path)

asyncio.run(test_integration())
```

### 6.2 使用 preset_kwargs 隐藏 db_path

如果数据库路径对 Agent 是固定的，可以用 `preset_kwargs` 隐藏它：

```python
agent.toolkit.register_tool_function(
    query_sqlite,
    preset_kwargs={"db_path": db_path},
)
```

注册后，`db_path` 会从 JSON Schema 的 properties 和 required 中移除（`_toolkit.py` 第 441-459 行）。LLM 只看到 `sql` 参数，不知道数据库路径。

---

## 7. PR 检查清单

提交 Tool 相关 PR 前的验证步骤：

- [ ] 函数有完整的 type hints，所有参数都有类型标注
- [ ] docstring 遵循 Google 风格，包含 `Args` 和 `Returns` 段
- [ ] 返回值是 `ToolResponse`，content 中使用 `TextBlock`
- [ ] 错误场景返回 `ToolResponse`（包含错误信息），不抛异常
- [ ] 通过 `pytest tests/ -v` 全部通过
- [ ] 通过 `pre-commit run --all-files`
- [ ] 使用 `from .._logging import logger` 而非 `print()`
- [ ] 第三方库在函数内部导入（lazy import）
- [ ] 文件命名以 `_` 开头（内部模块）或在 `__init__.py` 中导出（公开 API）

---

## 8. 下一章预告

下一章我们将造一个 Memory 后端——把数据存到 Redis，实现跨会话的记忆持久化。
