# 第十六章：中间件与洋葱模型——工具执行的拦截层

**难度**：进阶

> 你的搜索工具被五个 agent 并发调用，API 配额快撑不住了。你需要加一层限流，但不能改工具函数的代码——它来自第三方库。你翻了翻 Toolkit 的 API，发现了 `register_middleware`。这一章拆解 AgentScope 如何用洋葱模型实现工具执行的可插拔拦截。

---

## 1. 开场场景

你有一个搜索工具，注册到了 Toolkit 中：

```python
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

toolkit = Toolkit()

async def search(query: str) -> ToolResponse:
    """Search the web."""
    result = await external_api(query)
    return ToolResponse(content=[TextBlock(type="text", text=result)])

toolkit.register_tool_function(search)
```

五个 agent 同时调用 `search`，API 的 QPS 限制是 2。你需要：
- 在调用前检查速率
- 如果超限，直接返回错误，不执行函数
- 如果正常，放行并记录耗时

你不能改 `search` 函数——它是第三方库提供的。解决方案是中间件。

---

## 2. 设计模式概览

AgentScope 的中间件采用**洋葱模型（Onion Model）**：请求从外层中间件穿入内层，最终到达工具函数；响应从内层穿出外层。每一层都可以在"穿入"时做前置处理，在"穿出"时做后置处理。

```
请求方向 -->
+--------------------------------------------------+
| Middleware A (最先注册)                            |
|   +--------------------------------------------+ |
|   | Middleware B (后注册)                       | |
|   |   +------------------------------------+   | |
|   |   | call_tool_function (实际工具执行)    |   | |
|   |   +------------------------------------+   | |
|   +--------------------------------------------+ |
+--------------------------------------------------+
<-- 响应方向
```

关键源码位置：

```
Toolkit.register_middleware()          (_toolkit.py 第 1441 行)
    |   将 middleware 追加到 self._middlewares 列表
    v
@_apply_middlewares                    (_toolkit.py 第 57 行)
    |   运行时读取 _middlewares，逆序构建调用链
    v
@trace_toolkit                         (_trace.py 第 322 行)
    |   最外层，OpenTelemetry 追踪
    v
Toolkit.call_tool_function()           (_toolkit.py 第 853 行)
```

装饰器的叠放顺序决定了执行层次：`@trace_toolkit` 在最外层，`@_apply_middlewares` 在中间，工具执行逻辑在最内层。

---

## 3. 源码分析

### 3.1 中间件的注册入口

`register_middleware` 定义在 `_toolkit.py` 第 1441-1539 行：

```python
def register_middleware(
    self,
    middleware: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]]
        | AsyncGenerator[ToolResponse, None],
    ],
) -> None:
```

实现只有一行（第 1539 行）：

```python
self._middlewares.append(middleware)
```

中间件被追加到 `self._middlewares` 列表（在 `__init__` 第 173 行初始化为空列表）。注册顺序决定了洋葱层数：先注册的中间件在最外层。

中间件函数的签名必须满足：

```python
async def my_middleware(
    kwargs: dict,           # 包含 tool_call 等上下文
    next_handler: Callable,  # 下一层处理器
) -> AsyncGenerator[ToolResponse, None]:
```

`kwargs` 字典当前包含 `tool_call`（`ToolUseBlock` 类型），未来版本可能扩展更多上下文字段。

### 3.2 _apply_middlewares：运行时装饰器

`_apply_middlewares` 定义在 `_toolkit.py` 第 57-114 行。它是一个装饰器工厂，包裹在 `call_tool_function` 上（第 852 行）。

核心结构如下：

```python
def _apply_middlewares(
    func: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> Callable[..., AsyncGenerator[ToolResponse, None]]:

    @wraps(func)
    async def wrapper(
        self: "Toolkit",
        tool_call: ToolUseBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        middlewares = getattr(self, "_middlewares", [])  # 第 78 行

        if not middlewares:
            # 快速路径：无中间件时直接调用
            async for chunk in await func(self, tool_call):  # 第 82 行
                yield chunk
            return
        ...
```

**快速路径**（第 80-84 行）：如果没有注册中间件，跳过整个洋葱构建，直接 `await func(self, tool_call)` 拿到 `AsyncGenerator`，逐个 `yield`。这确保中间件机制在未使用时零开销。

### 3.3 构建洋葱链

当有中间件时（第 86-112 行），构建过程分三步：

**第一步：定义基础处理器**（第 87-91 行）：

```python
async def base_handler(
    **kwargs: Any,
) -> AsyncGenerator[ToolResponse, None]:
    """Base handler that calls the original function."""
    return await func(self, **kwargs)
```

`base_handler` 是洋葱的最内层——它调用原始的 `call_tool_function` 方法体。注意它接收 `**kwargs`，与中间件的调用接口一致。

**第二步：逆序包装**（第 94-108 行）：

```python
current_handler = base_handler
for middleware in reversed(middlewares):

    def make_handler(mw: Callable, handler: Callable) -> Callable:

        async def wrapped(
            **kwargs: Any,
        ) -> AsyncGenerator[ToolResponse, None]:
            return mw(kwargs, handler)

        return wrapped

    current_handler = make_handler(middleware, current_handler)
```

关键在 `reversed(middlewares)`。列表中先注册的中间件排在前面，但 `reversed` 使它们成为最外层。假设注册顺序是 `[mw_a, mw_b]`：

1. `reversed` 得到 `[mw_b, mw_a]`
2. 先用 `mw_b` 包裹 `base_handler` → `handler_b`
3. 再用 `mw_a` 包裹 `handler_b` → `handler_a`

最终执行链：`mw_a → mw_b → base_handler → func`。这确保了**先注册的中间件在最外层**。

`make_handler` 是一个闭包工厂，解决 Python 循环变量捕获的经典问题——如果直接在循环内定义 `wrapped` 而不用工厂函数，所有 `wrapped` 都会捕获同一个 `middleware` 和 `handler` 引用。

**第三步：执行调用链**（第 111-112 行）：

```python
async for chunk in await current_handler(tool_call=tool_call):
    yield chunk
```

外层 `wrapper` 本身是一个 `AsyncGenerator`。它 `await current_handler(...)` 拿到内层的 `AsyncGenerator`，然后逐个 `yield` 出去。这使得洋葱链的输出被统一为流式接口。

### 3.4 中间件的执行流

回到开场的限流场景，一个实际的中间件写法如下：

```python
import asyncio
import time

_last_call_time = 0.0
_MIN_INTERVAL = 0.5  # 最小调用间隔 0.5 秒

async def rate_limit_middleware(
    kwargs: dict,
    next_handler: Callable,
) -> AsyncGenerator[ToolResponse, None]:
    """限流中间件：确保工具调用间隔不低于 MIN_INTERVAL。"""
    global _last_call_time

    now = time.monotonic()
    elapsed = now - _last_call_time

    if elapsed < _MIN_INTERVAL:
        # 前置拦截：直接返回错误，不调用 next_handler
        yield ToolResponse(
            content=[TextBlock(
                type="text",
                text=f"Rate limited. Retry after "
                     f"{_MIN_INTERVAL - elapsed:.1f}s.",
            )],
        )
        return

    # 放行：调用下一层
    _last_call_time = time.monotonic()
    async for response in await next_handler(**kwargs):
        # 可以在这里拦截和修改每个响应 chunk
        yield response

# 注册
toolkit.register_middleware(rate_limit_middleware)
```

执行流：
1. `_apply_middlewares.wrapper` 被调用
2. 构建链：`rate_limit_middleware → base_handler → call_tool_function`
3. 进入 `rate_limit_middleware`
4. 前置检查：如果限流，`yield` 错误响应后 `return`，`next_handler` 不会被调用
5. 如果放行，`await next_handler(**kwargs)` 触发 `base_handler`，进而调用原始工具函数
6. 工具函数的每个 `ToolResponse` chunk 流经 `rate_limit_middleware`，最终到达调用者

### 3.5 多层中间件的叠加

注册两个中间件后：

```python
toolkit.register_middleware(logging_middleware)     # 外层
toolkit.register_middleware(rate_limit_middleware)   # 内层
```

执行顺序：

```
请求 → logging_middleware (前置: 记录开始时间)
         → rate_limit_middleware (前置: 检查限流)
              → call_tool_function (实际执行)
         ← rate_limit_middleware (后置: 无)
     ← logging_middleware (后置: 记录耗时)
响应 ←
```

如果 `rate_limit_middleware` 拦截了请求（`return` 而不调用 `next_handler`），`call_tool_function` 不会执行，但 `logging_middleware` 的后置逻辑仍会运行——因为控制流回到了 `logging_middleware` 的 `for` 循环之后。

### 3.6 与 trace_toolkit 的协作

`call_tool_function` 上的装饰器叠放（`_toolkit.py` 第 851-853 行）：

```python
@trace_toolkit
@_apply_middlewares
async def call_tool_function(
    self,
    tool_call: ToolUseBlock,
) -> AsyncGenerator[ToolResponse, None]:
```

Python 装饰器从下往上叠：`_apply_middlewares` 先包裹函数体，`trace_toolkit` 再包裹 `_apply_middlewares` 的输出。执行时从外到内：

```
trace_toolkit (创建 OpenTelemetry span)
  → _apply_middlewares.wrapper (构建洋葱链)
    → middleware chain
      → call_tool_function 函数体
```

这个顺序是刻意的（`register_middleware` 的 docstring 第 1532-1535 行有说明）：`trace_toolkit` 在最外层，确保无论中间件是否拦截请求，追踪信息都被完整记录。

---

## 4. 设计一瞥

### 为什么中间件用 AsyncGenerator 而不是普通函数？

`call_tool_function` 的返回类型是 `AsyncGenerator[ToolResponse, None]`——一个异步生成器。中间件必须兼容这个接口，所以也必须是 `AsyncGenerator`。

这个选择带来三个能力：

**1. 流式拦截。** 工具函数可能流式返回多个 `ToolResponse` chunk（比如长时间运行的任务逐步报告进度）。中间件用 `async for response in await next_handler(**kwargs)` 逐个处理每个 chunk——可以修改、过滤、或者记录。

如果中间件是普通异步函数（只返回一个 `ToolResponse`），它无法介入流式输出的中间过程。

**2. 拦截而不执行。** 中间件可以 `yield` 一个替代响应然后 `return`，完全跳过 `next_handler`。这让中间件能实现鉴权、缓存、熔断等模式——这些模式的核心能力是"阻止执行"。

**3. 统一接口。** 所有层（`trace_toolkit`、`_apply_middlewares`、`call_tool_function`）使用同一个 `AsyncGenerator` 接口。这意味着它们可以自由组合，不需要为每一层定义不同的适配器。

### 为什么在运行时构建洋葱链而不是注册时？

`_apply_middlewares` 不是在 `register_middleware` 时构建调用链，而是在每次 `call_tool_function` 被调用时重新构建（第 78 行 `middlewares = getattr(self, "_middlewares", [])`）。

这有两个原因：

**1. 动态性。** 中间件可以在运行时增删——你可以在 agent 执行过程中调用 `toolkit._middlewares.clear()` 或 `toolkit._middlewares.append(mw)`。如果注册时构建链，运行时变更不会生效。

**2. 实例隔离。** `@_apply_middlewares` 是类级别的装饰器，但中间件列表是实例属性 `self._middlewares`。不同的 `Toolkit` 实例可以有不同的中间件组合。运行时构建确保每次调用读取的是当前实例的中间件列表。

---

## 5. 横向对比

中间件/洋葱模型在 Web 框架中是常见模式。

**Express.js (Node.js)** 用 `(req, res, next)` 三参数模型。中间件调用 `next()` 进入下一层，不调用则终止链。每个中间件操作的是同一个 `req`/`res` 对象。与 AgentScope 的区别：Express 是同步的回调模型，而 AgentScope 是异步的生成器模型——Express 中间件不能流式修改响应体。

**Django (Python)** 用 `MiddlewareMixin`，提供 `process_request` 和 `process_response` 两个钩子。中间件在配置文件中声明，启动时构建链。与 AgentScope 的区别：Django 的前置/后置处理是分开的两个方法，而 AgentScope 中间件是一个函数内完成前置和后置——中间件的代码结构更直观。

**FastAPI (Python)** 的 `BaseHTTPMiddleware` 类似 Django，但基于 `async/await`。它的问题众所周知：中间件内部不能真正流式返回响应（因为 `BaseHTTPMiddleware` 会等待整个响应体构建完毕）。AgentScope 的 `AsyncGenerator` 方案正是为了解决这个问题——每个 chunk 被逐个流经中间件，不需要等待全部完成。

**Koa (Node.js)** 最接近 AgentScope 的设计。Koa 的洋葱模型用 `async function (ctx, next)`，调用 `await next()` 进入下一层，`await next()` 返回后做后置处理。AgentScope 用 `async for ... in await next_handler(**kwargs)` 达到同样的效果，但天然支持流式输出。

| 特性 | AgentScope | Express.js | FastAPI | Koa |
|------|-----------|------------|---------|-----|
| 模型 | AsyncGenerator | 回调 | async/await | async/await |
| 流式拦截 | 原生支持 | 不支持 | 不支持 (BaseHTTPMiddleware) | 有限 |
| 动态注册 | 运行时 | 启动时 | 启动时 | 启动时 |
| 前置/后置 | 同一函数 | 同一函数 | 分开方法 | 同一函数 |

---

## 6. 调试实践

### 场景一：中间件没有被执行

**症状**：注册了限流中间件，但工具调用不受限制。

**排查步骤**：

1. 确认注册发生在调用之前。`register_middleware` 是追加式的，后注册不影响已有的调用。

2. 确认中间件签名正确。必须接收 `kwargs` 和 `next_handler` 两个参数：

```python
# 错误：缺少 next_handler 参数
async def bad_middleware(kwargs: dict):
    ...

# 正确
async def good_middleware(kwargs: dict, next_handler: Callable):
    ...
```

3. 确认中间件调用了 `next_handler`。如果忘记调用且没有 `yield` 替代响应，调用者不会收到任何 `ToolResponse`。

### 场景二：中间件执行顺序不符合预期

**症状**：后注册的中间件反而先执行了前置逻辑。

这通常是混淆了注册顺序和执行顺序。记住：**先注册 = 最外层 = 前置逻辑最先执行**。

```python
toolkit.register_middleware(mw_a)  # 最外层，前置最先执行
toolkit.register_middleware(mw_b)  # 内层，前置后执行
```

如果需要 `mw_b` 的前置逻辑在 `mw_a` 之前执行，调整注册顺序即可。

### 场景三：流式输出被中间件吞掉

**症状**：工具函数返回多个 `ToolResponse` chunk，但调用者只收到一个。

检查中间件是否只 `yield` 了最后一个 chunk：

```python
# 错误：只保留最后一个
async def bad_middleware(kwargs, next_handler):
    last = None
    async for response in await next_handler(**kwargs):
        last = response
    yield last  # 只有最后一个 chunk

# 正确：透传所有 chunk
async def good_middleware(kwargs, next_handler):
    async for response in await next_handler(**kwargs):
        yield response
```

### 场景四：调试中间件链的构建

在 `_apply_middlewares.wrapper` 中加临时日志：

```python
# 在 _toolkit.py 第 78 行后临时加入
middlewares = getattr(self, "_middlewares", [])
print(f"Active middlewares: {[mw.__name__ for mw in middlewares]}")
```

这能确认中间件列表在调用时是否包含了预期的中间件。

---

## 7. 检查点

1. `register_middleware` 将中间件追加到 `_middlewares` 列表。`_apply_middlewares` 在构建链时用 `reversed()` 遍历这个列表。为什么需要 `reversed`？如果不 `reversed` 会发生什么？
2. `_apply_middlewares` 中的 `make_handler` 工厂函数解决了什么问题？如果去掉它，直接在循环内定义 `wrapped`，会有什么 bug？
3. 中间件的前置拦截（`yield` 后 `return`）如何影响外层中间件？外层中间件的后置逻辑是否仍然执行？
4. 为什么 `_apply_middlewares` 在每次调用时重新构建洋葱链，而不是在注册时构建一次？
5. `@trace_toolkit` 和 `@_apply_middlewares` 的叠放顺序（`_toolkit.py` 第 851-852 行）能否调换？调换后对追踪有什么影响？
6. 如果一个中间件既不调用 `next_handler`，也不 `yield` 任何 `ToolResponse`，调用者会观察到什么行为？

---

## 8. 下一章预告

中间件给了你拦截工具执行的能力，但跨 agent 的工具协作需要更复杂的协调——下一章看 AgentScope 的工具组机制如何实现动态工具集管理。
