# 学习笔记：Python 异步编程

**日期**：2026/05/05
**学习阶段**：阶段一 - Python 基础
**关联教程**：[02 - 异步编程](../python/02_async_await.md)
**关联源码**：`src/agentscope/agent/_agent_base.py`（`observe`、`reply` 方法均为 async）

---

## 1. async/await 基础

### 核心问题：Python 的 async 和 Java 的 CompletableFuture 有什么区别？

**解答**：Python 的 async/await 是语言级协程语法，而 CompletableFuture 是库级别的异步编排。

```python
# Python - 协程
async def fetch_data(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()

# 多个并发
results = await asyncio.gather(
    fetch_data("https://api1.com"),
    fetch_data("https://api2.com"),
)
```

| Java CompletableFuture | Python async/await |
|------------------------|---------------------|
| `supplyAsync(() -> ...)` | `async def ...` + `await` |
| `thenApply(fn)` | 直接 `result = await ...` |
| `allOf(f1, f2, f3)` | `asyncio.gather(c1, c2, c3)` |
| `CompletableFuture<Void>` | `async def foo() -> None` |

---

## 2. AgentScope 中的异步模式

### 源码对照：AgentBase 的 `__call__` 方法

```python
# src/agentscope/agent/_agent_base.py 第 448 行
async def __call__(self, *args, **kwargs) -> Msg:
    """调用 reply 并广播——所有 Agent 调用都必须 await"""
    reply_msg = await self.reply(*args, **kwargs)
    await self._broadcast_to_subscribers(reply_msg)
    return reply_msg
```

**关键理解**：
- Agent 的 `reply()`、`observe()`、`print()` 全部是 `async` 方法
- 调用时**必须** `await agent(msg)`，否则拿到的是 coroutine 对象而非结果
- `_broadcast_to_subscribers` 也是 async，广播会等待所有订阅者 observe 完毕

---

## 3. 事件循环（Event Loop）

```python
import asyncio

# 获取/创建事件循环
loop = asyncio.get_event_loop()

# 运行单个协程
result = await my_async_func()

# 运行多个并发
results = await asyncio.gather(
    agent_a("问题1"),
    agent_b("问题2"),
)
```

| Java | Python |
|------|--------|
| `ForkJoinPool.commonPool()` | `asyncio.get_event_loop()` |
| `Executors.newFixedThreadPool(n)` | `asyncio.Semaphore(n)` 限流 |
| Netty EventLoop | `asyncio.loop` |

---

## 4. 我遇到的问题和调试过程

### 问题：忘记 await 导致返回 coroutine 对象

```python
# 错误写法
result = agent("你好")  # result 是 coroutine，不是 Msg
print(result)           # <coroutine object AgentBase.__call__ at 0x...>

# 正确写法
result = await agent("你好")  # result 是 Msg 对象
```

**教训**：Python 不会在运行时报错"你忘了 await"（除非后面用了 result 的属性），这是一个常见的静默错误源。

---

## 下一步学习

- [ ] 装饰器（`@wraps` 在 Hook 机制中的应用）
- [ ] 上下文管理器（MsgHub 的 `async with` 实现）
