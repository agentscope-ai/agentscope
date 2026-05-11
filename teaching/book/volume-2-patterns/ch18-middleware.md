# 第 18 章 中间件管道

> 本章你将理解：Toolkit 的中间件机制、洋葱模型在工具执行中的应用。

---

## 18.1 中间件模式

中间件（Middleware）是一种在请求-响应链中插入自定义逻辑的模式。每层中间件可以：

- 在请求到达目标之前修改它
- 在响应返回之后修改它
- 完全拦截请求

洋葱模型（第 10 章提到过）是中间件的标准实现方式。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 18.2 源码入口

| 文件 | 内容 |
|------|------|
| `src/agentscope/tool/_toolkit.py` | 中间件注册和执行 |
| `src/agentscope/tool/_types.py` | 中间件类型定义 |

---

## 18.3 逐行阅读

### 中间件注册

Toolkit 通过 `register_middleware()` 注册中间件：

```python
toolkit.register_middleware(logging_middleware)
toolkit.register_middleware(retry_middleware)
```

中间件是一个函数，签名如下：

```python
async def middleware(agent_self, kwargs):
    # 可以修改 kwargs
    return kwargs
```

### 执行顺序

中间件按注册顺序执行，形成洋葱：

```
请求 → logging_middleware → retry_middleware → 工具函数
                                                    ↓
响应 ← logging_middleware ← retry_middleware ← 执行结果
```

### 典型中间件

**日志中间件**：

```python
async def logging_middleware(agent_self, kwargs):
    print(f"[TOOL] 调用工具: {kwargs['name']}")
    return kwargs  # 不修改，只是记录
```

**错误重试中间件**：

```python
async def retry_middleware(agent_self, kwargs):
    for attempt in range(3):
        try:
            return kwargs  # 正常传递
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(1)
```

### 设计一瞥：中间件 vs Hook

两者都是"在不修改原函数的情况下添加行为"，但作用域不同：

| 特性 | Hook | 中间件 |
|------|------|--------|
| 作用对象 | Agent 的 `reply`, `observe`, `print` | 工具函数的执行 |
| 实现方式 | 元类自动包装 | 手动注册 |
| 位置 | `_agent_meta.py` | `_toolkit.py` |

---

## 18.4 试一试

### 编写一个计时中间件

```python
import time

async def timing_middleware(agent_self, kwargs):
    start = time.time()
    print(f"  [计时] 开始执行 {kwargs.get('name', '?')}")
    result = kwargs
    elapsed = time.time() - start
    print(f"  [计时] 耗时 {elapsed:.3f}s")
    return result

from agentscope.tool import Toolkit
toolkit = Toolkit()
toolkit.register_tool_function(get_weather)
toolkit.register_middleware(timing_middleware)
```

---

## 18.5 检查点

你现在已经理解了：

- **中间件模式**：在工具执行链中插入自定义逻辑
- **洋葱模型**：请求从外到内，响应从内到外
- **Hook vs 中间件**：Hook 用于 Agent 方法，中间件用于工具执行

---

## 下一章预告
