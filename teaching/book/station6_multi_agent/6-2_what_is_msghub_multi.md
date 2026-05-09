# 6-2 MsgHub是什么（多Agent消息中枢）

> **目标**：理解MsgHub的发布-订阅模式在多Agent协作中的作用

---

## 🎯 这一章的目标

学完之后，你能：
- 理解MsgHub的发布-订阅模式
- 使用MsgHub实现Agent之间的松耦合通信
- 设计基于事件的多Agent系统

---

## 🚀 MsgHub多Agent协作

```python showLineNumbers
from agentscope.message import Msg
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub

# 创建消息中枢
hub = MsgHub()

# Agent订阅消息
analyst = ReActAgent(name="Analyst", model=..., sys_prompt="...")
notifier = ReActAgent(name="Notifier", model=..., sys_prompt="...")
logger = ReActAgent(name="Logger", model=..., sys_prompt="...")

hub.subscribe(analyst)
hub.subscribe(notifier)
hub.subscribe(logger)

# 发布消息 - 所有订阅者都会收到
await hub.publish(Msg(
    name="system",
    content="任务完成",
    role="system"
))
```

---

## 🔍 发布-订阅模式

```
┌─────────────────────────────────────────────────────────────┐
│                      MsgHub                                │
│                                                             │
│   ┌─────────┐                                              │
│   │发布者   │ ──► Msg ──► ┌─────────────────────────┐   │
│   └─────────┘              │        广播              │   │
│                            └─────────┬───────────────┘   │
│                                      │                   │
│                    ┌───────────────┼───────────────┐     │
│                    ▼               ▼               ▼     │
│               ┌────────┐     ┌────────┐     ┌────────┐  │
│               │订阅者A │     │订阅者B │     │订阅者C │  │
│               └────────┘     └────────┘     └────────┘  │
│                                                             │
│   发布者不关心谁会收到，订阅者自动收到                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔬 关键代码段解析

### 代码段1：MsgHub的订阅机制是怎么工作的？

```python showLineNumbers
# 创建MsgHub并订阅Agent
hub = MsgHub()

analyst = ReActAgent(name="Analyst", model=..., sys_prompt="...")
notifier = ReActAgent(name="Notifier", model=..., sys_prompt="...")
logger = ReActAgent(name="Logger", model=..., sys_prompt="...")

# 订阅Agent
hub.subscribe(analyst)
hub.subscribe(notifier)
hub.subscribe(logger)
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| subscribe做了什么？ | 把Agent加入内部列表 |
| 订阅后会发生什么？ | Agent会收到所有发布的消息 |
| 可以动态添加吗？ | 可以，随时调用subscribe |

```
┌─────────────────────────────────────────────────────────────┐
│              MsgHub订阅机制                              │
│                                                             │
│   hub = MsgHub()                                          │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.subscribed_agents = []                       │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   hub.subscribe(analyst)                                   │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.subscribed_agents = [analyst]                  │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   hub.subscribe(notifier)                                  │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.subscribed_agents = [analyst, notifier]        │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   hub.subscribe(logger)                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.subscribed_agents = [analyst, notifier, logger]│  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：订阅就是**注册观察者**。发布者只管发，订阅者自动收到。

---

### 代码段2：MsgHub发布消息的完整流程

```python showLineNumbers
# 发布消息
msg = Msg(name="system", content="任务完成", role="system")
await hub.publish(msg)

# 发布后的内部流程
# 1. 遍历所有订阅者
# 2. 给每个订阅者发送消息
# 3. 订阅者处理消息
```

**思路说明**：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 获取订阅者列表 | `self.subscribed_agents` |
| 2 | 遍历列表 | 对每个订阅者执行 |
| 3 | 调用订阅者的receive方法 | `agent.receive(msg)` |

```
┌─────────────────────────────────────────────────────────────┐
│              消息发布流程                                │
│                                                             │
│   hub.publish(msg)                                       │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  for agent in self.subscribed_agents:              │  │
│   │      agent.receive(msg)                            │  │
│   └─────────────────────────────────────────────────────┘  │
│        │                                                 │
│        ├──► analyst.receive(msg) ──► Analyst处理      │
│        ├──► notifier.receive(msg) ──► Notifier处理    │
│        └──► logger.receive(msg) ──► Logger处理        │
│                                                             │
│   所有订阅者同时收到消息，互不干扰                     │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：发布-订阅的核心是**广播**——一条消息同时发送给所有订阅者。

---

### 代码段3：MsgHub的with语法自动管理订阅

```python showLineNumbers
# 使用with语法，自动管理订阅生命周期
with MsgHub([analyst, notifier, logger]) as hub:
    hub.publish(Msg(name="system", content="启动任务"))
    # 退出with时自动取消订阅

# 等价于
hub = MsgHub()
hub.subscribe(analyst)
hub.subscribe(notifier)
hub.subscribe(logger)
try:
    hub.publish(Msg(name="system", content="启动任务"))
finally:
    hub.unsubscribe(analyst)
    hub.unsubscribe(notifier)
    hub.unsubscribe(logger)
```

**思路说明**：

| 方式 | 优点 | 适用场景 |
|------|------|----------|
| with语法 | 自动管理订阅/取消订阅 | 临时性任务 |
| 手动subscribe | 灵活控制 | 长期订阅 |

```
┌─────────────────────────────────────────────────────────────┐
│              with语法的生命周期管理                        │
│                                                             │
│   with MsgHub([A, B, C]) as hub:                         │
│       │                                                   │
│       ├──► __enter__: 自动订阅 A, B, C                   │
│       │                                                   │
│       ├──► hub.publish(msg) ──► 广播消息                 │
│       │                                                   │
│       └──► __exit__: 自动取消订阅 A, B, C                │
│                                                             │
│   优点：不用担心忘记unsubscribe                         │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：with语法是**资源管理**的Python习惯用法，确保订阅和取消订阅成对出现。

---

### 代码段4：MsgHub和Pipeline的选择

```python showLineNumbers
# 场景1：需要顺序处理，用Pipeline
# A的结果传给B，B的结果传给C
pipeline = SequentialPipeline([analyzer, summarizer, formatter])
result = await pipeline(input)

# 场景2：需要广播通知，用MsgHub
# 一个消息同时通知所有订阅者
hub = MsgHub([email_notifier, sms_notifier, logger])
await hub.publish(Msg(content="任务完成"))
```

**思路说明**：

| 场景 | 选择 | 原因 |
|------|------|------|
| A→B→C顺序处理 | SequentialPipeline | 数据依次传递 |
| 同时通知多方 | MsgHub | 广播模式 |
| 并行处理再汇总 | FanoutPipeline | 一对多分发 |

```
┌─────────────────────────────────────────────────────────────┐
│              组件选择指南                                │
│                                                             │
│   需要顺序依赖？                                          │
│        │                                                 │
│        ├──► Yes ──► SequentialPipeline                  │
│        │         A ──► B ──► C                        │
│        │                                                   │
│        └──► No ──► 需要同时通知？                      │
│                      │                                   │
│                      ├──► Yes ──► MsgHub                │
│                      │     发布 ──► [A, B, C]           │
│                      │                                   │
│                      └──► No ──► FanoutPipeline         │
│                            input ──► [A, B, C]           │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Pipeline是**有向无环图**，MsgHub是**广播图**。根据数据流选择合适组件。

---

## 💡 Java开发者注意

MsgHub类似Java的**EventBus**或**Message Broker**：

```java
// Google Guava EventBus
EventBus eventBus = new EventBus();
eventBus.register(subscriber);
eventBus.post(new TaskEvent("完成"));
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **MsgHub和Pipeline的区别？**
   - Pipeline：硬编码顺序
   - MsgHub：动态订阅，松耦合

2. **MsgHub适合什么场景？**
   - 事件通知
   - 日志广播
   - 监控报警

</details>

---

★ **Insight** ─────────────────────────────────────
- **MsgHub = 广播站**，发布者发消息，订阅者自动收到
- 适合**松耦合**的多Agent系统
─────────────────────────────────────────────────
