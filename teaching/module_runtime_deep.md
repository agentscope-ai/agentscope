# Runtime 运行时系统源码深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [核心类继承体系](#3-核心类继承体系)
4. [源码解读](#4-源码解读)
   - [SequentialPipeline 顺序执行器](#41-sequentialpipeline-顺序执行器)
   - [FanoutPipeline 并发分发器](#42-fanoutpipeline-并发分发器)
   - [stream_printing_messages 流式消息处理](#43-stream_printing_messages-流式消息处理)
   - [关键方法流程图](#44-关键方法流程图)
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
└── _functional.py        # 顺序/分发/流式处理函数实现
```

---

## 3. 核心类继承体系

```
AgentBase (agent 模块)
    │
    └── SequentialPipeline
    │       __call__(msg) -> Msg|list[Msg]
    │
    └── FanoutPipeline
            __call__(msg) -> list[Msg]
```

**注意**: SequentialPipeline 和 FanoutPipeline 并不继承 AgentBase，而是组合持有 AgentBase 实例，通过委托模式实现执行。

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

## 6. 代码示例

### 6.1 顺序执行示例

```python
import asyncio
from agentscope import AgentBase, Msg
from agentscope.pipeline import SequentialPipeline

# 定义简单的 Agent
class EchoAgent(AgentBase):
    async def __call__(self, msg):
        return Msg("assistant", f"Echo: {msg.content}", "assistant")

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

```python
import asyncio
from agentscope import AgentBase, Msg
from agentscope.pipeline import FanoutPipeline

class MathAgent(AgentBase):
    def __init__(self, name, operation, **kwargs):
        super().__init__(name=name, **kwargs)
        self.operation = operation

    async def __call__(self, msg):
        # ⚠️ 安全警告: 此示例仅用于教学演示。
        # 生产环境应使用 ast.literal_eval() 或专用数学库，
        # 并对 msg.content 进行严格的输入验证。
        result = eval(f"{msg.content} {self.operation}")
        return Msg("assistant", str(result), "assistant")

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

### 6.3 流式消息收集示例

```python
import asyncio
from agentscope import AgentBase, Msg
from agentscope.pipeline import stream_printing_messages

class StreamingAgent(AgentBase):
    async def __call__(self, msg):
        for i in range(3):
            await self.print(Msg("assistant", f"chunk {i}", "assistant"))
        return Msg("assistant", "done", "assistant")

async def main():
    agent = StreamingAgent(name="streamer")

    async for msg, is_last in stream_printing_messages(
        [agent],
        agent(Msg("user", "start", "user"))
    ):
        print(f"Received: {msg.content}, last={is_last}")

asyncio.run(main())
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
            return await agent(msg, **kwargs)

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

---

**提示**: 练习题的参考答案可在 AgentScope 官方文档中找到。

---

## 小结

| 特性 | 实现方式 |
|------|----------|
| 顺序执行 | `sequential_pipeline()` 链式传递 |
| 并发分发 | `fanout_pipeline()` + `asyncio.gather` |
| 流式处理 | `stream_printing_messages()` 异步生成器 |
| 类封装 | SequentialPipeline / FanoutPipeline 委托模式 |
| 消息隔离 | `deepcopy` 保证每个代理独立副本 |

Runtime 模块提供了灵活的代理执行编排能力，函数式接口适合简单场景，类接口适合需要复用和状态的场景。

## 章节关联

| 关联模块 | 关联点 |
|----------|--------|
| [智能体模块](module_agent_deep.md) | Pipeline 持有 AgentBase 实例进行编排 |
| [调度器模块](module_dispatcher_deep.md) | MsgHub 管理代理间消息路由 |
| [管道模块](module_pipeline_infra_deep.md) | 详细的管道基础设施分析 |
| [消息模块](module_message_deep.md) | Msg 对象在管道中传递和拷贝 |

## 参考资料

- Pipeline 函数: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py`
- Pipeline 类: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_class.py`

---

*文档版本: 1.0*
*最后更新: 2026-04-28*
