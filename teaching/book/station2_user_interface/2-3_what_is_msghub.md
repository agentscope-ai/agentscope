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
from agentscope import Msg
from agentscope.pipeline import MsgHub
from agentscope import ReActAgent

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
