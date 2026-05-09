# 第6章 发布-订阅模式（MsgHub）

> **目标**：理解MsgHub如何实现多Agent松耦合通信

---

## 🎯 学习目标

学完之后，你能：
- 理解发布-订阅模式
- 使用MsgHub协调多Agent
- 广播消息给所有参与者
- 区分Pipeline和MsgHub的使用场景

---

## 🚀 先跑起来

```python
from agentscope.pipeline import MsgHub

# 创建MsgHub协调多Agent
async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # 广播消息给所有参与者
    await hub.broadcast(Msg(
        name="system",
        content="任务开始",
        role="system"
    ))
```

---

## 🔍 发布-订阅模式

### 核心概念

```
发布者 ──广播──→ MsgHub ──分发──→ [订阅者1, 订阅者2, ...]
```

**vs Pipeline的区别**：

| 特性 | Pipeline | MsgHub |
|------|----------|--------|
| 消息传递 | 点对点，依次传递 | 广播给所有 |
| 耦合度 | 紧耦合（A→B→C） | 松耦合 |
| 执行顺序 | 顺序执行 | 并行处理 |
| 适用场景 | 流水线处理 | 事件通知 |

---

## 💡 Java开发者注意

MsgHub类似Java的消息中间件：

```python
# Python MsgHub
async with MsgHub(participants=[a1, a2]) as hub:
    await hub.broadcast(msg)
```

```java
// Java EventBus (Google Guava)
eventBus.register(subscriber);
eventBus.post(message);  // 广播给所有订阅者
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **MsgHub和Pipeline的核心区别？**
   - Pipeline：消息依次传递，下游依赖上游
   - MsgHub：广播出去，所有订阅者同时收到

2. **MsgHub适合什么场景？**
   - 事件通知（新人加入、任务完成）
   - 日志广播
   - 监控报警

</details>

---

★ **Insight** ─────────────────────────────────────
- **MsgHub = 广播站**，发布者发消息，所有订阅者收到
- **松耦合**，发布者不关心谁在听
- **Pipeline = 流水线**，有依赖关系，必须顺序执行
─────────────────────────────────────────────────
