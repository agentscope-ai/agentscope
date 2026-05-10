# 第二十五章：高级扩展——中间件、分组与 Agent Skills

**难度**：中级

> 前几章我们学会了注册单个工具、接入 MCP Server。但在生产环境，你需要更多控制：给工具调用加限流、按权限分组、让 Agent 动态加载专项能力。本章你要亲自动手——写一个限流中间件，用 Tool Group 做权限隔离，注册 Agent Skill 扩展 Agent 的专项能力。写完之后，你会对 Toolkit 的高级管控机制有完整的实操经验。

---

## 1. 实战目标

完成本章后，你将：

1. 理解 `_apply_middlewares`（`_toolkit.py` 第 57-114 行）的洋葱模型执行流程
2. 编写一个基于时间窗口的限流中间件，并用 `register_middleware` 注册
3. 使用 `create_tool_group` / `update_tool_groups` 创建和切换工具分组
4. 理解 `get_json_schemas`（第 558-619 行）如何按 active 状态过滤工具
5. 用 `register_agent_skill` 注册包含 SKILL.md 的技能目录
6. 编写集成测试，验证中间件 + 分组 + 技能的完整协作

---

## 2. 第一步：最小可用版本——限流中间件

### 2.1 洋葱模型原理

`_apply_middlewares`（`_toolkit.py` 第 57-114 行）是装饰器，包裹 `call_tool_function`。核心机制：

1. **第 78 行**：从 `self._middlewares` 读取已注册的中间件列表
2. **第 87-91 行**：创建 `base_handler`，即原始的 `call_tool_function`
3. **第 94-108 行**：用 `reversed` 逆序遍历中间件，从内到外层层包裹。第一个注册的中间件在最外层
4. **第 111-112 行**：执行最外层 handler，用 `async for` 消费最终结果

每个中间件签名必须是：

```python
async def middleware(
    kwargs: dict,            # 包含 tool_call 等上下文
    next_handler: Callable,  # 下一个 handler
) -> AsyncGenerator[ToolResponse, None]:
```

中间件可以选择：在 `next_handler` 前做预处理、跳过调用直接 yield 错误、在 `async for` 中修改每个 response chunk。

### 2.2 最简单的限流中间件

创建文件 `examples/middleware_rate_limit.py`：

```python
# -*- coding: utf-8 -*-
"""Rate limiting middleware for toolkit."""
import time
from typing import Any, AsyncGenerator, Callable, Coroutine

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


def create_rate_limiter(min_interval: float = 1.0) -> Callable:
    """Create a rate-limiting middleware with the given minimum interval.

    Args:
        min_interval (`float`, defaults to `1.0`):
            The minimum seconds between consecutive tool calls.

    Returns:
        `Callable`: The configured middleware function.
    """
    state = {"last_call_time": 0.0}

    async def rate_limit(
        kwargs: dict,
        next_handler: Callable[
            ..., Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
        ],
    ) -> AsyncGenerator[ToolResponse, None]:
        """Enforce a minimum interval between consecutive tool calls."""
        elapsed = time.monotonic() - state["last_call_time"]
        if elapsed < min_interval:
            wait = round(min_interval - elapsed, 2)
            yield ToolResponse(content=[
                TextBlock(type="text",
                    text=f"RateLimitError: Please wait {wait}s "
                         f"before the next tool call."),
            ])
            return

        state["last_call_time"] = time.monotonic()
        async for chunk in await next_handler(**kwargs):
            yield chunk

    return rate_limit
```

要点：

- 中间件是 **async generator 函数**，签名为 `(kwargs, next_handler) -> AsyncGenerator`
- 限流触发时 `yield` 错误 `ToolResponse` 然后 `return`，不调用 `next_handler`
- 用闭包的 `state` 字典保存上次调用时间，避免全局变量

---

## 3. 第二步：注册并测试

### 3.1 注册中间件

`register_middleware`（`_toolkit.py` 第 1441-1539 行）只做一件事——把中间件追加到 `self._middlewares` 列表。第 1539 行：

```python
self._middlewares.append(middleware)
```

注册多个中间件时，第一个注册的在最外层（最后被 reversed 遍历到），最后注册的在最内层。参考 `tests/toolkit_middleware_test.py` 第 68-86 行的测试，执行顺序为：

```
middleware_1(外层) -> middleware_2(内层) -> tool -> post2 -> post1
```

### 3.2 编写测试

创建 `tests/middleware_rate_limit_test.py`：

```python
# -*- coding: utf-8 -*-
"""Test the rate-limiting middleware."""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolUseBlock
from agentscope.tool import ToolResponse, Toolkit, TextBlock


async def echo_tool(text: str) -> ToolResponse:
    """Echo the input text.

    Args:
        text (`str`): The text to echo.

    Returns:
        `ToolResponse`: The echoed text.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=f"Echo: {text}")],
    )


class RateLimitMiddlewareTest(IsolatedAsyncioTestCase):
    """Test the rate-limiting middleware."""

    async def asyncSetUp(self) -> None:
        from examples.middleware_rate_limit import create_rate_limiter
        self.toolkit = Toolkit()
        self.toolkit.register_tool_function(echo_tool)
        self.middleware = create_rate_limiter(min_interval=0.5)
        self.toolkit.register_middleware(self.middleware)

    async def test_first_call_succeeds(self) -> None:
        """第一次调用不受限流影响。"""
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="echo_tool",
                         input={"text": "hello"}, id="t1"),
        )
        async for chunk in res:
            self.assertIn("Echo: hello", chunk.content[0]["text"])

    async def test_rapid_call_blocked(self) -> None:
        """间隔过短的第二次调用被限流。"""
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="echo_tool",
                         input={"text": "first"}, id="t2"),
        )
        async for _ in res:
            pass

        # 立即第二次调用，应被限流
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="echo_tool",
                         input={"text": "second"}, id="t3"),
        )
        async for chunk in res:
            self.assertIn("RateLimitError", chunk.content[0]["text"])

    async def test_wait_then_succeeds(self) -> None:
        """等待足够时间后调用成功。"""
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="echo_tool",
                         input={"text": "first"}, id="t4"),
        )
        async for _ in res:
            pass

        await asyncio.sleep(0.6)  # 等待超过 min_interval

        res = await self.toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="echo_tool",
                         input={"text": "delayed"}, id="t5"),
        )
        async for chunk in res:
            self.assertIn("Echo: delayed", chunk.content[0]["text"])
```

运行：`pytest tests/middleware_rate_limit_test.py -v`

---

## 4. 第三步：进阶功能——工具分组与 Agent Skills

### 4.1 工具分组（Tool Groups）

`create_tool_group`（`_toolkit.py` 第 187-220 行）创建命名分组。**第 209 行**保留 `"basic"` 作为默认组名，不可创建同名 group。`ToolGroup` 数据类（`_types.py` 第 136-149 行）包含 `name`、`active`、`description`、`notes` 四个字段。

`get_json_schemas`（第 615-619 行）过滤逻辑：

```python
return [
    tool.extended_json_schema
    for tool in self.tools.values()
    if tool.group == "basic" or self.groups[tool.group].active
]
```

`basic` 组始终可见，其他组只有 `active=True` 时才暴露给 LLM。

### 4.2 创建带分组的工具集

```python
import json
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock


async def read_file(path: str) -> ToolResponse:
    """Read a text file and return its content.

    Args:
        path (`str`): The file path to read.

    Returns:
        `ToolResponse`: The file content.
    """
    with open(path, "r", encoding="utf-8") as f:
        return ToolResponse(content=[TextBlock(type="text", text=f.read())])


async def query_users(limit: int = 10) -> ToolResponse:
    """Query users from the database.

    Args:
        limit (`int`, defaults to `10`): Maximum users to return.

    Returns:
        `ToolResponse`: The query results.
    """
    users = [{"id": i, "name": f"user_{i}"} for i in range(limit)]
    return ToolResponse(
        content=[TextBlock(type="text", text=json.dumps(users, indent=2))],
    )


async def delete_user(user_id: int) -> ToolResponse:
    """Delete a user from the database.

    Args:
        user_id (`int`): The ID of the user to delete.

    Returns:
        `ToolResponse`: The deletion result.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=f"User {user_id} deleted.")],
    )


def build_toolkit() -> Toolkit:
    """Build a toolkit with grouped tools."""
    toolkit = Toolkit()
    toolkit.create_tool_group(
        "database", "Database query tools for reading data.",
        active=False,
        notes="Always start with small limit values.",
    )
    toolkit.create_tool_group(
        "admin", "Administrative tools for modifying data.",
        active=False,
        notes="WARNING: Confirm with the user before deleting.",
    )
    toolkit.register_tool_function(read_file)                            # basic
    toolkit.register_tool_function(query_users, group_name="database")
    toolkit.register_tool_function(delete_user, group_name="admin")
    return toolkit
```

### 4.3 分组激活与可见性

```python
toolkit = build_toolkit()
assert len(toolkit.get_json_schemas()) == 1   # 只有 read_file

toolkit.update_tool_groups(["database"], active=True)
assert len(toolkit.get_json_schemas()) == 2   # + query_users

toolkit.update_tool_groups(["admin"], active=True)
assert len(toolkit.get_json_schemas()) == 3   # 全部可见
```

### 4.4 调用不活跃组的工具

`call_tool_function`（第 891-909 行）在中间件执行之前检查 active 状态。不活跃组的工具返回 `FunctionInactiveError`，中间件不会触发：

```python
toolkit.update_tool_groups(["database"], active=False)
res = await toolkit.call_tool_function(
    ToolUseBlock(type="tool_use", name="query_users",
                 input={"limit": 5}, id="t1"),
)
async for chunk in res:
    assert "FunctionInactiveError" in chunk.content[0]["text"]
```

### 4.5 Agent Skills

`register_agent_skill`（第 1328-1394 行）注册技能目录，要求：

1. 目录包含 `SKILL.md` 文件
2. `SKILL.md` 有 YAML Front Matter，含 `name` 和 `description`
3. `AgentSkill` 是 `TypedDict`（`_types.py` 第 152-160 行）

创建 `examples/skills/data_analyzer/SKILL.md`：

```markdown
---
name: data_analyzer
description: Advanced data analysis skill with pandas support.
---

# Data Analyzer Skill

## How to Use
1. Read the target CSV file using `read_file` tool
2. Apply analysis as described in `analyze.py`
```

注册并获取 prompt：

```python
import os

toolkit.register_agent_skill(
    skill_dir=os.path.join("examples", "skills", "data_analyzer"),
)

prompt = toolkit.get_agent_skill_prompt()  # 第 1411-1439 行
# 包含默认 instruction + 每个技能的 name/description/dir
```

`get_agent_skill_prompt` 的输出格式由 `__init__` 参数控制：`agent_skill_instruction`（第 139-146 行默认值）和 `agent_skill_template`（第 148-150 行），后者支持 `{name}`、`{description}`、`{dir}` 占位符。

---

## 5. 第四步：错误处理——中间件中的异常传播

### 5.1 异常不会被自动捕获

`_apply_middlewares`（第 57-114 行）没有 try/except。中间件抛出的异常直接传播到 `call_tool_function` 的调用者。

### 5.2 带错误恢复的限流中间件

```python
# -*- coding: utf-8 -*-
"""Safe rate limiting middleware with error handling."""
import time
import traceback
from typing import Any, AsyncGenerator, Callable, Coroutine

from agentscope._logging import logger
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


def create_safe_rate_limiter(min_interval: float = 1.0) -> Callable:
    """Create a rate limiter with error handling.

    Args:
        min_interval (`float`, defaults to `1.0`):
            Minimum seconds between tool calls.

    Returns:
        `Callable`: The configured middleware function.
    """
    state = {"last_call_time": 0.0}

    async def safe_rate_limit(
        kwargs: dict,
        next_handler: Callable[
            ..., Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
        ],
    ) -> AsyncGenerator[ToolResponse, None]:
        """Rate-limiting middleware with error handling."""
        try:
            elapsed = time.monotonic() - state["last_call_time"]
            if elapsed < min_interval:
                wait = round(min_interval - elapsed, 2)
                yield ToolResponse(content=[
                    TextBlock(type="text",
                        text=f"RateLimitError: Retry after {wait}s."),
                ])
                return

            state["last_call_time"] = time.monotonic()
            async for chunk in await next_handler(**kwargs):
                yield chunk

        except Exception as e:
            logger.error("Middleware error: %s\n%s", e, traceback.format_exc())
            yield ToolResponse(content=[
                TextBlock(type="text", text=f"MiddlewareError: {e}"),
            ])

    return safe_rate_limit
```

关键设计：中间件自己捕获的异常返回 `ToolResponse` 保持统一接口；`next_handler` 的异常不捕获，让它传播到 `call_tool_function` 的 try/except（第 1006-1014 行），框架会包装成 `ToolResponse`。

---

## 6. 第五步：集成测试

创建 `tests/toolkit_advanced_extension_test.py`：

```python
# -*- coding: utf-8 -*-
"""Integration test for middleware, groups, and skills."""
import os
import tempfile
import time
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolUseBlock
from agentscope.tool import ToolResponse, Toolkit, TextBlock


async def basic_echo(text: str) -> ToolResponse:
    """Echo text.

    Args:
        text (`str`): Input text.

    Returns:
        `ToolResponse`: Echoed text.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=f"basic: {text}")],
    )


async def db_query(sql: str) -> ToolResponse:
    """Execute a database query.

    Args:
        sql (`str`): The SQL query.

    Returns:
        `ToolResponse`: The query result.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=f"query: {sql}")],
    )


def make_rate_limiter(interval: float) -> dict:
    """Create a rate limiter with shared state."""
    state = {"last": 0.0}

    async def limiter(kwargs, next_handler):
        elapsed = time.monotonic() - state["last"]
        if elapsed < interval:
            yield ToolResponse(content=[
                TextBlock(type="text", text="RateLimitError: Too fast."),
            ])
            return
        state["last"] = time.monotonic()
        async for chunk in await next_handler(**kwargs):
            yield chunk

    return {"middleware": limiter, "state": state}


class AdvancedExtensionTest(IsolatedAsyncioTestCase):
    """Integration test for middleware + groups + skills."""

    async def test_middleware_with_inactive_group(self) -> None:
        """不活跃组的工具在中间件之前就被拦截。"""
        toolkit = Toolkit()
        rl = make_rate_limiter(10.0)
        toolkit.register_middleware(rl["middleware"])
        toolkit.create_tool_group("db", "Database tools", active=False)
        toolkit.register_tool_function(db_query, group_name="db")

        res = await toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="db_query",
                         input={"sql": "SELECT 1"}, id="t1"),
        )
        async for chunk in res:
            self.assertIn("FunctionInactiveError", chunk.content[0]["text"])

    async def test_middleware_with_active_group(self) -> None:
        """活跃组的工具正常执行，中间件生效。"""
        toolkit = Toolkit()
        rl = make_rate_limiter(10.0)
        toolkit.register_middleware(rl["middleware"])
        toolkit.create_tool_group("db", "Database tools", active=True)
        toolkit.register_tool_function(db_query, group_name="db")

        res = await toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="db_query",
                         input={"sql": "SELECT 1"}, id="t2"),
        )
        async for chunk in res:
            self.assertIn("query: SELECT 1", chunk.content[0]["text"])

        # 第二次调用被限流
        res = await toolkit.call_tool_function(
            ToolUseBlock(type="tool_use", name="db_query",
                         input={"sql": "SELECT 2"}, id="t3"),
        )
        async for chunk in res:
            self.assertIn("RateLimitError", chunk.content[0]["text"])

    async def test_group_activation_changes_visibility(self) -> None:
        """激活/停用分组改变工具可见性。"""
        toolkit = Toolkit()
        toolkit.create_tool_group("db", "Database tools", active=False)
        toolkit.register_tool_function(basic_echo)
        toolkit.register_tool_function(db_query, group_name="db")

        self.assertEqual(len(toolkit.get_json_schemas()), 1)

        toolkit.update_tool_groups(["db"], True)
        self.assertEqual(len(toolkit.get_json_schemas()), 2)

    async def test_agent_skill_prompt(self) -> None:
        """注册技能后能获取 prompt。"""
        toolkit = Toolkit()
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "SKILL.md"), "w") as f:
                f.write("---\nname: test_skill\n"
                        "description: A test skill.\n---\n\n"
                        "# Test Skill\n\nContent.\n")
            toolkit.register_agent_skill(skill_dir=tmpdir)

        prompt = toolkit.get_agent_skill_prompt()
        self.assertIn("test_skill", prompt)

    async def test_remove_tool_group_cascades(self) -> None:
        """删除分组同时移除该分组下的工具。"""
        toolkit = Toolkit()
        toolkit.create_tool_group("db", "Database tools", active=True)
        toolkit.register_tool_function(basic_echo)
        toolkit.register_tool_function(db_query, group_name="db")
        self.assertEqual(len(toolkit.tools), 2)

        toolkit.remove_tool_groups(["db"])  # 第 241-271 行
        self.assertEqual(len(toolkit.tools), 1)
        self.assertNotIn("db_query", toolkit.tools)
```

运行：`pytest tests/toolkit_advanced_extension_test.py -v`

---

## 7. PR 检查清单

提交中间件/分组/Skill 相关 PR 前的验证步骤：

- [ ] 中间件是 async generator 函数，签名为 `(kwargs, next_handler) -> AsyncGenerator[ToolResponse, None]`
- [ ] 调用 `next_handler` 时使用 `await next_handler(**kwargs)`，不要漏掉 `await`
- [ ] 限流/鉴权失败时 `yield ToolResponse` 然后 `return`，不调用 `next_handler`
- [ ] `create_tool_group` 在 `register_tool_function` 之前调用，否则第 367-369 行会抛 `ValueError`
- [ ] 不使用 `"basic"` 作为自定义 group 名（第 209 行保留）
- [ ] Agent Skill 目录包含 `SKILL.md`，Front Matter 有 `name` 和 `description`
- [ ] 通过 `pytest tests/ -v` 全部通过
- [ ] 通过 `pre-commit run --all-files`

---

## 8. 下一章预告

下一章我们将进入 Volume 4——用 ReActAgent 构建一个完整的多工具协作应用，把前面学的工具注册、MCP 集成、中间件、分组全部串联起来。
