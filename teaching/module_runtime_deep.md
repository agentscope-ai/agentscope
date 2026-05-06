# Runtime 运行时系统源码深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [核心类继承体系](#3-核心类继承体系)
4. [源码解读](#4-源码解读)
   - [SequentialPipeline 顺序执行器](#41-sequentialpipeline-顺序执行器)
   - [FanoutPipeline 并发分发器](#42-fanoutpipeline-并发分发器)
   - [sequential_pipeline 顺序执行函数](#43-sequential_pipeline-顺序执行函数)
   - [fanout_pipeline 并发分发函数](#44-fanout_pipeline-并发分发函数)
   - [stream_printing_messages 流式消息处理](#45-stream_printing_messages-流式消息处理)
   - [关键方法流程图](#46-关键方法流程图)
5. [设计模式总结](#5-设计模式总结)
6. [代码示例](#6-代码示例)
7. [练习题](#7-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 SequentialPipeline 和 FanoutPipeline 的核心方法 | 列举、识别 |
| 理解 | 解释顺序执行与并发分发两种模式的设计意图与适用场景 | 解释、比较 |
| 应用 | 使用 `sequential_pipeline()` 和 `fanout_pipeline()` 构建多代理执行流 | 实现、配置 |
| 分析 | 分析 `stream_printing_messages` 的流式收集与打印机制 | 分析、追踪 |
| 评价 | 评价类接口（Pipeline 类）与函数接口（_functional.py）的优劣 | 评价、推荐 |
| 创造 | 设计一个支持条件分支的自定义 Pipeline 模式 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python `asyncio` 基础（`async def`、`await`、`asyncio.gather`）
- [ ] 委托模式（Delegation Pattern）概念
- [ ] `deepcopy` 的用途与性能影响
- [ ] AgentBase 的基本接口（参见智能体模块）

**预计学习时间**: 35 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `async def` / `await` | `CompletableFuture` / `thenCompose` | 异步组合 |
| `asyncio.gather()` | `CompletableFuture.allOf()` | 并发等待所有任务 |
| `deepcopy(msg)` | `msg.clone()` / 序列化深拷贝 | 确保消息独立 |
| 委托模式 | Decorator 模式 | 组合优于继承 |
| `async for ... in` | `Flux.fromStream()` | 异步迭代 |

---

## 1. 模块概述

> **交叉引用**: 本模块详解 SequentialPipeline 和 FanoutPipeline 的运行时执行逻辑。完整的 Pipeline 基础设施（含 MsgHub、Formatter、Session、Tracing）请参见 [Pipeline 与基础设施深度分析](module_pipeline_infra_deep.md)。消息在管道间的传递和拷贝依赖 Msg 类，参见 [Message 消息系统深度分析](module_message_deep.md)。
>
> **与 Pipeline 基础设施模块的关系**: 本模块侧重 Pipeline 的**执行逻辑**（SequentialPipeline/FanoutPipeline 的源码级分析）。[module_pipeline_infra_deep.md](module_pipeline_infra_deep.md) 则从**架构层面**覆盖 Pipeline + MsgHub + Formatter + Session + Tracing 的完整基础设施栈。建议先读本模块理解执行机制，再读 Pipeline 基础设施模块理解整体架构。

Runtime 模块是 AgentScope 的执行运行时系统，负责管理和协调多代理的执行流程。该模块提供了两种核心的执行模式：

1. **顺序执行 (Sequential)**: 多个代理按顺序链式执行，一个代理的输出作为下一个代理的输入
2. **并发分发 (Fanout)**: 同一输入同时分发给多个代理并行处理，结果通过 gather 收集

### 1.1 核心价值

- **异步执行**: 基于 asyncio 实现高效的异步并发
- **可复用性**: 提供类和函数两种接口，支持 pipeline 实例复用
- **流式支持**: 内置流式消息打印收集机制
- **深度拷贝**: 自动处理消息拷贝，避免状态污染

---

## 2. 目录结构

```
src/agentscope/pipeline/
├── __init__.py           # Pipeline 模块导出
├── _class.py             # SequentialPipeline, FanoutPipeline 类
├── _functional.py        # 顺序/分发/流式处理函数实现
├── _msghub.py            # MsgHub 消息广播（详见调度器模块）
└── _chat_room.py         # ChatRoom 实时代理通信
```

---

## 3. 核心类继承体系

```
SequentialPipeline                    FanoutPipeline
    │                                     │
    ├── agents: list[AgentBase]           ├── agents: list[AgentBase]
    │                                     ├── enable_gather: bool
    │                                     │
    └── __call__(msg)                     └── __call__(msg, **kwargs)
            │                                     │
            ▼                                     ▼
    sequential_pipeline()              fanout_pipeline()
```

**注意**: SequentialPipeline 和 FanoutPipeline 不继承任何基类（`class SequentialPipeline:`），而是组合持有 `list[AgentBase]` 实例，通过委托模式将 `__call__` 转发给对应的函数式实现。

---

## 4. 源码解读

### 4.1 SequentialPipeline 顺序执行器

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_class.py` (第 10-40 行)

```python
class SequentialPipeline:
    """An async sequential pipeline class, which executes a sequence of
    agents sequentially. Compared with functional pipeline, this class
    can be re-used."""

    def __init__(
        self,
        agents: list[AgentBase],
    ) -> None:
        """Initialize a sequential pipeline class

        Args:
            agents (`list[AgentBase]`):
                A list of agents.
        """
        self.agents = agents

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg | list[Msg] | None:
        """Execute the sequential pipeline

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The initial input that will be passed to the first agent.
        """
        return await sequential_pipeline(
            agents=self.agents,
            msg=msg,
        )
```

**源码分析**:
- 第 10-13 行: 类的 docstring 说明这是顺序执行管道，用于链式执行多个代理
- 第 25 行: `self.agents` 保存代理列表
- 第 27-40 行: `__call__` 使实例可调用，内部委托给 `sequential_pipeline` 函数

### 4.2 FanoutPipeline 并发分发器

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_class.py` (第 43-90 行)

```python
class FanoutPipeline:
    """An async fanout pipeline class, which distributes the same input to
    multiple agents. Compared with functional pipeline, this class can be
    re-used and configured with default parameters."""

    def __init__(
        self,
        agents: list[AgentBase],
        enable_gather: bool = True,
    ) -> None:
        """Initialize a fanout pipeline class

        Args:
            agents (`list[AgentBase]`):
                A list of agents to execute.
            enable_gather (`bool`, defaults to `True`):
                Whether to execute agents concurrently
                using `asyncio.gather()`. If False, agents are executed
                sequentially.
        """
        self.agents = agents
        self.enable_gather = enable_gather

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Execute the fanout pipeline

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The input message that will be distributed to all agents.
            **kwargs (`Any`):
                Additional keyword arguments passed to each agent during
                execution.

        Returns:
            `list[Msg]`:
                A list of output messages from all agents.
        """

        return await fanout_pipeline(
            agents=self.agents,
            msg=msg,
            enable_gather=self.enable_gather,
            **kwargs,
        )
```

**源码分析**:
- 第 63-64 行: `enable_gather=True` 时并发执行，`False` 时顺序执行
- 第 66-90 行: `__call__` 支持 `**kwargs` 传递给每个代理

### 4.3 sequential_pipeline 顺序执行函数

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py` (第 10-46 行)

```python
async def sequential_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
) -> Msg | list[Msg] | None:
    """An async syntactic sugar pipeline that executes a sequence of agents
    sequentially. The output of the previous agent will be passed as the
    input to the next agent. The final output will be the output of the
    last agent.

    Example:
        .. code-block:: python

            agent1 = ReActAgent(...)
            agent2 = ReActAgent(...)
            agent3 = ReActAgent(...)

            msg_input = Msg("user", "Hello", "user")

            msg_output = await sequential_pipeline(
                [agent1, agent2, agent3],
                msg_input
            )

    Args:
        agents (`list[AgentBase]`):
            A list of agents.
        msg (`Msg | list[Msg] | None`, defaults to `None`):
            The initial input that will be passed to the first agent.
    Returns:
        `Msg | list[Msg] | None`:
            The output of the last agent in the sequence.
    """
    for agent in agents:
        msg = await agent(msg)
    return msg
```

**源码分析**:
- 第 42-43 行: 核心逻辑 - 遍历每个代理，将前一个代理的输出作为下一个代理的输入
- 简单而高效的同步链式调用模式

### 4.4 fanout_pipeline 并发分发函数

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py` (第 47-106 行)

```python
async def fanout_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    enable_gather: bool = True,
    **kwargs: Any,
) -> list[Msg]:
    """A fanout pipeline that distributes the same input to multiple agents.
    This pipeline sends the same message (or a deep copy of it) to all agents
    and collects their responses. Agents can be executed either concurrently
    using asyncio.gather() or sequentially depending on the enable_gather
    parameter.

    Example:
        .. code-block:: python

            agent1 = ReActAgent(...)
            agent2 = ReActAgent(...)
            agent3 = ReActAgent(...)

            msg_input = Msg("user", "Hello", "user")

            # Concurrent execution (default)
            results = await fanout_pipeline(
                [agent1, agent2, agent3],
                msg_input
            )

            # Sequential execution
            results = await fanout_pipeline(
                [agent1, agent2, agent3],
                msg_input,
                enable_gather=False
            )

    Args:
        agents (`list[AgentBase]`):
            A list of agents.
        msg (`Msg | list[Msg] | None`, defaults to `None`):
            The initial input that will be passed to all agents.
        enable_gather (`bool`, defaults to `True`):
            Whether to execute agents concurrently using `asyncio.gather()`.
            If False, agents are executed sequentially.
        **kwargs (`Any`):
            Additional keyword arguments passed to each agent during execution.

    Returns:
        `list[Msg]`:
            A list of response messages from each agent.
    """
    if enable_gather:
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in agents
        ]

        return await asyncio.gather(*tasks)
    else:
        return [await agent(deepcopy(msg), **kwargs) for agent in agents]
```

**源码分析**:
- 第 97-99 行: **并发模式** - 使用 `asyncio.create_task` 为每个代理创建任务，用 `deepcopy` 避免状态共享
- 第 103-104 行: **顺序模式** - 列表推导式逐个 await 执行
- 第 98 行: 消息通过 `deepcopy` 拷贝，保证每个代理收到独立副本

### 4.5 stream_printing_messages 流式消息处理

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py` (第 107-192 行)

```python
async def stream_printing_messages(
    agents: list[AgentBase],
    coroutine_task: Coroutine,
    queue: asyncio.Queue | None = None,
    end_signal: str = "[END]",
    yield_speech: bool = False,
) -> AsyncGenerator[
    Tuple[Msg, bool] | Tuple[Msg, bool, AudioBlock | list[AudioBlock] | None],
    None,
]:
    """This pipeline will gather the printing messages from agents when
    execute the given coroutine task, and yield them one by one.
    Only the messages that are printed by `await self.print(msg)` in the agent
    will be forwarded to the message queue and yielded by this pipeline.
    ...
    """
    # Enable the message queue to get the intermediate messages
    queue = queue or asyncio.Queue()
    for agent in agents:
        # Use one queue to gather messages from all agents
        agent.set_msg_queue_enabled(True, queue)

    # Execute the agent asynchronously
    task = asyncio.create_task(coroutine_task)

    if task.done():
        await queue.put(end_signal)
    else:
        task.add_done_callback(lambda _: queue.put_nowait(end_signal))

    # Receive the messages from the agent's message queue
    while True:
        printing_msg = await queue.get()

        if isinstance(printing_msg, str) and printing_msg == end_signal:
            break

        if yield_speech:
            yield printing_msg
        else:
            msg, last, _ = printing_msg
            yield msg, last

    # Check exception after processing all messages
    exception = task.exception()
    if exception is not None:
        raise exception from None
```

**源码分析**:
- 第 159-163 行: 为所有代理启用共享消息队列
- 第 165-171 行: 创建异步任务并注册完成回调
- 第 173-187 行: 循环从队列获取消息，遇到结束信号退出
- 第 189-192 行: 任务完成后检查并重新抛出异常

### 4.6 关键方法流程图

#### SequentialPipeline 执行流程

```
输入消息
    │
    ▼
┌─────────────────┐
│  遍历 agents    │
└────────┬────────┘
         │
    ┌────▼────┐
    │ agent1  │
    │(await)  │
    └────┬────┘
         │ 输出1
    ┌────▼────┐
    │ agent2  │
    │(await)  │
    └────┬────┘
         │ 输出2
    ┌────▼────┐
    │ agent3  │
    │(await)  │
    └────┬────┘
         │
         ▼
    最终输出
```

#### FanoutPipeline 执行流程 (enable_gather=True)

```
输入消息
    │
    ├──deepcopy──▶ agent1 ──┐
    ├──deepcopy──▶ agent2 ──┼── asyncio.gather ──▶ [r1, r2, r3]
    └──deepcopy──▶ agent3 ──┘
```

---

## 5. 设计模式总结

| 模式 | 应用位置 | 说明 |
|------|----------|------|
| **委托模式** | SequentialPipeline, FanoutPipeline | 类委托给同名函数执行，保留可复用性 |
| **异步并发** | fanout_pipeline | 使用 asyncio.gather 实现真并发 |
| **深度拷贝** | fanout_pipeline | deepcopy 避免消息状态污染 |
| **生成器模式** | stream_printing_messages | AsyncGenerator 逐个 yield 流式消息 |
| **任务回调** | stream_printing_messages | add_done_callback 处理异步完成通知 |
| **队列通信** | stream_printing_messages | asyncio.Queue 实现多 agent 消息收集 |

---

### 边界情况与陷阱

#### Critical: fanout_pipeline 的 deepcopy 陷阱

```python
# deepcopy 在消息包含不可拷贝对象时会失败
from agentscope.message import Msg

class UnpickleableObject:
    def __reduce__(self):
        raise PicklingError("Cannot pickle")

msg = Msg(name="test", content="hello", role="user")
msg.metadata = {"obj": UnpickleableObject()}  # 不可深拷贝的对象

# 这会抛出异常
await fanout_pipeline([agent1, agent2], msg)
```

**解决方案**：使用 `msg.copy()` 浅拷贝，或实现自定义的拷贝逻辑。

#### High: asyncio.gather 异常传播

```python
# asyncio.gather() 默认行为：一个任务异常会立即传播
async def failing_agent(msg):
    raise ValueError("Agent failed")

tasks = [
    asyncio.create_task(agent(deepcopy(msg)))
    for agent in [normal_agent, failing_agent, another_agent]
]
# 当 failing_agent 抛出异常时，其他任务可能被取消（取决于 Python 版本）
try:
    await asyncio.gather(*tasks)
except ValueError as e:
    print(f"Caught: {e}")  # 其他 agent 的结果丢失
```

**解决方案**：使用 `return_exceptions=True` 参数收集所有结果：
```python
results = await asyncio.gather(*tasks, return_exceptions=True)
# results = [success_msg, ValueError("Agent failed"), success_msg]
```

#### High: stream_printing_messages 的队列阻塞

```python
# 如果生成器暂停消费但 agent 继续生产，队列会堆积
# 最终可能导致内存溢出
queue = asyncio.Queue()  # 默认无限制

async for msg in stream_printing_messages([agent], main_task):
    pass  # 快速消费
    # 如果这里是 pass 而 agent 产生大量消息，会堆积在内存中
```

**解决方案**：设置队列大小限制并处理 Full 异常：
```python
queue = asyncio.Queue(maxsize=100)
```

#### Medium: SequentialPipeline 的状态累积

```python
# sequential_pipeline 直接传递消息引用，不拷贝
# 如果中间 agent 修改了消息，会影响后续 agent
async def modifying_agent(msg):
    msg.content += " (modified)"  # 直接修改输入！
    return msg

# agent1 修改了 msg，agent2 会看到修改后的版本
result = await sequential_pipeline([agent1, modifying_agent, agent3], original_msg)
```

#### Medium: 任务取消时的资源泄漏

```python
# 如果在 stream_printing_messages 期间取消任务
# 代理的消息队列可能没有正确清理
async def risky_usage():
    agent = StreamingAgent()
    try:
        async for msg in stream_printing_messages([agent], long_task):
            if some_condition:
                raise KeyboardInterrupt  # 取消任务
    finally:
        agent.set_msg_queue_enabled(False)  # 确保清理
```

---

### 性能考量

#### Pipeline 执行性能对比

| 模式 | 适用场景 | 延迟来源 | 内存开销 |
|------|----------|----------|----------|
| sequential_pipeline | 链式处理，少量 agent | 串行等待 | 低（无拷贝）|
| fanout_pipeline (gather=True) | 并行分发，大量 agent | 并发执行 | 高（每次 deepcopy）|
| fanout_pipeline (gather=False) | 内存敏感场景 | 串行等待 | 中（一次 deepcopy）|
| stream_printing_messages | 流式响应 | 队列+生成 | 取决于队列大小 |

#### deepcopy 性能开销

```python
import time
from copy import deepcopy
from agentscope.message import Msg

# 测试不同大小消息的 deepcopy 开销
small_msg = Msg(name="test", content="hello", role="user")  # ~200 bytes
large_msg = Msg(name="test", content="x" * 100000, role="user")  # ~100KB

# 小消息：~0.001ms
start = time.perf_counter()
for _ in range(1000):
    deepcopy(small_msg)
print(f"Small: {(time.perf_counter() - start) * 1000:.2f}ms / 1000 iters")

# 大消息：~10-50ms
start = time.perf_counter()
for _ in range(100):
    deepcopy(large_msg)
print(f"Large: {(time.perf_counter() - start) * 1000:.2f}ms / 100 iters")
```

**经验法则**：
- 消息 < 10KB：deepcopy 开销可忽略
- 消息 10KB-100KB：注意并发场景下的累积延迟
- 消息 > 100KB：考虑使用浅拷贝或自定义序列化

#### 异步任务创建开销

```python
# asyncio.create_task() 有 ~0.1ms 开销
# 对于大量短时任务，可能比直接 await 更慢
tasks = [asyncio.create_task(quick_agent(msg)) for _ in range(1000)]
# 创建 1000 个任务的开销 ~100ms

# 改用列表推导式直接 await：
results = [await quick_agent(msg) for _ in range(1000)]
# 对于短时任务可能更快（取决于并发需求）
```

#### stream_printing_messages 队列开销

```python
# 队列大小影响内存和吞吐量
queue = asyncio.Queue()  # 无限制，可能导致内存溢出
queue = asyncio.Queue(maxsize=100)  # 限制队列大小

# 队列满时的行为：
# - put() 会阻塞直到有空间
# - put_nowait() 会抛出 QueueFull
```

---

## 6. 代码示例

### 6.1 顺序执行示例

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import SequentialPipeline

# 定义简单的 Agent —— 子类应重写 reply()，而非 __call__()
class EchoAgent(AgentBase):
    def __init__(self, name: str):
        super().__init__()
        self.name = name

    async def reply(self, msg):
        return Msg(self.name, f"Echo: {msg.content}", "assistant")

async def main():
    # 创建 pipeline
    pipeline = SequentialPipeline([
        EchoAgent(name="agent1"),
        EchoAgent(name="agent2"),
        EchoAgent(name="agent3"),
    ])

    # 执行
    result = await pipeline(Msg("user", "Hello", "user"))
    print(result)  # Echo: Echo: Echo: Hello

asyncio.run(main())
```

**运行结果**:

```
Msg(name='assistant', content='Echo: Echo: Echo: Hello', role='assistant')
```

> 三次 echo 叠加：agent1 输出 "Echo: Hello" → agent2 输出 "Echo: Echo: Hello" → agent3 输出 "Echo: Echo: Echo: Hello"

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import FanoutPipeline

class MathAgent(AgentBase):
    def __init__(self, name: str, operation: str):
        super().__init__()
        self.name = name
        self.operation = operation

    async def reply(self, msg):
        # ⚠️ 安全警告: 此示例仅用于教学演示。
        # 生产环境应使用 ast.literal_eval() 或专用数学库，
        # 并对 msg.content 进行严格的输入验证。
        result = eval(f"{msg.content} {self.operation}")
        return Msg(self.name, str(result), "assistant")

async def main():
    pipeline = FanoutPipeline([
        MathAgent(name="add", operation="+ 10"),
        MathAgent(name="multiply", operation="* 10"),
        MathAgent(name="subtract", operation="- 5"),
    ])

    results = await pipeline(Msg("user", "100", "user"))
    # [Msg("110"), Msg("1000"), Msg("95")]

    for r in results:
        print(r.content)

asyncio.run(main())
```

**预期输出**：
```
110
1000
95
```

### 6.3 流式消息收集示例

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import stream_printing_messages

class StreamingAgent(AgentBase):
    def __init__(self, name: str):
        super().__init__()
        self.name = name

    async def reply(self, msg):
        for i in range(3):
            await self.print(Msg(self.name, f"chunk {i}", "assistant"))
        return Msg(self.name, "done", "assistant")

async def main():
    agent = StreamingAgent(name="streamer")

    async for msg, is_last in stream_printing_messages(
        [agent],
        agent(Msg("user", "start", "user"))
    ):
        print(f"Received: {msg.content}, last={is_last}")

asyncio.run(main())
```

**预期输出**：
```
Received: chunk 0, last=False
Received: chunk 1, last=False
Received: chunk 2, last=True
```

---

## 7. 练习题

### 7.1 基础题

#### 练习 1：Pipeline 链式执行（带转换函数）

**任务**：修改 `sequential_pipeline` 实现，支持在每个 agent 执行后对输出进行自定义转换函数。

**参考答案**：

```python
from typing import Callable, TypeVar
from agentscope.agent import AgentBase
from agentscope.message import Msg

T = TypeVar('T', bound=Msg | list[Msg] | None)

async def sequential_pipeline_with_transform(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    transform: Callable[[Msg, AgentBase], Msg] | None = None,
) -> Msg | list[Msg] | None:
    """顺序执行 pipeline，支持每个 agent 后的转换函数。

    Args:
        agents: Agent 列表
        msg: 初始输入消息
        transform: 可选的转换函数，接收(输出消息, 当前agent)，返回转换后的消息

    示例:
        >>> def add_suffix(msg: Msg, agent: AgentBase) -> Msg:
        ...     return Msg(msg.role, msg.content + f" (from {agent.name})", msg.role)
        >>> result = await sequential_pipeline_with_transform(
        ...     [agent1, agent2], Msg("user", "Hello", "user"), transform=add_suffix
        ... )
    """
    for agent in agents:
        msg = await agent(msg)
        if transform is not None and msg is not None:
            msg = transform(msg, agent)
    return msg
```

#### 练习 2：Fanout 并发控制（限流）

**任务**：实现一个 `LimitedFanoutPipeline`，限制同时执行的 agent 数量为 N（使用 asyncio.Semaphore）。

**参考答案**：

```python
import asyncio
from copy import deepcopy
from typing import Any
from agentscope.agent import AgentBase
from agentscope.message import Msg

class LimitedFanoutPipeline:
    """限制并发数量的 Fanout Pipeline。"""

    def __init__(
        self,
        agents: list[AgentBase],
        max_concurrency: int = 3,
    ) -> None:
        """初始化限流 Pipeline。

        Args:
            agents: Agent 列表
            max_concurrency: 最大同时执行数量
        """
        self.agents = agents
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def _execute_with_limit(
        self,
        agent: AgentBase,
        msg: Msg | list[Msg] | None,
        **kwargs: Any,
    ) -> Msg:
        """使用信号量限制执行单个 agent。"""
        async with self.semaphore:
            return await agent(deepcopy(msg), **kwargs)

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """执行限流并发。

        示例:
            >>> pipeline = LimitedFanoutPipeline([a1, a2, a3, a4, a5], max_concurrency=2)
            >>> results = await pipeline(Msg("user", "test", "user"))
            # 最多同时执行 2 个 agent
        """
        tasks = [
            asyncio.create_task(self._execute_with_limit(agent, msg, **kwargs))
            for agent in self.agents
        ]
        return await asyncio.gather(*tasks)
```

### 7.2 进阶级

#### 练习 3：条件分支 Pipeline

**任务**：实现 `ConditionalPipeline`，根据前一个 agent 的输出决定下一步执行哪个 agent。

**参考答案**：

```python
from typing import Callable, Sequence
from agentscope.agent import AgentBase
from agentscope.message import Msg

class ConditionalPipeline:
    """条件分支 Pipeline，根据条件选择下一个执行的 agent。"""

    def __init__(
        self,
        branches: list[tuple[Callable[[Msg], bool], AgentBase]],
        default_agent: AgentBase | None = None,
    ) -> None:
        """初始化条件分支 Pipeline。

        Args:
            branches: [(条件函数, agent), ...] 列表，按顺序检查条件
            default_agent: 默认执行的 agent（当所有条件都不满足时）

        示例:
            >>> def is_math(q: Msg) -> bool:
            ...     return any(k in q.content for k in ['+', '-', '*', '/'])
            >>> pipeline = ConditionalPipeline(
            ...     branches=[
            ...         (is_math, math_agent),
            ...     ],
            ...     default_agent=general_agent,
            ... )
        """
        self.branches = branches  # [(condition, agent), ...]
        self.default_agent = default_agent

    async def __call__(self, msg: Msg) -> Msg:
        """执行条件分支。"""
        current_msg = msg

        while True:
            next_agent = None

            # 检查每个分支条件
            for condition, agent in self.branches:
                if condition(current_msg):
                    next_agent = agent
                    break

            # 如果没有匹配的条件，使用默认 agent
            if next_agent is None:
                if self.default_agent is None:
                    return current_msg  # 没有可执行的 agent，返回当前消息
                next_agent = self.default_agent

            # 执行选定的 agent
            current_msg = await next_agent(current_msg)

            # 如果是默认 agent 或者没有更多条件，退出循环
            if next_agent == self.default_agent:
                return current_msg
```

#### 练习 4：错误恢复 Pipeline

**任务**：修改 `fanout_pipeline`，当某个 agent 执行失败时，记录错误但继续执行其他 agent，最终返回 (成功结果列表, 错误列表)。

**参考答案**：

```python
import asyncio
import traceback
from typing import Any
from dataclasses import dataclass
from agentscope.agent import AgentBase
from agentscope.message import Msg

@dataclass
class AgentError:
    """Agent 执行错误信息。"""
    agent_name: str
    error_type: str
    error_message: str
    traceback: str

async def fanout_pipeline_with_error_handling(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    **kwargs: Any,
) -> tuple[list[Msg], list[AgentError]]:
    """并发执行 pipeline，捕获并收集错误。

    Returns:
        (成功结果列表, 错误列表)

    示例:
        >>> successes, errors = await fanout_pipeline_with_error_handling(
        ...     [agent1, agent2, agent3], Msg("user", "test", "user")
        ... )
        >>> print(f"成功: {len(successes)}, 失败: {len(errors)}")
        >>> for err in errors:
        ...     print(f"{err.agent_name}: {err.error_message}")
    """
    errors: list[AgentError] = []
    successes: list[Msg] = []

    async def safe_execute(agent: AgentBase) -> Msg | AgentError:
        """安全执行单个 agent，捕获异常。"""
        try:
            return await agent(msg, **kwargs)
        except Exception as e:
            return AgentError(
                agent_name=getattr(agent, 'name', str(agent)),
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=traceback.format_exc(),
            )

    # 并发执行所有 agent
    results = await asyncio.gather(*[safe_execute(a) for a in agents])

    # 分离成功和失败的结果
    for agent, result in zip(agents, results):
        if isinstance(result, AgentError):
            errors.append(result)
        else:
            successes.append(result)

    return successes, errors
```

### 7.3 挑战题

#### 练习 5：Pipeline DAG 调度

**任务**：设计一个通用的 DAG Pipeline，支持定义 agent 之间的依赖关系图，并按拓扑顺序执行。

**参考答案**：

```python
from typing import Any
from collections import defaultdict, deque
from agentscope.agent import AgentBase
from agentscope.message import Msg

class DAGPipeline:
    """有向无环图 Pipeline，按拓扑顺序执行 agent。

    依赖关系定义:
        {
            "agent_name": ["dependency_agent1", "dependency_agent2"]
        }

    示例:
        >>> # 定义依赖关系: B 依赖 A, C 依赖 A, D 依赖 B 和 C
        >>> dag = DAGPipeline(
        ...     agents=[agent_a, agent_b, agent_c, agent_d],
        ...     dependencies={
        ...         "agent_b": ["agent_a"],
        ...         "agent_c": ["agent_a"],
        ...         "agent_d": ["agent_b", "agent_c"],
        ...     }
        ... )
        >>> result = await dag(Msg("user", "start", "user"))
    """

    def __init__(
        self,
        agents: list[AgentBase],
        dependencies: dict[str, list[str]],
    ) -> None:
        self.agents = {a.name: a for a in agents}
        self.dependencies = dependencies  # agent_name -> [依赖的agent名称]

        # 验证所有 agent 都存在
        all_deps = set()
        for deps in dependencies.values():
            all_deps.update(deps)
        missing = all_deps - set(self.agents.keys())
        if missing:
            raise ValueError(f"Missing agents in dependencies: {missing}")

    def _topological_sort(self) -> list[str]:
        """计算拓扑排序顺序。"""
        in_degree = defaultdict(int)
        adj_list = defaultdict(list)

        # 初始化所有 agent 的入度
        for name in self.agents:
            in_degree[name] = 0

        # 构建邻接表和入度
        for name, deps in self.dependencies.items():
            for dep in deps:
                adj_list[dep].append(name)
                in_degree[name] += 1

        # Kahn's algorithm
        queue = deque([name for name, degree in in_degree.items() if degree == 0])
        result = []

        while queue:
            node = queue.popleft()
            result.append(node)

            for neighbor in adj_list[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.agents):
            raise ValueError("Circular dependency detected in DAG")

        return result

    async def __call__(self, msg: Msg) -> dict[str, Msg]:
        """执行 DAG Pipeline。

        Returns:
            dict: {agent_name: output_msg} 的字典
        """
        execution_order = self._topological_sort()
        results: dict[str, Msg] = {}
        current_msg = msg

        for name in execution_order:
            agent = self.agents[name]

            # 将前一个 agent 的输出作为输入（如果有的话）
            if results:
                # 使用最后一个执行的结果作为输入
                last_result = results[execution_order[list(results.keys()).index(name) - 1]] if len(results) > 0 else msg
                current_msg = last_result

            results[name] = await agent(current_msg)

        return results
```

### 练习 6: Sequential 与 Fanout 执行路径对比 [基础]

**题目描述**：
分析以下代码执行后，`sequential_pipeline` 和 `fanout_pipeline` 的消息传递路径有何不同？分别指出每个 agent 收到的 `msg` 对象特征（引用还是拷贝）。

```python
import asyncio
from agentscope.pipeline import sequential_pipeline, fanout_pipeline
from agentscope import ReActAgent, Msg

# 假设 a1, a2, a3 已初始化
msg = Msg(name="user", content="hello", role="user")

# 顺序执行
seq_result = await sequential_pipeline([a1, a2, a3], msg)
print(f"sequential result type: {type(seq_result)}")

# 并发执行
fanout_results = await fanout_pipeline([a1, a2, a3], msg)
print(f"fanout results type: {type(fanout_results)}, len: {len(fanout_results)}")
```

**预期输出/行为**：
- `seq_result` 是 `Msg` 对象（最后一个 agent 的输出）
- `fanout_results` 是 `list[Msg]`（所有 agent 输出的列表）
- 顺序执行中，每个 agent 收到的是上一个 agent 的**直接输出引用**；并发执行中，每个 agent 收到的是**深拷贝**的原始消息

<details>
<summary>参考答案</summary>

```python
# sequential_pipeline: 链式（前一个输出直接作为后一个输入）
# a1 receives: msg
# a2 receives: a1(msg) output (same object reference)
# a3 receives: a2(a1(msg)) output (same object reference)
# returns: a3's output

# fanout_pipeline: 并发（deepcopy 保证隔离）
# a1 receives: deepcopy(msg)
# a2 receives: deepcopy(msg)  # 独立的副本
# a3 receives: deepcopy(msg)  # 独立的副本
# returns: list of all outputs

# 验证消息 ID（是否同属一个对象）
async def verify():
    original = Msg(name="user", content="test", role="user")
    original_id = id(original)

    results = await fanout_pipeline([a1, a2], original)
    for r in results:
        print(f"input id: {original_id}, result id: {id(r)}, same: {id(r) == original_id}")

# 输出: False（深拷贝产生不同对象）
```
</details>

### 练习 7: 异步生成器消息泄漏分析 [中级]

**题目描述**：
`stream_printing_messages()` 是异步生成器。如果消费者只迭代部分消息就终止（如按下 Ctrl+C），分析会产生什么后果。

```python
async def consume_partial():
    stream = stream_printing_messages([a1, a2], Msg("user", "hello", "user"))
    count = 0
    async for msg in stream:
        print(msg)
        count += 1
        if count >= 3:  # 只取前 3 条消息
            break  # 消费者提前退出
    # stream 未完全消费，生成器会怎样？

async def main():
    await consume_partial()
    # 后续代码正常执行？
```

**预期输出/行为**：
生成器在消费者退出后会在下一个 `yield` 点挂起，不会抛出异常。如果生成器内部有 `finally` 清理逻辑，会正常执行。

<details>
<summary>参考答案</summary>

```python
# 异步生成器的行为：
# 1. 消费者提前退出时，生成器在下次迭代尝试时挂起
# 2. 由于没有 GC 引用，生成器最终被垃圾回收时调用 __anext__ 并触发 StopAsyncIteration
# 3. 如果生成器有 finally 块，会在清理时执行
#
# 验证方法：
async def safe_stream():
    try:
        async for msg in stream_printing_messages([a1, a2], initial):
            if some_condition:
                break
    finally:
        print("清理逻辑：关闭连接/释放资源")

# 结论：提前退出不会导致异常传播，但未消费的消息会丢失
# 框架应在 stream_printing_messages 内部使用 try/finally 保护
```
</details>

### 练习 8: 带优先级的 Fanout Pipeline [挑战]

**题目描述**：
设计一个 `PriorityFanoutPipeline`，支持为每个 agent 分配优先级，高优先级 agent 先执行，结果先返回。

**预期输出/行为**：
给定 agents `[a1(priority=1), a2(priority=3), a3(priority=2)]`，执行顺序应为 `a2 → a3 → a1`，最终结果按优先级排序返回。

<details>
<summary>参考答案</summary>

```python
import asyncio
from dataclasses import dataclass
from typing import Any
from agentscope.agent import AgentBase
from agentscope.message import Msg

@dataclass
class PriorityAgent:
    agent: AgentBase
    priority: int  # 数值越大优先级越高

class PriorityFanoutPipeline:
    def __init__(self, agents: list[tuple[AgentBase, int]]):
        # agents: [(agent, priority), ...]
        self.priority_agents = sorted(
            [PriorityAgent(a, p) for a, p in agents],
            key=lambda x: x.priority,
            reverse=True,  # 高优先级排前面
        )

    async def __call__(
        self,
        msg: Msg,
        **kwargs: Any,
    ) -> list[Msg]:
        async def execute(pa: PriorityAgent) -> tuple[int, Msg]:
            result = await pa.agent(msg, **kwargs)
            return pa.priority, result

        tasks = [execute(pa) for pa in self.priority_agents]
        # 按优先级并发执行
        results_with_priority = await asyncio.gather(*tasks)
        # 按原始优先级顺序返回结果
        return [msg for _, msg in sorted(results_with_priority, key=lambda x: x[0], reverse=True)]

# 使用示例
pipeline = PriorityFanoutPipeline([
    (a1, 1),  # 低优先级
    (a2, 3),  # 高优先级
    (a3, 2),  # 中优先级
])
results = await pipeline(Msg("user", "hello", "user"))
# a2 先执行，results[0] 是 a2 的输出
```
</details>

### 练习 9: Pipeline 执行顺序追踪 [基础]

**题目描述**：
在 `sequential_pipeline` 中，为每个 agent 添加日志打印执行顺序，验证以下代码的执行顺序。

```python
import asyncio
from agentscope.pipeline import sequential_pipeline
from agentscope import ReActAgent, Msg

# 假设 a1, a2, a3 已初始化，name 分别为 "A", "B", "C"
# 每个 agent 的 reply() 方法内会 print(f"{self.name} executing")

async def test_order():
    result = await sequential_pipeline(
        [a1, a2, a3],
        Msg("user", "hello", "user")
    )
    # 预期打印顺序是什么？
```

**预期输出/行为**：
执行顺序严格为 `A executing → B executing → C executing`（按列表顺序串行执行），无并发。

<details>
<summary>参考答案</summary>

```python
# sequential_pipeline 的实现原理：
# for agent in agents:
#     msg = await agent(msg)  # 串行等待，每个 agent 执行完才轮到下一个
#
# 验证：添加日志
async def test_order():
    execution_log = []

    original_reply = ReActAgent.reply
    async def logged_reply(self, *args, **kwargs):
        execution_log.append(self.name)
        return await original_reply(self, *args, **kwargs)

    ReActAgent.reply = logged_reply

    try:
        result = await sequential_pipeline([a1, a2, a3], initial)
    finally:
        ReActAgent.reply = original_reply  # 恢复

    print(execution_log)
    # 输出: ['A', 'B', 'C'] 或 ['Agent1', 'Agent2', 'Agent3']
    # 确认严格串行，无乱序
```
</details>

---

## 设计模式总结（补充）

> 以下是对本模块涉及设计模式的补充分析。核心模式已在第 5 节总结。

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Chain of Responsibility** | SequentialPipeline | 消息沿链式传递，每个 Agent 的输出是下一个的输入 |
| **Fork-Join** | FanoutPipeline | 并发分发 `deepcopy(msg)` + `asyncio.gather()` 合并结果 |
| **Delegation（委托）** | Pipeline 类 → 函数 | 类接口（可复用）委托给函数实现（轻量级） |
| **Prototype（原型）** | `deepcopy(msg)` | 在 `fanout_pipeline` 中为每个 Agent 创建消息的独立副本 |

## 小结

| 特性 | 实现方式 |
|------|----------|
| 顺序执行 | `sequential_pipeline()` 链式传递 |
| 并发分发 | `fanout_pipeline()` + `asyncio.gather` |
| 流式处理 | `stream_printing_messages()` 异步生成器 |
| 类封装 | SequentialPipeline / FanoutPipeline 委托模式 |
| 消息隔离 | `deepcopy` 保证每个代理独立副本 |

## 练习题

### 基础题

**Q1**: `fanout_pipeline()` 为什么对消息使用 `deepcopy`？`sequential_pipeline()` 为什么不需要？

**Q2**: `fanout_pipeline()` 使用 `asyncio.gather()` 并发执行。如果一个 Agent 抛出异常，其他 Agent 会怎样？

### 中级题

**Q3**: 比较 `sequential_pipeline()` 函数和 `SequentialPipeline` 类的优缺点。什么场景下应该选择哪种？

**Q4**: `stream_printing_messages()` 是异步生成器。如果消费者只取了部分消息就停止，会发生什么？

### 挑战题

**Q5**: 设计一个带超时和重试机制的 Pipeline，当一个 Agent 执行超过指定时间后自动重试。需要考虑幂等性问题。

---

### 参考答案

**A1**: 注意 `sequential_pipeline()` 实际上**不使用** `deepcopy`——消息直接从上一个 Agent 传递给下一个 Agent（因为顺序执行时每个 Agent 的输出就是下一个 Agent 的输入，不需要拷贝）。只有 `fanout_pipeline()` 才使用 `deepcopy`，因为同一消息需要分发给多个 Agent 并发处理，如果共享引用，Agent A 修改消息会影响 Agent B 看到的内容。深拷贝虽然开销更大，但在并发场景下保证了隔离性。

**A2**: 默认情况下 `asyncio.gather()` 会在任一任务抛出异常时传播该异常，其他已启动的任务会继续运行（不会被取消）。如果要取消其他任务，可以使用 `asyncio.gather(..., return_exceptions=True)` 收集所有结果，或使用 `asyncio.TaskGroup`（Python 3.11+）。

**A3**: 函数式接口简单直接，适合一次性使用。类接口支持复用（同一 Pipeline 实例可多次调用）和状态管理（如注入 TokenCounter、保存历史）。如果只是简单串联 2-3 个 Agent，用函数即可；如果需要复杂配置、重用或集成 Session 管理，用类更合适。

**A4**: 异步生成器的消费是惰性的——消费者停止迭代后，生成器会在下一个 `yield` 点挂起。`async for` 循环正常退出不会产生错误，但未消费的消息会丢失。如果生成器中有需要清理的资源，应使用 `try/finally` 确保清理。

**A5**: 关键设计：(1) 使用 `asyncio.wait_for(agent.reply(msg), timeout=seconds)` 设置超时；(2) 超时后重试，最多 N 次；(3) 幂等性要求 Agent 的 `reply()` 对相同输入产生相同效果——如果 Agent 有副作用（如调用外部 API），需要记录已执行的步骤并在重试时跳过；(4) 可以使用 ToolResponse 的 `id` 字段追踪已执行的工具调用。

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [智能体模块](module_agent_deep.md#3-agentbase-源码解读) | Pipeline 持有 AgentBase 实例进行编排 | 第 3.1 节 |
| [调度器模块](module_dispatcher_deep.md#4-源码解读) | MsgHub 管理代理间消息路由 | 第 4.1-4.6 节 |
| [管道模块](module_pipeline_infra_deep.md#2-pipeline-工作流编排) | 详细的管道基础设施分析 | 第 2.1-2.3 节 |
| [消息模块](module_message_deep.md#3-核心类与函数源码解读) | Msg 对象在管道中传递和拷贝 | 第 3.1 节 |


---

## 参考资料

- Pipeline 函数: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py`
- Pipeline 类: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_class.py`

---

*文档版本: 1.0*
*最后更新: 2026-04-28*
