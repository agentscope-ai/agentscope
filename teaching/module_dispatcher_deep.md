# Dispatcher 调度器源码深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [核心类继承体系](#3-核心类继承体系)
4. [源码解读](#4-源码解读)
   - [MsgHub 消息中心](#41-msghub-消息中心)
   - [消息广播流程](#42-消息广播流程)
   - [订阅者管理机制](#43-订阅者管理机制)
   - [关键方法流程图](#44-关键方法流程图)
5. [设计模式总结](#5-设计模式总结)
6. [代码示例](#6-代码示例)
7. [练习题](#7-练习题)

---

## 1. 模块概述

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
AgentBase (agent 模块)
    │
    └── MsgHub
            ├── __aenter__ / __aexit__  # 异步上下文管理
            ├── add()                    # 添加参与者
            ├── delete()                  # 删除参与者
            ├── broadcast()               # 广播消息
            └── set_auto_broadcast()      # 设置自动广播
                    │
                    └── _reset_subscriber()  # 重置订阅关系
```

**注意**: MsgHub 不继承 AgentBase，而是作为独立的消息路由组件存在。

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
        if isinstance(agent, AgentBase):
            participant = [agent]

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
  消息自动触发
       │
       ▼
  Agent2.observe(msg)  ───┐
  Agent3.observe(msg)  ───┼── 广播给所有订阅者
  Agent4.observe(msg)  ───┘
```

---

## 5. 设计模式总结

| 模式 | 应用位置 | 说明 |
|------|----------|------|
| **中介者模式** | MsgHub | 作为代理间的消息中介，降低耦合 |
| **发布订阅模式** | observe/subscribers | 代理订阅消息，MsgHub 负责分发 |
| **上下文管理器** | __aenter__/__aexit__ | 自动管理订阅生命周期 |
| **迭代器模式** | participants | 支持 Sequence 接口，灵活遍历 |
| **警告日志** | delete() | 优雅处理删除不存在的参与者 |

---

## 6. 代码示例

### 6.1 基本用法

```python
import asyncio
from agentscope import AgentBase, Msg
from agentscope.pipeline import MsgHub

class SimpleAgent(AgentBase):
    async def __call__(self, msg):
        return Msg("assistant", f"{self.name}: {msg.content}", "assistant")

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

### 6.2 手动广播模式

```python
import asyncio
from agentscope import AgentBase, Msg
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

### 6.3 动态管理参与者

```python
import asyncio
from agentscope import AgentBase, Msg
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

### 6.4 等价手动实现对比

```python
# MsgHub 简化写法
async with MsgHub(participants=[agent1, agent2, agent3]):
    x1 = await agent1(Msg("user", "Hello", "user"))
    # agent2, agent3 自动收到 x1

# 等价的手动实现（需在 async 函数中执行）
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

---

**提示**: 练习题的参考答案可在 AgentScope 官方文档中找到。
