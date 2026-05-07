# 管道与基础设施模块深度剖析

**预计时间**：40 分钟
**先修要求**：已完成 [核心概念](04_core_concepts.md)

## 目录

1. [模块概述](#1-模块概述)
2. [Pipeline 工作流编排](#2-pipeline-工作流编排)
   - [2.1 消息中心 MsgHub](#21-消息中心-msghub)
   - [2.2 Pipeline 类型](#22-pipeline-类型)
   - [2.3 Pipeline 源码深度解析](#23-pipeline-源码深度解析)
     - [2.3.1 `_msghub.py` 消息中心概念示例](#231-_msghubpy-消息中心概念示例)
     - [2.3.2 `_functional.py` 函数式管道概念示例](#232-_functionalpy-函数式管道概念示例)
     - [2.3.3 `_class.py` Pipeline 类封装概念示例](#233-_classpy-pipeline-类封装概念示例)
     - [2.3.4 `_chat_room.py` 聊天室概念示例](#234-_chat_roompy-聊天室概念示例)
     - [2.3.5 Pipeline 执行流程总结](#235-pipeline-执行流程总结)
3. [Formatter 消息格式化](#3-formatter-消息格式化)
4. [Realtime 实时交互](#4-realtime-实时交互)
5. [Session 会话管理](#5-session-会话管理)
6. [Tracing 追踪系统](#6-tracing-追踪系统)
7. [A2A 协议](#7-a2a-协议)
8. [其他基础设施模块](#8-其他基础设施模块)
9. [代码示例](#9-代码示例)
10. [练习题](#10-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 AgentScope Pipeline 编排层包含的核心组件（MsgHub、Formatter、Session、Tracing、A2A） | 列举、识别 |
| 理解 | 解释 MsgHub 的发布-订阅机制与 AgentBase `_broadcast_to_subscribers` 的协作关系 | 解释、比较 |
| 应用 | 使用 Pipeline 函数式接口和 MsgHub 组装一个多智能体协作工作流 | 实现、配置 |
| 分析 | 分析 Formatter 如何针对不同模型 API 的消息格式要求进行适配和转换 | 分析、调查 |
| 评价 | 评价 A2A 协议与 MCP 协议在智能体间通信场景中的适用性差异 | 评价、对比 |
| 创造 | 设计一个支持断点恢复、分布式会话管理和全链路追踪的 Pipeline 生产架构 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 异步编程基础 (`async`/`await`、`async with`)
- [ ] WebSocket 通信的基本概念
- [ ] OpenTelemetry 或分布式追踪的基础概念
- [ ] 了解 HTTP/REST API 和进程间通信 (IPC) 的基本原理

**预计学习时间**: 35 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `async with MsgHub(...)` | try-with-resources (AutoCloseable) | 上下文管理 |
| `asyncio.gather()` | `CompletableFuture.allOf()` | 并发聚合 |
| Formatter 模式 | Spring `HttpMessageConverter` | 格式适配 |
| OpenTelemetry Tracing | Micrometer + OpenTelemetry Java Agent | 可观测性 |

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/pipeline/
├── __init__.py
├── _msghub.py              # 消息中心（发布-订阅模式）
├── _chat_room.py           # 聊天室（实时多代理广播）
├── _class.py               # Pipeline 类封装（面向对象）
└── _functional.py           # Pipeline 函数式封装

src/agentscope/formatter/
├── __init__.py
├── _formatter_base.py      # 格式化器基类
├── _openai_formatter.py    # OpenAI 格式化器
├── _dashscope_formatter.py # DashScope 格式化器
├── _anthropic_formatter.py # Anthropic 格式化器
├── _gemini_formatter.py    # Gemini 格式化器
├── _ollama_formatter.py    # Ollama 格式化器
├── _deepseek_formatter.py  # DeepSeek 格式化器
├── _truncated_formatter_base.py  # 截断格式化器
└── _a2a_formatter.py       # A2A 格式化器

src/agentscope/realtime/
├── __init__.py
├── _base.py                # 实时代理基类
├── _dashscope_realtime_model.py  # DashScope 实时代理
├── _openai_realtime_model.py     # OpenAI 实时代理
├── _gemini_realtime_model.py     # Gemini 实时代理
└── _events/                # 实时事件定义

src/agentscope/session/
├── __init__.py
├── _session_base.py        # 会话基类
├── _json_session.py        # JSON 文件会话
├── _redis_session.py       # Redis 会话
└── _tablestore_session.py # 表格存储会话

src/agentscope/tracing/
├── __init__.py
├── _trace.py               # 追踪装饰器（OpenTelemetry）
├── _attributes.py          # 追踪属性定义
├── _converter.py           # ContentBlock 到 OTel 格式转换
├── _extractor.py           # 追踪数据提取器
├── _setup.py               # 追踪初始化
└── _utils.py               # 追踪工具

src/agentscope/a2a/
├── __init__.py
├── _base.py                # Agent Card 解析器基类
├── _file_resolver.py       # 文件解析器
├── _well_known_resolver.py # Well-Known 解析器
└── _nacos_resolver.py      # Nacos 解析器
```

---

## 2. Pipeline 工作流编排

### 2.1 消息中心 MsgHub

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py`

MsgHub 是 AgentScope 的消息路由中心，基于**发布-订阅模式**，支持多代理间的消息自动广播。其核心设计思想是通过上下文管理器简化多代理通信。

> **交叉引用**: 消息广播机制基于 Agent 的 `_broadcast_to_subscribers` 方法实现，详见 `module_agent_deep.md` 的「订阅发布机制」章节。

#### MsgHub 核心机制

```python
class MsgHub:
    """MsgHub class that controls the subscription of the participated agents."""

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: list[Msg] | Msg | None = None,
        enable_auto_broadcast: bool = True,
        name: str | None = None,
    ) -> None:
        # 第68行: 生成唯一名称
        self.name = name or shortuuid.uuid()
        self.participants = list(participants)
        self.announcement = announcement
        self.enable_auto_broadcast = enable_auto_broadcast
```

**关键设计**:

1. **上下文管理器**: `__aenter__` 重置订阅关系，`__aexit__` 清理订阅
2. **自动广播**: 当任一代理回复消息时，自动将该消息广播给所有其他参与代理
3. **订阅关系管理**: 每个代理维护一个订阅者字典，键是 MsgHub 名称

### 2.2 Pipeline 类型

#### SequentialPipeline

顺序执行，上一个代理的输出作为下一个代理的输入:

```
UserInput -> Agent1 -> Agent2 -> Agent3 -> FinalOutput
```

#### FanoutPipeline

分支执行，多个代理并行处理相同输入:

```
                    -> Agent1 ->
UserInput -> Splitter            -> Aggregator -> Output
                    -> Agent2 ->
                    -> Agent3 ->
```

### 2.3 Pipeline 源码深度解析

#### 2.3.1 `_msghub.py` 消息中心概念示例

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py`

> **重要说明**: 以下代码为**概念示例**（含中文注释），用于说明 MsgHub 的结构和机制。实际源码为英文，可参考上述文件路径。

```python
# ============================================================
# 概念示例代码 (PSEUDOCODE - CONCEPTUAL EXAMPLE)
# 以下代码仅用于说明 MsgHub 的结构和机制
# 实际源码为英文，请参考 /src/agentscope/pipeline/_msghub.py
# ============================================================
# -*- coding: utf-8 -*-
"""MsgHub is designed to share messages among a group of agents."""

from collections.abc import Sequence
from typing import Any

import shortuuid

from .._logging import logger
from ..agent import AgentBase
from ..message import Msg


class MsgHub:
    """MsgHub class that controls the subscription of the participated agents.

    MsgHub 通过上下文管理器管理一组代理的消息订阅关系。
    当任一代理回复消息时，自动将该消息广播给所有其他参与代理。
    """

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: list[Msg] | Msg | None = None,
        enable_auto_broadcast: bool = True,
        name: str | None = None,
    ) -> None:
        """初始化 MsgHub 上下文管理器.

        Args:
            participants: 参与 MsgHub 的代理序列
            announcement: 进入 MsgHub 时广播的公告消息
            enable_auto_broadcast: 是否启用自动广播
            name: MsgHub 名称，默认生成随机 UUID
        """
        # 第68行: 生成唯一名称
        self.name = name or shortuuid.uuid()
        self.participants = list(participants)
        self.announcement = announcement
        self.enable_auto_broadcast = enable_auto_broadcast

    async def __aenter__(self) -> "MsgHub":
        """进入 MsgHub 上下文时调用.

        执行流程:
        1. 重置所有参与者的订阅关系
        2. 如果有公告消息，则广播给所有参与者
        """
        self._reset_subscriber()

        # 广播公告消息
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)

        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """退出 MsgHub 上下文时调用.

        清理所有参与者的订阅关系
        """
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

    def _reset_subscriber(self) -> None:
        """重置所有参与者的订阅关系.

        每个代理维护一个订阅者字典，键是 MsgHub 名称，
        值是应该接收该代理消息的其他代理列表。
        """
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.reset_subscribers(self.name, self.participants)

    def add(
        self,
        new_participant: list[AgentBase] | AgentBase,
    ) -> None:
        """添加新的参与者到 MsgHub.

        Args:
            new_participant: 要添加的代理或代理列表
        """
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
        """从参与者列表中删除代理.

        Args:
            participant: 要删除的代理或代理列表
        """
        if isinstance(participant, AgentBase):
            participant = [participant]

        for agent in participant:
            if agent in self.participants:
                self.participants.pop(self.participants.index(agent))
            else:
                logger.warning(
                    "Cannot find the agent with ID %s, skip its deletion.",
                    agent.id,
                )

        self._reset_subscriber()

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """广播消息给所有参与者.

        Args:
            msg: 要广播的消息或消息列表
        """
        for agent in self.participants:
            await agent.observe(msg)

    def set_auto_broadcast(self, enable: bool) -> None:
        """启用/禁用自动广播功能.

        Args:
            enable: True 启用自动广播，False 禁用
        """
        if enable:
            self.enable_auto_broadcast = True
            self._reset_subscriber()
        else:
            self.enable_auto_broadcast = False
            for agent in self.participants:
                agent.remove_subscribers(self.name)
```

> **注**: 以上为概念示例代码，用于说明 MsgHub 的结构和机制。实际源码为英文，可参考 `/src/agentscope/pipeline/_msghub.py`。

**MsgHub 执行流程图**:

```
进入上下文 (__aenter__)
    │
    ├── _reset_subscriber()
    │       │
    │       └── 为每个参与者设置订阅关系
    │             agent.reset_subscribers(hub_name, all_participants)
    │
    └── broadcast(announcement)  [可选]
            │
            └── 遍历所有参与者，调用 agent.observe(msg)

代理执行期间
    │
    └── 代理调用 observe() 时，自动广播给所有订阅者

退出上下文 (__aexit__)
    │
    └── remove_subscribers(hub_name)
            │
            └── 清理所有参与者的订阅关系
```

**使用示例**:

```python
# 使用 MsgHub 自动管理多代理消息传递
async with MsgHub(participants=[agent1, agent2, agent3],
            announcement=Msg("system", "开始协作", "system")):
    x1 = agent1(Msg("user", "你好", "user"))
    # agent1 的回复会自动广播给 agent2 和 agent3

    x2 = agent2(x1)
    # agent2 的回复会自动广播给 agent1 和 agent3
```

#### 2.3.2 `_functional.py` 函数式管道概念示例

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_functional.py`

> **重要说明**: 以下代码为**概念示例**（含中文注释），用于说明函数式管道的结构和机制。实际源码为英文，可参考上述文件路径。

函数式管道提供轻量级的流水线组合方式，适合一次性使用场景。

```python
# ============================================================
# 概念示例代码 (PSEUDOCODE - CONCEPTUAL EXAMPLE)
# 以下代码仅用于说明函数式管道的结构和机制
# 实际源码为英文，请参考 /src/agentscope/pipeline/_functional.py
# ============================================================
# -*- coding: utf-8 -*-
"""Functional counterpart for Pipeline"""
import asyncio
from copy import deepcopy
from typing import Any, AsyncGenerator, Tuple, Coroutine
from ..agent import AgentBase
from ..message import Msg, AudioBlock


async def sequential_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
) -> Msg | list[Msg] | None:
    """顺序管道：依次执行一系列代理.

    执行流程:
    1. 将初始消息传递给第一个代理
    2. 将第一个代理的输出作为第二个代理的输入
    3. 依此类推，直到所有代理执行完毕
    4. 返回最后一个代理的输出
    """
    # 第42-43行: 核心顺序执行逻辑
    for agent in agents:
        msg = await agent(msg)
    return msg


async def fanout_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    enable_gather: bool = True,
    **kwargs: Any,
) -> list[Msg]:
    """扇出管道：将同一消息分发给多个代理.

    执行流程:
    1. 决定执行模式（并发或顺序）
    2. 为每个代理准备消息（深拷贝避免共享状态）
    3. 执行所有代理
    4. 收集并返回所有响应
    """
    if enable_gather:
        # 第96-102行: 并发执行模式
        # 使用 asyncio.create_task 创建独立任务
        # 关键：使用 deepcopy 避免消息被多个代理共享修改
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in agents
        ]

        # 等待所有任务完成
        return await asyncio.gather(*tasks)
    else:
        # 第103-104行: 顺序执行模式
        return [await agent(deepcopy(msg), **kwargs) for agent in agents]


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
    """流式消息管道：实时收集并 yield 代理的打印消息.

    这个管道用于捕获代理在执行过程中通过 `await self.print()` 输出的中间消息，
    并将其实时 yield 给调用者。主要用于流式响应场景。

    执行流程:
    1. 启用代理的消息队列功能
    2. 创建异步任务执行主协程
    3. 从消息队列中循环读取打印消息
    4. 检测结束信号后退出循环
    5. 检查任务是否有异常
    """
    # 第159-163行: 设置消息队列
    queue = queue or asyncio.Queue()
    for agent in agents:
        # 启用消息队列，让代理的 print() 输出进入队列
        agent.set_msg_queue_enabled(True, queue)

    # 第166行: 创建异步任务执行主协程
    task = asyncio.create_task(coroutine_task)

    # 第168-171行: 设置完成回调
    if task.done():
        # 任务已完成，立即发送结束信号
        await queue.put(end_signal)
    else:
        # 任务未完成，添加回调在完成时发送结束信号
        task.add_done_callback(lambda _: queue.put_nowait(end_signal))

    # 第173-187行: 循环读取消息
    while True:
        # 从队列获取打印消息
        printing_msg = await queue.get()

        # 检查是否是结束信号
        if isinstance(printing_msg, str) and printing_msg == end_signal:
            break

        # yield 消息
        if yield_speech:
            yield printing_msg
        else:
            msg, last, _ = printing_msg
            yield msg, last

    # 第189-192行: 检查任务异常
    exception = task.exception()
    if exception is not None:
        raise exception from None
```

> **注**: 以上为概念示例代码，用于说明函数式管道的结构和机制。实际源码为英文，可参考 `/src/agentscope/pipeline/_functional.py`。

**函数式管道执行流程对比**:

```
sequential_pipeline (顺序执行):
┌─────────────────────────────────────────────────────────┐
│  msg ──► agent1 ──► msg1 ──► agent2 ──► msg2 ──► ...   │
└─────────────────────────────────────────────────────────┘

fanout_pipeline with enable_gather=True (并发执行):
┌─────────────────────────────────────────────────────────┐
│                        msg                               │
│                    ┌───┴───┐                            │
│              ┌─────▼─┐ ┌───▼────┐ ┌─────▼─┐            │
│              │agent1 │ │agent2  │ │agent3 │            │
│              └───┬───┘ └───┬────┘ └───┬────┘            │
│                  │         │         │                  │
│                  └────┬────┴────┬────┘                  │
│                       ▼         ▼                        │
│                   [msg1, msg2, msg3]                       │
└─────────────────────────────────────────────────────────┘

fanout_pipeline with enable_gather=False (顺序执行):
┌─────────────────────────────────────────────────────────┐
│  msg ──► agent1 ──► msg1 ──┐                            │
│  msg ──► agent2 ──► msg2 ──┼──► [msg1, msg2, msg3]      │
│  msg ──► agent3 ──► msg3 ──┘                            │
└─────────────────────────────────────────────────────────┘
```

#### 2.3.3 `_class.py` Pipeline 类封装概念示例

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_class.py`

> **重要说明**: 以下代码为**概念示例**（含中文注释），用于说明 Pipeline 类的结构和机制。实际源码为英文，可参考上述文件路径。

Pipeline 类封装了函数式接口，提供了可复用的面向对象方式。

```python
# ============================================================
# 概念示例代码 (PSEUDOCODE - CONCEPTUAL EXAMPLE)
# 以下代码仅用于说明 Pipeline 类的结构和机制
# 实际源码为英文，请参考 /src/agentscope/pipeline/_class.py
# ============================================================
# -*- coding: utf-8 -*-
"""Pipeline classes."""
from typing import Any

from ._functional import sequential_pipeline, fanout_pipeline
from ..agent import AgentBase
from ..message import Msg


class SequentialPipeline:
    """顺序 Pipeline 类：依次执行一系列代理.

    与函数式接口相比，此类支持多次复用（无需每次传递代理列表）。
    """

    def __init__(
        self,
        agents: list[AgentBase],
    ) -> None:
        """初始化顺序 Pipeline.

        Args:
            agents: 要执行的代理列表
        """
        self.agents = agents

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg | list[Msg] | None:
        """执行顺序 Pipeline.

        Args:
            msg: 初始输入消息

        Returns:
            最后一个代理的输出
        """
        # 第37-40行: 委托给函数式接口
        return await sequential_pipeline(
            agents=self.agents,
            msg=msg,
        )


class FanoutPipeline:
    """扇出 Pipeline 类：将输入分发给多个代理.

    支持并发（enable_gather=True）和顺序（enable_gather=False）两种模式。
    与函数式接口相比，此类支持多次复用和默认参数配置。
    """

    def __init__(
        self,
        agents: list[AgentBase],
        enable_gather: bool = True,
    ) -> None:
        """初始化扇出 Pipeline.

        Args:
            agents: 要执行的代理列表
            enable_gather: True=并发执行，False=顺序执行
        """
        self.agents = agents
        self.enable_gather = enable_gather

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """执行扇出 Pipeline.

        Args:
            msg: 要分发的输入消息
            **kwargs: 传递给每个代理的额外参数

        Returns:
            所有代理的响应列表
        """
        # 第85-90行: 委托给函数式接口
        return await fanout_pipeline(
            agents=self.agents,
            msg=msg,
            enable_gather=self.enable_gather,
            **kwargs,
        )
```

> **注**: 以上为概念示例代码，用于说明 Pipeline 类的结构和机制。实际源码为英文，可参考 `/src/agentscope/pipeline/_class.py`。

**Pipeline 类 vs 函数式接口**:

| 特性 | SequentialPipeline/FanoutPipeline | sequential_pipeline/fanout_pipeline |
|------|-----------------------------------|--------------------------------------|
| 复用性 | 可多次调用 | 一次性使用 |
| 配置 | 在构造时配置 | 在调用时传递参数 |
| 适用场景 | 生产环境，多次执行 | 快速原型，一次性流程 |

#### 2.3.4 `_chat_room.py` 聊天室概念示例

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_chat_room.py`

> **重要说明**: 以下代码为**概念示例**（含中文注释），用于说明 ChatRoom 的结构和机制。实际源码为英文，可参考上述文件路径。

ChatRoom 用于实时多代理协作场景，支持消息广播和前端交互。

```python
# ============================================================
# 概念示例代码 (PSEUDOCODE - CONCEPTUAL EXAMPLE)
# 以下代码仅用于说明 ChatRoom 的结构和机制
# 实际源码为英文，请参考 /src/agentscope/pipeline/_chat_room.py
# ============================================================
# -*- coding: utf-8 -*-
"""The Voice chat room"""
import asyncio
from asyncio import Queue

from ..agent import RealtimeAgent
from ..realtime import ClientEvents, ServerEvents


class ChatRoom:
    """聊天室抽象：管理多个实时代理之间的消息广播.

    ChatRoom 维护一个内部队列用于收集所有代理的消息，
    并将消息转发到前端和其他代理。
    """

    def __init__(self, agents: list[RealtimeAgent]) -> None:
        """初始化聊天室.

        Args:
            agents: 参与聊天室的实时代理列表
        """
        self.agents = agents

        # 内部队列：收集所有代理的消息
        self._queue = Queue()

        # 转发循环任务
        self._task = None

    async def start(self, outgoing_queue: Queue) -> None:
        """启动聊天室：建立所有代理的连接.

        执行流程:
        1. 为每个代理启动连接
        2. 创建转发循环任务

        Args:
            outgoing_queue: 用于向前端推送消息的队列
        """
        # 第39-40行: 启动所有代理
        for agent in self.agents:
            await agent.start(self._queue)

        # 第42-43行: 启动转发循环
        self._task = asyncio.create_task(self._forward_loop(outgoing_queue))

    async def _forward_loop(self, outgoing_queue: Queue) -> None:
        """消息转发循环.

        执行流程:
        1. 从内部队列获取消息
        2. 判断消息类型（ClientEvents 或 ServerEvents）
        3. 客户端事件：分发给所有代理
        4. 服务器事件：转发到前端，并广播给其他代理

        Args:
            outgoing_queue: 用于向前端推送消息的队列
        """
        while True:
            # 第54-56行: 从队列获取消息
            event = await self._queue.get()

            # 第59-63行: 处理客户端事件（来自前端）
            if isinstance(event, ClientEvents.EventBase):
                # 分发给所有代理
                for agent in self.agents:
                    await agent.handle_input(event)

            # 第65-77行: 处理服务器事件（来自代理）
            elif isinstance(event, ServerEvents.EventBase):
                # 转发到前端队列
                await outgoing_queue.put(event)

                # 广播给其他代理（除发送者外）
                sender_id = getattr(event, "agent_id", None)
                if sender_id:
                    for agent in self.agents:
                        if agent.id != sender_id:
                            await agent.handle_input(event)

    async def stop(self) -> None:
        """停止聊天室：关闭所有代理连接."""
        # 第79-87行: 停止所有代理并取消转发循环任务
        for agent in self.agents:
            await agent.stop()

        if not self._task.done():
            self._task.cancel()

    async def handle_input(self, event: ClientEvents.EventBase) -> None:
        """处理来自前端的消息并分发给所有代理.

        Args:
            event: 来自前端的事件
        """
        await self._queue.put(event)
```

> **注**: 以上为概念示例代码，用于说明 ChatRoom 的结构和机制。实际源码为英文，可参考 `/src/agentscope/pipeline/_chat_room.py`。

**ChatRoom 消息流**:

```
                    ┌─────────────────────────────────────────┐
                    │              ChatRoom                   │
                    │                                         │
前端 ──► ClientEvents ──► _forward_loop ──► agent.handle_input│
                                        │                    │
                                        ├──► agent1          │
                                        ├──► agent2          │
                                        └──► agent3          │
                    │                                         │
                    │   ServerEvents ◄──── agent1              │
                    │         │                │              │
                    │         ▼                ▼              │
outgoing_queue ◄────┼──── ServerEvents ◄──── agent2              │
                    │         │                               │
                    │         └──► agent3 (exclude sender)     │
                    │                                         │
                    └─────────────────────────────────────────┘
```

#### 2.3.5 Pipeline 执行流程总结

**完整 Pipeline 执行流程图**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Pipeline 模块入口                            │
│  __init__.py 导出: MsgHub, SequentialPipeline, FanoutPipeline,   │
│                    sequential_pipeline, fanout_pipeline,        │
│                    stream_printing_messages, ChatRoom            │
└─────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │  MsgHub     │    │ Sequential  │    │  Fanout     │
    │  消息中心   │    │ Pipeline    │    │  Pipeline   │
    └─────────────┘    └─────────────┘    └─────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
    发布-订阅广播        顺序执行              并发/顺序分发
           │                  │                  │
           │            agent1(msg) ◄─────┐     │
           │                  │           │     │
           │                  ▼           │     │
           │            agent2(result) ◄──┘     │
           │                  │                │
           │                  ▼                │
           │            agent3(result) ◄───────┘
           │                  │                │
           ▼                  ▼                ▼
    所有订阅者收到     返回最终结果      返回所有结果列表
    广播消息
```

**关键设计模式**:

1. **发布-订阅模式 (MsgHub)**: 解耦消息生产者和消费者
2. **管道模式 (Pipeline)**: 链接多个处理步骤
3. **策略模式**: fanout_pipeline 支持并发/顺序执行策略
4. **装饰器模式**: stream_printing_messages 包装现有协程

---

### 边界情况与陷阱

#### MsgHub 的发布订阅陷阱

**Critical: 循环订阅导致死循环**

```python
# 如果参与者列表包含 MsgHub 自身，会导致无限广播
async with MsgHub(participants=[agent1, msg_hub_instance, agent2]):
    # 错误：msg_hub_instance 也是参与者，会收到自己的广播
    pass
```

**High: enable_auto_broadcast 与手动 broadcast 的交互**

```python
# 当 enable_auto_broadcast=False 时，broadcast() 仍然可以手动调用
# 但不会触发自动广播，可能导致消息丢失
hub = MsgHub(participants=[a1, a2], enable_auto_broadcast=False)
async with hub:
    await hub.broadcast(msg)  # 只发送给 a1, a2，不会自动广播
    # 后续 a1 的回复不会被自动广播！
```

#### FanoutPipeline 的并发陷阱

**High: deepcopy 性能开销**

```python
# deepcopy 在消息较大时会产生显著性能开销
# 对于包含大量历史的消息，每次分发都会完整拷贝
large_msg = Msg(content=very_long_history * 1000)  # 10MB 消息
fanout_pipeline([agent1, agent2, agent3], large_msg)  # 3次拷贝 = 30MB
```

**Medium: 并发异常传播**

```python
# asyncio.gather() 默认行为：一个任务异常会取消其他任务
tasks = [asyncio.create_task(agent(deepcopy(msg))) for agent in agents]
# 如果 agent2 抛出异常，agent1 和 agent3 会被取消
results = await asyncio.gather(*tasks)  # 异常会传播
```

#### Formatter 的格式陷阱

**High: 不同模型 API 的 tool_call 格式差异**

```python
# OpenAI: tool_call 是独立字段
{"role": "assistant", "tool_calls": [...]}

# Anthropic: tool_use 是 content 内的块
{"role": "user", "content": [{"type": "tool_use", ...}]}

# Gemini: function_call 是独立字段
{"role": "model", "function_call": {...}}

# 如果错误混用，会导致 API 报 400 错误
```

**Medium: 消息截断的语义丢失**

```python
# TruncatedFormatter 截断消息时，可能截断在句子中间
# 导致 LLM 收到不完整的句子，理解出现偏差
content = "Please buy 100 shares of AAPL, 50 shares of MSFT, and 30..."  # 被截断
# LLM 可能只看到 "Please buy 100 shares of AAPL, 50 shares of MSFT, and 30"
```

#### Session 的持久化陷阱

**High: StateModule 未正确初始化的对象无法加载**

```python
# 如果 StateModule 子类在 __init__ 中忘记调用 super().__init__()
# load_state_dict 会静默失败（strict=False 时）
class BadAgent(AgentBase):
    def __init__(self):
        self.memory = InMemoryMemory()  # 忘记调用 super().__init__()！
        # 此时 _module_dict 不存在，memory 不会被追踪

agent.load_state_dict(state)  # memory 字段不会被恢复！
```

**Medium: 并发写入同一 Session**

```python
# 多个协程同时保存同一 session_id，会产生竞态条件
# RedisSession 不提供原子性保证
await asyncio.gather(
    session.save_session_state("s1", user_id="u1", agent=agent1),
    session.save_session_state("s1", user_id="u1", agent=agent2),  # 覆盖！
)
```

#### Tracing 的采样陷阱

**Medium: trace_enabled 默认关闭导致数据丢失**

```python
# 如果忘记在 init() 中启用 tracing，追踪数据不会发送
agentscope.init(project="my-project")  # 没有 tracing_url
# 所有 @trace_reply 等装饰器不会记录任何数据
# 在生产环境排查问题时会丢失关键链路信息
```

---

### 性能考量

#### Pipeline 执行性能

| 场景 | 推荐模式 | 原因 |
|------|----------|------|
| 少量快速 Agent | SequentialPipeline | 避免并发开销 |
| 大量耗时 Agent | FanoutPipeline (enable_gather=True) | 最大化并发 |
| 内存敏感 | FanoutPipeline (enable_gather=False) | 避免 deepcopy |
| 流式响应 | stream_printing_messages | 支持增量输出 |

**deepcopy 开销分析**：
- 消息 < 1KB：开销可忽略 (< 1ms)
- 消息 1KB-100KB：需要注意 (~5-50ms)
- 消息 > 100KB：考虑使用 `msg.copy()` 或只拷贝必要字段

#### Formatter 性能

**消息格式转换开销**：
- SimpleFormatter: 最快（直接字典转换）
- OpenAIChatFormatter: 中等（需要处理 tool_calls）
- TruncatedFormatterBase: 最慢（需要计算 token 并截断）

**优化建议**：
- 批量格式化时复用 Formatter 实例
- 避免在热路径中重复创建 Formatter 对象

#### Session 性能

**存储后端选择**：

| 后端 | 适用场景 | 延迟 | 容量 |
|------|----------|------|------|
| JSONSession | 开发/小规模 | ~10ms | 本地磁盘 |
| RedisSession | 生产/分布式 | ~1ms | 内存（可持久化）|
| TablestoreSession | 超大规模 | ~10ms | 云存储无限 |

---

## 3. Formatter 消息格式化

### 3.1 FormatterBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/formatter/_formatter_base.py`

Formatter 是消息格式化器基类，负责将 AgentScope 的 `Msg` 对象转换为各个模型提供商所需的格式。

```python
class FormatterBase:
    """The base class for formatters."""

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """Assert that the input is a list of Msg objects."""
        if not isinstance(msgs, list):
            raise TypeError("Input must be a list of Msg objects.")

        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"Expected Msg object, got {type(msg)} instead.",
                )

    @staticmethod
    def convert_tool_result_to_string(
        output: str | List[TextBlock | ImageBlock | AudioBlock | VideoBlock],
    ) -> tuple[str, Sequence[Tuple[str, ImageBlock | AudioBlock | TextBlock | VideoBlock]]]:
        """Turn the tool result list into a textual output to be compatible
        with the LLM API that doesn't support multimodal data in the tool
        result."""
```

**关键方法说明**:

| 方法 | 作用 |
|------|------|
| `format()` | 将 Msg 对象转换为 API 所需的字典格式 |
| `assert_list_of_msgs()` | 验证输入是 Msg 对象列表 |
| `convert_tool_result_to_string()` | 将工具结果（支持多模态）转换为文本格式 |

### 3.2 OpenAI Formatter

**文件**: `_openai_formatter.py`

```python
class OpenAIChatFormatter(TruncatedFormatterBase):
    """Formatter for OpenAI API messages."""

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages for OpenAI API.

        Converts AgentScope Msg objects to OpenAI message format:
        {
            "role": "user" | "assistant" | "system",
            "content": str | list[...],
            "name": str (optional)
        }
        """
```

### 3.3 截断格式化器

**文件**: `_truncated_formatter_base.py`

用于处理超出模型上下文窗口的过长消息，实现自动截断功能。

---

## 4. Realtime 实时交互

### 4.1 RealtimeModelBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/realtime/_base.py`

RealtimeModelBase 是实时代理模型的核心基类，通过 WebSocket 与实时 API 建立双向通信。

```python
class RealtimeModelBase:
    """The realtime model base class."""

    model_name: str
    """The model name"""

    support_input_modalities: list[str]
    """The supported input modalities of the DashScope realtime model."""

    websocket_url: str
    """The websocket URL of the realtime model API."""

    websocket_headers: dict[str, str]
    """The websocket headers of the realtime model API."""

    input_sample_rate: int
    """The input audio sample rate."""

    output_sample_rate: int
    """The output audio sample rate."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._incoming_queue = Queue()
        self._incoming_task = None
        self._websocket: ClientConnection | None = None

    async def connect(
        self,
        outgoing_queue: Queue,
        instructions: str,
        tools: list[dict] | None = None,
    ) -> None:
        """Establish a connection to the realtime model."""
        import websockets

        self._websocket = await websockets.connect(
            self.websocket_url,
            additional_headers=self.websocket_headers,
        )

        self._incoming_task = asyncio.create_task(
            self._receive_model_event_loop(outgoing_queue),
        )

        session_config = self._build_session_config(instructions, tools)
        await self._websocket.send(json.dumps(session_config, ensure_ascii=False))

    @abstractmethod
    async def send(
        self,
        data: AudioBlock | TextBlock | ImageBlock | ToolResultBlock,
    ) -> None:
        """Send data to the realtime model for processing."""

    @abstractmethod
    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the session configuration message to initialize or update
        the realtime model session."""

    @abstractmethod
    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse the message received from the realtime model API."""
```

### 4.2 Realtime 事件类型

**文件**: `src/agentscope/realtime/_events/`

Realtime 模块使用 `ModelEvents` 事件体系，包括:
- `ClientEvents`: 来自前端的事件
- `ServerEvents`: 来自服务器/代理的事件

### 4.3 实现示例 - DashScope Realtime

**文件**: `_dashscope_realtime_model.py`

DashScope 实时代理模型实现了与阿里云 DashScope Realtime API 的对接。

---

## 5. Session 会话管理

### 5.1 SessionBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/session/_session_base.py`

SessionBase 是会话管理的抽象基类，定义了会话状态的保存和加载接口。

```python
class SessionBase:
    """The base class for session in agentscope."""

    @abstractmethod
    async def save_session_state(
        self,
        session_id: str,
        user_id: str = "",
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the session state

        Args:
            session_id: The session id.
            user_id: The user ID for the storage.
            **state_modules_mapping: A dictionary mapping of state module names
                to their instances.
        """

    @abstractmethod
    async def load_session_state(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the session state

        Args:
            session_id: The session id.
            user_id: The user ID for the storage.
            allow_not_exist: Whether to allow the session to not exist.
            **state_modules_mapping: The mapping of state modules to be loaded.
        """
```

**设计特点**:

1. **基于 StateModule**: 会话状态通过 `StateModule` 实例保存，而非原始字典
2. **支持多用户**: 通过 `user_id` 区分不同用户的会话
3. **懒加载**: `allow_not_exist` 控制会话不存在时的行为

### 5.2 会话实现

AgentScope 提供了多种会话存储后端:

| 实现 | 文件 | 说明 |
|------|------|------|
| JSONSession | `_json_session.py` | 基于本地 JSON 文件 |
| RedisSession | `_redis_session.py` | 基于 Redis 分布式存储 |
| TablestoreSession | `_tablestore_session.py` | 基于阿里云表格存储 |

---

## 6. Tracing 追踪系统

### 6.1 基于 OpenTelemetry 的追踪架构

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tracing/_trace.py`

AgentScope 的追踪系统基于 **OpenTelemetry** 标准实现，支持分布式追踪。

**核心组件**:

1. **Span**: 追踪的基本单元，表示一个操作
2. **Tracer**: 用于创建 Span 的追踪器
3. **属性提取器**: 从函数参数/返回值中提取追踪属性

### 6.2 追踪装饰器

AgentScope 提供了多个专用追踪装饰器:

```python
# trace_reply - 追踪代理回复
def trace_reply(
    func: Callable[..., Coroutine[Any, Any, Msg]],
) -> Callable[..., Coroutine[Any, Any, Msg]]:
    """Trace the agent reply call with OpenTelemetry."""

# trace_llm - 追踪 LLM 调用
def trace_llm(
    func: Callable[..., Coroutine[Any, Any, ChatResponse | AsyncGenerator[ChatResponse, None]]],
) -> Callable[..., Coroutine[Any, Any, ChatResponse | AsyncGenerator[ChatResponse, None]]]:
    """Trace the LLM call with OpenTelemetry."""

# trace_toolkit - 追踪工具调用
def trace_toolkit(
    func: Callable[..., AsyncGenerator[ToolResponse, None]],
) -> Callable[..., Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]]]:
    """Trace the toolkit call_tool_function method with OpenTelemetry."""

# trace_embedding - 追踪 Embedding 调用
def trace_embedding(
    func: Callable[..., Coroutine[Any, Any, EmbeddingResponse]],
) -> Callable[..., Coroutine[Any, Any, EmbeddingResponse]]:
    """Trace the embedding call with OpenTelemetry."""

# trace_format - 追踪 Formatter 调用
def trace_format(
    func: Callable[..., Coroutine[Any, Any, list[dict]]],
) -> Callable[..., Coroutine[Any, Any, list[dict]]]:
    """Trace the format function of the formatter with OpenTelemetry."""

# trace - 通用追踪装饰器
def trace(name: str | None = None) -> Callable:
    """A generic tracing decorator for synchronous and asynchronous functions."""
```

### 6.3 追踪属性提取

**文件**: `_extractor.py`

追踪系统使用属性提取器从函数参数和返回值中提取有价值的追踪信息:

```python
# Agent 请求属性
_get_agent_request_attributes()
_get_agent_span_name()
_get_agent_response_attributes()

# LLM 请求/响应属性
_get_llm_request_attributes()
_get_llm_span_name()
_get_llm_response_attributes()

# 工具请求/响应属性
_get_tool_request_attributes()
_get_tool_span_name()
_get_tool_response_attributes()

# Formatter 属性
_get_formatter_request_attributes()
_get_formatter_span_name()
_get_formatter_response_attributes()

# Embedding 属性
_get_embedding_request_attributes()
_get_embedding_span_name()
_get_embedding_response_attributes()
```

### 6.4 生成器追踪

对于流式输出（AsyncGenerator），追踪系统使用专门的包装器:

```python
async def _trace_async_generator_wrapper(
    res: AsyncGenerator[T, None],
    span: Span,
) -> AsyncGenerator[T, None]:
    """Trace the async generator output with OpenTelemetry.

    - 追踪每个 chunk 的输出
    - 在最后一个 chunk 时设置响应属性
    - 处理异常并记录错误状态
    """
```

### 6.5 追踪配置

**文件**: `_setup.py`

追踪系统通过 `_get_tracer()` 获取配置的 Tracer，并通过 `_config.trace_enabled` 控制是否启用。

---

## 7. A2A 协议

### 7.1 A2A 协议概述

A2A (Agent-to-Agent) 协议定义了 Agent 之间通信的标准格式。AgentScope 实现了 Agent Card 机制，用于服务发现。

### 7.2 AgentCardResolverBase

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/a2a/_base.py`

```python
class AgentCardResolverBase:
    """Base class for A2A agent card resolvers, responsible for fetching
    agent cards from various sources. Implementations must provide the
    `get_agent_card` method to retrieve the agent card.
    """

    @abstractmethod
    async def get_agent_card(self, *args: Any, **kwargs: Any) -> AgentCard:
        """Get Agent Card from the configured source.

        Returns:
            The resolved agent card object.
        """
```

### 7.3 Agent Card 解析器实现

| 实现 | 文件 | 说明 |
|------|------|------|
| FileAgentCardResolver | `_file_resolver.py` | 从本地文件加载 |
| WellKnownAgentCardResolver | `_well_known_resolver.py` | 从 .well-known 目录加载 |
| NacosAgentCardResolver | `_nacos_resolver.py` | 从 Nacos 注册中心发现 |

### 7.4 A2A 消息格式

A2A 消息使用 `a2a.types` 中的标准类型，包括:
- `AgentCard`: 代理能力描述卡片
- `A2AMessage`: 代理间通信消息
- `Task`: 任务对象
- `Message`: 消息对象

---

## 8. 其他基础设施模块

### 8.1 Module 状态管理

**文件**: `src/agentscope/module/_state_module.py`

> **注意**: 以下为 StateModule 的概念性概述。完整的源码解读请参考 [StateModule 深度分析](module_state_deep.md)。

StateModule 是具有状态管理能力的模块基类，几乎所有核心组件（Agent、Memory、Toolkit、PlanNotebook）都继承自它：

```python
class StateModule:
    """支持嵌套状态序列化和反序列化的基础模块。"""

    def __init__(self) -> None:
        self._module_dict = OrderedDict()    # 追踪嵌套的 StateModule 子实例
        self._attribute_dict = OrderedDict() # 追踪注册的普通属性及其序列化函数

    def register_state(self, attr_name, custom_to_json=None, custom_from_json=None):
        """注册需要自定义序列化的属性"""
        ...

    def state_dict(self) -> dict:
        """递归收集所有嵌套模块和注册属性的状态"""
        ...

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """递归恢复状态，支持 strict/宽松模式"""
        ...
```

**关键机制**：
- `__setattr__` 自动追踪子 StateModule 实例到 `_module_dict`
- `register_state()` 注册需要序列化的普通属性到 `_attribute_dict`
- `state_dict()` 递归收集嵌套模块状态，形成树状结构
- `load_state_dict()` 递归恢复，支持 strict（缺键报错）和宽松（缺键跳过）两种模式

---

## 9. 代码示例

### 9.1 创建顺序 Pipeline

```python showLineNumbers
from agentscope.pipeline import SequentialPipeline
from agentscope import AgentBase, Msg

# 创建代理
agent1 = MyAgent(name="agent1")
agent2 = MyAgent(name="agent2")
agent3 = MyAgent(name="agent3")

# 创建 Pipeline
pipeline = SequentialPipeline(agents=[agent1, agent2, agent3])

# 运行
initial = Msg(name="user", content="Start", role="user")
result = await pipeline(initial)
print(f"Final result: {result.content}")
```

**预期输出**：
```
[Pipeline] agent1: Start -> processing
[Pipeline] agent2: agent1 result -> processing
[Pipeline] agent3: agent2 result -> processing
Final result: agent3 output
```

### 9.2 创建并行 Pipeline

```python showLineNumbers
from agentscope.pipeline import FanoutPipeline
import asyncio

# 创建分支代理
agents = [
    SearchAgent(name="searcher1"),
    SearchAgent(name="searcher2"),
    SearchAgent(name="searcher3"),
]

# 创建 Pipeline
pipeline = FanoutPipeline(agents=agents)

# 运行（并发执行）
initial = Msg(name="user", content="Search for AI news", role="user")
results = await pipeline(initial)  # 返回 list[Msg]
```

**预期输出**：
```
[Pipeline] Fanout: Dispatching to 3 agents in parallel
[Pipeline] searcher1: Searching...
[Pipeline] searcher2: Searching...
[Pipeline] searcher3: Searching...
[Pipeline] Fanout: Gathered 3 results
```

### 9.3 使用 MsgHub 进行多代理协作

```python showLineNumbers
from agentscope.pipeline import MsgHub
from agentscope import AgentBase, Msg

# 使用 MsgHub 自动管理多代理消息传递
async with MsgHub(
    participants=[agent1, agent2, agent3],
    announcement=Msg("system", "开始协作", "system")
):
    x1 = agent1(Msg("user", "你好", "user"))
    # agent1 的回复会自动广播给 agent2 和 agent3

    x2 = agent2(x1)
    # agent2 的回复会自动广播给 agent1 和 agent3
```

**预期输出**：
```
[MsgHub] Broadcasting announcement: 开始协作
[agent2] Observed: Msg(name='system', content='开始协作')
[agent3] Observed: Msg(name='system', content='开始协作')
[agent1] -> Reply: 你好
[agent2] Observed: Msg(name='agent1', content='你好')
[agent3] Observed: Msg(name='agent1', content='你好')
```

### 9.4 使用 ChatRoom 进行实时广播

```python showLineNumbers
from agentscope.pipeline import ChatRoom
from agentscope.agent import RealtimeAgent

# 创建聊天室
room = ChatRoom(agents=[agent1, agent2, agent3])
await room.start(outgoing_queue)

# 处理来自前端的消息
await room.handle_input(event)

# 停止聊天室
await room.stop()
```

### 9.5 使用 Formatter

```python showLineNumbers
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg

# OpenAI Formatter（继承自 TruncatedFormatterBase，非直接继承 FormatterBase）
formatter = OpenAIChatFormatter()
messages = [
    Msg(name="system", content="You are helpful.", role="system"),
    Msg(name="user", content="Hello!", role="user"),
]

formatted = await formatter.format(messages)
print(formatted)
```

**预期输出**：
```
[{'role': 'system', 'content': 'You are helpful.'},
 {'role': 'user', 'content': 'Hello!'}]
```

### 9.6 会话管理

```python showLineNumbers
from agentscope.session import JSONSession

# 创建会话（save_dir 参数指定存储目录）
session = JSONSession(save_dir="./sessions/")

# 保存会话状态
await session.save_session_state(
    session_id="session123",
    user_id="user456",
    agent=agent,  # 传入 StateModule 实例
)

# 加载会话状态
await session.load_session_state(
    session_id="session123",
    user_id="user456",
    agent=agent,
)
```

**预期输出**：
```
[Session] Saved state for session123
[Session] Loaded state for session123
```

### 9.7 启用追踪

```python showLineNumbers
import agentscope

# 启用追踪（通过 init 函数配置 tracing endpoint）
agentscope.init(
    project="my-project",
    tracing_url="http://localhost:4317",
)
```

---

## 本章关联

### 与其他模块的关系

| 关联模块 | 关联内容 | 参考位置 |
|----------|----------|----------|
| [Agent 模块深度剖析](module_agent_deep.md) | MsgHub 如何基于 AgentBase 的 `_broadcast_to_subscribers` 实现消息广播，Pipeline 如何编排 Agent 的执行顺序 | 第 3.5 节订阅发布机制、第 2.1 节 MsgHub |
| [Tool/MCP 模块深度剖析](module_tool_mcp_deep.md) | A2A 协议与 MCP 协议的对比，智能体间通信的两种范式 | 第 7 章 A2A 协议、第 5 章 MCP 协议 |
| [Model 模块深度剖析](module_model_deep.md) | Formatter 如何针对不同模型 API 格式化消息，Realtime 模块的流式输出与模型流式响应的关系 | 第 3 章 Formatter、第 4 章 Realtime |
| [Memory/RAG 模块深度剖析](module_memory_rag_deep.md) | Session 如何持久化记忆状态，Tracing 如何追踪 RAG 检索和记忆操作的链路 | 第 5 章 Session、第 6 章 Tracing |
| [最佳实践参考](reference_best_practices.md) | 多智能体协作模式、生产部署架构（Docker/Kubernetes）、性能优化策略 | 多智能体协作、生产部署章节 |

### 前置知识

- **异步上下文管理器**: 需要理解 `async with` 和 `__aenter__`/`__aexit__` 的工作原理
- **发布-订阅模式**: 需要理解消息广播的基本概念
- **WebSocket**: 如学习 Realtime 章节，需要了解 WebSocket 通信基础

### 后续学习建议

1. 完成本模块练习题后，建议继续学习 [Agent 模块](module_agent_deep.md)，深入理解智能体的生命周期和 Hook 机制
2. 如需构建分布式多智能体系统，建议结合 [Tool/MCP 模块](module_tool_mcp_deep.md) 的 MCP 协议，设计跨进程的智能体通信方案
3. 如需部署生产环境，建议参考 [最佳实践](reference_best_practices.md) 中的 Docker/Kubernetes 部署指南

---

## 10. 练习题

### 10.1 基础题

1. **分析 MsgHub 的消息广播机制，参考 `_msghub.py` 第130-138行 `broadcast` 方法。**

2. **比较 SequentialPipeline 和 FanoutPipeline 的执行模式差异。**

3. **解释 `fanout_pipeline` 中 `deepcopy(msg)` 的作用（参考第98行）。**

4. **分析 `stream_printing_messages` 如何捕获代理的中间打印消息（参考第159-192行）。**

5. **解释 ChatRoom 中 ClientEvents 和 ServerEvents 的处理差异（参考第60-77行）。**

### 10.2 进阶题

6. **设计一个新的 Pipeline 类型，实现条件分支。**

7. **分析 Formatter 如何处理不同模型的消息格式差异。**

8. **设计一个支持断点恢复的 Pipeline，参考 MsgHub 的状态管理方式。**

9. **在 MsgHub 中添加消息过滤功能，支持基于条件的消息路由。**

10. **分析 RealtimeModelBase 的 WebSocket 通信机制。**

### 10.3 挑战题

11. **实现一个分布式 Pipeline，支持跨进程的代理协作（参考 ChatRoom 的消息转发机制）。**

12. **实现一个 Agent Card 解析器，从 Kubernetes API Server 获取代理信息。**

13. **为 FanoutPipeline 添加超时控制和错误处理机制。**

14. **分析 OpenTelemetry 追踪数据的收集和导出流程。**

### 练习 10.15: Sequential 与 Fanout 执行顺序验证 [基础]

**题目描述**：
给定以下代码，分别预测 `sequential_pipeline` 和 `fanout_pipeline` 的消息传递顺序，并指出两者的根本差异。

```python
import asyncio
from agentscope.pipeline import sequential_pipeline, fanout_pipeline
from agentscope import ReActAgent, Msg

# 假设 a1, a2, a3 是已初始化的 ReActAgent
async def test_sequential():
    result = await sequential_pipeline([a1, a2, a3], Msg("user", "hello", "user"))
    return result  # 返回什么？

async def test_fanout():
    results = await fanout_pipeline([a1, a2, a3], Msg("user", "hello", "user"))
    return results  # 返回什么？
```

**预期输出/行为**：
- `sequential_pipeline` 返回值类型是 `Msg`，`fanout_pipeline` 返回值类型是 `list[Msg]`
- 顺序执行时，a1 的输出是 a2 的输入；并发执行时，a1/a2/a3 各自收到原始消息的深拷贝

<details>
<summary>参考答案</summary>

```python
# sequential_pipeline: 链式传递，每个 agent 的输出是下一个的输入
# 返回值: 最后一个 agent 的输出 (Msg)
#
# fanout_pipeline: 并发分发，使用 deepcopy 保证消息隔离
# 返回值: 所有 agent 输出的列表 (list[Msg])
#
# 验证方法:
print(type(await test_sequential()))  # <class 'agentscope.message.Msg'>
print(type(await test_fanout()))       # <class 'list'>

# 执行顺序验证（加日志）:
async def sequential_with_log(agents, msg):
    for i, agent in enumerate(agents):
        print(f"sequential: agent {i} executing...")
        msg = await agent(msg)
    return msg

async def fanout_with_log(agents, msg):
    async def run(agent, i):
        print(f"fanout: agent {i} executing...")
        return await agent(msg)
    tasks = [run(a, i) for i, a in enumerate(agents)]
    return await asyncio.gather(*tasks)
```
</details>

### 练习 10.16: Pipeline 消息转换函数 [基础]

**题目描述**：
实现一个 `transform_pipeline`，在每个 agent 执行后对其输出消息进行转换（如添加前缀 `[{agent.name}]`）。

```python
from typing import Callable
from agentscope import AgentBase, Msg

async def transform_pipeline(
    agents: list[AgentBase],
    msg: Msg,
    transform: Callable[[Msg, AgentBase], Msg],
) -> Msg:
    # 实现：遍历 agents，对每个输出应用 transform 函数
    # 返回最终结果
    ...
```

**预期输出/行为**：
给定 2 个 agent，transform 函数添加 agent 名称前缀，输出消息的 `content` 应包含 `[agent1_name]` 和 `[agent2_name]` 前缀。

<details>
<summary>参考答案</summary>

```python
from typing import Callable
from agentscope import AgentBase, Msg

async def transform_pipeline(
    agents: list[AgentBase],
    msg: Msg,
    transform: Callable[[Msg, AgentBase], Msg],
) -> Msg:
    for agent in agents:
        output = await agent(msg)
        if output is not None:
            output = transform(output, agent)
        msg = output  # 转换后的消息传递给下一个 agent
    return msg

# 使用示例
def add_prefix(msg: Msg, agent: AgentBase) -> Msg:
    return Msg(
        role=msg.role,
        content=f"[{agent.name}] {msg.content}",
        name=msg.name,
    )

result = await transform_pipeline([agent1, agent2], initial_msg, add_prefix)
# result.content 以 "[agent1_name] ..." 开头
```
</details>

### 练习 10.17: MsgHub 广播路径追踪 [中级]

**题目描述**：
分析 MsgHub 中 `broadcast` 方法与 AgentBase 的 `_broadcast_to_subscribers` 方法的区别，指出以下场景中消息实际走了哪条路径。

```python
async with MsgHub(participants=[a1, a2], announce=True) as hub:
    result = await a1(Msg("user", "hello", "user"))
    # result 会被广播给 a2 吗？
```

**预期输出/行为**：
当 `announce=True` 时，AgentBase 的 `_broadcast_to_subscribers` 会触发自动广播，消息经过 `MsgHub._announced_host` 广播。`broadcast()` 只影响手动调用 `hub.broadcast()` 的场景。

<details>
<summary>参考答案</summary>

```python
# broadcast() vs _broadcast_to_subscribers() 的区别：
# - broadcast() 是 MsgHub 的公开方法，供外部手动广播
# - _broadcast_to_subscribers() 是 AgentBase 的内部方法，
#   在 agent.reply() 完成后自动触发
#
# announce=True 时：
# - agent.reply() 完成后 → _broadcast_to_subscribers()
#   → MsgHub._announced_host 捕获 → 广播给所有 participants
#
# 所以 result 会自动广播给 a2，无需手动调用 hub.broadcast()
#
# 验证方法：给 a2 的 observe() 方法加日志
def logging_observe(self, msg):
    print(f"{self.name} received: {msg.content}")
    return super().observe(msg)

a2.observe = lambda msg: logging_observe(a2, msg)
```
</details>

### 练习 10.18: 带断点恢复的 Pipeline [挑战]

**题目描述**：
设计一个 `ResumablePipeline`，支持在 Pipeline 执行中断后从上一个成功 agent 的位置恢复，而无需重新执行已完成的部分。

**预期输出/行为**：
模拟 agent2 执行中途失败的场景，重试时只有 agent2 和 agent3 重新执行，agent1 的结果从缓存读取。

<details>
<summary>参考答案</summary>

```python
import asyncio
from typing import Any
from agentscope import AgentBase, Msg

class ResumablePipeline:
    """支持断点恢复的 Pipeline。"""

    def __init__(self, agents: list[AgentBase]):
        self.agents = agents
        self._cache: dict[int, Msg] = {}  # agent index → output cache

    async def __call__(
        self,
        msg: Msg,
        start_from: int = 0,
    ) -> Msg:
        current_msg = msg
        for i, agent in enumerate(self.agents):
            if i < start_from:
                # 跳过已执行的部分，从缓存恢复
                current_msg = self._cache[i]
                continue

            try:
                current_msg = await agent(current_msg)
                self._cache[i] = current_msg  # 缓存成功的结果
            except Exception as e:
                print(f"Agent {i} ({agent.name}) failed: {e}")
                # 从缓存的最后一个成功位置恢复
                raise e

        return current_msg

# 使用示例
async def main():
    pipeline = ResumablePipeline([a1, a2, a3])
    msg = Msg("user", "hello", "user")

    try:
        result = await pipeline(msg)
    except Exception:
        # 从 agent2 (index=1) 恢复
        result = await pipeline(msg, start_from=1)
```
</details>

---

## 参考答案

### 10.1 基础题

**第1题：MsgHub 广播机制**

MsgHub 的 `broadcast` 方法遍历所有订阅者，调用每个代理的 `observe()` 方法传递消息，同时排除消息发送者自身（`exclude` 参数）。核心流程：发送者 reply → broadcast → 订阅者 observe。

**第2题：Sequential vs Fanout 差异**

| 维度 | SequentialPipeline | FanoutPipeline |
|------|-------------------|----------------|
| 执行方式 | 链式（前一个输出作为后一个输入） | 并发（同一输入分发给所有代理） |
| 并发度 | 1（串行） | N（代理数量） |
| 返回值 | 最后一个代理的输出 | 所有代理输出的列表 |
| 适用场景 | 流水线处理 | 投票/多角度分析 |

**第3题：deepcopy 作用**

`deepcopy(msg)` 确保每个代理收到独立的消息副本，避免代理 A 修改消息后影响代理 B 的输入，保证并发安全。

**第4题：stream_printing_messages**

通过为每个代理调用 `agent.set_msg_queue_enabled(True, queue)` 启用消息队列机制，代理在执行期间通过 `await self.print()` 输出的中间消息会进入 `asyncio.Queue`，`stream_printing_messages` 从队列中循环读取并 yield 给调用者，实现流式消息收集。

**第5题：ChatRoom 事件差异**

ClientEvents 由客户端发送（如 `send_message`），ServerEvents 由服务器推送（如 `receive_message`）。客户端发起操作，服务器被动通知，形成请求-响应模式。

### 10.2 进阶题

**第6题：条件分支 Pipeline**

```python
class ConditionalPipeline:
    def __init__(self, condition_fn, true_agent, false_agent):
        self.condition_fn = condition_fn
        self.true_agent = true_agent
        self.false_agent = false_agent

    async def __call__(self, msg):
        target = self.true_agent if self.condition_fn(msg) else self.false_agent
        return await target(msg)
```

**第7题：Formatter 格式差异**

Formatter 根据模型类型（OpenAI/DashScope/Anthropic）将统一的 Msg 格式转换为各 API 所需的消息格式，主要差异在于：content 字段结构、tool_call 格式、system message 位置等。

具体差异示例：
```python
# OpenAI Chat API - tool_calls 格式
{"role": "assistant", "tool_calls": [{"id": "xxx", "type": "function", "function": {"name": "func", "arguments": "{}"}}]}

# Anthropic API - tool_use 格式
{"role": "assistant", "tool_use": {"id": "xxx", "name": "func", "input": {}}}

# OpenAI Function Calling - function_call 格式（旧版）
{"role": "assistant", "function_call": {"name": "func", "arguments": "{}"}}
```

**第9题：消息过滤 MsgHub**

```python
class FilteredMsgHub(MsgHub):
    def __init__(self, *agents, msg_filter=None):
        super().__init__(*agents)
        self.msg_filter = msg_filter

    async def _broadcast_to_subscribers(self, msg, exclude):
        if self.msg_filter and not self.msg_filter(msg):
            return
        await super()._broadcast_to_subscribers(msg, exclude)
```

### 10.3 挑战题

**第13题：带超时的 FanoutPipeline**

```python
import asyncio

class TimedFanoutPipeline(FanoutPipeline):
    def __init__(self, agents, timeout=30.0):
        super().__init__(agents)
        self.timeout = timeout

    async def __call__(self, msg):
        tasks = [asyncio.wait_for(agent(msg), timeout=self.timeout)
                 for agent in self.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r if not isinstance(r, Exception) else Msg("system", str(r), "error")
                for r in results]
```

---

## 小结

| 组件 | 职责 | 关键特性 |
|------|------|----------|
| SequentialPipeline | 链式执行 | 前一个输出作为后一个输入 |
| FanoutPipeline | 并发分发 | asyncio.gather 并行执行 |
| MsgHub | 消息路由 | 发布-订阅 + 上下文管理 |
| Formatter | 消息格式化 | 适配多模型 API |
| Session | 状态持久化 | save/load 状态 |
| Tracing | 可观测性 | OpenTelemetry 集成 |
| A2A | 跨进程通信 | AgentCard + HTTP |
| Realtime | 实时交互 | WebSocket 双向通信 |

## 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Chain of Responsibility** | SequentialPipeline | 消息沿链式传递，每个 Agent 处理后传给下一个 |
| **Fork-Join** | FanoutPipeline | 并发分发 → 收集合并结果 |
| **Pub-Sub（发布订阅）** | MsgHub | Agent 订阅消息，广播时通知所有订阅者 |
| **Context Manager** | MsgHub, Session | `async with` 管理生命周期 |
| **Adapter** | Formatter | 统一 Msg 格式适配不同 API |
| **Observer** | MsgHub broadcast | 消息变更时自动通知观察者（Agent） |
| **State（状态）** | StateModule | 状态序列化/反序列化支持持久化 |

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [智能体模块](module_agent_deep.md#3-agentbase-源码解读) | MsgHub 如何基于 AgentBase 的 `_broadcast_to_subscribers` 实现消息广播，Pipeline 如何编排 Agent 的执行顺序 | 第 3.5 节 |
| [工具模块](module_tool_mcp_deep.md#7-mcp-协议与-java-jdbcodbc-驱动对比) | A2A 协议与 MCP 协议的对比，智能体间通信的两种范式 | 第 7.1-7.4 节 |
| [模型模块](module_model_deep.md#3-格式化器与-消息转换) | Formatter 如何针对不同模型 API 格式化消息，Realtime 模块的流式输出与模型流式响应的关系 | 第 3.1-3.4 节 |
| [记忆模块](module_memory_rag_deep.md#5-rag-模块架构) | Session 如何持久化记忆状态，Tracing 如何追踪 RAG 检索和记忆操作的链路 | 第 5.4 节、第 6.3 节 |
| [调度器模块](module_dispatcher_deep.md#4-源码解读) | MsgHub 消息路由机制详解 | 第 4.1-4.6 节 |
| [追踪模块](module_tracing_deep.md#6-练习题) | 练习题参考答案涉及 Pipeline 追踪 | 第 6 节 |


---

## 参考资料

- Pipeline 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/`
- Formatter 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/formatter/`
- Realtime 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/realtime/`
- Session 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/session/`
- Tracing 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tracing/`
- A2A 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/a2a/`

---

*文档版本: 2.5*
*最后更新: 2026-04-28*
*更新内容:*
- *修正 Tracing 系统描述，基于 OpenTelemetry 实现（而非简单自定义实现）*
- *修正 Session 模块，SessionBase 使用 save_session_state/load_session_state 接口*
- *修正 A2A 模块，AgentCard 位于 a2a.types 包中*
- *修正 Realtime 模块，基于 RealtimeModelBase 而非 RealtimeConnection*
- *修正 Formatter 模块，移除不存在的 parse 方法*
- *将 Pipeline 代码示例标注为"概念示例"，在代码块内添加 PSEUDOCODE 标注*
- *更新所有源码行号引用以匹配实际文件*
- *添加更多实现细节和关键设计模式分析*
- *修正练习题第5题行号（ChatRoom 59-77→60-77）*
- *统一术语：将"智能体"改为"Agent"，保持术语一致性*

## 本章小结

- MsgHub 是消息中心，通过发布-订阅机制驱动多 Agent 间的消息广播和协作
- Pipeline 提供函数式接口（`_functional.py`）和类封装接口（`_class.py`）两种编排方式
- Formatter 模块针对不同模型 API 的消息格式要求进行适配转换
- Realtime 模块基于 RealtimeModelBase 实现实时交互，Session 模块管理会话状态持久化
- Tracing 系统基于 OpenTelemetry 实现全链路追踪，A2A 协议支持跨框架 Agent 通信

## 下一章

→ [Config 配置模块](module_config_deep.md)
