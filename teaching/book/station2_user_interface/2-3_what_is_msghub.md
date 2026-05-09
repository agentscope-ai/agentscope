# 2-3 MsgHub是什么

> **目标**：理解MsgHub的发布-订阅模式和消息广播机制

---

## 🎯 这一章的目标

学完之后，你能：
- 理解发布-订阅模式
- 使用MsgHub进行消息广播
- 区分MsgHub和Pipeline的使用场景

---

## 🚀 先跑起来

```python showLineNumbers
from agentscope.message import Msg
from agentscope.pipeline import MsgHub
from agentscope.agent import ReActAgent

# 创建消息中心
hub = MsgHub()

# Agent订阅消息
analyst = ReActAgent(name="Analyst", model=..., sys_prompt="...")
reporter = ReActAgent(name="Reporter", model=..., sys_prompt="...")
critic = ReActAgent(name="Critic", model=..., sys_prompt="...")

# Agent订阅
hub.subscribe(analyst)
hub.subscribe(reporter)
hub.subscribe(critic)

# 发布消息 - 所有订阅者都会收到
hub.publish(Msg(
    name="publisher",
    content="开始分析这个项目",
    role="system"
))

# 或者用with语法
with MsgHub([analyst, reporter, critic]) as hub:
    hub.publish(Msg(name="user", content="启动分析", role="user"))
```

---

## 🔍 发布-订阅模式

### 核心概念

```
┌─────────────────────────────────────────────────────────────┐
│                      MsgHub（消息中心）                       │
│                                                             │
│   ┌───────┐                                                 │
│   │发布者 │ ──► 消息 ──► ┌─────────────────────────┐      │
│   └───────┘              │        广播              │      │
│                         └─────────┬─────────────────┘      │
│                                   │                         │
│                    ┌──────────────┼──────────────┐         │
│                    ▼              ▼              ▼         │
│               ┌────────┐     ┌────────┐     ┌────────┐     │
│               │订阅者A│     │订阅者B│     │订阅者C│     │
│               └────────┘     └────────┘     └────────┘     │
│                                                             │
│   发布者只管发，订阅者会自动收到                               │
└─────────────────────────────────────────────────────────────┘
```

### 对比：Pipeline vs MsgHub

| 特性 | Pipeline | MsgHub |
|------|----------|--------|
| 连接方式 | 硬编码顺序 | 动态订阅 |
| 消息传递 | 上一节点的输出是下一节点的输入 | 所有订阅者收到相同消息 |
| 关系 | "流水线" | "广播电台" |
| 依赖 | 有顺序依赖 | 无依赖 |
| 适合场景 | 固定流程 | 事件通知、多方协作 |

---

## 🔍 追踪MsgHub的消息流

```mermaid
sequenceDiagram
    participant P as Publisher
    participant Hub as MsgHub
    participant A as Agent A
    participant B as Agent B
    participant C as Agent C
    
    Note over A,B,C: 订阅消息
    A->>Hub: subscribe(A)
    B->>Hub: subscribe(B)
    C->>Hub: subscribe(C)
    
    P->>Hub: publish(Msg)
    
    Note over Hub: 广播消息
    Hub->>A: 推送Msg
    Hub->>B: 推送Msg
    Hub->>C: 推送Msg
```

---

## 🔬 关键代码段解析

### 代码段1：为什么需要MsgHub？

```python showLineNumbers
# 这是第24-46行
hub = MsgHub()

# Agent订阅消息
analyst = ReActAgent(name="Analyst", ...)
reporter = ReActAgent(name="Reporter", ...)
critic = ReActAgent(name="Critic", ...)

hub.subscribe(analyst)
hub.subscribe(reporter)
hub.subscribe(critic)

# 发布消息 - 所有订阅者都会收到
hub.publish(Msg(name="publisher", content="开始分析", role="system"))
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么需要发布-订阅？ | 发布者和订阅者解耦，不需要知道彼此 |
| `subscribe`做了什么？ | 把Agent注册到订阅者列表 |
| `publish`后发生什么？ | 所有订阅者同时收到消息 |

```
┌─────────────────────────────────────────────────────────────┐
│            发布-订阅 vs 直接调用                           │
│                                                             │
│   直接调用（紧耦合）：                                     │
│   analyst.receive(msg)                                     │
│   reporter.receive(msg)    ← 需要知道所有接收者           │
│   critic.receive(msg)                                      │
│                                                             │
│   发布-订阅（松耦合）：                                   │
│   hub.publish(msg)  ──► 广播给所有订阅者                  │
│                    ↑                                       │
│                    │                                       │
│            发布者不需要知道谁在听                         │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：发布-订阅模式的核心是**松耦合**。发布者只管发消息，不关心谁会收到；订阅者只管收消息，不关心谁发的。

---

### 代码段2：MsgHub的with语法

```python showLineNumbers
# with语法，自动管理订阅生命周期
with MsgHub([analyst, reporter, critic]) as hub:
    hub.publish(Msg(name="user", content="启动分析", role="user"))
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| `with`语法有什么好处？ | 自动订阅和取消订阅 |
| 什么时候用with？ | 临时性的广播任务 |
| 什么时候用subscribe？ | 长期订阅，需要手动管理 |

```
┌─────────────────────────────────────────────────────────────┐
│                 with语法的生命周期                          │
│                                                             │
│   with MsgHub([A, B, C]) as hub:                          │
│       │                                                    │
│       ├──► 进入with：自动订阅 A, B, C                     │
│       │                                                    │
│       ├──► 执行 hub.publish()                             │
│       │                                                    │
│       └──► 退出with：自动取消订阅 A, B, C                │
│                                                             │
│   适合：临时任务、一次性广播                               │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：`with`语法简化了资源管理，确保订阅和取消订阅成对出现，避免资源泄漏。

---

### 代码段3：MsgHub vs Pipeline 选型

```python showLineNumbers
# 场景1：固定流程，用Pipeline
pipeline = SequentialPipeline([A, B, C])
result = await pipeline(input)
# A的结果自动传给B，B的结果自动传给C

# 场景2：事件通知，用MsgHub
hub = MsgHub([A, B, C])
hub.publish(Msg(content="任务完成"))
# 所有订阅者同时收到通知
```

**思路说明**：

| 场景 | 选择 | 原因 |
|------|------|------|
| 翻译→校对→格式化 | SequentialPipeline | 有顺序依赖 |
| 任务完成通知多人 | MsgHub | 广播通知 |
| 头脑风暴（多专家意见） | FanoutPipeline | 并行收集 |
| 监控系统报警 | MsgHub | 事件驱动 |

```
┌─────────────────────────────────────────────────────────────┐
│                 组件选择决策树                              │
│                                                             │
│   有顺序依赖吗？                                          │
│        │                                                  │
│        ├──► Yes ──► 固定顺序？                         │
│        │                      │                            │
│        │                      ├──► Yes ──► SequentialPipeline│
│        │                      │                            │
│        │                      └──► No ──► FanoutPipeline   │
│        │                                                  │
│        └──► No ──► 需要广播？                         │
│                             │                            │
│                             ├──► Yes ──► MsgHub          │
│                             │                            │
│                             └──► No ──► 直接调用        │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：不同场景用不同组件。Pipeline适合有依赖的顺序任务，MsgHub适合无依赖的广播通知。

---

## 💡 Java开发者注意

MsgHub类似Java的**EventBus**或者**Message Broker**：

```java
// Google Guava EventBus
eventBus.register(subscriber);
eventBus.post(new MessageEvent("hello"));

// Java Message Broker (Kafka/RabbitMQ)
producer.send("topic", message);
consumer.subscribe("topic");
```

### Publish-Subscribe vs Message Queue

| | Publish-Subscribe | Message Queue |
|--|-----------------|--------------|
| 消费者 | 动态订阅 | 预先定义 |
| 消息处理 | 广播 | 轮询/竞争消费 |
| 适合 | 事件通知 | 异步任务 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **MsgHub和Pipeline的核心区别是什么？**
   - Pipeline：硬编码顺序，上一节点的输出是下一节点的输入
   - MsgHub：动态订阅，所有订阅者收到相同消息

2. **什么场景下用MsgHub比Pipeline更好？**
   - 需要通知多个Agent但不知道具体是哪些
   - 需要动态添加/移除订阅者
   - 类似"广播"或"事件通知"场景

3. **MsgHub的消息会保存吗？**
   - 默认不保存，只广播给当前订阅者
   - 需要持久化可以用session或额外存储

</details>

---

★ **Insight** ─────────────────────────────────────
- **MsgHub = 广播电台**，发布者发消息，所有订阅者都能收到
- **Pipeline = 流水线**，按顺序处理，上一步输出是下一步输入
- 选择哪个取决于：是需要广播通知，还是顺序处理
─────────────────────────────────────────────────
