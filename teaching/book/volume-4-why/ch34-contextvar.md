# 第 34 章：为什么用 ContextVar——并发安全的配置传递

> **难度**：进阶
>
> `agentscope.init()` 设置的全局配置（模型名、日志级别、追踪开关等）在异步环境中怎么保证安全？为什么用 `ContextVar` 而不是全局变量？

## 决策回顾

打开 `src/agentscope/_run_config.py`：

```python
# _run_config.py:15
_ConfigCls = _ConfigManager(
    trace_enabled=ContextVar(bool),
    ...
)
```

`ContextVar` 是 Python 3.7 引入的异步上下文变量。每个异步任务（`asyncio.Task`）看到自己的独立副本。

---

## 被否方案一：全局变量

**方案**：用模块级变量存配置：

```python
# 方案一：全局变量
_config = {
    "trace_enabled": False,
    "model_name": "gpt-4o",
    ...
}
```

**问题**：

```python
import asyncio

async def task_a():
    _config["model_name"] = "gpt-4o"
    await asyncio.sleep(1)
    print(_config["model_name"])  # 期望 "gpt-4o"，实际可能是 "claude"！

async def task_b():
    _config["model_name"] = "claude"

async def main():
    await asyncio.gather(task_a(), task_b())  # 并发执行

asyncio.run(main())
```

全局变量在异步并发下不安全——两个协程共享同一个变量，互相覆盖。

---

## 被否方案二：threading.local

**方案**：用线程局部存储：

```python
import threading
_config = threading.local()
_config.trace_enabled = False
```

**问题**：

Python 的 `asyncio` 是单线程的——多个协程在同一个线程中运行。`threading.local` 在异步场景下**无效**——所有协程看到同一个副本。

```
线程 1
├── 协程 A → 修改 _config.model_name = "gpt-4o"
├── 协程 B → 修改 _config.model_name = "claude"
└── 协程 A → 读取 _config.model_name → "claude"  ← 错误！
```

---

## AgentScope 的选择：ContextVar

`ContextVar` 是为 `asyncio` 设计的上下文隔离机制：

```python
from contextvars import ContextVar

trace_enabled: ContextVar[bool] = ContextVar("trace_enabled", default=False)

async def task_a():
    trace_enabled.set(True)
    await asyncio.sleep(1)
    print(trace_enabled.get())  # True——不受 task_b 影响

async def task_b():
    trace_enabled.set(False)

async def main():
    await asyncio.gather(task_a(), task_b())

asyncio.run(main())
```

每个异步任务看到自己设置的值，互不干扰。

### ContextVar 的工作原理

```
asyncio 事件循环
├── Task A 的上下文
│   └── trace_enabled = True
├── Task B 的上下文
│   └── trace_enabled = False
└── Task C 的上下文
    └── trace_enabled = <默认值>
```

`asyncio.create_task()` 会复制当前上下文。新任务修改 ContextVar 不影响父任务。

---

## 后果分析

### 好处

1. **异步安全**：每个任务有独立副本，无需加锁
2. **隐式传递**：不需要通过函数参数逐层传递配置
3. **可组合**：多个 `init()` 调用在不同的任务中互不干扰

### 麻烦

1. **调试困难**：配置值不可见——需要 `print(var.get())` 才能知道当前值
2. **测试注意**：测试中的 `init()` 调用可能影响同一任务中的其他测试
3. **API 不熟悉**：很多 Python 开发者不了解 `ContextVar`

---

## 横向对比

| 方案 | 线程安全 | 异步安全 | 适用场景 |
|------|---------|---------|---------|
| 全局变量 | 不安全 | 不安全 | 单线程脚本 |
| `threading.local` | 安全 | 不安全 | 多线程 |
| `ContextVar` | 安全 | 安全 | asyncio |
| 函数参数传递 | 安全 | 安全 | 所有场景（但代码冗长） |

AgentScope 全异步架构 → `ContextVar` 是唯一同时满足线程安全和异步安全的方案。

AgentScope 官方文档的 Getting Started > Initialization 页面展示了 `agentscope.init()` 的参数配置方法，包括模型配置、日志级别、追踪设置等。本章解释了这些配置在源码中是如何通过 `ContextVar` 存储和传递的。

Python 的 `contextvars` 模块对 ContextVar 的核心 API 说明是：

> `ContextVar.set(value) -> Token` 用于在当前上下文中设置新值。`ContextVar.get()` 返回当前上下文中的值。每个 asyncio Task 会自动获得父任务上下文的副本——子任务的修改不影响父任务。
>
> — Python 标准库文档, `contextvars` 模块

这意味着在 AgentScope 中，每个 Agent 的 asyncio Task 都有独立的配置副本，互不干扰。这正是 `agentscope.init()` 使用 `ContextVar` 存储配置的原因。

---

## 你的判断

1. 如果 AgentScope 未来要支持多线程（不仅 asyncio），ContextVar 还够用吗？
2. "隐式传递配置" vs "显式参数传递"——哪个更容易维护？

---

## 下一章预告

配置传递是跨层的问题。接下来我们看另一个跨层的设计选择——Formatter 为什么要独立于 Model 存在？
