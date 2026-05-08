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
