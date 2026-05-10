# 附录 A：Python 高级特性速查

本附录汇总 AgentScope 源码中频繁使用的 Python 高级特性。
假设读者已掌握 Python 基础，此处仅作速查参考。

---

## A.1 async/await 与 asyncio

AgentScope 的所有 Agent 操作均为异步。核心模式如下：

```python
import asyncio

async def fetch_model_response(prompt: str) -> str:
    """异步调用模型"""
    response = await model(prompt)
    return response

async def run_multiple_agents(agents, msg):
    """并发执行多个 Agent"""
    tasks = [agent(msg) for agent in agents]
    results = await asyncio.gather(*tasks)
    return results
```

关键点：
- `async def` 定义协程函数，调用时返回 coroutine 对象（不会立即执行）
- `await` 挂起当前协程，等待被等待对象完成
- `asyncio.gather()` 并发执行多个协程
- AgentScope 中 `agent.reply()`、`model()` 均为 async 函数

```python
# AgentScope 中的实际用法 (src/agentscope/_utils/_common.py)
async def _execute_async_or_sync_func(func, *args, **kwargs):
    """统一处理同步和异步函数"""
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return func(*args, **kwargs)
```

---

## A.2 TypedDict vs dataclass vs Pydantic BaseModel

三者均用于定义结构化数据，但语义和用途不同。

### TypedDict — 类型提示用的字典

```python
from typing_extensions import TypedDict
from typing import Literal, List, Required

# AgentScope 中的消息块定义 (src/agentscope/message/_message_block.py)
class TextBlock(TypedDict, total=False):
    type: Required[Literal["text"]]
    text: str

class ToolUseBlock(TypedDict, total=False):
    type: Required[Literal["tool_use"]]
    id: Required[str]
    name: Required[str]
    input: Required[dict[str, object]]
```

特点：运行时就是普通 dict，零开销；`total=False` 表示字段可选，
`Required[]` 标记必需字段。适合 JSON Schema 一一对应的场景。

### dataclass — 轻量级数据容器

```python
from dataclasses import dataclass
from typing import Callable, Any, Optional

# AgentScope 中的序列化函数描述 (src/agentscope/module/_state_module.py)
@dataclass
class _JSONSerializeFunction:
    to_json: Optional[Callable[[Any], Any]] = None
    load_json: Optional[Callable[[Any], Any]] = None
```

特点：自动生成 `__init__`、`__repr__`；适合纯数据持有对象，
不涉及验证逻辑。

### Pydantic BaseModel — 带验证的数据模型

```python
from pydantic import BaseModel

# AgentScope 中的计划模型定义 (src/agentscope/plan/_plan_model.py)
class SubTask(BaseModel):
    name: str
    description: str
    status: str = "pending"

class Plan(BaseModel):
    goal: str
    subtasks: list[SubTask]
```

特点：自动类型转换、验证、JSON 序列化；适合配置、API 请求/响应、
需要运行时校验的场景。AgentScope 的 Plan、Tuner 配置等均使用
Pydantic。

---

## A.3 contextvars.ContextVar

用于在异步环境中安全地传递上下文状态，避免全局变量的竞态条件。

```python
from contextvars import ContextVar

# AgentScope 顶层定义 (src/agentscope/__init__.py)
_run_config_var: ContextVar = ContextVar("_run_config_var")
```

特点：
- 每个 asyncio Task 拥有独立的上下文副本
- 修改不影响其他协程的值
- 常用于存储请求级别的配置、追踪 ID 等

```python
# 设置值
_run_config_var.set(config)

# 获取值
config = _run_config_var.get()
```

---

## A.4 元类（Metaclass）

AgentScope 使用元类自动为 Agent 方法注入 Hook 机制。

```python
# src/agentscope/agent/_agent_meta.py
class _AgentMeta(type):
    """Agent 元类，自动为 reply、print、observe 方法包装 Hook"""

    def __new__(mcs, name, bases, attrs):
        # 遍历需要包装的方法名
        for func_name in ["reply", "print", "observe"]:
            if func_name in attrs:
                # 用 Hook 包装器替换原方法
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)

# 使用元类
class AgentBase(StateModule, metaclass=_AgentMeta):
    ...  # reply、print、observe 自动被 Hook 包装
```

关键概念：
- `type` 是所有类的默认元类
- `__new__(mcs, name, bases, attrs)` 在类创建时调用，可修改 attrs
- `_ReActAgentMeta` 继承 `_AgentMeta`，额外包装 `_reasoning` 和 `_acting`
- 元类是类级别的"装饰器工厂"，在定义时一次性生效

---

## A.5 AsyncGenerator 与 yield

用于流式输出（streaming）场景。

```python
from typing import AsyncGenerator

async def stream_model_response(prompt: str) -> AsyncGenerator[str, None]:
    """流式返回模型响应"""
    async for chunk in model.stream(prompt):
        yield chunk.content

# 消费端
async for text in stream_model_response("hello"):
    print(text, end="", flush=True)
```

在 AgentScope 中，Tool 函数可返回同步或异步 Generator：

```python
# src/agentscope/types/_tool.py 中的类型定义
ToolFunction = Callable[
    ...,
    Union[
        ToolResponse,                                    # 同步返回
        Awaitable[ToolResponse],                         # 异步返回
        Generator[ToolResponse, None, None],             # 同步生成器
        AsyncGenerator[ToolResponse, None],              # 异步生成器
        Coroutine[Any, Any, AsyncGenerator[...]],        # 异步函数返回异步生成器
    ],
]
```

---

## A.6 functools.wraps 与装饰器

`@wraps` 保留被装饰函数的元信息（名称、文档字符串等）。

```python
from functools import wraps

# AgentScope 中的 Hook 包装 (src/agentscope/agent/_agent_meta.py)
def _wrap_with_hooks(original_func):
    @wraps(original_func)  # 保留原函数的 __name__、__doc__ 等
    async def async_wrapper(self, *args, **kwargs):
        # 执行 pre-hooks
        ...
        # 调用原函数
        result = await original_func(self, *args, **kwargs)
        # 执行 post-hooks
        ...
        return result
    return async_wrapper
```

不使用 `@wraps` 时，`async_wrapper.__name__` 会是 `"async_wrapper"`，
导致调试和日志混乱。

---

## A.7 类型提示进阶

### Union 与 Optional

```python
from typing import Union, Optional

# Union: 多种类型之一
def process(data: Union[str, bytes]) -> str:
    ...

# Optional[X] 等价于 Union[X, None]
def register(attr_name: str, custom_to_json: Optional[Callable] = None):
    ...
```

### TypeVar 与 Generic

```python
from typing import TypeVar, Generic

T = TypeVar("T")

class Container(Generic[T]):
    def __init__(self, value: T):
        self.value = T
```

### Literal

```python
from typing import Literal

# AgentScope 中用于区分 Block 类型 (src/agentscope/message/_message_block.py)
class TextBlock(TypedDict, total=False):
    type: Required[Literal["text"]]

class ToolUseBlock(TypedDict, total=False):
    type: Required[Literal["tool_use"]]
```

`Literal` 将值限定为指定的字面量，配合 TypedDict 实现可辨识联合
（discriminated union）。

---

## A.8 `__init__.py` 重导出与 `__all__`

AgentScope 使用 `__init__.py` 控制模块的公共 API。

```python
# src/agentscope/memory/__init__.py
from ._working_memory import (
    MemoryBase,
    InMemoryMemory,
    RedisMemory,
    AsyncSQLAlchemyMemory,
    TablestoreMemory,
)
from ._long_term_memory import (
    LongTermMemoryBase,
    Mem0LongTermMemory,
    ReMePersonalLongTermMemory,
    ReMeTaskLongTermMemory,
    ReMeToolLongTermMemory,
)

__all__ = [
    "MemoryBase", "InMemoryMemory", "RedisMemory",
    "AsyncSQLAlchemyMemory", "TablestoreMemory",
    "LongTermMemoryBase", "Mem0LongTermMemory",
    "ReMePersonalLongTermMemory", "ReMeTaskLongTermMemory",
    "ReMeToolLongTermMemory",
]
```

模式要点：
- 内部模块用 `_` 前缀（如 `_working_memory`、`_base.py`）
- `__init__.py` 负责从内部模块重导出到公共命名空间
- 用户只需 `from agentscope.memory import InMemoryMemory`
- `__all__` 控制 `from module import *` 的范围，并明确公共 API

顶层 `src/agentscope/__init__.py` 同样重导出所有子模块：

```python
from . import memory
from . import model
from . import agent
# ... 等等
```

---

## A.9 deepcopy 与状态隔离

AgentScope 在 Hook 机制中使用 `deepcopy` 防止 Hook 修改原始数据。

```python
from copy import deepcopy

# src/agentscope/agent/_agent_meta.py 中的 Hook 执行
for pre_hook in pre_hooks:
    # 传递深拷贝，Hook 的修改不影响原始 kwargs
    modified_keywords = await _execute_async_or_sync_func(
        pre_hook,
        self,
        deepcopy(current_normalized_kwargs),  # 深拷贝
    )
```

`deepcopy` vs 浅拷贝：
- `dict.copy()` / `list.copy()` — 只复制一层，嵌套对象仍共享引用
- `copy.deepcopy()` — 递归复制所有层级，完全独立

在 Hook 场景中，如果不使用 deepcopy，一个 Hook 的修改会影响
后续 Hook 和原函数的输入，导致难以调试的副作用。

---

## A.10 DictMixin — 属性式字典访问

AgentScope 定义了 `DictMixin` 让响应对象同时支持 `.` 和 `[]` 访问：

```python
# src/agentscope/_utils/_mixin.py
class DictMixin(dict):
    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__
```

```python
# 使用示例
response = ChatResponse()
response.content = "hello"     # 等价于 response["content"] = "hello"
print(response.content)         # 等价于 print(response["content"])
```

`ChatResponse`、`ChatUsage`、`EmbeddingResponse`、`TTSResponse`
等均继承 `DictMixin`。

---

## A.11 `__setattr__` 拦截与自动注册

`StateModule` 通过重写 `__setattr__` 自动追踪嵌套的 StateModule 子对象：

```python
# src/agentscope/module/_state_module.py
class StateModule:
    def __setattr__(self, key, value):
        if isinstance(value, StateModule):
            self._module_dict[key] = value  # 自动注册
        super().__setattr__(key, value)
```

这意味着任何赋值操作都会被拦截检查：
- 如果值是 `StateModule` 实例，自动加入 `_module_dict`
- `state_dict()` 递归遍历 `_module_dict` 生成完整状态快照
- `load_state_dict()` 递归恢复状态

AgentBase、Toolkit、PlanNotebook 等核心类均继承 StateModule。
