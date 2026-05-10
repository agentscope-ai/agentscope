# 第三十二章 为什么用 ContextVar

在 `src/agentscope/__init__.py` 第 22-41 行，框架的全局配置不是用模块级变量存储的，而是用五个 `ContextVar` 实例包裹后传入 `_ConfigCls`：

```python
_config = _ConfigCls(
    run_id=ContextVar("run_id", default=shortuuid.uuid()),
    project=ContextVar("project", default="UnnamedProject_At" + ...),
    name=ContextVar("name", default=...),
    created_at=ContextVar("created_at", default=...),
    trace_enabled=ContextVar("trace_enabled", default=False),
)
```

注释写得很清楚（第 21 行）：`A thread and async safe global configuration instance`。这不是一句空话——`ContextVar` 是 Python 3.7 引入的标准库原语，专门为协程和线程的并发场景设计，让每个执行上下文拥有独立的变量副本。

## 一、决策回顾

### 1.1 _ConfigCls 的封装方式

`_run_config.py` 全文只有 73 行。它的核心设计是把 `ContextVar` 的 `.get()` / `.set()` 接口封装成 Python property：

```python
class _ConfigCls:
    def __init__(
        self,
        run_id: ContextVar[str],
        project: ContextVar[str],
        name: ContextVar[str],
        created_at: ContextVar[str],
        trace_enabled: ContextVar[bool],
    ) -> None:
        self._run_id = run_id
        self._project = project
        self._name = name
        self._created_at = created_at
        self._trace_enabled = trace_enabled

    @property
    def run_id(self) -> str:
        return self._run_id.get()

    @run_id.setter
    def run_id(self, value: str) -> None:
        self._run_id.set(value)
```

每个字段都是同样的模式：`ContextVar` 实例存为私有属性 `_xxx`，property 的 getter 调用 `.get()`，setter 调用 `.set()`。对外表现为普通的属性读写，内部自动完成上下文隔离。

这意味着在 `__init__.py` 的 `init()` 函数中（第 106-113 行），设置配置的代码看起来和操作普通对象一样：

```python
if project:
    _config.project = project
if name:
    _config.name = name
if run_id:
    _config.run_id = run_id
```

而在消费端，读取配置也像访问普通属性：

```python
# tracing/_extractor.py 第 61 行
_config.run_id

# tracing/_trace.py 第 77 行
return _config.trace_enabled

# agent/_user_input.py 第 207 行
_config.project

# hooks/__init__.py 第 27 行
run_id=_config.run_id
```

### 1.2 消费点分布

`_config` 在代码库中被六个模块导入使用：

- `tracing/_extractor.py`（第 6 行）：读取 `run_id` 作为 OpenTelemetry span 的 `conversation_id`
- `tracing/_trace.py`（第 18 行）：读取 `trace_enabled` 判断是否启用追踪
- `agent/_user_input.py`（第 18 行）：读取 `project` 用于 Studio 日志
- `hooks/__init__.py`（第 8 行）：读取 `run_id` 注册 Studio 钩子
- `tuner/model_selection/_model_selection.py`（第 350 行）：设置 `trace_enabled = True`
- `evaluate/_evaluator/_general_evaluator.py`（第 91 行）和 `_ray_evaluator.py`（第 140 行）：设置 `trace_enabled = True`

这些消费点分布在追踪、代理、钩子、调参、评测五个子系统中。如果配置需要通过函数参数传递，意味着每一个调用链——从 `init()` 到 `agent.reply()` 到 `tracer.start_span()`——都需要透传 `run_id`、`project`、`trace_enabled` 等参数。

### 1.3 并发场景的需求

AgentScope 是一个异步优先的框架。`AgentBase.reply` 是 `async def`，`ReActAgent` 的推理循环在协程中执行，`MsgHub` 可能同时管理多个 Agent 的消息流。在 `evaluate/_evaluator/_general_evaluator.py` 中（第 93 行），评测器在协程内设置 `_config.trace_enabled = True`：

```python
from ... import _config
_config.trace_enabled = True
solution_result = await solution(task, ...)
```

如果有两个评测任务并发运行，一个启用了 trace，一个没有，`ContextVar` 保证每个协程看到自己的 `trace_enabled` 值，互不干扰。如果用模块级全局变量，第二个协程的 `_config.trace_enabled = True` 会影响第一个协程的行为。

## 二、被否方案

### 2.1 模块级全局变量

最简单的方案——用模块顶层变量存配置：

```python
# _run_config.py（伪代码）
_run_id: str = shortuuid.uuid()
_project: str = "UnnamedProject"
_name: str = "default"
_trace_enabled: bool = False

def get_run_id() -> str:
    return _run_id

def set_run_id(value: str) -> None:
    global _run_id
    _run_id = value
```

优点：零学习成本，调试时直接 `print(_run_id)`。缺点：在 asyncio 场景中，两个协程共享同一个 `_run_id`。如果协程 A 调用 `set_run_id("eval-1")`，协程 B 读取 `get_run_id()` 会得到 `"eval-1"`，而不是 B 期望的值。

在 AgentScope 的场景中，`evaluate` 模块的 `_general_evaluator.py`（第 85-102 行）和 `_ray_evaluator.py`（第 140-158 行）都在并发上下文中设置 `trace_enabled`。如果用全局变量，后设置的值会覆盖前一个，导致追踪行为不可预测。

### 2.2 threading.local

Python 传统的线程隔离方案：

```python
import threading

_config_local = threading.local()
_config_local.run_id = shortuuid.uuid()
_config_local.project = "UnnamedProject"
_config_local.trace_enabled = False
```

`threading.local` 在多线程环境中工作良好——每个线程有独立的存储空间。问题在于它不了解 asyncio 的协程。在同一个线程中运行的两个协程共享同一个 `threading.local` 实例，因为 asyncio 默认在单线程中调度所有协程。

```python
import asyncio
import threading

local = threading.local()
local.value = "main"

async def task_a():
    local.value = "A"     # 修改了共享的 local
    await asyncio.sleep(0.1)
    print(local.value)    # 可能是 "B"，不是 "A"

async def task_b():
    local.value = "B"     # 覆盖了 A 的值

async def main():
    await asyncio.gather(task_a(), task_b())
```

AgentScope 的 `evaluate` 模块用 `asyncio.gather` 并发运行评测任务。`threading.local` 在这种场景下不能提供协程级别的隔离。

### 2.3 参数透传

把配置作为参数传入每个需要的函数：

```python
class AgentBase:
    async def reply(self, msg: Msg, *, run_id: str, project: str,
                    trace_enabled: bool) -> Msg:
        ...

class ChatModelBase:
    async def __call__(self, messages: list[Msg], *, run_id: str,
                       trace_enabled: bool) -> ChatResponse:
        ...

def _get_common_attributes(run_id: str) -> Dict[str, str]:
    return {"conversation_id": run_id}
```

优点：依赖关系显式可见，测试时直接传参。缺点：`run_id` 和 `trace_enabled` 需要从 `init()` 一路透传到 `tracing/_extractor.py` 的 `_get_common_attributes()`。中间的 `AgentBase.reply()` → `ReActAgent` 的推理循环 → `ChatModelBase.__call__()` → `Formatter.format()` → `_get_common_attributes()` 这条链路上，每一层都需要在签名中添加这些参数。

这是一种被称为"参数钻孔"（parameter drilling）的反模式：配置参数像钻头一样从顶层穿透所有中间层，即使中间层根本不使用这些参数。

## 三、后果分析

### 3.1 收益

**协程级隔离。** `ContextVar` 的核心特性：每个 asyncio Task 拥有独立的变量副本。当 `Task A` 调用 `_config.run_id = "eval-1"` 时，`Task B` 的 `_config.run_id` 不受影响。这在 `_general_evaluator.py` 第 93 行的并发评测场景中至关重要。

Python 3.7 的 `ContextVar` 在 `asyncio` 中自动生效。当一个协程通过 `asyncio.create_task()` 创建子任务时，子任务继承父任务的上下文副本。子任务对 `ContextVar` 的修改不会传播回父任务。这是 Python 语言规范保证的行为，不是第三方库的约定。

**零侵入读取。** 从消费端代码看，读取 `_config.run_id` 和读取普通属性没有区别。`tracing/_extractor.py` 第 61 行直接写 `_config.run_id`，不需要知道这是 `ContextVar` 还是全局变量。`_ConfigCls` 的 property 封装（`_run_config.py` 第 25-33 行）把 `.get()` 调用隐藏在 getter 内部。

**无需参数透传。** 六个消费 `_config` 的模块（tracing、agent、hooks、tuner、evaluate）分布在不同的调用深度。最深的一条路径从 `init()` 到 `tracing/_extractor.py` 的 `_get_common_attributes()` 经过了四层调用。如果用参数透传，这条路径上的每个函数签名都需要添加 `run_id` 参数。`ContextVar` 让消费端直接 `from .. import _config` 就能获取正确的值。

### 3.2 代价

**隐式依赖。** `tracing/_extractor.py` 第 6 行的 `from .. import _config` 是一个隐式依赖。函数 `_get_common_attributes()`（第 52 行）的签名中没有 `run_id` 参数，但它内部读取了 `_config.run_id`。阅读这个函数的签名无法知道它依赖运行时配置。必须阅读函数体才能发现这个依赖。

对比参数透传方案，`_get_common_attributes(run_id: str)` 的签名明确声明了依赖。在大型代码库中，显式依赖更容易追踪和重构。

**测试复杂度增加。** 测试 `_get_common_attributes()` 时，需要先设置 `ContextVar` 的值：

```python
from agentscope import _config

def test_get_common_attributes():
    old_run_id = _config.run_id
    _config.run_id = "test-run-id"     # 设置 ContextVar
    try:
        attrs = _get_common_attributes()
        assert attrs["conversation_id"] == "test-run-id"
    finally:
        _config.run_id = old_run_id    # 恢复原值
```

如果不恢复原值，后续测试可能读到被污染的配置。`ContextVar.set()` 返回一个 `Token` 对象，可以用 `ContextVar.reset(token)` 恢复，但 `_ConfigCls` 的 property setter 没有暴露这个 token。这意味着恢复操作需要再次调用 setter，而不是原子性的 reset。

**调试不透明。** 当 `_config.run_id` 返回意外值时，无法通过简单的断点或日志追踪"谁在什么时候设置了这个值"。`ContextVar` 的修改点分散在 `init()`、`_general_evaluator.py`、`_ray_evaluator.py`、`_model_selection.py` 等多处。没有全局的变更通知机制。

### 3.3 实际影响评估

AgentScope 的 `_config` 只有五个字段，全部是简单类型（`str` 或 `bool`）。设置点集中在 `init()` 函数和评测/调参模块。消费点虽然分散在六个模块，但读取模式都是简单的属性访问。

`_ConfigCls` 的封装（73 行代码）是合理的折中：它把 `ContextVar` 的 `.get()`/`.set()` API 隐藏在 property 后面，让调用者不需要了解 `ContextVar` 的存在。如果将来需要替换底层机制（比如换成显式的配置对象），只需要修改 `_run_config.py` 这一个文件，消费端代码不变。

## 四、横向对比

### 4.1 LangGraph

LangGraph 使用 Python 的 `contextvars` 模块实现配置管理。它定义了多个 `ContextVar`（如 `config`）来传递运行时配置。LangGraph 的方案与 AgentScope 类似，但它的 ContextVar 消费更深——图的每个节点执行时都需要读取上下文配置。

LangGraph 还提供了 `ConfigurableRunnable` 类，允许在运行时动态配置链的行为。这是 `ContextVar` 之上的进一步封装。

### 4.2 Prefect

Prefect 大量使用 `contextvars` 来管理任务运行上下文。它的 `TaskRunContext` 和 `FlowRunContext` 都是 `ContextVar`，用于在任务执行期间提供对运行时状态（任务 ID、重试计数、缓存等）的访问。

Prefect 的模式比 AgentScope 更重——它的 ContextVar 存储的是完整的上下文对象，包含十几个字段，而 AgentScope 的每个 ContextVar 只存储一个标量值。

### 4.3 OpenTelemetry SDK

OpenTelemetry 的 Python SDK 使用 `ContextVar` 存储 trace context（`Context` 对象）。这是 `ContextVar` 在 Python 生态中最核心的应用场景之一。OpenTelemetry 的 `contextvars` 用法和 AgentScope 如出一辙：异步代码中每个 span 有独立的上下文。

AgentScope 的 tracing 模块本身就是 OpenTelemetry 的消费者（`tracing/_trace.py` 导入了 `opentelemetry`），所以使用 `ContextVar` 管理 `run_id` 在概念上与 OpenTelemetry 的 trace context 保持一致。

### 4.4 对比总结

| 方案 | 协程隔离 | 线程隔离 | 读取侵入性 | 参数透传 |
|------|---------|---------|-----------|---------|
| ContextVar | 有 | 有 | 零 | 不需要 |
| 模块级变量 | 无 | 无 | 零 | 不需要 |
| threading.local | 无 | 有 | 零 | 不需要 |
| 参数透传 | 有 | 有 | 高 | 必须 |

AgentScope 选择了第一行。这和 LangGraph、Prefect、OpenTelemetry 的选择一致——当框架需要在异步环境中传递"横向"配置时，`ContextVar` 是 Python 标准库提供的唯一协程感知原语。

## 五、你的判断

回顾 `_run_config.py` 的 74 行代码和 `__init__.py` 第 22-41 行的配置初始化。整章讨论的核心问题是：

**当配置需要在协程之间隔离，但又不想污染每个函数签名时，隐式状态是否是可以接受的代价？**

`ContextVar` 方案的前提假设是：`run_id`、`trace_enabled` 这些配置属于"环境信息"而非"业务数据"。它们描述的是"当前运行在什么上下文中"，而不是"当前要做什么"。环境信息通过 `ContextVar` 隐式传递，业务数据通过函数参数显式传递——这个区分在概念上是清晰的。

但边界并不总是清楚。如果将来某个配置字段（比如 `trace_enabled`）需要根据请求动态决定，那它就从"环境信息"变成了"业务参数"，但仍然通过 `ContextVar` 传递。这时隐式状态的不可见性就变成了负担。

你可以问自己：在你的项目中，"环境信息"和"业务数据"的边界在哪里？`ContextVar` 的隐式传递在哪个规模点开始让代码变得难以理解？有没有一种折中方案——比如用依赖注入容器替代 `ContextVar`——能在隔离性和可见性之间取得更好的平衡？
