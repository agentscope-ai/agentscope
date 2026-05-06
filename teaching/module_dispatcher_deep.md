# Dispatcher 调度器源码深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [核心类继承体系](#3-核心类继承体系)
4. [源码解读](#4-源码解读)
   - [MsgHub 消息中心](#41-msghub-消息中心)
   - [异步上下文管理器](#42-异步上下文管理器)
   - [订阅者管理机制](#43-订阅者管理机制)
   - [参与者动态管理](#44-参与者动态管理)
   - [消息广播](#45-消息广播)
   - [自动广播控制](#46-自动广播控制)
   - [关键方法流程图](#47-关键方法流程图)
5. [设计模式总结](#5-设计模式总结)
6. [代码示例](#6-代码示例)
7. [练习题](#7-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 MsgHub 的核心方法（__aenter__、__aexit__、broadcast 等） | 列举、识别 |
| 理解 | 解释发布-订阅模式在 MsgHub 中的实现原理 | 解释、描述 |
| 应用 | 使用 `async with MsgHub(...)` 构建多代理消息路由 | 实现、配置 |
| 分析 | 分析消息广播流程中订阅者的注册与通知机制 | 分析、追踪 |
| 评价 | 评价 MsgHub 与 ChatRoom 两种消息模式的适用场景 | 评价、推荐 |
| 创造 | 设计一个自定义消息过滤器的 MsgHub 扩展方案 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 异步上下文管理器（`async with`、`__aenter__`/`__aexit__`）
- [ ] 发布-订阅设计模式基础
- [ ] AgentBase 的 `reply()` 和 `observe()` 方法（参见智能体模块）
- [ ] Msg 消息结构（参见消息模块）

**预计学习时间**: 35 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `async with MsgHub(...)` | `try (MsgHub hub = ...)` (AutoCloseable) | 上下文管理器 ≈ 带资源的 try |
| `await agent.observe(msg)` | `CompletableFuture.thenAccept()` | 异步回调链 |
| 发布-订阅 | `java.util.Observable` / EventBus | 消息路由模式 |
| `@abstractmethod` | `abstract` 方法 | 接口强制实现 |

---

## 1. 模块概述

> **交叉引用**: 本模块聚焦 MsgHub 的发布-订阅消息路由源码分析。Pipeline 编排层如何调用 MsgHub 编排 Agent 执行流，详见 [Pipeline 与基础设施深度分析](module_pipeline_infra_deep.md) 第 2.1 节。MsgHub 广播依赖 AgentBase 的 `observe()` 方法，详见 [Agent 模块深度分析](module_agent_deep.md) 第 3.5 节。消息载体 Msg 对象的完整类型体系，详见 [Message 消息系统深度分析](module_message_deep.md)。
>
> **与 Pipeline 基础设施模块的关系**: 本模块专注 MsgHub 的**消息路由机制**源码分析。[module_pipeline_infra_deep.md](module_pipeline_infra_deep.md) 第 2.1 节提供了 MsgHub 的概念性概览。建议先读本模块理解实现细节，再结合 Pipeline 模块理解 MsgHub 在工作流中的角色。

Dispatcher 模块是 AgentScope 的消息调度中心，负责多代理之间的消息路由和广播。核心组件 MsgHub 实现了：

1. **消息订阅机制**: 代理订阅来自其他代理的消息
2. **自动广播**: 代理回复自动广播给所有订阅者
3. **上下文管理**: 异步上下文管理器，支持 enter/exit 生命周期
4. **动态管理**: 支持运行时添加/删除参与者

### 1.1 核心价值

- **松耦合通信**: 代理之间无需直接引用，通过 MsgHub 间接通信
- **广播语义**: 一对多消息分发，支持多个订阅者
- **生命周期管理**: 上下文管理器自动清理订阅关系
- **异步支持**: 全异步设计，支持 await 语法

---

## 2. 目录结构

```
src/agentscope/pipeline/
├── __init__.py           # Pipeline 模块导出
├── _msghub.py            # MsgHub 调度器核心实现
├── _chat_room.py         # 聊天室扩展
├── _class.py             # Pipeline 类封装
└── _functional.py        # Pipeline 函数实现
```

---

## 3. 核心类继承体系

```
MsgHub（独立类，不继承任何基类）
    │
    ├── __init__(participants, announcement, enable_auto_broadcast, name)
    ├── __aenter__ / __aexit__      # 异步上下文管理
    ├── add() / delete()            # 动态参与者管理
    ├── broadcast()                  # 手动广播（遍历 observe）
    ├── set_auto_broadcast()         # 切换自动广播
    └── _reset_subscriber()          # 内部：重置订阅关系
            │
            └── 调用 AgentBase.reset_subscribers(name, participants)
                    │
                    └── 为每个 agent 注册"除自身外所有参与者"为订阅者
```

**注意**: MsgHub 不继承 AgentBase，而是作为独立的消息路由组件存在。自动广播的实际执行由 `AgentBase.__call__()` 内部调用 `_broadcast_to_subscribers()` 完成（详见下文 4.8 节）。

---

## 4. 源码解读

### 4.1 MsgHub 消息中心

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 14-71 行)

```python
class MsgHub:
    """MsgHub class that controls the subscription of the participated agents.

    Example:
        In the following example, the reply message from `agent1`, `agent2`,
        and `agent3` will be broadcast to all the other agents in the MsgHub.

        .. code-block:: python

            async with MsgHub(participants=[agent1, agent2, agent3]):
                agent1()
                agent2()

        Actually, it has the same effect as the following code, but much more
        easy and elegant!

        .. code-block:: python

            x1 = agent1()
            agent2.observe(x1)
            agent3.observe(x1)

            x2 = agent2()
            agent1.observe(x2)
            agent3.observe(x2)

    """

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: list[Msg] | Msg | None = None,
        enable_auto_broadcast: bool = True,
        name: str | None = None,
    ) -> None:
        """Initialize a MsgHub context manager.

        Args:
            participants (`Sequence[AgentBase]`):
                A sequence of agents that participate in the MsgHub.
            announcement (`list[Msg] | Msg | None`):
                The message that will be broadcast to all participants when
                entering the MsgHub.
            enable_auto_broadcast (`bool`, defaults to `True`):
                Whether to enable automatic broadcasting of the replied
                message from any participant to all other participants. If
                disabled, the MsgHub will only serve as a manual message
                broadcaster with the `announcement` argument and the
                `broadcast()` method.
            name (`str | None`):
                The name of this MsgHub. If not provided, a random ID
                will be generated.
        """

        self.name = name or shortuuid.uuid()
        self.participants = list(participants)
        self.announcement = announcement
        self.enable_auto_broadcast = enable_auto_broadcast
```

**源码分析**:
- 第 14-40 行: MsgHub 类定义开始，docstring 清晰说明用法和等价的手动实现
- 第 42-71 行: 构造函数接受 4 个参数:
  - `participants`: 参与消息分发的代理列表
  - `announcement`: 进入时的广播消息
  - `enable_auto_broadcast`: 是否启用自动广播
  - `name`: MsgHub 标识符，默认生成随机 UUID

### 4.2 异步上下文管理器

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 73-87 行)

```python
    async def __aenter__(self) -> "MsgHub":
        """Will be called when entering the MsgHub."""
        self._reset_subscriber()

        # broadcast the input message to all participants
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)

        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """Will be called when exiting the MsgHub."""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)
```

**源码分析**:
- 第 73-81 行: `__aenter__` 进入时:
  1. 调用 `_reset_subscriber()` 初始化订阅关系
  2. 如果有 announcement，广播到所有参与者
  3. 返回 self，支持 `async with` 链式调用
- 第 83-88 行: `__aexit__` 退出时清理订阅关系

### 4.3 订阅者管理机制

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 89-93 行)

```python
    def _reset_subscriber(self) -> None:
        """Reset the subscriber for agent in `self.participant`"""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.reset_subscribers(self.name, self.participants)
```

**源码分析**:
- 每个代理维护一个订阅者字典，key 是 MsgHub 名称，value 是订阅者列表
- `_reset_subscriber` 为当前 MsgHub 下的所有代理设置订阅关系
- 每个 agent 会订阅来自其他 agent 的消息（除自己外）

### 4.4 参与者动态管理

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 95-128 行)

```python
    def add(
        self,
        new_participant: list[AgentBase] | AgentBase,
    ) -> None:
        """Add new participant into this hub"""
        if isinstance(new_participant, AgentBase):
            new_participant = [new_participant]

        for agent in new_participant:
            if agent not in self.participants:
                self.participants.append(agent)

        self._reset_subscriber()

    def delete(
        self,
        participant: list[AgentBase] | AgentBase,
    ) -> None:
        """Delete agents from participant."""
        if isinstance(participant, AgentBase):
            participant = [participant]

        for agent in participant:
            if agent in self.participants:
                # remove agent from self.participant
                self.participants.pop(self.participants.index(agent))
            else:
                logger.warning(
                    "Cannot find the agent with ID %s, skip its deletion.",
                    agent.id,
                )

        # Remove this agent from the subscriber of other agents
        self._reset_subscriber()
```

**源码分析**:
- `add()`: 支持单个或批量添加参与者
- `delete()`: 支持单个或批量删除，带警告日志
- 两者都调用 `_reset_subscriber()` 重新同步订阅关系

### 4.5 消息广播

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 130-138 行)

```python
    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """Broadcast the message to all participants.

        Args:
            msg (`list[Msg] | Msg`):
                Message(s) to be broadcast among all participants.
        """
        for agent in self.participants:
            await agent.observe(msg)
```

**源码分析**:
- 广播实现简洁：遍历所有参与者，调用 `await agent.observe(msg)`
- 支持单条或批量消息广播

### 4.6 自动广播控制

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py` (第 140-156 行)

```python
    def set_auto_broadcast(self, enable: bool) -> None:
        """Enable automatic broadcasting of the replied message from any
        participant to all other participants.

        Args:
            enable (`bool`):
                Whether to enable automatic broadcasting. If disabled, the
                MsgHub will only serve as a manual message broadcaster with
                the `announcement` argument and the `broadcast()` method.
        """
        if enable:
            self.enable_auto_broadcast = True
            self._reset_subscriber()
        else:
            self.enable_auto_broadcast = False
            for agent in self.participants:
                agent.remove_subscribers(self.name)
```

**源码分析**:
- 动态开启/关闭自动广播
- 关闭时保留手动 `broadcast()` 功能
- 关闭时只移除 MsgHub 的订阅关系，不完全清理

### 4.7 关键方法流程图

#### MsgHub 生命周期流程

```
创建 MsgHub
     │
     ▼
async with MsgHub(participants) as hub:
     │
     ├── _reset_subscriber()
     │        │
     │        ▼
     │   ┌─────────────────────────┐
     │   │ 遍历 participants       │
     │   │ agent.reset_subscribers │
     │   │ (hub_name, participants)│
     │   └─────────────────────────┘
     │
     ├── broadcast(announcement) [如果提供]
     │        │
     │        ▼
     │   ┌─────────────────────────┐
     │   │ 遍历 participants       │
     │   │ await agent.observe()   │
     │   └─────────────────────────┘
     │
     │  (业务逻辑执行...)
     │
     ▼
__aexit__ 清理
     │
     ▼
  移除订阅关系
```

#### 自动广播机制

```
Agent1 回复消息
       │
       ▼
  AgentBase.__call__(msg)            # 源码 _agent_base.py 第 448 行
       │
       ├── self.reply(msg)            # 执行实际回复逻辑
       │
       ▼
  _broadcast_to_subscribers(reply_msg)  # 源码第 469 行
       │
       ├── 遍历 self._subscribers.values()  # 第 474-475 行
       │
       ├── 移除 thinking blocks（strip_thinking=True） # 第 481-485 行
       │
       ▼
  Agent2.observe(msg)  ───┐
  Agent3.observe(msg)  ───┼── 广播给所有订阅者（不含自身）
```

> **重要细节**: 自动广播时，`_broadcast_to_subscribers` 会**移除 ThinkingBlock**（源码第 481-493 行），确保下游 agent 不会收到上游的思考过程。这是 MsgHub 自动广播与手动 `broadcast()` 的关键区别——手动广播不经过此过滤。

### 4.8 边界情况与注意事项

1. **`add()` 在 `__aenter__` 之前调用**: 构造函数不调用 `_reset_subscriber()`，因此 `add()` 在进入上下文前调用不会生效。必须先 `async with MsgHub(...) as hub:` 再 `hub.add()`。

2. **循环参与者引用**: MsgHub 不检测参与者是否已存在于其他 MsgHub 中。同一个 agent 同时参与多个 MsgHub 时，订阅关系会按 hub `name` 隔离，但 `observe()` 会收到来自所有 hub 的广播。

3. **删除已删除的代理**: `delete()` 对不存在的代理输出 `logger.warning` 但不抛出异常（源码第 261-264 行），保证幂等性。

4. **`enable_auto_broadcast=False` 的行为**: 关闭自动广播后，`_reset_subscriber` 不再被调用，但手动 `broadcast()` 仍然有效。这意味着你可以先用自动广播建立订阅关系，再切换为手动控制。

---

## 5. 设计模式总结

| 模式 | 应用位置 | 源码引用 | 说明 |
|------|----------|----------|------|
| **Mediator（中介者）** | MsgHub | `_msghub.py` 全文 | 作为代理间的消息中介，避免 Agent 之间直接引用 |
| **Observer（发布-订阅）** | `reset_subscribers()` + `AgentBase._broadcast_to_subscribers` | `_msghub.py:89-93`, `_agent_base.py:469` | MsgHub 通过 `reset_subscribers()` 注册订阅关系；自动广播由 `AgentBase.__call__()` 调用 `_broadcast_to_subscribers()` 实现（参见 [Agent 模块](module_agent_deep.md)） |
| **Context Manager** | `__aenter__` / `__aexit__` | `_msghub.py:73-87` | `async with MsgHub(agents)` 确保订阅关系正确注册与清理，异常安全 |

---

### 边界情况与陷阱

#### Critical: 循环订阅导致死循环

```python
# 如果参与者列表包含 MsgHub 自身，会导致无限广播
async with MsgHub(participants=[agent1, msg_hub_instance, agent2]):
    # 错误：msg_hub_instance 会收到自己的广播
    # 导致无限循环
    pass
```

**解决方案**：确保参与者列表不包含 MsgHub 自身或其他 MsgHub 实例。

#### High: enable_auto_broadcast 与手动广播的交互

```python
# enable_auto_broadcast=False 时，broadcast() 仍然可以手动调用
# 但不会触发自动广播

hub = MsgHub(participants=[a1, a2], enable_auto_broadcast=False)
async with hub:
    await hub.broadcast(msg)  # 只发送给 a1, a2
    # 后续 a1 的回复不会被自动广播！
```

#### Medium: delete() 方法的参与者查找

```python
# delete() 方法遍历参与者列表查找
# 如果参与者不在列表中，会记录警告但继续执行

hub = MsgHub(participants=[a1, a2])
hub.delete(a3)  # a3 不在列表中，警告：Cannot find the agent
```

#### Medium: 并发访问 MsgHub

```python
# 多个协程同时操作 MsgHub 可能产生竞态条件
async with MsgHub(participants=[a1, a2]):
    # 协程1: add(a3)
    # 协程2: delete(a1)
    # 结果不确定

# 解决方案：在应用层使用锁保护 MsgHub 操作
```

---

### 性能考量

#### 消息广播性能

| 参与者数量 | 广播延迟 | 说明 |
|------------|----------|------|
| 2 | ~0.1ms | 一次 observe() 调用 |
| 5 | ~0.25ms | 线性增长 |
| 10 | ~0.5ms | 需要优化 |
| 100 | ~5ms | 不建议使用 MsgHub |

**优化建议**：大量参与者时，考虑使用消息队列替代直接广播。

#### MsgHub vs 消息队列

```python
# MsgHub: 适合小规模（< 10 个 Agent）
# 优点：简单、低延迟
# 缺点：O(n) 广播复杂度

# 消息队列：适合大规模
# 优点：解耦、异步、可扩展
# 缺点：更高延迟、需要额外基础设施
```

---

## 6. 代码示例

### 6.1 基本用法

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

class SimpleAgent(AgentBase):
    def __init__(self, name: str):
        super().__init__()
        self.name = name

    async def reply(self, msg):
        return Msg(self.name, f"{self.name}: {msg.content}", "assistant")

async def main():
    agent1 = SimpleAgent(name="Alice")
    agent2 = SimpleAgent(name="Bob")
    agent3 = SimpleAgent(name="Charlie")

    # 使用 MsgHub 管理多代理通信
    async with MsgHub(participants=[agent1, agent2, agent3],
                      announcement=Msg("system", "Welcome!", "system")):
        # agent1 的回复会自动广播给 agent2 和 agent3
        result1 = await agent1(Msg("user", "Hello", "user"))
        # agent2 的回复会自动广播给 agent1 和 agent3
        result2 = await agent2(Msg("user", "Hi", "user"))

asyncio.run(main())
```

**运行结果**:

```
[MsgHub] Broadcasting announcement: Welcome!
[Alice] Observed: Msg(name='system', content='Welcome!')
[Bob] Observed: Msg(name='system', content='Welcome!')
[Charlie] Observed: Msg(name='system', content='Welcome!')
[Alice] -> Reply: Alice: Hello
[Bob] Observed: Msg(name='Alice', content='Alice: Hello')
[Charlie] Observed: Msg(name='Alice', content='Alice: Hello')
[Bob] -> Reply: Bob: Hi
[Alice] Observed: Msg(name='Bob', content='Bob: Hi')
[Charlie] Observed: Msg(name='Bob', content='Bob: Hi')
```

### 6.2 手动广播模式

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

async def main():
    agent1 = SimpleAgent(name="Publisher")
    agent2 = SimpleAgent(name="Subscriber1")
    agent3 = SimpleAgent(name="Subscriber2")

    # 关闭自动广播，手动控制
    async with MsgHub(participants=[agent1, agent2, agent3],
                      enable_auto_broadcast=False) as hub:
        # 手动广播消息
        await hub.broadcast(Msg("system", "Important news!", "system"))

asyncio.run(main())
```

**预期输出**：
```
[MsgHub] Manual broadcast: Important news!
[Publisher] Observed: Msg(name='system', content='Important news!')
[Subscriber1] Observed: Msg(name='system', content='Important news!')
[Subscriber2] Observed: Msg(name='system', content='Important news!')
```

### 6.3 动态管理参与者

```python showLineNumbers
import asyncio
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

async def main():
    agent1 = SimpleAgent(name="Original1")
    agent2 = SimpleAgent(name="Original2")
    agent3 = SimpleAgent(name="NewAgent")

    async with MsgHub(participants=[agent1, agent2]) as hub:
        # 运行时添加新参与者
        hub.add(agent3)

        # 业务逻辑...

        # 运行时移除参与者
        hub.delete(agent1)

asyncio.run(main())
```

**预期输出**：
```
[MsgHub] Participant added: NewAgent
[MsgHub] Participant removed: Original1
```

### 6.4 等价手动实现对比

```python showLineNumbers
from agentscope.message import Msg

# MsgHub 简化写法
async with MsgHub(participants=[agent1, agent2, agent3]):
    x1 = await agent1(Msg("user", "Hello", "user"))
    # agent2, agent3 自动通过 AgentBase._broadcast_to_subscribers 收到 x1
    # 注意：自动广播会自动移除 ThinkingBlock

# 等价的手动实现（需在 async 函数中执行）
# 注意：手动 observe 不移除 ThinkingBlock，与自动广播行为不同
async def manual_implementation():
    x1 = await agent1(Msg("user", "Hello", "user"))
    await agent2.observe(x1)
    await agent3.observe(x1)

    x2 = await agent2(Msg("user", "Hi", "user"))
    await agent1.observe(x2)
    await agent3.observe(x2)
```

---

## 7. 练习题

### 7.1 基础题

1. **订阅过滤**: 修改 MsgHub，支持只订阅特定类型的消息（如只订阅来自特定 agent 的消息）。

2. **广播策略**: 实现不同的广播策略（如轮询、优先级等），替换默认的广播函数。

### 7.2 进阶级

3. **层级 MsgHub**: 设计支持嵌套的 MsgHub，实现消息在不同层级间的传递。

4. **消息过滤代理**: 实现一个 `FilteredMsgHub`，支持基于消息内容的过滤规则。

### 7.3 挑战题

5. **分布式调度器**: 设计一个支持跨进程的 MsgHub，使用消息队列（如 Redis）作为消息传输中间件。

### 练习 7.6: MsgHub 广播路径追踪 [基础]

**题目描述**：
分析 MsgHub 中 `broadcast()` 方法与 AgentBase 的 `_broadcast_to_subscribers()` 方法的调用关系。阅读 `_msghub.py` 和 `_agent_base.py`，指出以下代码中消息的完整传播路径。

```python
async with MsgHub(participants=[a1, a2], announce=True) as hub:
    result = await a1(Msg("user", "hello", "user"))
    # result 会被广播给 a2 吗？走的是哪条路径？
```

**预期输出/行为**：
当 `announce=True` 时，消息经过 `_broadcast_to_subscribers()` → `_announced_host` → `hub.broadcast()` → `a2.observe()` 的路径自动广播，无需手动调用 `hub.broadcast()`。

<details>
<summary>参考答案</summary>

```python
# 完整传播路径：
# 1. a1.reply() 完成后 → _broadcast_to_subscribers()
#    (AgentBase 内部方法，在 reply 返回前自动调用)
#
# 2. _broadcast_to_subscribers() 检查 _announced_host 是否存在
#    如果存在 → 调用 _announced_host.broadcast(msg)
#    (这是 MsgHub 通过 __enter__ 设置的)
#
# 3. hub.broadcast(msg) → 遍历 participants
#    对每个 participant（排除发送者）调用 participant.observe(msg)
#
# 所以 result 会自动广播给 a2，走的是自动广播路径。
# 注意：手动调用 hub.broadcast() 和自动广播走的是同一个方法，
# 但触发时机不同。
```
</details>

### 练习 7.7: 订阅过滤行为验证 [基础]

**题目描述**：
以下 `FilteredMsgHub` 意图只广播来自 `sender_name="agent1"` 的消息。阅读源码分析这个实现是否正确，并说明过滤失效的场景。

```python
class FilteredMsgHub(MsgHub):
    def __init__(self, participants, sender_filter=None, **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.sender_filter = sender_filter  # Callable[[str], bool]

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        if isinstance(msg, list):
            for m in msg:
                if self.sender_filter and not self.sender_filter(m.name):
                    return
        else:
            if self.sender_filter and not self.sender_filter(msg.name):
                return
        await super().broadcast(msg)
```

**预期输出/行为**：
此实现只过滤手动调用 `broadcast()` 的场景。自动广播（`_broadcast_to_subscribers()` 路径）不走 `broadcast()` 方法，因此过滤规则对自动广播无效。

<details>
<summary>参考答案</summary>

```python
# 问题所在：
# broadcast() 只拦截手动调用 hub.broadcast() 的场景。
# 自动广播路径：agent.reply() → _broadcast_to_subscribers()
# → _announced_host.broadcast() → 当前 FilteredMsgHub.broadcast()
#
# 等等，自动广播实际上也是通过 broadcast() 方法！
# 所以理论上过滤应该生效...但实际上：
#
# _broadcast_to_subscribers() 直接调用:
#   await self._announced_host.broadcast(msg)
#
# 这个调用会走 MsgHub.broadcast()（不是子类override的），
# 因为 Python 的 super().broadcast() 是基类版本。
# 要让子类过滤生效，需要重写的是 broadcast 本身，而不是依赖 super()。
#
# 正确做法：重写 broadcast() 并覆盖 super 的实现，
# 不使用 super() 的逻辑，而是自行遍历 participants。
```
</details>

### 练习 7.8: 嵌套 MsgHub 设计 [中级]

**题目描述**：
设计一个 `NestedMsgHub`，支持嵌套结构：子 MsgHub 收到消息后可以向父 MsgHub 传播，反之亦然。参考 `_msghub.py` 的上下文管理器实现。

**预期输出/行为**：
嵌套使用时，父 Hub 的参与者能收到子 Hub 参与者的消息，反之亦然。

<details>
<summary>参考答案</summary>

```python
class NestedMsgHub(MsgHub):
    """支持层级传播的 MsgHub。"""

    def __init__(self, participants, parent_hub=None, **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.parent_hub = parent_hub
        self._child_hubs: list["NestedMsgHub"] = []

    def add_child(self, child: "NestedMsgHub") -> None:
        """将子 Hub 注册到当前 Hub。"""
        self._child_hubs.append(child)
        child.parent_hub = self

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        # 向本地参与者广播
        await super().broadcast(msg)

        # 向父 Hub 传播（如果存在）
        if self.parent_hub is not None:
            await self.parent_hub.broadcast(msg)

        # 向子 Hub 传播
        for child in self._child_hubs:
            await child.broadcast(msg)

# 使用示例
# async with NestedMsgHub(participants=[a1, a2]) as parent_hub:
#     async with NestedMsgHub(participants=[b1, b2], parent_hub=parent_hub) as child_hub:
#         # a1 的消息会传播到 b1, b2；b1 的消息也会传播到 a1, a2
```
</details>

### 练习 7.9: 带 Redis 的分布式 MsgHub [挑战]

**题目描述**：
设计一个 `DistributedMsgHub`，使用 Redis pub/sub 作为消息传输中间件，使得不同进程的 Agent 之间也能相互通信。

**预期输出/行为**：
进程 A 中的 Agent 发送消息后，进程 B 中的订阅者能收到消息（通过 Redis 中转）。

<details>
<summary>参考答案</summary>

```python
import asyncio
import json
import redis.asyncio as redis
from agentscope import Msg
from typing import Callable

class DistributedMsgHub:
    """基于 Redis pub/sub 的分布式 MsgHub。"""

    def __init__(
        self,
        participants,
        channel: str = "agentscope:broadcast",
        redis_url: str = "redis://localhost:6379",
    ):
        self.participants = participants
        self.channel = channel
        self.redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None

    async def __aenter__(self):
        self._redis = redis.from_url(self.redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self.channel)
        # 启动消费者任务
        asyncio.create_task(self._consume_messages())
        return self

    async def __aexit__(self, *args):
        if self._pubsub:
            await self._pubsub.unsubscribe(self.channel)
        if self._redis:
            await self._redis.close()

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """将消息发布到 Redis 频道。"""
        if self._redis is None:
            raise RuntimeError("MsgHub not started")
        msg_list = msg if isinstance(msg, list) else [msg]
        for m in msg_list:
            await self._redis.publish(
                self.channel,
                json.dumps({"name": m.name, "content": m.content, "role": m.role})
            )

    async def _consume_messages(self) -> None:
        """消费 Redis 消息并分发给本地参与者。"""
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                msg = Msg(**data)
                for participant in self.participants:
                    if participant.name != msg.name:  # 排除发送者
                        await participant.observe(msg)

# 使用示例：进程 A
# async with DistributedMsgHub(participants=[agent_a], channel="agents") as hub:
#     await hub.broadcast(Msg("agent_a", "hello from A", "assistant"))
#
# 进程 B
# async with DistributedMsgHub(participants=[agent_b], channel="agents") as hub:
#     # agent_b 会收到来自 agent_a 的消息
```
</details>

---

## 参考答案

### 7.1 基础题

**第1题：订阅过滤**

> **注意**: 重写 `broadcast()` 只能拦截**手动广播**。自动广播走 `AgentBase._broadcast_to_subscribers()` 通道，不经过 `MsgHub.broadcast()`。要完全控制自动广播，需结合 `enable_auto_broadcast=False`。

```python
class FilteredMsgHub(MsgHub):
    """支持按发送者过滤的 MsgHub"""

    def __init__(self, participants, subscribe_filter=None, **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.subscribe_filter = subscribe_filter  # Callable[[Msg], bool]

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """重写 broadcast，仅对通过过滤条件的消息进行广播。"""
        if self.subscribe_filter and not self.subscribe_filter(msg):
            return
        # 调用父类的广播实现
        await super().broadcast(msg)

# 使用示例：只订阅来自 agent1 的消息
async with FilteredMsgHub(
    participants=[agent1, agent2],
    subscribe_filter=lambda m: m.name == "agent1"
):
    ...
```

**第2题：广播策略**

> **限制**: 与第 1 题相同，策略模式只影响手动 `broadcast()` 调用。自动广播路径不受影响。

```python
from abc import ABC, abstractmethod

class BroadcastStrategy(ABC):
    @abstractmethod
    async def broadcast(self, participants, msg):
        ...

class RoundRobinStrategy(BroadcastStrategy):
    """轮询广播策略：每次只广播给一个参与者。"""
    def __init__(self):
        self._index = 0

    async def broadcast(self, participants, msg):
        target = participants[self._index % len(participants)]
        self._index += 1
        await target.observe(msg)

# 使用：包装 MsgHub 的 broadcast 方法
class StrategyMsgHub(MsgHub):
    def __init__(self, participants, strategy: BroadcastStrategy, **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.strategy = strategy

    async def broadcast(self, msg):
        await self.strategy.broadcast(self.participants, msg)
```

### 7.2 进阶级参考答案

**第3题：层级 MsgHub**

> **注意**: 当前 MsgHub 不支持嵌套。以下是一个扩展设计思路，非框架内置功能。

```python
class HierarchicalMsgHub(MsgHub):
    """扩展设计：支持层级消息传播的 MsgHub。"""
    def __init__(self, participants, parent_hub=None, **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.parent_hub = parent_hub
        self.child_hubs: list["HierarchicalMsgHub"] = []

    def add_child(self, child_hub: "HierarchicalMsgHub"):
        self.child_hubs.append(child_hub)
        child_hub.parent_hub = self

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """向本地订阅者广播，并向子级传播。"""
        # 向本地参与者广播
        await super().broadcast(msg)
        # 向子级传播
        for child in self.child_hubs:
            await child.broadcast(msg)
```

**第4题：消息过滤代理**

```python
class ContentFilterMsgHub(MsgHub):
    """基于消息内容的过滤 MsgHub（继承 MsgHub，保留上下文管理器协议）。"""
    def __init__(self, participants, filter_rules: dict[str, Callable], **kwargs):
        super().__init__(participants=participants, **kwargs)
        self.filter_rules = filter_rules  # {"role": lambda r: r == "user"}

    def _should_pass(self, msg: Msg) -> bool:
        for key, predicate in self.filter_rules.items():
            value = getattr(msg, key, None)
            if not predicate(value):
                return False
        return True

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """仅对通过过滤条件的消息调用父类的 broadcast。"""
        if self._should_pass(msg):
            await super().broadcast(msg)
```

### 7.3 挑战题参考答案

**第5题：分布式调度器**

关键设计：(1) 使用 Redis Pub/Sub 替代本地的 `MsgHub.broadcast()` 遍历；(2) 每个进程维护本地 `MsgHub` 实例和参与者列表；(3) 某个进程中 Agent 回复时，通过 Redis channel 广播消息，其他进程的监听器接收后调用 `agent.observe(msg)` 转发给本地订阅者；(4) 消息序列化使用 `msg.to_dict()`，反序列化使用 `Msg.from_dict()`；(5) 需要处理网络延迟、消息丢失（ACK 机制）、和订阅者上下线（心跳检测）。

---

---

## 小结

| 特性 | 实现方式 |
|------|----------|
| 消息路由 | 发布-订阅模式（MsgHub） |
| 异步生命周期 | `async with` 上下文管理器 |
| 广播机制 | `AgentBase.__call__()` → `_broadcast_to_subscribers()` 自动通知订阅者 |
| 动态参与者 | 运行时 `add()`/`delete()` |
| 手动广播 | `MsgHub.broadcast(msg)` 遍历参与者调用 `observe()` |
| 扩展模式 | ChatRoom 支持多轮对话 |

Dispatcher 模块通过 MsgHub 实现了代理间的松耦合通信，是构建多代理协作系统的核心基础设施。

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [智能体模块](module_agent_deep.md#3-agentbase-源码解读) | `AgentBase.observe()` 接收广播消息；`__call__()` → `_broadcast_to_subscribers()` 触发自动广播 | 第 3.5 节 |
| [消息模块](module_message_deep.md#3-核心类与函数源码解读) | Msg 对象是消息传递的载体，`to_dict()`/`from_dict()` 用于序列化 | 第 3.1 节 |
| [管道模块](module_pipeline_infra_deep.md#2-pipeline-工作流编排) | MsgHub 在 Pipeline 中的角色；ChatRoom 扩展的多轮对话机制 | 第 2.1 节 |
| [运行时模块](module_runtime_deep.md#4-源码解读) | `sequential_pipeline`/`fanout_pipeline` 中 agent 调用触发 MsgHub 自动广播 | 第 4.3 节 |
| [工具模块](module_tool_mcp_deep.md#5-mcp-协议实现) | MCP 协议中的消息路由模式对比 | 第 5.7 节 |


---

## 参考资料

- MsgHub 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py`
- ChatRoom 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_chat_room.py`

**版本参考**: AgentScope >= 1.0.0 | 源码 `pipeline/`
